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
SYNC_SOURCES = {x.strip().lower() for x in str(os.getenv("SYNC_SOURCES", "yandex_market,wildberries,yandex_kit,ozon")).split(",") if x.strip()}
TARGET_EXTERNAL_ID = str(os.getenv("TARGET_EXTERNAL_ID", "")).strip()
OZON_CUTOFF = os.getenv("OZON_ORDER_IMPORT_CUTOFF", "2026-09-22T11:08:40Z")
YANDEX_DAYS = 30
WB_DAYS = 60

LABEL = {
    "ozon": "Ozon",
    "yandex_market": "Яндекс Маркет",
    "wildberries": "Wildberries",
    "yandex_kit": "Яндекс KIT",
}

CHAT_AUTOMATION_START = os.getenv("CHAT_AUTOMATION_START", "2026-09-23T19:35:00Z")

WELCOME_MESSAGE = """Здравствуйте! 👋

Спасибо за ваш заказ и за то, что выбрали наш магазин «Мегаполис»! 😊

Мы уже получили заказ и приступили к его обработке. Постараемся всё подготовить и передать в доставку максимально быстро.

Если у вас появятся вопросы по заказу, товару или доставке — напишите нам в чат, мы обязательно поможем.

Желаем приятной покупки!
С уважением, команда «Мегаполис» ❤️"""

REVIEW_MESSAGE = """Спасибо за ваш заказ! ❤️

Если покупка вам понравилась, будем очень благодарны за оценку **5 звёзд ⭐⭐⭐⭐⭐**.

Для нас это очень важно — ваша высокая оценка помогает нашему магазину развиваться и становиться лучше.

Спасибо, что выбрали «Мегаполис»!"""

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

def ymd(v: Any) -> str:
    text = s(v)
    if not text:
        return ""
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    x = dt(text)
    return x.strftime("%Y-%m-%d") if x else ""

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
                # WB and some marketplace APIs return an explicit safe retry delay.
                # Respect it; short blind retries can keep the token bucket exhausted.
                raw_retry = r.headers.get("X-Ratelimit-Retry") or r.headers.get("Retry-After")
                raw_reset = r.headers.get("X-Ratelimit-Reset")
                try:
                    wait = float(raw_retry) if raw_retry not in (None, "") else 0.0
                except (TypeError, ValueError):
                    wait = 0.0
                if wait <= 0:
                    try:
                        wait = float(raw_reset) if raw_reset not in (None, "") else 0.0
                    except (TypeError, ValueError):
                        wait = 0.0
                if wait <= 0:
                    wait = 65.0
                time.sleep(min(600.0, wait + 2.0))
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
    "chat_welcome_sent": 0,
    "chat_review_sent": 0,
    "chat_blocked": 0,
    "chat_errors": 0,
    "errors": 0,
}

def sr(source: str) -> dict:
    return report["sources"].setdefault(source, {
        "read": 0, "eligible": 0, "created": 0, "existing": 0,
        "status_updated": 0, "skipped": 0, "errors": 0,
        "chat_welcome_sent": 0, "chat_review_sent": 0,
        "chat_blocked": 0, "chat_errors": 0,
    })

class ChatError(RuntimeError):
    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent

def chat_eligible_order(o: dict) -> bool:
    created = dt(o.get("created_at"))
    started = dt(CHAT_AUTOMATION_START)
    return bool(created and started and created >= started)

def get_order_params(order_id: str) -> dict:
    info = wa.call("shop.order.getInfo", params={"id": order_id})
    return dict(info.get("params") or {}) if isinstance(info, dict) else {}

def save_order_params(order_id: str, updates: dict):
    if DRY_RUN or not order_id:
        return
    info = wa.call("shop.order.getInfo", params={"id": order_id})
    params = dict(info.get("params") or {}) if isinstance(info, dict) else {}
    for k, v in updates.items():
        params[k] = str(v)
    wa.call("shop.order.save", http_method="POST", data={"id": order_id, "params": params})

