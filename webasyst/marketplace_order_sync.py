#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from client import WebasystClient

DRY_RUN = str(os.getenv("DRY_RUN", "1")).strip().lower() not in {"0", "false", "no", "off"}
OZON_CUTOFF = os.getenv("OZON_ORDER_IMPORT_CUTOFF", "2026-09-22T11:08:40Z")
YANDEX_DAYS = 30
WB_DAYS = 60

LABEL = {
    "ozon": "Ozon",
    "yandex_market": "Яндекс Маркет",
    "wildberries": "Wildberries",
    "yandex_kit": "Яндекс KIT",
}

# Only Webasyst transitions. This script contains no marketplace write endpoint.
GRAPH = {
    "new": [("process", "processing"), ("otmenen", "otmenen")],
    "processing": [
        ("ozhidaem-oplaty", "ozhidaem-oplaty"),
        ("pay", "paid"),
        ("izgotavlivaetsya", "izgotavlivaetsya"),
        ("ship", "shipped"),
        ("complete", "completed"),
        ("otmenen", "otmenen"),
    ],
    "ozhidaem-oplaty": [
        ("pay", "paid"), ("izgotavlivaetsya", "izgotavlivaetsya"),
        ("ship", "shipped"), ("complete", "completed"), ("otmenen", "otmenen"),
    ],
    "paid": [
        ("izgotavlivaetsya", "izgotavlivaetsya"), ("ship", "shipped"),
        ("complete", "completed"), ("refund", "refunded"),
    ],
    "izgotavlivaetsya": [
        ("pay", "paid"), ("ship", "shipped"), ("complete", "completed"),
        ("otmenen", "otmenen"), ("refund", "refunded"),
    ],
    "shipped": [("complete", "completed"), ("refund", "refunded")],
    "completed": [("refund", "refunded"), ("otmenen", "otmenen")],
}

def s(v: Any) -> str:
    return str(v or "").strip()

def num(v: Any, default=0.0) -> float:
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except Exception:
        return default

def dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    text = s(v)
    try:
        x = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if x.tzinfo is None:
            x = x.replace(tzinfo=timezone.utc)
        return x.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None

def iso(x: datetime) -> str:
    return x.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def key(source: str, external_id: str) -> str:
    return hashlib.sha256(f"{source}|{external_id}".encode()).hexdigest()[:32]

class Net:
    def __init__(self):
        self.s = requests.Session()
    def req(self, method, url, *, headers=None, params=None, body=None, data=None, tries=10):
        last = None
        for i in range(tries):
            try:
                r = self.s.request(method, url, headers=headers, params=params, json=body, data=data, timeout=90)
            except requests.RequestException as e:
                last = e
                time.sleep(min(20, 2 + i * 2))
                continue
            if r.status_code == 429:
                time.sleep(min(30, 3 + i * 3))
                last = RuntimeError("429")
                continue
            if r.status_code >= 500:
                time.sleep(min(20, 2 + i * 2))
                last = RuntimeError(f"HTTP {r.status_code}")
                continue
            return r
        raise RuntimeError(str(last or "request failed"))

net = Net()
wa = WebasystClient(min_request_interval=0.30)
sku_cache: Dict[str, Optional[dict]] = {}
kit_sku_cache: Dict[str, str] = {}

report = {
    "dry_run": DRY_RUN,
    "started_at": iso(datetime.now(timezone.utc)),
    "reverse_status_writes": 0,
    "sources": {},
    "created": 0,
    "existing": 0,
    "status_updated": 0,
    "skipped_unmatched_sku": 0,
    "unmatched": [],
    "errors": 0,
}

def sr(source: str) -> dict:
    return report["sources"].setdefault(source, {
        "read": 0, "eligible": 0, "created": 0, "existing": 0,
        "status_updated": 0, "skipped": 0, "errors": 0,
    })

