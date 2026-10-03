import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from xml.etree import ElementTree as ET


API_URL = "https://norden.group/api-products/"
CATEGORIES_URL = "https://norden.group/api-categories/"
FULL_XML_URL = "https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.xml"
PRICE_XML_URL = "https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.group+-K8%25.xml"

TECHNICAL_XML_TAGS = {
    "Ссылка", "Код", "Наименование", "Группа", "Артикул",
    "ВесЕдиницаИзмерения", "ВесЗнаменатель", "ВесИспользовать",
    "ВесМожноУказыватьВДокументах", "ВесЧислитель", "ВестиУчетПоГТД",
    "ВидНоменклатуры", "ЕдиницаИзмерения", "ДлинаЕдиницаИзмерения",
    "ДлинаЗнаменатель", "ДлинаИспользовать", "ДлинаМожноУказыватьВДокументах",
    "ДлинаЧислитель", "КодДляПоиска", "Марка", "НаборУпаковок",
    "НаименованиеПолное", "ОбъемДАЛ", "Производитель", "СтавкаНДС",
    "ТипНоменклатуры", "ОбъемЕдиницаИзмерения", "СезоннаяГруппа",
    "КоллекцияНоменклатуры", "АртикулДляПоиска", "ОбъемЕдиницаИзмерения1",
    "ОбъемЗнаменатель", "ОбъемИспользовать", "ОбъемЧислитель",
}


def _s(value):
    return str(value or "").replace("\xa0", " ").strip()


