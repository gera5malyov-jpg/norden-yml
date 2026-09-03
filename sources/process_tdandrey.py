from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

import requests

BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "state" / "tdandrey.json"
OUTPUT_PATH = BASE_DIR / "changed" / "tdandrey.xml"
SOURCE_URL = "https://lk.tdandrey.ru/partner-export/v1/a163560406b5776bda46922e46cf300ce492"
PAGE_LIMIT = 5000
MAX_PAGES = 1000

PRODUCT_LIST_KEYS = ("products", "items", "data", "result", "results", "offers", "goods")
PRODUCT_TAGS = {"product", "item", "offer", "good"}
ID_KEYS = ("id", "product_id", "productid", "offer_id", "offerid", "sku", "article", "articul", "vendor_code", "vendorcode", "code")
NAME_KEYS = ("name", "title", "product_name", "productname")
VENDOR_KEYS = ("vendor.name", "vendor", "brand", "manufacturer", "producer")
VENDOR_CODE_KEYS = ("vendor_code", "vendorcode", "article", "articul", "sku", "code")
BARCODE_KEYS = ("barcode", "bar_code", "ean", "ean13")
DESCRIPTION_KEYS = ("description", "desc", "text", "annotation")
CATEGORY_ID_KEYS = ("category_id", "categoryid", "category.id")
CATEGORY_NAME_KEYS = ("category_name", "categoryname", "category.name", "category_title", "categorytitle")

WAREHOUSES = (
    ("spb", "СПБ", "Санкт-Петербург"),
    ("msk", "Москва", "Москва"),
    ("ptg", "Пятигорск", "Пятигорск"),
)


def add_query(url: str, **params: object) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: str(v) for k, v in params.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xml_to_obj(element: ET.Element) -> object:
    children = list(element)
    result: dict[str, object] = {f"@{local_name(k)}": v for k, v in element.attrib.items()}
    text = (element.text or "").strip()
    if not children:
        if result:
            if text:
                result["#text"] = text
            return result
        return text
    grouped: dict[str, list[object]] = {}
    for child in children:
        grouped.setdefault(local_name(child.tag), []).append(xml_to_obj(child))
    for key, values in grouped.items():
        result[key] = values[0] if len(values) == 1 else values
    if text:
        result["#text"] = text
    return result