def chat_http(method: str, url: str, *, headers=None, params=None, body=None):
    r = net.req(method, url, headers=headers, params=params, body=body, tries=4)
    if r.ok:
        if not r.content:
            return {}
        try:
            return r.json()
        except Exception:
            return {}
    permanent = r.status_code in {400, 401, 403, 404, 409}
    raise ChatError(f"HTTP {r.status_code}: {r.text[:400]}", permanent=permanent)

def send_marketplace_chat(o: dict, message: str, chat_id: str = "") -> str:
    source = s(o.get("source"))
    ext = s(o.get("external_id"))

    if source == "yandex_market":
        token = s(os.getenv("YANDEX_MARKET_API_KEY"))
        business_id = s(o.get("business_id"))
        if not token or not business_id or not ext:
            raise ChatError("Yandex Market chat: missing token/businessId/orderId", permanent=True)
        h = {"Api-Key": token, "Accept": "application/json", "Content-Type": "application/json"}
        if not chat_id:
            d = chat_http(
                "POST",
                f"https://api.partner.market.yandex.ru/v2/businesses/{business_id}/chats/new",
                headers=h,
                body={"context": {"type": "ORDER", "id": int(ext)}},
            )
            chat_id = s((d.get("result") or {}).get("chatId")) if isinstance(d, dict) else ""
            if not chat_id:
                raise ChatError("Yandex Market chat/new returned no chatId", permanent=True)
        chat_http(
            "POST",
            f"https://api.partner.market.yandex.ru/v2/businesses/{business_id}/chats/message",
            headers=h,
            params={"chatId": chat_id},
            body={"message": message},
        )
        return chat_id

    if source == "ozon":
        cid = s(os.getenv("OZON_CLIENT_ID"))
        api_key = s(os.getenv("OZON_API_KEY"))
        if not cid or not api_key or not ext:
            raise ChatError("Ozon chat: missing Client-Id/Api-Key/posting_number", permanent=True)
        h = {"Client-Id": cid, "Api-Key": api_key, "Accept": "application/json", "Content-Type": "application/json"}
        if not chat_id:
            if s(o.get("ozon_kind")).upper() == "FBO":
                raise ChatError("Ozon FBO: seller cannot proactively start this order chat", permanent=True)
            d = chat_http(
                "POST",
                "https://api-seller.ozon.ru/v1/chat/start",
                headers=h,
                body={"posting_number": ext},
            )
            chat_id = s((d.get("result") or {}).get("chat_id")) if isinstance(d, dict) else ""
            if not chat_id:
                raise ChatError("Ozon chat/start returned no chat_id", permanent=True)
        chat_http(
            "POST",
            "https://api-seller.ozon.ru/v1/chat/send/message",
            headers=h,
            body={"chat_id": chat_id, "text": message},
        )
        return chat_id

    if source == "wildberries":
        raise ChatError(
            "Wildberries: seller API cannot proactively start a buyer chat; the buyer must start the chat first",
            permanent=True,
        )

    raise ChatError(f"Chat is not supported for source {source}", permanent=True)