def _stock(value):
    text = _s(value).replace(" ", "").replace(",", ".")
    if not text:
        return 0.0
    if text.startswith(">"):
        text = text[1:]
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def _http_bytes(url, headers=None, timeout=120, max_bytes=100 * 1024 * 1024):
    req = urllib.request.Request(
        url,
        headers=dict({"User-Agent": "Megasuppliers-Supplier-Engine/1.1"}, **(headers or {})),
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > max_bytes:
            raise ValueError("Norden source exceeds configured size limit")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Norden source exceeds configured size limit")
    return data


def _http_json(url, headers=None, timeout=120):
    return json.loads(_http_bytes(url, headers=headers, timeout=timeout).decode("utf-8"))


def _category_paths(secret):
    data = _http_json(CATEGORIES_URL, headers={"secret": secret, "Accept": "application/json"})
    if not isinstance(data, list):
        return {}
    by_id = {_s(row.get("category_id")): row for row in data if isinstance(row, dict)}
    cache = {}

    def chain(cid):
        cid = _s(cid)
        if cid in cache:
            return cache[cid]
        out = []
        seen = set()
        cur = cid
        while cur and cur not in seen and cur in by_id:
            seen.add(cur)
            row = by_id[cur]
            title = _s(row.get("name"))
            if title:
                out.append(title)
            parent = _s(row.get("parent_id"))
            cur = "" if parent in ("", "0") else parent
        out.reverse()
        cache[cid] = out
        return out

    return {cid: chain(cid) for cid in by_id}


def _api_products(secret):
    if not secret:
        raise RuntimeError("NORDEN_SECRET is empty")
    headers = {"secret": secret, "Accept": "application/json"}
    products = []
    page = 1
    last_call = 0.0
    while True:
        if page > 1:
            delay = 6.2 - (time.monotonic() - last_call)
            if delay > 0:
                time.sleep(delay)
        url = API_URL + "?" + urllib.parse.urlencode({"page": page})
        data = _http_json(url, headers=headers)
        last_call = time.monotonic()
        if not isinstance(data, dict):
            raise RuntimeError("Norden API returned unexpected payload")
        batch = data.get("products") or []
        if not isinstance(batch, list):
            raise RuntimeError("Norden API products payload is invalid")
        products.extend(row for row in batch if isinstance(row, dict))
        page_data = data.get("page_data") or {}
        total = int(str(page_data.get("total_items") or 0).replace(" ", "") or 0)
        per_page = int(str(page_data.get("items_per_page") or len(batch) or 500))
        if not batch or (total and len(products) >= total) or len(batch) < per_page:
            break
        page += 1
    return products


def _features_to_dict(features):
    out = {}
    for row in features or []:
        if not isinstance(row, dict):
            continue
        name = _s(row.get("name"))
        value = _s(row.get("value"))
        if name and value:
            out[name] = value
    return out



def _dedupe_rows(rows):
    by_article = {}
    duplicates = []
    for row in rows:
        article = _s((row or {}).get("product_code"))
        if not article:
            continue
        if article in by_article:
            duplicates.append(article)
        by_article[article] = row
    return list(by_article.values()), sorted(set(duplicates))


def _api_rows(secret):
    products = _api_products(secret)
    try:
        paths = _category_paths(secret)
    except Exception:
        paths = {}

    rows = []
    for raw in products:
        article = _s(raw.get("product_code"))
        if not article:
            continue
        category_ids = [_s(x) for x in _s(raw.get("category")).split(",") if _s(x)]
        candidates = [paths.get(cid) or [] for cid in category_ids]
        candidates = [x for x in candidates if x]
        category = " > ".join(max(candidates, key=len)) if candidates else _s(raw.get("category"))
        rows.append({
            "product_code": article,
            "Kod": _s(raw.get("Kod")),
            "name": _s(raw.get("name")) or article,
            "description": _s(raw.get("description")),
            "price": raw.get("price"),
            "price_rrc": raw.get("price_rrc"),
            "qty": raw.get("qty"),
            "category": category,
            "images": [_s(x) for x in (raw.get("images") or []) if _s(x)],
            "features": _features_to_dict(raw.get("features")),
        })
    rows, _ = _dedupe_rows(rows)
    if not rows:
        raise RuntimeError("Norden API returned no products")
    return rows


def _fallback_rows():
    price_root = ET.fromstring(_http_bytes(PRICE_XML_URL))
    price_index = {}
    for item in price_root.iter("Номенклатура"):
        article = _s(item.findtext("Артикул"))
        if not article:
            continue
        purchase = None
        for price in item.findall("Цена"):
            if _s(price.attrib.get("ВидЦен")).casefold() == "опт":
                purchase = _s(price.text)
                break
        stock = None
        for node in item.findall("СвободныйОстаток"):
            if _s(node.attrib.get("Склад")) == "Основной склад":
                stock = _stock(node.text)
                break
        price_index[article] = {"price": purchase, "qty": stock}

    full_root = ET.fromstring(_http_bytes(FULL_XML_URL))
    rows = []
    for item in full_root.iter("Номенклатура"):
        article = _s(item.findtext("Артикул"))
        if not article:
            continue
        name = _s(item.findtext("НаименованиеПолное")) or _s(item.findtext("Наименование")) or article
        group = _s(item.findtext("Группа"))
        category = " > ".join([x.strip() for x in group.split("///") if x.strip()])
        images = []
        features = {}
        for child in list(item):
            tag = _s(child.tag)
            value = _s(child.text)
            if not tag or not value:
                continue
            if tag.startswith("Ссылканафото"):
                images.append(value)
                continue
            if tag in TECHNICAL_XML_TAGS or tag in {"Цена", "ОбщийОстаток", "СвободныйОстаток", "Заказано"}:
                continue
            features[tag] = value
        features["Код Norden"] = _s(item.findtext("Код"))
        price = price_index.get(article) or {}
        rows.append({
            "product_code": article,
            "Kod": _s(item.findtext("Код")),
            "name": name,
            "description": _s(item.findtext("Особенностимодели")),
            "price": price.get("price"),
            "price_rrc": None,
            "qty": price.get("qty"),
            "category": category or "Norden",
            "images": list(dict.fromkeys(images)),
            "features": features,
        })
    rows, _ = _dedupe_rows(rows)
    if not rows:
        raise RuntimeError("Norden fallback XML returned no products")
    return rows


def canonical_bytes(rows):
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_norden_source():
    secret = os.getenv("NORDEN_SECRET", "").strip()
    api_error = ""
    try:
        rows = _api_rows(secret)
        return rows, canonical_bytes(rows), {
            "origin": "api",
            "fallback_used": False,
            "api_error": "",
        }
    except Exception as exc:
        api_error = "%s: %s" % (type(exc).__name__, str(exc)[:400])

    rows = _fallback_rows()
    return rows, canonical_bytes(rows), {
        "origin": "xml_fallback",
        "fallback_used": True,
        "api_error": api_error,
    }