def wa_sku(code: str) -> Optional[dict]:
    code = s(code)
    if not code:
        return None
    if code in sku_cache:
        return sku_cache[code]
    p = wa.call("shop.product.search", params={
        "hash": f"search/query={code}", "limit": 100, "fields": "id,name,skus"
    })
    rows = p.get("products") if isinstance(p, dict) else p if isinstance(p, list) else []
    found = []
    for prod in rows or []:
        skus = prod.get("skus") or {}
        skus = list(skus.values()) if isinstance(skus, dict) else skus if isinstance(skus, list) else []
        for x in skus:
            if isinstance(x, dict) and s(x.get("sku")) == code and s(x.get("id")):
                found.append({"sku_id": s(x.get("id")), "product_id": s(prod.get("id")), "sku": code})
    unique = {(x["sku_id"], x["product_id"]): x for x in found}
    ans = next(iter(unique.values())) if len(unique) == 1 else None
    sku_cache[code] = ans
    return ans

def existing_order(source: str, ext: str) -> Optional[dict]:
    h = key(source, ext)
    p = wa.call("shop.order.search", params={
        "hash": f"search/params.mp_key={h}", "limit": 10, "fields": "*,state"
    })
    rows = p.get("orders") if isinstance(p, dict) else p if isinstance(p, list) else []
    rows = [x for x in (rows or []) if isinstance(x, dict)]
    if len(rows) > 1:
        raise RuntimeError(f"duplicate imported order {source}/{ext}")
    return rows[0] if rows else None

def path_to(start: str, target: str):
    if not target or start == target:
        return []
    q = deque([(start, [])])
    seen = {start}
    while q:
        state, path = q.popleft()
        for action, nxt in GRAPH.get(state, []):
            if nxt in seen:
                continue
            np = path + [(action, nxt)]
            if nxt == target:
                return np
            seen.add(nxt)
            q.append((nxt, np))
    return None

def change_state(order_id: str, target: str) -> bool:
    info = wa.call("shop.order.getInfo", params={"id": order_id})
    current = s(info.get("state_id")) if isinstance(info, dict) else ""
    route = path_to(current, target)
    if route is None or not route:
        return False
    changed = False
    for action, expected in route:
        acts = wa.call("shop.order.actions", params={"id": order_id})
        acts = acts if isinstance(acts, list) else (acts.get("actions") or acts.get("items") or []) if isinstance(acts, dict) else []
        allowed = {s(x.get("id")) for x in acts if isinstance(x, dict)}
        if action not in allowed:
            return changed
        if not DRY_RUN:
            wa.call("shop.order.action", http_method="POST", data={"id": order_id, "action": action})
        changed = True
    return changed

def comment(o: dict) -> str:
    source = LABEL[o["source"]]
    return (
        f"Импортировано из {source}.\n"
        f"Номер заказа источника: {o['external_id']}\n"
        f"Дата заказа источника: {o.get('created_at','')}\n"
        f"Статус источника: {o.get('status_raw','')}\n"
        f"Статусы синхронизируются только {source} → Webasyst. "
        "Из Webasyst статусы обратно не передаются."
    )

def create_order(o: dict, items: List[dict]) -> str:
    if DRY_RUN:
        return "DRY-RUN"
    add_items = [{"sku_id": x["sku_id"], "quantity": x["quantity"]} for x in items]
    params = {
        "mp_key": key(o["source"], o["external_id"]),
        "mp_source": o["source"],
        "mp_external_id": o["external_id"],
        "mp_status": o.get("status_raw") or "",
        "mp_created_at": o.get("created_at") or "",
        "shipping_name": o.get("shipping_name") or LABEL[o["source"]],
        "payment_name": o.get("payment_name") or LABEL[o["source"]],
    }
    created = wa.call("shop.order.add", http_method="POST", data={
        "items": add_items,
        "contact": {"name": LABEL[o["source"]]},
        "comment": comment(o),
        "params": params,
    })
    oid = s(created.get("id")) if isinstance(created, dict) else ""
    if not oid:
        raise RuntimeError("Webasyst did not return order id")
    # Preserve marketplace line prices after creation.
    created_items = created.get("items") or []
    created_by_sku = {}
    for ci in created_items:
        if isinstance(ci, dict) and s(ci.get("sku_id")):
            created_by_sku.setdefault(s(ci.get("sku_id")), []).append(ci)
    save_items = []
    for x in items:
        candidates = created_by_sku.get(s(x["sku_id"])) or []
        ci = candidates.pop(0) if candidates else {}
        row = {
            "item_id": s(ci.get("id")),
            "product_id": x["product_id"],
            "sku_id": x["sku_id"],
            "quantity": x["quantity"],
        }
        if x.get("price") is not None:
            row["price"] = f"{float(x['price']):.2f}"
        save_items.append(row)
    wa.call("shop.order.save", http_method="POST", data={"id": oid, "items": save_items, "params": params})
    return oid