def maybe_send_marketplace_message(order_id: str, o: dict, message_kind: str):
    if DRY_RUN or not order_id or o.get("source") not in {"yandex_market", "ozon", "wildberries"}:
        return
    if not chat_eligible_order(o):
        return

    target_state = s(o.get("target_state"))
    if message_kind == "welcome" and target_state in {"completed", "otmenen", "refunded"}:
        return
    if message_kind == "review" and target_state != "completed":
        return

    sent_key = "mp_chat_welcome_sent_at" if message_kind == "welcome" else "mp_chat_review_sent_at"
    blocked_key = "mp_chat_welcome_blocked" if message_kind == "welcome" else "mp_chat_review_blocked"
    message = WELCOME_MESSAGE if message_kind == "welcome" else REVIEW_MESSAGE

    params = get_order_params(order_id)
    if s(params.get(sent_key)) or s(params.get(blocked_key)):
        return

    try:
        chat_id = send_marketplace_chat(o, message, s(params.get("mp_chat_id")))
        updates = {
            sent_key: iso(datetime.now(timezone.utc)),
            "mp_chat_id": chat_id,
        }
        save_order_params(order_id, updates)
        stat = sr(o["source"])
        counter = "chat_welcome_sent" if message_kind == "welcome" else "chat_review_sent"
        stat[counter] += 1
        report[counter] += 1
        print(f"ЧАТ {LABEL[o['source']]} {o['external_id']}: отправлено сообщение {message_kind}")
    except ChatError as e:
        stat = sr(o["source"])
        if e.permanent:
            save_order_params(order_id, {blocked_key: str(e)[:500]})
            stat["chat_blocked"] += 1
            report["chat_blocked"] += 1
            print(f"ЧАТ НЕДОСТУПЕН {o['source']}/{o['external_id']}: {str(e)[:500]}", file=sys.stderr)
        else:
            stat["chat_errors"] += 1
            report["chat_errors"] += 1
            print(f"ОШИБКА ЧАТА {o['source']}/{o['external_id']}: {str(e)[:500]}", file=sys.stderr)
    except Exception as e:
        stat = sr(o["source"])
        stat["chat_errors"] += 1
        report["chat_errors"] += 1
        print(f"ОШИБКА ЧАТА {o['source']}/{o['external_id']}: {type(e).__name__}: {str(e)[:500]}", file=sys.stderr)

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

def existing_order_by_param(param: str, value: str) -> Optional[dict]:
    value = s(value)
    if not value:
        return None
    p = wa.call("shop.order.search", params={
        "hash": f"search/params.{param}={value}", "limit": 10, "fields": "*,state"
    })
    rows = p.get("orders") if isinstance(p, dict) else p if isinstance(p, list) else []
    rows = [x for x in (rows or []) if isinstance(x, dict)]
    if len(rows) > 1:
        raise RuntimeError(f"duplicate imported order param {param}={value}")
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
    lines = [
        f"Импортировано из {source}.",
        f"Номер заказа источника: {o['external_id']}",
        f"Дата заказа источника: {o.get('created_at','')}",
        f"Статус источника: {o.get('status_raw','')}",
    ]
    if o.get("delivery_price") not in (None, ""):
        lines.append(f"Стоимость доставки: {float(o['delivery_price']):.2f} ₽")
    if o.get("lift_price") not in (None, ""):
        lines.append(f"Стоимость подъема: {float(o['lift_price']):.2f} ₽")
    if s(o.get("lift_type")):
        lines.append(f"Тип подъема: {s(o.get('lift_type'))}")
    if s(o.get("recipient_name")):
        lines.append(f"Получатель: {s(o.get('recipient_name'))}")
    if s(o.get("delivery_to") or o.get("delivery_from")):
        lines.append(f"Крайняя дата доставки: {s(o.get('delivery_to') or o.get('delivery_from'))}")
    lines.append(f"Статусы синхронизируются только {source} → Webasyst. Из Webasyst статусы обратно не передаются.")
    return "\n".join(lines)

def marketplace_order_params(o: dict) -> dict:
    params = {
        "mp_key": key(o["source"], o["external_id"]),
        "mp_source": o["source"],
        "mp_external_id": o["external_id"],
        "mp_status": o.get("status_raw") or "",
        "mp_created_at": o.get("created_at") or "",
        "shipping_name": o.get("shipping_name") or LABEL[o["source"]],
        "payment_name": o.get("payment_name") or LABEL[o["source"]],
    }
    for k in ("delivery_price", "lift_price", "lift_type", "delivery_from", "delivery_to", "delivery_time_from", "delivery_time_to", "recipient_name", "wb_order_id", "wb_rid", "wb_order_uid", "wb_delivery_type"):
        v = o.get(k)
        if v not in (None, ""):
            params["mp_" + k] = str(v)
    return params

