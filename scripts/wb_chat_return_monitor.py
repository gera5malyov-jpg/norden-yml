#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

WB_TOKEN = (os.getenv("WB_API_TOKEN") or "").strip()
STATE_FILE = Path((os.getenv("STATE_FILE") or "state/wb_chat_return_monitor.json").strip())
SUMMARY_FILE = Path((os.getenv("SUMMARY_FILE") or "state/wb_chat_return_summary.json").strip())
INITIAL_LOOKBACK_HOURS = max(1, int((os.getenv("INITIAL_LOOKBACK_HOURS") or "24").strip()))
CHAT_PAGE_DELAY_SECONDS = max(10.2, float((os.getenv("CHAT_PAGE_DELAY_SECONDS") or "10.2").strip()))
MAX_CHAT_PAGES = max(1, int((os.getenv("MAX_CHAT_PAGES") or "20").strip()))

CHAT_EVENTS_URL = "https://buyer-chat-api.wildberries.ru/api/v1/seller/events"
RETURNS_URL = "https://returns-api.wildberries.ru/api/v1/claims"

if not WB_TOKEN:
    raise SystemExit("WB_API_TOKEN is missing")

HEADERS = {"Authorization": WB_TOKEN, "Accept": "application/json"}
session = requests.Session()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def request_json(url: str, *, params: dict | None = None, tries: int = 6) -> Any:
    last_error = None
    for attempt in range(tries):
        try:
            r = session.get(url, headers=HEADERS, params=params, timeout=90)
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < tries:
                time.sleep(min(30, 2 + attempt * 3))
                continue
            raise
        if r.status_code == 429 and attempt + 1 < tries:
            raw = r.headers.get("X-RateLimit-Retry") or r.headers.get("Retry-After") or "65"
            try:
                wait = float(raw)
            except (TypeError, ValueError):
                wait = 65.0
            time.sleep(min(180.0, max(10.2, wait + 2.0)))
            continue
        if r.status_code >= 500 and attempt + 1 < tries:
            time.sleep(min(30, 3 + attempt * 4))
            continue
        if not r.ok:
            raise RuntimeError(f"GET {url} HTTP {r.status_code}: {r.text[:700]}")
        try:
            return r.json() if r.content else {}
        except ValueError as exc:
            raise RuntimeError(f"GET {url}: invalid JSON: {r.text[:500]}") from exc
    raise RuntimeError(str(last_error or f"GET {url} failed"))


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def event_key(event: dict) -> str:
    val = str(event.get("eventID") or event.get("eventId") or "").strip()
    if val:
        return val
    raw = "|".join([
        str(event.get("chatID") or event.get("chatId") or ""),
        str(event.get("addTimestamp") or ""),
        str(event.get("sender") or ""),
        str((event.get("message") or {}).get("text") if isinstance(event.get("message"), dict) else ""),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_event_page(payload: Any) -> tuple[list[dict], int | None, int | None]:
    if not isinstance(payload, dict):
        return [], None, None

    root = payload
    if isinstance(payload.get("result"), dict):
        root = payload["result"]
    elif isinstance(payload.get("data"), dict):
        root = payload["data"]

    events = root.get("events") if isinstance(root, dict) else []
    if not isinstance(events, list):
        events = []

    nxt = root.get("next") if isinstance(root, dict) else None
    if nxt in (None, ""):
        nxt = payload.get("next")
    try:
        nxt = int(nxt) if nxt not in (None, "") else None
    except (TypeError, ValueError):
        nxt = None

    total = root.get("totalEvents") if isinstance(root, dict) else None
    if total in (None, ""):
        total = payload.get("totalEvents")
    try:
        total = int(total) if total not in (None, "") else None
    except (TypeError, ValueError):
        total = None

    return [x for x in events if isinstance(x, dict)], nxt, total


def fetch_chat_events(start_cursor: int | None) -> tuple[list[dict], int | None, bool]:
    cursor = int(start_cursor) if start_cursor not in (None, 0, "") else None
    all_events: list[dict] = []
    exhausted = False

    for page_index in range(MAX_CHAT_PAGES):
        params = {"next": cursor} if cursor is not None else None
        payload = request_json(CHAT_EVENTS_URL, params=params)
        events, nxt, total = parse_event_page(payload)
        all_events.extend(events)

        if total == 0 or not events:
            exhausted = True
            if nxt is not None:
                cursor = nxt
            break
        if nxt is None or nxt == cursor:
            break

        cursor = nxt
        if page_index + 1 < MAX_CHAT_PAGES:
            time.sleep(CHAT_PAGE_DELAY_SECONDS)

    return all_events, cursor, exhausted


def fetch_active_claims() -> list[dict]:
    rows: list[dict] = []
    offset = 0
    limit = 100
    for _ in range(100):
        payload = request_json(
            RETURNS_URL,
            params={"is_archive": "false", "limit": limit, "offset": offset},
        )
        page = payload.get("claims") if isinstance(payload, dict) else []
        page = [x for x in (page or []) if isinstance(x, dict)]
        rows.extend(page)
        total = payload.get("total") if isinstance(payload, dict) else None
        if len(page) < limit:
            break
        offset += len(page)
        try:
            if total is not None and offset >= int(total):
                break
        except (TypeError, ValueError):
            pass
        time.sleep(1.2)
    return rows


def is_client_message(event: dict) -> bool:
    if str(event.get("sender") or "").strip().lower() != "client":
        return False
    event_type = str(event.get("eventType") or event.get("type") or "").strip().lower()
    return event_type in {"", "message"}


def event_timestamp_ms(event: dict) -> int:
    try:
        return int(event.get("addTimestamp") or 0)
    except (TypeError, ValueError):
        return 0


def main() -> int:
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}

    now = utc_now()
    cutoff_ms = int((now - timedelta(hours=INITIAL_LOOKBACK_HOURS)).timestamp() * 1000)

    prior_event_ids = [str(x) for x in (state.get("seen_event_ids") or []) if str(x).strip()]
    prior_claim_ids = [str(x) for x in (state.get("seen_claim_ids") or []) if str(x).strip()]
    seen_event_ids = set(prior_event_ids)
    seen_claim_ids = set(prior_claim_ids)

    initialized = bool(state.get("initialized_at"))
    try:
        stored_cursor = int(state.get("chat_next")) if state.get("chat_next") not in (None, "") else None
    except (TypeError, ValueError):
        stored_cursor = None

    events, next_cursor, chat_exhausted = fetch_chat_events(stored_cursor if initialized else None)

    incoming = []
    for event in events:
        if not is_client_message(event):
            continue
        k = event_key(event)
        if k in seen_event_ids:
            continue
        if not initialized and event_timestamp_ms(event) and event_timestamp_ms(event) < cutoff_ms:
            continue
        incoming.append(event)

    claims = fetch_active_claims()
    new_claims = []
    for claim in claims:
        cid = str(claim.get("id") or "").strip()
        if not cid or cid in seen_claim_ids:
            continue
        new_claims.append(claim)

    ordered_event_ids = list(prior_event_ids)
    for event in events:
        k = event_key(event)
        if k and k not in seen_event_ids:
            ordered_event_ids.append(k)
            seen_event_ids.add(k)

    ordered_claim_ids = list(prior_claim_ids)
    for claim in claims:
        cid = str(claim.get("id") or "").strip()
        if cid and cid not in seen_claim_ids:
            ordered_claim_ids.append(cid)
            seen_claim_ids.add(cid)

    batch_seed = f"{iso_now()}|{len(incoming)}|{len(new_claims)}|{next_cursor or ''}"
    batch_id = hashlib.sha256(batch_seed.encode("utf-8")).hexdigest()[:24]

    summary = {
        "version": 1,
        "batch_id": batch_id,
        "run_at": iso_now(),
        "new_client_messages": len(incoming),
        "new_return_claims": len(new_claims),
        "active_return_claims": len(claims),
        "chat_events_read": len(events),
        "chat_cursor_exhausted": chat_exhausted,
        "sent": False,
        "sent_at": None,
        "recipient": "shop@office-mag.com",
        "privacy_note": "Only counts are stored; customer message text, names, phones, order IDs and photos are not written to the public repository.",
    }
    save_json(SUMMARY_FILE, summary)

    new_state = {
        "version": 3,
        "initialized_at": state.get("initialized_at") or iso_now(),
        "last_run_at": iso_now(),
        "chat_next": next_cursor,
        "seen_event_ids": ordered_event_ids[-5000:],
        "seen_claim_ids": ordered_claim_ids[-10000:],
    }
    save_json(STATE_FILE, new_state)

    print(json.dumps({
        "ok": True,
        "initialized": initialized,
        "chat_events_read": len(events),
        "new_client_messages": len(incoming),
        "active_claims": len(claims),
        "new_claims": len(new_claims),
        "chat_cursor_exhausted": chat_exhausted,
        "recipient": "shop@office-mag.com",
        "delivery_channel": "ChatGPT Gmail connector",
        "batch_id": batch_id,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MONITOR_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        raise