def process(o: dict):
    source = o["source"]
    stat = sr(source)
    stat["read"] += 1
    ext = s(o.get("external_id"))
    if not ext or not o.get("items"):
        stat["skipped"] += 1
        return
    stat["eligible"] += 1
    resolved = []
    for item in o["items"]:
        match = wa_sku(s(item.get("sku")))
        if not match:
            stat["skipped"] += 1
            report["skipped_unmatched_sku"] += 1
            if len(report["unmatched"]) < 30:
                report["unmatched"].append({
                    "source": source,
                    "external_id": ext,
                    "sku": s(item.get("sku")),
                })
            return
        resolved.append({
            **match,
            "quantity": max(1, int(num(item.get("quantity"), 1))),
            "price": item.get("price"),
        })
    try:
        old = existing_order(source, ext)
        if old:
            oid = s(old.get("id"))
            stat["existing"] += 1
            report["existing"] += 1
            # Do not rewrite order items when only the source status changed.
        else:
            oid = create_order(o, resolved)
            stat["created"] += 1
            report["created"] += 1
        if oid != "DRY-RUN" and change_state(oid, s(o.get("target_state"))):
            stat["status_updated"] += 1
            report["status_updated"] += 1
        elif oid == "DRY-RUN" and s(o.get("target_state")) not in {"", "new"}:
            stat["status_updated"] += 1
    except Exception as e:
        stat["errors"] += 1
        report["errors"] += 1
        print(f"ОШИБКА {source}/{ext}: {type(e).__name__}: {str(e)[:500]}", file=sys.stderr)

# ---------- Yandex Market ----------

def yandex_state(status: str, sub: str) -> str:
    st, sb = s(status).upper(), s(sub).upper()
    if st == "UNPAID": return "ozhidaem-oplaty"
    if st in {"PLACING", "RESERVED", "PENDING"}: return "new"
    if st == "PROCESSING": return "izgotavlivaetsya" if sb == "READY_TO_SHIP" else "processing"
    if st in {"DELIVERY", "PICKUP"}: return "shipped"
    if st == "DELIVERED": return "completed"
    if st == "CANCELLED": return "otmenen"
    if st in {"PARTIALLY_RETURNED", "RETURNED"}: return "refunded"
    return "new"

def pval(x: Any) -> float:
    return num(x.get("value")) if isinstance(x, dict) else num(x)