def extract_json_items(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    lowered = {str(k).lower(): k for k in payload}
    for name in PRODUCT_LIST_KEYS:
        original = lowered.get(name)
        if original is None:
            continue
        value = payload[original]
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = extract_json_items(value)
            if nested:
                return nested
    lists = [value for value in payload.values() if isinstance(value, list)]
    return lists[0] if len(lists) == 1 else []


def extract_xml_items(root: ET.Element) -> list[object]:
    parent = {child: p for p in root.iter() for child in p}
    candidates = [e for e in root.iter() if local_name(e.tag).lower() in PRODUCT_TAGS]
    if candidates:
        by_depth: list[tuple[int, ET.Element]] = []
        for e in candidates:
            depth = 0
            cur = e
            while cur in parent:
                depth += 1
                cur = parent[cur]
            by_depth.append((depth, e))
        minimum = min(depth for depth, _ in by_depth)
        return [xml_to_obj(e) for depth, e in by_depth if depth == minimum]
    children = list(root)
    if children and len({local_name(c.tag) for c in children}) == 1:
        return [xml_to_obj(c) for c in children]
    return []


def parse_page(response: requests.Response) -> tuple[list[object], str]:
    raw = response.content.lstrip()
    content_type = (response.headers.get("content-type") or "").lower()
    if "json" in content_type or raw.startswith((b"{", b"[")):
        return extract_json_items(response.json()), "json"
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise ValueError(f"Неподдерживаемый ответ API: {content_type or 'unknown'}") from exc
    return extract_xml_items(root), "xml"


def flatten(value: object, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(flatten(child, path))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            out.extend(flatten(child, f"{prefix}[{i}]"))
    elif value is not None:
        out.append((prefix, "true" if value is True else "false" if value is False else str(value)))
    return out


def norm(path: str) -> str:
    return re.sub(r"\[\d+\]", "", path).replace("@", "").strip().lower()


def first_value(flat: list[tuple[str, str]], keys: tuple[str, ...]) -> str | None:
    wanted = {k.lower() for k in keys}
    for path, value in flat:
        p = norm(path)
        leaf = p.rsplit(".", 1)[-1]
        if (p in wanted or leaf in wanted) and value.strip():
            return value.strip()
    return None


def exact_value(flat: list[tuple[str, str]], paths: tuple[str, ...]) -> str | None:
    wanted = {p.lower() for p in paths}
    for path, value in flat:
        if norm(path) in wanted and value.strip():
            return value.strip()
    return None


def number(value: str) -> float | None:
    text = value.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def warehouse_values(flat: list[tuple[str, str]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for code, label, full_name in WAREHOUSES:
        stock = exact_value(flat, (f"stock.{code}.value",)) or "0"
        wholesale = exact_value(flat, (f"price.{code}.valueopt",)) or ""
        retail = exact_value(flat, (f"price.{code}.valueretail",)) or ""
        result[code] = {
            "label": label,
            "name": full_name,
            "stock": stock,
            "wholesale": wholesale,
            "retail": retail,
        }
    return result


def in_stock(flat: list[tuple[str, str]]) -> tuple[bool, list[str]]:
    warehouses = warehouse_values(flat)
    paths: list[str] = []
    has_stock = False
    for code, _label, _full_name in WAREHOUSES:
        paths.append(f"stock.{code}.value")
        value = number(warehouses[code]["stock"])
        if value is not None and value > 0:
            has_stock = True
    return has_stock, paths


def identity(flat: list[tuple[str, str]], index: int) -> str:
    value = first_value(flat, ID_KEYS)
    if value:
        return value
    digest = hashlib.sha256(json.dumps(flat, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
    return f"tdandrey-{index}-{digest}"


def pictures(flat: list[tuple[str, str]]) -> list[str]:
    result: list[str] = []
    for path, value in flat:
        p = norm(path)
        if "images.url" in p and value.strip().startswith(("http://", "https://")) and value.strip() not in result:
            result.append(value.strip())
    return result


def tag(name: str, value: str) -> str:
    return f"<{name}>{escape(value)}</{name}>"


def build_offer(product: object, index: int) -> tuple[str, bytes, str | None, str | None, list[str]] | None:
    flat = flatten(product)
    ok, stock_paths = in_stock(flat)
    if not ok:
        return None

    key = identity(flat, index)
    name = first_value(flat, NAME_KEYS) or key
    category_id = first_value(flat, CATEGORY_ID_KEYS)
    category_name = first_value(flat, CATEGORY_NAME_KEYS)
    warehouses = warehouse_values(flat)

    # Основная цена YML: первая доступная розничная цена в порядке СПБ -> Москва -> Пятигорск.
    retail_price = next((warehouses[code]["retail"] for code, _label, _name in WAREHOUSES if warehouses[code]["retail"]), None)

    lines = [f"<offer id={quoteattr(key)} available=\"true\">", tag("name", name)]
    product_url = exact_value(flat, ("url", "link", "product_url", "producturl"))
    if product_url:
        lines.append(tag("url", product_url))
    if retail_price:
        lines.append(tag("price", retail_price))
    lines.append("<currencyId>RUR</currencyId>")
    if category_id:
        lines.append(tag("categoryId", category_id))

    for element, value in (
        ("vendor", first_value(flat, VENDOR_KEYS)),
        ("vendorCode", first_value(flat, VENDOR_CODE_KEYS)),
        ("barcode", first_value(flat, BARCODE_KEYS)),
    ):
        if value:
            lines.append(tag(element, value))

    for value in pictures(flat):
        lines.append(tag("picture", value))

    description = first_value(flat, DESCRIPTION_KEYS)
    if description:
        lines.append(tag("description", description))

    # Все три склада и цены каждого склада отдельными понятными полями.
    for code, label, _full_name in WAREHOUSES:
        data = warehouses[code]
        lines.append(f"<param name={quoteattr('Остаток ' + label)}>{escape(data['stock'])}</param>")
        if data["wholesale"]:
            lines.append(f"<param name={quoteattr('Оптовая цена ' + label)}>{escape(data['wholesale'])}</param>")
        if data["retail"]:
            lines.append(f"<param name={quoteattr('Розничная цена ' + label)}>{escape(data['retail'])}</param>")

    # Полный набор исходных полей API сохраняем без потерь.
    for path, value in flat:
        if path:
            lines.append(f"<param name={quoteattr(path)}>{escape(value)}</param>")

    lines.append("</offer>")
    return key, "\n".join(lines).encode("utf-8"), category_id, category_name, stock_paths


def fetch_all() -> tuple[list[object], str]:
    all_items: list[object] = []
    seen: set[str] = set()
    response_format = "unknown"

    for page in range(1, MAX_PAGES + 1):
        response = requests.get(
            add_query(SOURCE_URL, page=page, limit=PAGE_LIMIT),
            timeout=(20, 180),
            headers={
                "User-Agent": "Megapolis-TDAndrey-YML/1.1",
                "Accept": "application/json,application/xml,text/xml,text/plain,*/*",
            },
        )
        response.raise_for_status()
        items, response_format = parse_page(response)
        if not items:
            break

        fingerprint = hashlib.sha256(response.content).hexdigest()
        if fingerprint in seen:
            raise ValueError(f"API повторил страницу {page}; пагинация остановлена")
        seen.add(fingerprint)
        all_items.extend(items)

        if len(items) < PAGE_LIMIT:
            break
    else:
        raise ValueError(f"Превышен защитный лимит {MAX_PAGES} страниц")

    return all_items, response_format


def prefix(categories: dict[str, str]) -> bytes:
    date = datetime.now().astimezone().isoformat(timespec="seconds")
    cats = "".join(
        f"      <category id={quoteattr(k)}>{escape(v)}</category>\n"
        for k, v in sorted(categories.items())
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<yml_catalog date="{escape(date)}">\n'
        '  <shop>\n'
        '    <name>TD Andrey</name>\n'
        '    <company>TD Andrey</company>\n'
        '    <currencies><currency id="RUR" rate="1"/></currencies>\n'
        f'    <categories>\n{cats}    </categories>\n'
        '    <offers>'
    ).encode("utf-8")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def write_if_changed(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def main() -> int:
    products, response_format = fetch_all()
    offers: dict[str, bytes] = {}
    categories: dict[str, str] = {}
    stock_paths: set[str] = set()
    filtered = 0
    duplicates: list[str] = []

    for index, product in enumerate(products):
        built = build_offer(product, index)
        if built is None:
            filtered += 1
            continue

        key, raw, category_id, category_name, paths = built
        stock_paths.update(paths)
        if key in offers:
            duplicates.append(key)
            key = f"{key}__duplicate__{index}"
            raw = raw.replace(
                b"<offer id=",
                f"<offer id={quoteattr(key)} data-original-id=".encode("utf-8"),
                1,
            )
        offers[key] = raw
        if category_id:
            categories[category_id] = category_name or category_id

    hashes = {
        key: hashlib.sha256(re.sub(rb">\s+<", b"><", raw.strip())).hexdigest()
        for key, raw in offers.items()
    }
    previous = load_state().get("offers", {})
    changed = [key for key, digest in hashes.items() if previous.get(key) != digest]
    removed = [key for key in previous if key not in hashes]

    # В YML попадают только товары, у которых есть остаток хотя бы на одном из трех складов.
    # Нулевые товары и tombstone для TD Andrey не публикуем.
    body = b"\n" + b"\n".join(offers[key] for key in changed) + b"\n" if changed else b"\n"
    yml = prefix(categories) + body + b"</offers>\n  </shop>\n</yml_catalog>\n"
    output_changed = write_if_changed(OUTPUT_PATH, yml)

    state = {
        "source_url": SOURCE_URL,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_format": response_format,
        "api_product_count": len(products),
        "in_stock_any_warehouse_offer_count": len(offers),
        "out_of_stock_all_warehouses_filtered_count": filtered,
        "changed_count": len(changed),
        "removed_from_in_stock_count": len(removed),
        "removed_from_in_stock_offer_keys": removed,
        "zero_stock_count": 0,
        "duplicate_offer_keys": duplicates,
        "detected_stock_field_paths": sorted(stock_paths),
        "warehouses": [
            {"code": code, "label": label, "name": full_name}
            for code, label, full_name in WAREHOUSES
        ],
        "offers": hashes,
    }
    state_bytes = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    state_changed = write_if_changed(STATE_PATH, state_bytes)

    print(
        f"[tdandrey] API={response_format}; всего={len(products)}; "
        f"в_наличии_хотя_бы_на_1_складе={len(offers)}; "
        f"без_наличия_на_всех_3_складах={filtered}; изменено={len(changed)}; "
        f"вышло_из_наличия={len(removed)}; файл_изменен={output_changed}; "
        f"состояние_изменено={state_changed}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())