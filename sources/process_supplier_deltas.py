from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "yml_sources.json"
STATE_DIR = BASE_DIR / "state"
OUTPUT_DIR = BASE_DIR / "changed"

OFFER_RE = re.compile(rb"<offer\b[^>]*(?:/>|>.*?</offer\s*>)", re.IGNORECASE | re.DOTALL)
ID_ATTR_RE = re.compile(rb"\bid\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)
FALLBACK_ID_RES = [
    re.compile(rb"<vendorCode\b[^>]*>(.*?)</vendorCode\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(rb"<sku\b[^>]*>(.*?)</sku\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(rb"<article\b[^>]*>(.*?)</article\s*>", re.IGNORECASE | re.DOTALL),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def split_feed(feed: bytes) -> tuple[bytes, list[bytes], bytes]:
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

    if not offers:
        raise ValueError("В контейнере <offers> не найдено ни одной позиции <offer>.")

    return prefix, offers, suffix


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


def process_supplier(key: str, item: dict) -> dict:
    url = item["url"]
    response = requests.get(
        url,
        timeout=(20, 120),
        headers={
            "User-Agent": "Megapolis-YML-Delta/1.0 (+GitHub Actions)",
            "Accept": "application/xml,text/xml,application/yaml,text/plain,*/*",
        },
    )
    response.raise_for_status()
    feed = response.content

    prefix, offers, suffix = split_feed(feed)

    current_hashes: dict[str, str] = {}
    raw_by_key: dict[str, bytes] = {}
    duplicate_keys: list[str] = []

    for index, raw in enumerate(offers):
        identity = offer_key(raw, index)
        if identity in current_hashes:
            duplicate_keys.append(identity)
            identity = f"{identity}__duplicate__{index}"
        digest = hashlib.sha256(normalize_offer(raw)).hexdigest()
        current_hashes[identity] = digest
        raw_by_key[identity] = raw

    state_path = STATE_DIR / f"{key}.json"
    previous_state = load_state(state_path)
    previous_hashes = previous_state.get("offers", {})

    changed_keys = [
        identity
        for identity, digest in current_hashes.items()
        if previous_hashes.get(identity) != digest
    ]
    removed_keys = [identity for identity in previous_hashes if identity not in current_hashes]

    # Позиции в результате копируются из исходного YML как есть. Меняется только состав <offers>:
    # остаются новые или изменившиеся позиции по сравнению с предыдущей успешной проверкой.
    separator = b"\n"
    changed_body = separator.join(raw_by_key[identity] for identity in changed_keys)
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
        "checked_at_utc": utc_now(),
        "offer_count": len(current_hashes),
        "changed_count": len(changed_keys),
        "removed_count": len(removed_keys),
        "removed_offer_keys": removed_keys,
        "duplicate_offer_keys": duplicate_keys,
        "offers": current_hashes,
    }
    state_changed = write_json_if_changed(state_path, state)

    return {
        "supplier": key,
        "total": len(current_hashes),
        "changed": len(changed_keys),
        "removed": len(removed_keys),
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
                f"удалено_из_источника={result['removed']}"
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