def load_yandex_market():
    token = s(os.getenv("YANDEX_MARKET_API_KEY"))
    if not token: return []
    h = {"Api-Key": token, "Accept": "application/json", "Content-Type": "application/json"}
    r = net.req("GET", "https://api.partner.market.yandex.ru/v2/campaigns", headers=h, params={"limit": 100})
    if not r.ok: raise RuntimeError(f"campaigns HTTP {r.status_code}")
    bids = []
    for c in (r.json().get("campaigns") or []):
        b = c.get("business") if isinstance(c, dict) else None
        bid = b.get("id") if isinstance(b, dict) else c.get("businessId") if isinstance(c, dict) else None
        if bid and bid not in bids: bids.append(bid)
    cutoff = datetime.now(timezone.utc) - timedelta(days=YANDEX_DAYS)
    out = []
    for bid in bids:
        token_page = None
        while True:
            params = {"limit": 50}
            if token_page: params["pageToken"] = token_page
            rr = net.req("POST", f"https://api.partner.market.yandex.ru/v1/businesses/{bid}/orders", headers=h, params=params, body={})
            if not rr.ok: raise RuntimeError(f"business orders HTTP {rr.status_code}")
            data = rr.json()
            root = data.get("result") if isinstance(data.get("result"), dict) else data
            rows = root.get("orders") or []
            for o in rows:
                if not isinstance(o, dict) or o.get("fake"): continue
                created = dt(o.get("creationDate"))
                if created and created < cutoff: continue
                items = []
                for it in o.get("items") or []:
                    qty = max(1, int(num(it.get("count"), 1)))
                    pr = it.get("prices") or {}
                    total = pval(pr.get("payment")) + pval(pr.get("cashback")) + pval(pr.get("subsidy"))
                    items.append({
                        "sku": s(it.get("offerId")),
                        "quantity": qty,
                        "price": total / qty if total > 0 else None,
                    })
                status, sub = s(o.get("status")), s(o.get("substatus"))
                delivery = o.get("delivery") or {}
                service = s(delivery.get("serviceName")) if isinstance(delivery, dict) else ""
                out.append({
                    "source": "yandex_market", "external_id": s(o.get("orderId")),
                    "created_at": iso(created) if created else s(o.get("creationDate")),
                    "status_raw": status + ("/" + sub if sub else ""),
                    "target_state": yandex_state(status, sub), "items": items,
                    "shipping_name": "Яндекс Маркет" + (f" / {service}" if service else ""),
                    "payment_name": "Яндекс Маркет / " + s(o.get("paymentType") or o.get("paymentMethod")),
                })
            paging = root.get("paging") if isinstance(root, dict) else None
            token_page = (paging or {}).get("nextPageToken") if isinstance(paging, dict) else None
            if not token_page: break
    return out

# ---------- Wildberries ----------

def load_wb():
    token = s(os.getenv("WB_API_TOKEN"))
    if not token: return []
    h = {"Authorization": token, "Accept": "application/json"}
    start = datetime.now(timezone.utc) - timedelta(days=WB_DAYS)
    params = {"dateFrom": start.strftime("%Y-%m-%dT%H:%M:%S"), "flag": 0}
    r = net.req("GET", "https://statistics-api.wildberries.ru/api/v1/supplier/orders", headers=h, params=params)
    if not r.ok: raise RuntimeError(f"WB orders HTTP {r.status_code}")
    orders = r.json() if isinstance(r.json(), list) else []
    # Statistics endpoints are limited to 1 request/minute per seller.
    time.sleep(62)
    rs = net.req("GET", "https://statistics-api.wildberries.ru/api/v1/supplier/sales", headers=h, params=params)
    sales = rs.json() if rs.ok and isinstance(rs.json(), list) else []
    sold = {s(x.get("srid")) for x in sales if isinstance(x, dict) and s(x.get("srid")) and not x.get("isStorno")}
    groups = defaultdict(list)
    for row in orders:
        if isinstance(row, dict):
            ext = s(row.get("gNumber") or row.get("srid"))
            if ext: groups[ext].append(row)
    out = []
    for ext, rows in groups.items():
        agg = defaultdict(lambda: [0, 0.0])
        canceled, all_sold, created = False, True, None
        for row in rows:
            code = s(row.get("supplierArticle"))
            if code:
                agg[code][0] += 1
                agg[code][1] += num(row.get("finishedPrice") or row.get("priceWithDisc") or row.get("totalPrice"))
            canceled = canceled or bool(row.get("isCancel"))
            sid = s(row.get("srid"))
            if sid and sid not in sold: all_sold = False
            d = dt(row.get("date"))
            if d and (created is None or d < created): created = d
        items = [{"sku": code, "quantity": q, "price": total/q if q else None} for code, (q, total) in agg.items()]
        out.append({
            "source": "wildberries", "external_id": ext,
            "created_at": iso(created) if created else "",
            "status_raw": "CANCELLED" if canceled else "SOLD" if all_sold else "ORDERED",
            "target_state": "otmenen" if canceled else "completed" if all_sold else "processing",
            "items": items, "shipping_name": "Wildberries", "payment_name": "Wildberries",
        })
    return out

