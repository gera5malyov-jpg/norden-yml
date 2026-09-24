#!/usr/bin/env python3
import json, os, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

OZON_BASE = "https://api-seller.ozon.ru"
CLIENT_ID = os.environ["OZON_CLIENT_ID"].strip()
API_KEY = os.environ["OZON_API_KEY"].strip()
OUT = Path("catalog/norden_export.json")
BRAND_ATTR_ID = 85
HEADERS = {
    "Client-Id": CLIENT_ID,
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "megapolis-ozon-catalog-export/1.1",
}

def post(path, payload, attempts=6):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(attempts):
        req = urllib.request.Request(OZON_BASE + path, data=data, headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if (exc.code == 429 or 500 <= exc.code < 600) and attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 30))
                continue
            raise RuntimeError(f"{path}: HTTP {exc.code}: {body[:1500]}")
        except Exception:
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 30))
                continue
            raise

def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i+n]

def list_visibility(visibility):
    out, last_id, seen = [], "", set()
    while True:
        payload = {"filter": {"visibility": visibility}, "limit": 1000}
        if last_id:
            payload["last_id"] = last_id
        d = post("/v3/product/list", payload)
        r = d.get("result") or {}
        items = r.get("items") or []
        out.extend(items)
        nxt = r.get("last_id") or ""
        total = int(r.get("total") or 0)
        if not items or len(out) >= total or not nxt or nxt == last_id or nxt in seen:
            break
        seen.add(last_id)
        last_id = nxt
    return out

def get_attributes(ids):
    out = []
    for batch in chunks(ids, 1000):
        d = post("/v4/product/info/attributes", {"filter":{"product_id":batch,"visibility":"ALL"},"limit":1000})
        out.extend(d.get("result") or [])
    return out

def get_info(ids):
    out, errs = [], []
    for batch in chunks(ids, 1000):
        try:
            d = post("/v3/product/info/list", {"product_id":batch})
            out.extend(d.get("items") or (d.get("result") or {}).get("items") or [])
        except Exception as e:
            errs.append(str(e))
    return out, errs

def get_prices(ids):
    out, errs = [], []
    for batch in chunks(ids, 100):
        cursor = ""
        while True:
            try:
                d = post("/v5/product/info/prices", {"cursor":cursor,"filter":{"product_id":batch,"visibility":"ALL"},"limit":100})
            except Exception as e:
                errs.append(str(e)); break
            items = d.get("items") or []
            out.extend(items)
            nxt = d.get("cursor") or ""
            if not items or not nxt or nxt == cursor: break
            cursor = nxt
    return out, errs

def get_stocks(ids):
    out, errs = [], []
    for batch in chunks(ids, 100):
        cursor = ""
        while True:
            payload = {"filter":{"product_id":batch,"visibility":"ALL"},"limit":100}
            if cursor: payload["cursor"] = cursor
            try:
                d = post("/v4/product/info/stocks", payload)
            except Exception as e:
                errs.append(str(e)); break
            items = d.get("items") or []
            out.extend(items)
            nxt = d.get("cursor") or ""
            if not items or not nxt or nxt == cursor: break
            cursor = nxt
    return out, errs

def brand_values(p):
    for a in p.get("attributes") or []:
        if int(a.get("id") or a.get("attribute_id") or 0) == BRAND_ATTR_ID:
            return [str(v.get("value") or "").strip() for v in a.get("values") or []]
    return []

active = list_visibility("ALL")
archived = list_visibility("ARCHIVED")
archived_ids = {int(x.get("product_id") or 0) for x in archived if int(x.get("product_id") or 0)}
by_id = {}
for x in active + archived:
    pid = int(x.get("product_id") or 0)
    if pid: by_id[pid] = x
all_ids = sorted(by_id)
attrs = get_attributes(all_ids)
attr_by_id = {int(x.get("id") or x.get("product_id") or 0):x for x in attrs}
norden_ids = sorted(pid for pid,p in attr_by_id.items() if any(v.upper()=="NORDEN" for v in brand_values(p)))
infos, info_errors = get_info(norden_ids)
prices, price_errors = get_prices(norden_ids)
stocks, stock_errors = get_stocks(norden_ids)
info_by_id = {int(x.get("id") or x.get("product_id") or 0):x for x in infos}
price_by_id = {int(x.get("product_id") or 0):x for x in prices}
stock_by_id = {}
for x in stocks:
    pid = int(x.get("product_id") or 0)
    if pid: stock_by_id.setdefault(pid,[]).append(x)
products = []
for pid in norden_ids:
    a = attr_by_id.get(pid) or {}
    products.append({
        "product_id": pid,
        "offer_id": str(a.get("offer_id") or by_id.get(pid,{}).get("offer_id") or ""),
        "name": str(a.get("name") or info_by_id.get(pid,{}).get("name") or ""),
        "brand": " | ".join(brand_values(a)),
        "archived": pid in archived_ids,
        "product_list": by_id.get(pid) or {},
        "attributes": a,
        "info": info_by_id.get(pid) or {},
        "price": price_by_id.get(pid) or {},
        "stocks": stock_by_id.get(pid) or [],
    })
payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "all_unique_ozon_products": len(all_ids),
    "archived_scanned": len(archived_ids),
    "norden_count": len(products),
    "errors": {"info":info_errors,"prices":price_errors,"stocks":stock_errors},
    "products": products,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print(json.dumps({"norden_count":len(products),"archived_norden":sum(1 for x in products if x["archived"]),"errors":payload["errors"]},ensure_ascii=False))
