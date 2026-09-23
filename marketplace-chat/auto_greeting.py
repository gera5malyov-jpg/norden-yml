from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "state.json"

GREETING = """Здравствуйте! 👋

Спасибо за ваш заказ и за то, что выбрали наш магазин «Мегаполис»! 😊

Мы уже получили заказ и приступили к его обработке. Постараемся всё подготовить и передать в доставку максимально быстро.

Если у вас появятся вопросы по заказу, товару или доставке — напишите нам в чат, мы обязательно поможем.

Желаем приятной покупки!
С уважением, команда «Мегаполис» ❤️"""

USER_AGENT = "megapolis-marketplace-greeting/1.0"
TIMEOUT = 30
LOOKBACK_DAYS = 2
MAX_RETRY_ATTEMPTS = 12


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def secret(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1, "providers": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state is not an object")
    except Exception:
        data = {"version": 1, "providers": {}}
    data.setdefault("version", 1)
    data.setdefault("providers", {})
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def provider_state(state: dict[str, Any], provider: str) -> dict[str, Any]:
    ps = state["providers"].setdefault(provider, {})
    ps.setdefault("bootstrapped", False)
    ps.setdefault("orders", {})
    return ps


def prune_orders(ps: dict[str, Any], keep: int = 5000) -> None:
    orders = ps.get("orders", {})
    if len(orders) <= keep:
        return
    ranked = sorted(
        orders.items(),
        key=lambda kv: kv[1].get("last_seen_at", kv[1].get("first_seen_at", "")),
        reverse=True,
    )
    ps["orders"] = dict(ranked[:keep])


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    try:
        response = requests.request(
            method,
            url,
            headers=hdrs,
            json=json_body,
            params=params,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ApiError(f"network error: {type(exc).__name__}") from exc

    if not response.ok:
        body = response.text.replace("\n", " ")[:500]
        raise ApiError(f"HTTP {response.status_code}: {body}", response.status_code)

    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiError(f"invalid JSON from {url}") from exc
    return payload if isinstance(payload, dict) else {"data": payload}


def make_summary(provider: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "configured": False,
        "bootstrapped_now": False,
        "orders_found": 0,
        "new_orders": 0,
        "messages_sent": 0,
        "blocked": 0,
        "retrying": 0,
        "errors": [],
    }


def mark_baseline(ps: dict[str, Any], keys: list[str], details: dict[str, dict[str, Any]]) -> None:
    now = iso(utcnow())
    for key in keys:
        if key not in ps["orders"]:
            ps["orders"][key] = {
                "status": "baseline",
                "first_seen_at": now,
                "last_seen_at": now,
                **details.get(key, {}),
            }
    ps["bootstrapped"] = True
    ps["bootstrapped_at"] = now


def note_error(
    record: dict[str, Any],
    exc: Exception,
    *,
    permanent_statuses: set[int] | None = None,
) -> bool:
    attempts = int(record.get("attempts", 0)) + 1
    record["attempts"] = attempts
    record["last_attempt_at"] = iso(utcnow())
    record["last_error"] = str(exc)[:1000]
    code = getattr(exc, "status_code", None)
    permanent = code in (permanent_statuses or set()) or attempts >= MAX_RETRY_ATTEMPTS
    record["status"] = "blocked" if permanent else "retry"
    return permanent


# ----------------------------- Ozon -----------------------------------------

def ozon_list_postings(client_id: str, api_key: str) -> list[dict[str, Any]]:
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    now = utcnow()
    offset = 0
    postings: list[dict[str, Any]] = []

    while True:
        body = {
            "dir": "DESC",
            "filter": {
                "since": iso(now - timedelta(days=LOOKBACK_DAYS)),
                "to": iso(now + timedelta(minutes=5)),
            },
            "limit": 1000,
            "offset": offset,
            "with": {
                "analytics_data": False,
                "barcodes": False,
                "financial_data": False,
                "translit": False,
            },
        }
        data = request_json(
            "POST",
            "https://api-seller.ozon.ru/v3/posting/fbs/list",
            headers=headers,
            json_body=body,
        )
        result = data.get("result") or {}
        batch = result.get("postings") or []
        if not isinstance(batch, list):
            batch = []
        postings.extend(x for x in batch if isinstance(x, dict))
        if not result.get("has_next") or not batch:
            break
        offset += len(batch)
        if offset >= 10000:
            break
    return postings


def ozon_send_greeting(client_id: str, api_key: str, posting_number: str) -> None:
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }
    start = request_json(
        "POST",
        "https://api-seller.ozon.ru/v1/chat/start",
        headers=headers,
        json_body={"posting_number": posting_number},
    )
    chat_id = ((start.get("result") or {}).get("chat_id") or "").strip()
    if not chat_id:
        raise ApiError("Ozon chat/start returned no chat_id")
    request_json(
        "POST",
        "https://api-seller.ozon.ru/v1/chat/send/message",
        headers=headers,
        json_body={"chat_id": chat_id, "text": GREETING},
    )


def run_ozon(state: dict[str, Any]) -> dict[str, Any]:
    summary = make_summary("ozon")
    client_id = secret("OZON_CLIENT_ID", "OZON_CLIENTID")
    api_key = secret("OZON_API_KEY", "OZON_TOKEN")
    if not client_id or not api_key:
        summary["errors"].append("OZON_CLIENT_ID/OZON_API_KEY are not configured")
        return summary

    summary["configured"] = True
    ps = provider_state(state, "ozon")
    try:
        postings = ozon_list_postings(client_id, api_key)
    except Exception as exc:
        summary["errors"].append(str(exc))
        return summary

    eligible: list[dict[str, Any]] = []
    for posting in postings:
        posting_number = str(posting.get("posting_number") or "").strip()
        status = str(posting.get("status") or "").lower()
        if not posting_number or status in {"cancelled", "canceled"}:
            continue
        eligible.append(posting)

    summary["orders_found"] = len(eligible)
    details = {
        str(p["posting_number"]): {
            "posting_number": str(p["posting_number"]),
            "marketplace_status": p.get("status"),
        }
        for p in eligible
    }
    keys = list(details)

    if not ps.get("bootstrapped"):
        mark_baseline(ps, keys, details)
        summary["bootstrapped_now"] = True
        prune_orders(ps)
        return summary

    now_text = iso(utcnow())
    for posting in eligible:
        key = str(posting["posting_number"])
        record = ps["orders"].get(key)
        if record and record.get("status") in {"sent", "baseline", "blocked"}:
            record["last_seen_at"] = now_text
            continue

        if record is None:
            summary["new_orders"] += 1
            record = {
                "status": "new",
                "posting_number": key,
                "marketplace_status": posting.get("status"),
                "first_seen_at": now_text,
                "last_seen_at": now_text,
                "attempts": 0,
            }
            ps["orders"][key] = record
        else:
            record["last_seen_at"] = now_text

        try:
            ozon_send_greeting(client_id, api_key, key)
            record["status"] = "sent"
            record["sent_at"] = iso(utcnow())
            record.pop("last_error", None)
            summary["messages_sent"] += 1
        except Exception as exc:
            permanent = note_error(record, exc, permanent_statuses={400, 403})
            (summary["blocked"] if permanent else summary["retrying"])
            if permanent:
                summary["blocked"] += 1
            else:
                summary["retrying"] += 1
            summary["errors"].append(f"{key}: {exc}")

    prune_orders(ps)
    return summary


# -------------------------- Yandex Market -----------------------------------

def yandex_headers(api_key: str, oauth_token: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Api-Key"] = api_key
    else:
        headers["Authorization"] = f"OAuth {oauth_token}"
    return headers


def yandex_business_ids(headers: dict[str, str]) -> list[str]:
    configured = secret("YANDEX_MARKET_BUSINESS_IDS", "YANDEX_MARKET_BUSINESS_ID")
    if configured:
        return sorted({x.strip() for x in configured.replace(";", ",").split(",") if x.strip()})

    data = request_json(
        "GET",
        "https://api.partner.market.yandex.ru/v2/campaigns",
        headers=headers,
    )
    ids: set[str] = set()
    campaigns = data.get("campaigns") or ((data.get("result") or {}).get("campaigns")) or []
    for campaign in campaigns if isinstance(campaigns, list) else []:
        if not isinstance(campaign, dict):
            continue
        business = campaign.get("business") or {}
        bid = business.get("id") if isinstance(business, dict) else None
        bid = bid or campaign.get("businessId") or campaign.get("business_id")
        if bid:
            ids.add(str(bid))
    if not ids:
        raise ApiError("Yandex Market: could not discover businessId; set YANDEX_MARKET_BUSINESS_ID")
    return sorted(ids)


def yandex_list_orders(headers: dict[str, str], business_id: str) -> list[dict[str, Any]]:
    now = utcnow()
    body = {
        "creationDateFrom": (now - timedelta(days=LOOKBACK_DAYS)).date().isoformat(),
        "creationDateTo": (now + timedelta(days=1)).date().isoformat(),
    }
    orders: list[dict[str, Any]] = []
    page_token: str | None = None

    for _ in range(50):
        params: dict[str, Any] = {"limit": 200}
        if page_token:
            params["pageToken"] = page_token
        data = request_json(
            "POST",
            f"https://api.partner.market.yandex.ru/v1/businesses/{business_id}/orders",
            headers=headers,
            json_body=body,
            params=params,
        )
        result = data.get("result") if isinstance(data.get("result"), dict) else data
        batch = result.get("orders") or []
        if not isinstance(batch, list):
            batch = []
        orders.extend(x for x in batch if isinstance(x, dict))

        paging = result.get("paging") or data.get("paging") or {}
        page_token = (
            paging.get("nextPageToken")
            or paging.get("next_page_token")
            or result.get("nextPageToken")
            or data.get("nextPageToken")
        )
        if not page_token or not batch:
            break
    return orders


def yandex_send_greeting(headers: dict[str, str], business_id: str, order_id: str) -> None:
    start = request_json(
        "POST",
        f"https://api.partner.market.yandex.ru/v2/businesses/{business_id}/chats/new",
        headers=headers,
        json_body={"context": {"type": "ORDER", "id": int(order_id)}},
    )
    chat_id = ((start.get("result") or {}).get("chatId"))
    if chat_id is None:
        raise ApiError("Yandex Market chats/new returned no chatId")
    request_json(
        "POST",
        f"https://api.partner.market.yandex.ru/v2/businesses/{business_id}/chats/message",
        headers=headers,
        params={"chatId": chat_id},
        json_body={"message": GREETING},
    )


def run_yandex(state: dict[str, Any]) -> dict[str, Any]:
    summary = make_summary("yandex_market")
    api_key = secret("YANDEX_MARKET_API_KEY", "YANDEX_API_KEY", "MARKET_API_KEY")
    oauth = secret("YANDEX_MARKET_OAUTH_TOKEN", "YANDEX_OAUTH_TOKEN")
    if not api_key and not oauth:
        summary["errors"].append("YANDEX_MARKET_API_KEY is not configured")
        return summary

    summary["configured"] = True
    ps = provider_state(state, "yandex_market")
    headers = yandex_headers(api_key, oauth)

    try:
        business_ids = yandex_business_ids(headers)
    except Exception as exc:
        summary["errors"].append(str(exc))
        return summary

    all_orders: list[tuple[str, dict[str, Any]]] = []
    for business_id in business_ids:
        try:
            for order in yandex_list_orders(headers, business_id):
                all_orders.append((business_id, order))
        except Exception as exc:
            summary["errors"].append(f"business {business_id}: {exc}")

    eligible: list[tuple[str, dict[str, Any]]] = []
    skip_statuses = {"PLACING", "RESERVED", "UNPAID", "CANCELLED"}
    for business_id, order in all_orders:
        order_id = order.get("id")
        status = str(order.get("status") or "").upper()
        if order_id is None or status in skip_statuses or bool(order.get("fake")):
            continue
        eligible.append((business_id, order))

    summary["orders_found"] = len(eligible)
    details: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for business_id, order in eligible:
        key = f"{business_id}:{order['id']}"
        keys.append(key)
        details[key] = {
            "business_id": business_id,
            "order_id": str(order["id"]),
            "marketplace_status": order.get("status"),
        }

    if not ps.get("bootstrapped") and not summary["errors"]:
        mark_baseline(ps, keys, details)
        summary["bootstrapped_now"] = True
        prune_orders(ps)
        return summary

    now_text = iso(utcnow())
    for business_id, order in eligible:
        key = f"{business_id}:{order['id']}"
        record = ps["orders"].get(key)
        if record and record.get("status") in {"sent", "baseline", "blocked"}:
            record["last_seen_at"] = now_text
            continue

        if record is None:
            summary["new_orders"] += 1
            record = {
                "status": "new",
                "business_id": business_id,
                "order_id": str(order["id"]),
                "marketplace_status": order.get("status"),
                "first_seen_at": now_text,
                "last_seen_at": now_text,
                "attempts": 0,
            }
            ps["orders"][key] = record
        else:
            record["last_seen_at"] = now_text

        try:
            yandex_send_greeting(headers, business_id, str(order["id"]))
            record["status"] = "sent"
            record["sent_at"] = iso(utcnow())
            record.pop("last_error", None)
            summary["messages_sent"] += 1
        except Exception as exc:
            permanent = note_error(record, exc, permanent_statuses={400, 401, 403})
            if permanent:
                summary["blocked"] += 1
            else:
                summary["retrying"] += 1
            summary["errors"].append(f"{key}: {exc}")

    prune_orders(ps)
    return summary


# -------------------------- Wildberries -------------------------------------

def wb_list_new_orders(token: str) -> list[dict[str, Any]]:
    data = request_json(
        "GET",
        "https://marketplace-api.wildberries.ru/api/v3/orders/new",
        headers={"Authorization": token},
    )
    orders = data.get("orders") or []
    return [x for x in orders if isinstance(x, dict)] if isinstance(orders, list) else []


def run_wildberries(state: dict[str, Any]) -> dict[str, Any]:
    summary = make_summary("wildberries")
    token = secret("WB_API_TOKEN", "WB_TOKEN", "WILDBERRIES_API_KEY")
    if not token:
        summary["errors"].append("WB_API_TOKEN is not configured")
        return summary

    summary["configured"] = True
    ps = provider_state(state, "wildberries")
    try:
        orders = wb_list_new_orders(token)
    except Exception as exc:
        summary["errors"].append(str(exc))
        return summary

    summary["orders_found"] = len(orders)
    details: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    for order in orders:
        order_id = order.get("id")
        if order_id is None:
            continue
        key = str(order_id)
        keys.append(key)
        details[key] = {
            "order_id": key,
            "rid": order.get("rid"),
        }

    if not ps.get("bootstrapped"):
        mark_baseline(ps, keys, details)
        summary["bootstrapped_now"] = True
        prune_orders(ps)
        return summary

    now_text = iso(utcnow())
    for order in orders:
        order_id = order.get("id")
        if order_id is None:
            continue
        key = str(order_id)
        record = ps["orders"].get(key)
        if record:
            record["last_seen_at"] = now_text
            continue

        summary["new_orders"] += 1
        summary["blocked"] += 1
        ps["orders"][key] = {
            "status": "blocked",
            "reason": "wildberries_api_cannot_start_buyer_chat",
            "order_id": key,
            "rid": order.get("rid"),
            "first_seen_at": now_text,
            "last_seen_at": now_text,
        }

    prune_orders(ps)
    return summary


def main() -> int:
    state = load_state()
    summaries = [
        run_ozon(state),
        run_yandex(state),
        run_wildberries(state),
    ]
    save_state(state)

    output = {
        "ok": True,
        "message_preview": GREETING,
        "providers": summaries,
        "notes": [
            "First successful run per provider is baseline-only: existing orders are not messaged.",
            "Wildberries seller API cannot proactively create a buyer chat; new WB orders are logged as blocked.",
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

    configured = [x for x in summaries if x["configured"]]
    if not configured:
        print("No marketplace API credentials are configured.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