def order_customer(o: dict) -> dict:
    buyer = o.get("buyer") or {}
    if not isinstance(buyer, dict):
        return {}
    out = {}
    last_name = s(buyer.get("lastName"))
    first_name = s(buyer.get("firstName"))
    middle_name = s(buyer.get("middleName"))
    full = s(buyer.get("name"))

    # Yandex gives structured name fields. Ozon gives one full-name string.
    # Populate both the computed contact fields and a full display name so Webasyst
    # does not keep the generic marketplace contact created by the first import.
    if not (last_name or first_name or middle_name) and full:
        parts = [x for x in full.split() if x]
        if len(parts) >= 2:
            last_name, first_name = parts[0], parts[1]
            middle_name = " ".join(parts[2:]) if len(parts) > 2 else ""
    if not full:
        full = " ".join(x for x in (last_name, first_name, middle_name) if x).strip()

    if full:
        out["name"] = full
    if first_name:
        out["firstname"] = first_name
    if last_name:
        out["lastname"] = last_name
    if middle_name:
        out["middlename"] = middle_name
    if s(buyer.get("phone")):
        out["phone"] = s(buyer.get("phone"))
    if s(buyer.get("customer_email") or buyer.get("email")):
        out["email"] = s(buyer.get("customer_email") or buyer.get("email"))
    return out

def order_shipping_address(o: dict) -> dict:
    a = o.get("shipping_address") or {}
    return a if isinstance(a, dict) else {}

def update_order_details(order_id: str, o: dict):
    if DRY_RUN or not order_id:
        return
    info = wa.call("shop.order.getInfo", params={"id": order_id})
    existing_params = dict(info.get("params") or {}) if isinstance(info, dict) else {}
    existing_params.update(marketplace_order_params(o))

    # Persist the delivery interval directly in order params too. Webasyst derives the
    # visible delivery interval from these params even in custom states where the
    # editshippingdetails workflow action is unavailable.
    delivery_date = ymd(o.get("delivery_to") or o.get("delivery_from"))
    if delivery_date:
        time_from = s(o.get("delivery_time_from"))[:5] or "00:00"
        time_to = s(o.get("delivery_time_to"))[:5] or "23:59"
        existing_params["shipping_start_datetime"] = f"{delivery_date} {time_from}:00"
        existing_params["shipping_end_datetime"] = f"{delivery_date} {time_to}:00"
        existing_params["shipping_params_desired_delivery.date"] = delivery_date
        existing_params["shipping_params_desired_delivery.date_str"] = delivery_date
        existing_params["shipping_params_desired_delivery.interval"] = f"{time_from}-{time_to}"

    data = {"id": order_id, "params": existing_params, "comment": comment(o)}
    customer = order_customer(o)
    if customer:
        data["customer"] = customer
    address = order_shipping_address(o)
    if address:
        data["shipping_address"] = address
    shipping_total = o.get("shipping_total")
    if shipping_total not in (None, ""):
        data["shipping"] = f"{float(shipping_total):.2f}"
    wa.call("shop.order.save", http_method="POST", data=data)

    # Webasyst stores the courier delivery deadline through the editshippingdetails action.
    delivery_date = ymd(o.get("delivery_to") or o.get("delivery_from"))
    if delivery_date:
        acts = wa.call("shop.order.actions", params={"id": order_id})
        acts = acts if isinstance(acts, list) else (acts.get("actions") or acts.get("items") or []) if isinstance(acts, dict) else []
        allowed = {s(x.get("id")) for x in acts if isinstance(x, dict)}
        if "editshippingdetails" in allowed:
            # Webasyst saves shipping_datetime only when date AND both time bounds are present.
            # If Yandex gives only the date, use the full day so the latest delivery date is preserved.
            time_from = s(o.get("delivery_time_from"))[:5] or "00:00"
            time_to = s(o.get("delivery_time_to"))[:5] or "23:59"
            action_data = {
                "id": order_id,
                "action": "editshippingdetails",
                "shipping_date": delivery_date,
                "shipping_time_from": time_from,
                "shipping_time_to": time_to,
            }
            wa.call("shop.order.action", http_method="POST", data=action_data)

