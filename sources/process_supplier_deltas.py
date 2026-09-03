from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
CONFIG_PATH = BASE_DIR / "yml_sources.json"
STATE_DIR = BASE_DIR / "state"
OUTPUT_DIR = BASE_DIR / "changed"
TOMBSTONE_DAYS = 7

OFFER_RE = re.compile(rb"<offer\b[^>]*(?:/>|>.*?</offer\s*>)", re.IGNORECASE | re.DOTALL)
ID_ATTR_RE = re.compile(rb"\bid\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)
FALLBACK_ID_RES = [
    re.compile(rb"<vendorCode\b[^>]*>(.*?)</vendorCode\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(rb"<sku\b[^>]*>(.*?)</sku\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(rb"<article\b[^>]*>(.*?)</article\s*>", re.IGNORECASE | re.DOTALL),
]

STOCK_TAG_NAMES = (
    b"quantity",
    b"qty",
    b"stock",
    b"stockQuantity",
    b"quantityInStock",
    b"availableQuantity",
    b"instock",
    b"inStock",
    b"rest",
)
STOCK_PARAM_HINTS = (
    "остат",
    "налич",
    "stock",
    "qty",
    "quantity",
    "in stock",
    "instock",
    "складской остат",
    "свободный остат",
)


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def utc_now() -> str:
    return utc_now_dt().isoformat(timespec="seconds")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_offer(raw: bytes) -> bytes:
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").strip()
    normalized = re.sub(rb">\s+<", b"><", normalized)
    return normalized


def offer_key(raw: bytes, index: int) -> str:
    opening_end = raw.find(b">")
    opening = raw if opening_end < 0 else raw[: opening_end + 1]
    match = ID_ATTR_RE.search(opening)
    if match:
        return match.group(2).decode("utf-8", errors="replace").strip()

    for pattern in FALLBACK_ID_RES:
        match = pattern.search(raw)
        if match:
            value = re.sub(rb"\s+", b" ", match.group(1)).strip()
            if value:
                return value.decode("utf-8", errors="replace")

    return f"__no_id__:{index}:{hashlib.sha256(normalize_offer(raw)).hexdigest()}"


def split_feed(feed: bytes, require_offers: bool = True) -> tuple[bytes, list[bytes], bytes]:
    open_match = re.search(rb"<offers\b[^>]*>", feed, flags=re.IGNORECASE)
    if not open_match:
        raise ValueError("В XML/YML не найден контейнер <offers>.")

    close_match = re.search(rb"</offers\s*>", feed[open_match.end() :], flags=re.IGNORECASE)
    if not close_match:
        raise ValueError("В XML/YML не найден закрывающий тег </offers>.")

    close_start = open_match.end() + close_match.start()
    prefix = feed[: open_match.end()]
    body = feed[open_match.end() : close_start]
    suffix = feed[close_start:]
    offers = OFFER_RE.findall(body)

    if require_offers and not offers:
        raise ValueError("В контейнере <offers> не найдено ни одной позиции <offer>.")

    return prefix, offers, suffix


def map_offers(offers: list[bytes]) -> tuple[dict[str, bytes], list[str]]:
    raw_by_key: dict[str, bytes] = {}
    duplicate_keys: list[str] = []

    for index, raw in enumerate(offers):
        identity = offer_key(raw, index)
        if identity in raw_by_key:
            duplicate_keys.append(identity)
            identity = f"{identity}__duplicate__{index}"
        raw_by_key[identity] = raw

    return raw_by_key, duplicate_keys


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_bytes_if_changed(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def write_json_if_changed(path: Path, data: dict) -> bool:
    content = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    encoded = content.encode("utf-8")
    return write_bytes_if_changed(path, encoded)


def force_available_false(raw: bytes) -> tuple[bytes, bool]:
    opening_match = re.match(rb"(<offer\b)(.*?)(/?>)", raw, flags=re.IGNORECASE | re.DOTALL)
    if not opening_match:
        return raw, False

    attrs = opening_match.group(2)
    available_re = re.compile(rb"(\bavailable\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE | re.DOTALL)
    if available_re.search(attrs):
        new_attrs = available_re.sub(rb'\1"false"', attrs, count=1)
    else:
        new_attrs = attrs.rstrip() + b' available="false"'

    replacement = opening_match.group(1) + new_attrs + opening_match.group(3)
    return replacement + raw[opening_match.end() :], True


def zero_stock_offer(raw: bytes) -> bytes:
    """Возвращает последнюю известную позицию, не меняя её поля кроме признаков/значений остатка."""
    result, _ = force_available_false(raw)

    # Частые XML/YML-теги количества на складе: <quantity>12</quantity>, <stock>5</stock> и т.п.
    for tag in STOCK_TAG_NAMES:
        pattern = re.compile(
            rb"(<" + tag + rb"\b[^>]*>)(.*?)(</" + tag + rb"\s*>)",
            re.IGNORECASE | re.DOTALL,
        )
        result = pattern.sub(rb"\g<1>0\g<3>", result)

    # YML outlet и аналогичные атрибуты остатка: instock="12", stock="5", quantity="3".
    result = re.sub(
        rb"(\b(?:instock|in_stock|stock|quantity|qty)\s*=\s*)([\"'])([^\"']*)(\2)",
        rb'\1"0"',
        result,
        flags=re.IGNORECASE,
    )

    # Поставщики нередко передают остаток через <param name="Остаток">12</param>.
    param_re = re.compile(
        rb"(<param\b[^>]*\bname\s*=\s*([\"'])(.*?)\2[^>]*>)(.*?)(</param\s*>)",
        re.IGNORECASE | re.DOTALL,
    )

    def zero_param(match: re.Match[bytes]) -> bytes:
        name = match.group(3).decode("utf-8", errors="ignore").strip().lower()
        if any(hint in name for hint in STOCK_PARAM_HINTS):
            return match.group(1) + b"0" + match.group(5)
        return match.group(0)

    result = param_re.sub(zero_param, result)
    return result


def git_show_bytes(revision: str, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=REPO_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def recover_offers_from_git(key: str, identities: list[str]) -> dict[str, bytes]:
    """
    Находит последнюю реально переданную версию исчезнувшего offer в истории delta-файла.
    Это позволяет не хранить полную копию многомегабайтной выгрузки на каждом запуске.
    """
    if not identities:
        return {}

    relative_path = f"sources/changed/{key}.xml"
    recovered: dict[str, bytes] = {}
    revision_cache: dict[str, dict[str, bytes]] = {}

    def offers_for_revision(revision: str) -> dict[str, bytes]:
        if revision in revision_cache:
            return revision_cache[revision]

        content = git_show_bytes(revision, relative_path)
        if not content:
            revision_cache[revision] = {}
            return {}

        try:
            _, revision_offers, _ = split_feed(content, require_offers=False)
            mapped, _ = map_offers(revision_offers)
        except Exception:
            mapped = {}
        revision_cache[revision] = mapped
        return mapped

    for identity in identities:
        # -S сокращает историю до коммитов, где идентификатор появлялся/исчезал в delta-файле.
        # Затем проверяем сам коммит и его родителя, чтобы взять последнюю версию позиции.
        log_proc = subprocess.run(
            ["git", "log", "--format=%H", "-S", identity, "--", relative_path],
            cwd=REPO_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
        candidate_shas = [line.strip() for line in log_proc.stdout.splitlines() if line.strip()]

        for sha in candidate_shas:
            for revision in (sha, f"{sha}^"):
                mapped = offers_for_revision(revision)
                if identity in mapped:
                    recovered[identity] = mapped[identity]
                    break
            if identity in recovered:
                break

    return recovered


def process_supplier(key: str, item: dict) -> dict:
    url = item["url"]
    response = requests.get(
        url,
        timeout=(20, 120),
        headers={
            "User-Agent": "Megapolis-YML-Delta/1.1 (+GitHub Actions)",
            "Accept": "application/xml,text/xml,application/yaml,text/plain,*/*",
        },
    )
    response.raise_for_status()
    feed = response.content

    prefix, offers, suffix = split_feed(feed)
    raw_by_key, duplicate_keys = map_offers(offers)
    current_hashes = {
        identity: hashlib.sha256(normalize_offer(raw)).hexdigest()
        for identity, raw in raw_by_key.items()
    }

    state_path = STATE_DIR / f"{key}.json"
    previous_state = load_state(state_path)
    previous_hashes = previous_state.get("offers", {})
    previous_tombstones = previous_state.get("tombstones", {})

    changed_keys = [
        identity
        for identity, digest in current_hashes.items()
        if previous_hashes.get(identity) != digest
    ]
    removed_keys = [identity for identity in previous_hashes if identity not in current_hashes]

    now = utc_now_dt()
    active_tombstones: dict[str, dict] = {}

    # Уже пропавшие товары повторяем с нулевым остатком на каждом запуске 7 суток.
    for identity, tombstone in previous_tombstones.items():
        if identity in current_hashes:
            # Товар вернулся в исходную выгрузку — нулевую запись больше не передаём.
            continue

        try:
            first_missing = parse_utc(tombstone["first_missing_at_utc"])
            zeroed_offer = base64.b64decode(tombstone["offer_b64"])
        except Exception:
            continue

        if now - first_missing < timedelta(days=TOMBSTONE_DAYS):
            active_tombstones[identity] = {
                "first_missing_at_utc": first_missing.isoformat(timespec="seconds"),
                "offer_b64": base64.b64encode(zeroed_offer).decode("ascii"),
            }

    # Первый запуск после исчезновения: достаём последнюю версию позиции из Git-истории
    # и превращаем только её остаток/доступность в ноль.
    new_removed_keys = [identity for identity in removed_keys if identity not in previous_tombstones]
    recovered = recover_offers_from_git(key, new_removed_keys)

    unrecovered: list[str] = []
    for identity in new_removed_keys:
        raw = recovered.get(identity)
        if raw is None:
            unrecovered.append(identity)
            continue

        zeroed = zero_stock_offer(raw)
        active_tombstones[identity] = {
            "first_missing_at_utc": now.isoformat(timespec="seconds"),
            "offer_b64": base64.b64encode(zeroed).decode("ascii"),
        }

    # Позиции результата:
    # 1) новые/изменённые — копируются из источника как есть;
    # 2) исчезнувшие — последняя известная версия с нулевым остатком, повторяемая 7 дней.
    output_offers = [raw_by_key[identity] for identity in changed_keys]
    output_offers.extend(
        base64.b64decode(tombstone["offer_b64"])
        for tombstone in active_tombstones.values()
    )

    separator = b"\n"
    changed_body = separator.join(output_offers)
    if changed_body:
        changed_body = separator + changed_body + separator
    else:
        changed_body = separator
    delta_feed = prefix + changed_body + suffix

    output_path = OUTPUT_DIR / f"{key}.xml"
    output_changed = write_bytes_if_changed(output_path, delta_feed)

    state = {
        "source_name": item.get("name", key),
        "source_url": url,
        "checked_at_utc": now.isoformat(timespec="seconds"),
        "offer_count": len(current_hashes),
        "changed_count": len(changed_keys),
        "removed_count": len(removed_keys),
        "removed_offer_keys": removed_keys,
        "zero_stock_retention_days": TOMBSTONE_DAYS,
        "zero_stock_count": len(active_tombstones),
        "zero_stock_offer_keys": sorted(active_tombstones),
        "unrecovered_removed_offer_keys": unrecovered,
        "duplicate_offer_keys": duplicate_keys,
        "tombstones": active_tombstones,
        "offers": current_hashes,
    }
    state_changed = write_json_if_changed(state_path, state)

    if unrecovered:
        print(
            f"[{key}] ПРЕДУПРЕЖДЕНИЕ: не удалось восстановить для обнуления: "
            + ", ".join(unrecovered),
            file=sys.stderr,
        )

    return {
        "supplier": key,
        "total": len(current_hashes),
        "changed": len(changed_keys),
        "removed": len(removed_keys),
        "zero_stock": len(active_tombstones),
        "unrecovered": len(unrecovered),
        "output_changed": output_changed,
        "state_changed": state_changed,
    }


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        suppliers = json.load(fh)

    failures: list[str] = []
    for key, item in suppliers.items():
        try:
            result = process_supplier(key, item)
            print(
                f"[{key}] всего={result['total']}; изменено={result['changed']}; "
                f"исчезло={result['removed']}; ноль_7дней={result['zero_stock']}; "
                f"не_восстановлено={result['unrecovered']}"
            )
        except Exception as exc:
            failures.append(key)
            print(f"[{key}] ОШИБКА: {exc}", file=sys.stderr)

    if failures:
        print(
            "Не удалось обновить: " + ", ".join(failures) + ". "
            "Их предыдущие файлы и состояние не были перезаписаны.",
            file=sys.stderr,
        )

    # Ошибка одного поставщика не должна блокировать сохранение успешно обработанных поставщиков.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