# ---------- Ozon ----------

def ozon_state(status: str) -> str:
    st = s(status).lower()
    if st == "awaiting_approve": return "new"
    if st in {"awaiting_packaging", "awaiting_deliver"}: return "izgotavlivaetsya"
    if st in {"delivering", "driver_pickup"}: return "shipped"
    if st == "delivered": return "completed"
    if st == "cancelled": return "otmenen"
    if st in {"arbitration", "client_arbitration"}: return "processing"
    return "new"

def load_ozon():
    cid, secret = s(os.getenv("OZON_CLIENT_ID")), s(os.getenv("OZON_API_KEY"))
    if not cid or not secret: return []
    h = {"Client-Id": cid, "Api-Key": secret, "Content-Type": "application/json"}
    now = datetime.now(timezone.utc)
    cutoff = dt(OZON_CUTOFF) or now
    since = now - timedelta(days=30)
    out = []
    for kind, url in (
        ("FBS", "https://api-seller.ozon.ru/v3/posting/fbs/list"),
        ("FBO", "https://api-seller.ozon.ru/v2/posting/fbo/list"),
    ):
        offset = 0
        while True:
            body = {
                "dir": "ASC", "filter": {"since": iso(since), "to": iso(now)},
                "limit": 1000, "offset": offset,
            }
            if kind == "FBS":
                body["with"] = {"analytics_data": False, "barcodes": False, "financial_data": False, "translit": False}
            else:
                body["translit"] = False
                body["with"] = {"analytics_data": False, "financial_data": False}
            r = net.req("POST", url, headers=h, body=body)
            if not r.ok:
                if kind == "FBO": break
                raise RuntimeError(f"Ozon {kind} HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            root = data.get("result")
            rows = (root.get("postings") or []) if isinstance(root, dict) else root if isinstance(root, list) else []
            for o in rows:
                created = dt(o.get("in_process_at") or o.get("created_at") or o.get("shipment_date"))
                if not created or created < cutoff: continue
                items = [{
                    "sku": s(x.get("offer_id")),
                    "quantity": max(1, int(num(x.get("quantity"), 1))),
                    "price": num(x.get("price")) if x.get("price") not in (None, "") else None,
                } for x in (o.get("products") or []) if isinstance(x, dict)]
                status, sub = s(o.get("status")), s(o.get("substatus"))
                out.append({
                    "source": "ozon", "external_id": s(o.get("posting_number")),
                    "created_at": iso(created), "status_raw": status + ("/"+sub if sub else ""),
                    "target_state": ozon_state(status), "items": items,
                    "shipping_name": f"Ozon / {kind}", "payment_name": "Ozon",
                })
            if len(rows) < 1000: break
            offset += len(rows)
    return out

# ---------- Yandex KIT ----------

def kit_state(status: str) -> str:
    st = s(status).upper()
    if st == "PENDING_PAYMENT": return "ozhidaem-oplaty"
    if st in {"NEW", "ORDER_PLACED", "WAIT_FOR_CONFIRMATION"}: return "new"
    if st in {"CREATING_INITIAL_RECEIPT", "SETUP_DELIVERY"}: return "processing"
    if st == "WAIT_FOR_DELIVERY": return "izgotavlivaetsya"
    if st in {"DELIVERED", "CREATING_FINAL_RECEIPTS", "COMPLETED"}: return "completed"
    if st in {"CANCELLATION_IN_PROGRESS", "DELIVERY_CANCELLED", "CANCELLED"}: return "otmenen"
    if st in {"FULL_REFUND", "PARTIAL_REFUND"}: return "refunded"
    return "new"

def kit_get(token: str, path: str, params=None):
    h = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    r = net.req("GET", "https://api.kit.yandex.net" + path, headers=h, params=params, tries=12)
    if not r.ok: raise RuntimeError(f"KIT {path} HTTP {r.status_code}: {r.text[:300]}")
    return r.json()

def kit_sku(token: str, variant_id: str) -> str:
    vid = s(variant_id)
    if not vid: return ""
    if vid in kit_sku_cache: return kit_sku_cache[vid]
    p = kit_get(token, f"/v1/variants/{vid}")
    code = s(p.get("sku")) if isinstance(p, dict) else ""
    kit_sku_cache[vid] = code
    return code

def load_kit():
    token = s(os.getenv("YANDEX_KIT_TOKEN"))
    if not token: return []
    orders, page = [], 1
    while True:
        d = kit_get(token, "/v1/orders", {"page": page, "per_page": 100})
        if isinstance(d, list):
            batch, total = d, None
        else:
            batch = next((d[k] for k in ("orders", "items", "results") if isinstance(d.get(k), list)), [])
            total = d.get("total_count") or d.get("total") or ((d.get("meta") or {}).get("total_count") if isinstance(d.get("meta"), dict) else None)
        orders.extend(x for x in batch if isinstance(x, dict))
        if not batch or len(batch) < 100 or (total is not None and len(orders) >= int(total)): break
        page += 1
    out = []
    for o in orders:
        chunks = o.get("delivery_chunks") or []
        agg = defaultdict(lambda: {"quantity": 0, "sum": 0.0})
        for ch in chunks:
            if not isinstance(ch, dict): continue
            for item in ch.get("items") or []:
                if not isinstance(item, dict): continue
                code = kit_sku(token, s(item.get("product_variant_id")))
                if not code: continue
                qty = max(1, int(num(item.get("quantity"), 1)))
                price = num(item.get("final_price") if item.get("final_price") not in (None, "") else item.get("price"))
                agg[code]["quantity"] += qty
                agg[code]["sum"] += price * qty
        items = []
        for code, a in agg.items():
            q = a["quantity"]
            items.append({"sku": code, "quantity": q, "price": a["sum"]/q if q else None})
        status = s(o.get("status"))
        client = o.get("client") or {}
        pay = o.get("payment") or {}
        service = ""
        if chunks and isinstance(chunks[0], dict):
            di = chunks[0].get("delivery_info") or {}
            if isinstance(di, dict):
                service = s(di.get("courier_delivery_service_type") or di.get("pickup_point_delivery_service_type") or di.get("delivery_service_type") or di.get("method"))
        out.append({
            "source": "yandex_kit",
            "external_id": s(o.get("order_number") or o.get("id")),
            "created_at": s(o.get("created_at")),
            "status_raw": status, "target_state": kit_state(status), "items": items,
            "shipping_name": "Яндекс KIT" + (f" / {service}" if service else ""),
            "payment_name": "Яндекс KIT" + (f" / {s(pay.get('method'))}" if isinstance(pay, dict) and s(pay.get("method")) else ""),
        })
    return out

def run_source(name: str, loader):
    try:
        rows = loader()
        unique = {}
        for o in rows:
            if s(o.get("external_id")):
                unique[(o["source"], o["external_id"])] = o
        for o in unique.values():
            process(o)
    except Exception as e:
        st = sr(name)
        st["errors"] += 1
        report["errors"] += 1
        print(f"ОШИБКА источника {name}: {type(e).__name__}: {str(e)[:600]}", file=sys.stderr)

def main():
    run_source("yandex_market", load_yandex_market)
    run_source("wildberries", load_wb)
    run_source("yandex_kit", load_kit)
    run_source("ozon", load_ozon)
    report["finished_at"] = iso(datetime.now(timezone.utc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["errors"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