def create_order(o: dict, items: List[dict]) -> str:
    if DRY_RUN:
        return "DRY-RUN"
    add_items = [{"sku_id": x["sku_id"], "quantity": x["quantity"]} for x in items]
    params = marketplace_order_params(o)
    customer = order_customer(o) or {"name": LABEL[o["source"]]}
    created = wa.call("shop.order.add", http_method="POST", data={
        "items": add_items,
        "contact": customer,
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
    save_data = {"id": oid, "items": save_items, "params": params, "comment": comment(o)}
    customer = order_customer(o)
    if customer:
        save_data["customer"] = customer
    address = order_shipping_address(o)
    if address:
        save_data["shipping_address"] = address
    if o.get("shipping_total") not in (None, ""):
        save_data["shipping"] = f"{float(o['shipping_total']):.2f}"
    wa.call("shop.order.save", http_method="POST", data=save_data)
    return oid

def process(o: dict):
    source = o["source"]
    stat = sr(source)
    stat["read"] += 1
    ext = s(o.get("external_id"))
    if TARGET_EXTERNAL_ID and ext != TARGET_EXTERNAL_ID:
        return
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
        if not old and source == "wildberries":
            old = existing_order_by_param("mp_wb_order_id", s(o.get("wb_order_id")))
        if not old and source == "wildberries":
            old = existing_order_by_param("mp_wb_rid", s(o.get("wb_rid")))
        if old:
            oid = s(old.get("id"))
            stat["existing"] += 1
            report["existing"] += 1
            # Do not rewrite order items when only the source status changed.
        else:
            oid = create_order(o, resolved)
            stat["created"] += 1
            report["created"] += 1
        if oid != "DRY-RUN":
            update_order_details(oid, o)
            maybe_send_marketplace_message(oid, o, "welcome")
        if oid != "DRY-RUN" and change_state(oid, s(o.get("target_state"))):
            stat["status_updated"] += 1
            report["status_updated"] += 1
        elif oid == "DRY-RUN" and s(o.get("target_state")) not in {"", "new"}:
            stat["status_updated"] += 1
        if oid != "DRY-RUN":
            maybe_send_marketplace_message(oid, o, "review")
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

def yandex_order_detail(headers: dict, campaign_id: str, order_id: str) -> dict:
    r = net.req("GET", f"https://api.partner.market.yandex.ru/v2/campaigns/{campaign_id}/orders/{order_id}", headers=headers, tries=3)
    if not r.ok:
        return {}
    d = r.json()
    if isinstance(d, dict):
        return d.get("order") if isinstance(d.get("order"), dict) else d.get("result") if isinstance(d.get("result"), dict) else d
    return {}

def yandex_buyer_info(headers: dict, campaign_id: str, order_id: str, status: str) -> dict:
    if s(status).upper() not in {"PROCESSING", "DELIVERY", "PICKUP", "DELIVERED"}:
        return {}
    r = net.req("GET", f"https://api.partner.market.yandex.ru/v2/campaigns/{campaign_id}/orders/{order_id}/buyer", headers=headers, tries=2)
    if not r.ok:
        return {}
    d = r.json()
    if isinstance(d, dict) and isinstance(d.get("result"), dict):
        return d["result"]
    return d if isinstance(d, dict) else {}

def normalize_yandex_address(address: Any) -> dict:
    if not isinstance(address, dict):
        return {}
    out = {}
    for k in ("country", "postcode", "city", "region", "street", "house", "building", "block", "apartment", "floor", "entrance"):
        if s(address.get(k)):
            out[k] = s(address.get(k))
    return out

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
                order_id = s(o.get("orderId"))
                campaign_id = s(o.get("campaignId"))
                detail = yandex_order_detail(h, campaign_id, order_id) if campaign_id and order_id else {}
                buyer = yandex_buyer_info(h, campaign_id, order_id, status) if campaign_id and order_id else {}
                if not buyer and isinstance(detail.get("buyer"), dict):
                    buyer = detail.get("buyer") or {}

                delivery = detail.get("delivery") if isinstance(detail.get("delivery"), dict) else (o.get("delivery") or {})
                service = s(delivery.get("serviceName")) if isinstance(delivery, dict) else ""
                dates = delivery.get("dates") if isinstance(delivery, dict) and isinstance(delivery.get("dates"), dict) else {}
                dprice = num(delivery.get("price")) if isinstance(delivery, dict) and delivery.get("price") not in (None, "") else None
                lprice = num(delivery.get("liftPrice")) if isinstance(delivery, dict) and delivery.get("liftPrice") not in (None, "") else None
                ltype = s(delivery.get("liftType")) if isinstance(delivery, dict) else ""
                if not ltype and isinstance(o.get("services"), dict):
                    ltype = s((o.get("services") or {}).get("liftType"))

                # Business API provides the total delivery amount incl. lift. Use it as a fallback.
                business_delivery = ((o.get("prices") or {}).get("delivery") or {}) if isinstance(o.get("prices"), dict) else {}
                business_delivery_total = pval(business_delivery.get("payment")) + pval(business_delivery.get("subsidy"))
                if dprice is None and business_delivery_total > 0:
                    dprice = business_delivery_total
                shipping_total = None
                if dprice is not None or lprice is not None:
                    shipping_total = float(dprice or 0) + float(lprice or 0)

                address = {}
                if isinstance(delivery, dict):
                    raw_address = delivery.get("address")
                    if not isinstance(raw_address, dict) and isinstance(delivery.get("courier"), dict):
                        raw_address = (delivery.get("courier") or {}).get("address")
                    address = normalize_yandex_address(raw_address)

                out.append({
                    "source": "yandex_market", "external_id": order_id,
                    "business_id": s(bid),
                    "created_at": iso(created) if created else s(o.get("creationDate")),
                    "status_raw": status + ("/" + sub if sub else ""),
                    "target_state": yandex_state(status, sub), "items": items,
                    "shipping_name": "Яндекс Маркет" + (f" / {service}" if service else ""),
                    "payment_name": "Яндекс Маркет / " + s(o.get("paymentType") or o.get("paymentMethod")),
                    "buyer": buyer,
                    "shipping_address": address,
                    "delivery_price": dprice,
                    "lift_price": lprice,
                    "lift_type": ltype,
                    "shipping_total": shipping_total,
                    "delivery_from": s(dates.get("fromDate")),
                    "delivery_to": s(dates.get("toDate") or dates.get("fromDate")),
                    "delivery_time_from": s(dates.get("fromTime")),
                    "delivery_time_to": s(dates.get("toTime")),
                })
            paging = root.get("paging") if isinstance(root, dict) else None
            token_page = (paging or {}).get("nextPageToken") if isinstance(paging, dict) else None
            if not token_page: break
    return out

# ---------- Wildberries ----------

def wb_marketplace_dbs_snapshot(headers: dict) -> tuple[dict, dict]:
    """Return DBS orders keyed by rid and buyer info keyed by numeric order id.
    Marketplace endpoints are used for address/customer enrichment; no status writes.
    """
    base = "https://marketplace-api.wildberries.ru"
    orders = []
    try:
        r = net.req("GET", base + "/api/v3/dbs/orders/new", headers=headers, tries=3)
        if r.ok and isinstance(r.json(), dict):
            orders.extend(x for x in (r.json().get("orders") or []) if isinstance(x, dict))
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    for back in (0, 30):
        end = now - timedelta(days=back)
        start = end - timedelta(days=29)
        nxt = 0
        for _ in range(20):
            try:
                r = net.req("GET", base + "/api/v3/dbs/orders", headers=headers, params={
                    "limit": 1000,
                    "next": nxt,
                    "dateFrom": int(start.timestamp()),
                    "dateTo": int(end.timestamp()),
                }, tries=3)
            except Exception:
                break
            if not r.ok:
                break
            d = r.json() if isinstance(r.json(), dict) else {}
            batch = [x for x in (d.get("orders") or []) if isinstance(x, dict)]
            orders.extend(batch)
            nn = int(d.get("next") or 0)
            if not batch or not nn or nn == nxt:
                break
            nxt = nn

    by_rid = {}
    ids = []
    for o in orders:
        rid = s(o.get("rid"))
        if rid:
            by_rid[rid] = o
        oid = o.get("id")
        if oid not in (None, ""):
            try:
                ids.append(int(oid))
            except Exception:
                pass

    clients = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i+100]
        if not chunk:
            continue
        try:
            r = net.req("POST", base + "/api/v3/dbs/orders/client", headers={**headers, "Content-Type": "application/json"}, body={"orders": chunk}, tries=3)
        except Exception:
            continue
        if not r.ok:
            continue
        d = r.json() if isinstance(r.json(), dict) else {}
        for row in (d.get("orders") or []):
            if not isinstance(row, dict):
                continue
            oid = row.get("orderID")
            if oid not in (None, ""):
                clients[str(oid)] = row
    return by_rid, clients

def wb_buyer_from_client(client: dict) -> dict:
    if not isinstance(client, dict):
        return {}
    name = s(client.get("fullName"))
    if not name:
        name = " ".join(x for x in (
            s(client.get("lastName")), s(client.get("firstName")), s(client.get("middleName"))
        ) if x).strip()
    replacement = s(client.get("replacementPhone"))
    phone = replacement or s(client.get("phone"))
    if phone and not replacement and s(client.get("phoneCode")):
        phone = phone + " доб. " + s(client.get("phoneCode"))
    out = {}
    if name:
        out["name"] = name
    if phone:
        out["phone"] = phone
    return out

def load_wb():
    token = s(os.getenv("WB_API_TOKEN"))
    if not token: return []
    h = {"Authorization": token, "Accept": "application/json"}
    dbs_by_rid, dbs_clients = wb_marketplace_dbs_snapshot(h)
    start = datetime.now(timezone.utc) - timedelta(days=WB_DAYS)
    params = {"dateFrom": start.strftime("%Y-%m-%dT%H:%M:%S"), "flag": 0}
    # Statistics API is strictly throttled. Net.req follows WB's X-Ratelimit-Retry header.
    r = net.req("GET", "https://statistics-api.wildberries.ru/api/v1/supplier/orders", headers=h, params=params, tries=4)
    if not r.ok: raise RuntimeError(f"WB orders HTTP {r.status_code}")
    orders = r.json() if isinstance(r.json(), list) else []

    # Do not call supplier/sales in the same run: the Statistics API token currently
    # has a seller-level throttle and the second request is rejected with 429.
    # Import/status therefore uses the order row itself: cancelled -> cancelled,
    # realized/supplied -> completed, otherwise -> processing.
    sold = set()
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
            realized = bool(row.get("isRealization")) or bool(row.get("isSupply"))
            if not realized:
                all_sold = False
            d = dt(row.get("date"))
            if d and (created is None or d < created): created = d
        items = [{"sku": code, "quantity": q, "price": total/q if q else None} for code, (q, total) in agg.items()]

        # Match the Statistics row to the DBS Marketplace order by rid/srid.
        # This lets us copy delivery address and buyer data into Webasyst without
        # changing the historical external id (gNumber), so no duplicate is created.
        detail = {}
        for row in rows:
            rid = s(row.get("srid"))
            if rid and rid in dbs_by_rid:
                detail = dbs_by_rid[rid]
                break
        client = {}
        shipping_address = {}
        wb_order_id = ""
        wb_rid = next((s(row.get("srid")) for row in rows if s(row.get("srid"))), "")
        wb_order_uid = ""
        wb_delivery_type = ""
        recipient_name = ""
        if detail:
            wb_order_id = s(detail.get("id"))
            wb_rid = s(detail.get("rid"))
            wb_order_uid = s(detail.get("orderUid"))
            wb_delivery_type = s(detail.get("deliveryType"))
            addr = detail.get("address") if isinstance(detail.get("address"), dict) else {}
            full_address = s(addr.get("fullAddress"))
            if full_address:
                shipping_address = {"street": full_address}
            client = dbs_clients.get(wb_order_id) or {}
            recipient_name = s(client.get("fullName")) if isinstance(client, dict) else ""

        out.append({
            "source": "wildberries", "external_id": ext,
            "created_at": iso(created) if created else "",
            "status_raw": "CANCELLED" if canceled else "SOLD" if all_sold else "ORDERED",
            "target_state": "otmenen" if canceled else "completed" if all_sold else "processing",
            "items": items, "shipping_name": "Wildberries", "payment_name": "Wildberries",
            "buyer": wb_buyer_from_client(client),
            "recipient_name": recipient_name,
            "shipping_address": shipping_address,
            "wb_order_id": wb_order_id,
            "wb_rid": wb_rid,
            "wb_order_uid": wb_order_uid,
            "wb_delivery_type": wb_delivery_type,
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

def ozon_posting_detail(headers: dict, posting_number: str) -> dict:
    body = {
        "posting_number": posting_number,
        "with": {
            "analytics_data": False,
            "barcodes": False,
            "financial_data": False,
            "translit": False,
        },
    }
    r = net.req("POST", "https://api-seller.ozon.ru/v3/posting/fbs/get", headers=headers, body=body, tries=3)
    if not r.ok:
        return {}
    d = r.json()
    return d.get("result") if isinstance(d, dict) and isinstance(d.get("result"), dict) else d if isinstance(d, dict) else {}

def normalize_ozon_address(address: Any) -> dict:
    if not isinstance(address, dict):
        return {}
    out = {}
    if s(address.get("country")):
        out["country"] = s(address.get("country"))
    if s(address.get("region")):
        out["region"] = s(address.get("region"))
    if s(address.get("city")):
        out["city"] = s(address.get("city"))
    if s(address.get("zip_code")):
        out["zip"] = s(address.get("zip_code"))
    if s(address.get("address_tail")):
        out["street"] = s(address.get("address_tail"))
    return out

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
                posting_number = s(o.get("posting_number"))
                detail = ozon_posting_detail(h, posting_number) if kind == "FBS" and posting_number else {}
                src = detail or o
                items = [{
                    "sku": s(x.get("offer_id")),
                    "quantity": max(1, int(num(x.get("quantity"), 1))),
                    "price": num(x.get("price")) if x.get("price") not in (None, "") else None,
                } for x in (src.get("products") or o.get("products") or []) if isinstance(x, dict)]
                status, sub = s(src.get("status") or o.get("status")), s(src.get("substatus") or o.get("substatus"))
                customer = src.get("customer") if isinstance(src.get("customer"), dict) else {}
                address = normalize_ozon_address(customer.get("address") if isinstance(customer, dict) else {})
                delivery_price = num(src.get("delivery_price")) if src.get("delivery_price") not in (None, "") else None
                prr = src.get("prr_option") if isinstance(src.get("prr_option"), dict) else {}
                lift_price = num(prr.get("price")) if prr.get("price") not in (None, "") else None
                lift_type = s(prr.get("code"))
                shipping_total = None
                if delivery_price is not None or lift_price is not None:
                    shipping_total = float(delivery_price or 0) + float(lift_price or 0)
                recipient_name = s(customer.get("name")) if isinstance(customer, dict) else ""
                out.append({
                    "source": "ozon", "external_id": posting_number,
                    "ozon_kind": kind,
                    "created_at": iso(created), "status_raw": status + ("/"+sub if sub else ""),
                    "target_state": ozon_state(status), "items": items,
                    "shipping_name": f"Ozon / {kind}", "payment_name": "Ozon",
                    "buyer": customer,
                    "recipient_name": recipient_name,
                    "shipping_address": address,
                    "delivery_price": delivery_price,
                    "lift_price": lift_price,
                    "lift_type": lift_type,
                    "shipping_total": shipping_total,
                    "delivery_to": s(src.get("delivering_date")),
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
    if "yandex_market" in SYNC_SOURCES:
        run_source("yandex_market", load_yandex_market)
    if "wildberries" in SYNC_SOURCES:
        run_source("wildberries", load_wb)
    if "yandex_kit" in SYNC_SOURCES:
        run_source("yandex_kit", load_kit)
    if "ozon" in SYNC_SOURCES:
        run_source("ozon", load_ozon)
    report["finished_at"] = iso(datetime.now(timezone.utc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["errors"] else 0

if __name__ == "__main__":
    raise SystemExit(main())
