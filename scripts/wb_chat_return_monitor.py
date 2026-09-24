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
OUTBOX_FILE = Path((os.getenv("OUTBOX_FILE") or "state/wb_chat_return_outbox.json").strip())
INITIAL_LOOKBACK_HOURS = max(1, int((os.getenv("INITIAL_LOOKBACK_HOURS") or "24").strip()))

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
            time.sleep(min(180.0, max(1.0, wait + 2.0)))
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
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def extract_events(payload: Any) -> tuple[list[dict], int | None]:
    events: list[dict] = []
    nxt: int | None = None
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)], None
    if not isinstance(payload, dict):
        return events, None

    for key in ("next", "nextCursor", "cursor"):
        v = payload.get(key)
        if v not in (None, ""):
            try:
                nxt = int(v)
                break
            except (TypeError, ValueError):
                pass

    for c in (payload.get("events"), payload.get("result"), payload.get("data")):
        if isinstance(c, list):
            events = [x for x in c if isinstance(x, dict)]
            break
        if isinstance(c, dict):
            for key in ("events", "items", "result"):
                rows = c.get(key)
                if isinstance(rows, list):
                    events = [x for x in rows if isinstance(x, dict)]
                    break
            if events:
                if nxt is None:
                    for key in ("next", "nextCursor", "cursor"):
                        v = c.get(key)
                        if v not in (None, ""):
                            try:
                                nxt = int(v)
                                break
                            except (TypeError, ValueError):
                                pass
                break
    return events, nxt


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


def fetch_chat_events(start_cursor: int) -> tuple[list[dict], int]:
    cursor = max(0, int(start_cursor))
    all_events: list[dict] = []
    seen_page_cursors = set()
    for _ in range(100):
        if cursor in seen_page_cursors:
            break
        seen_page_cursors.add(cursor)
        payload = request_json(CHAT_EVENTS_URL, params={"next": cursor})
        events, nxt = extract_events(payload)
        all_events.extend(events)

        max_ts = cursor
        for ev in events:
            try:
                max_ts = max(max_ts, int(ev.get("addTimestamp") or 0))
            except (TypeError, ValueError):
                pass
        if nxt is None:
            nxt = max_ts + 1 if max_ts > cursor else cursor
        if nxt <= cursor:
            break
        cursor = nxt
        if not events:
            break
    return all_events, cursor


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


def message_text(event: dict) -> str:
    msg = event.get("message")
    if isinstance(msg, dict):
        text = str(msg.get("text") or "").strip()
        if text:
            return text
        attachments = msg.get("attachments")
        if isinstance(attachments, list) and attachments:
            return "[Сообщение содержит вложение/фото]"
    return "[Сообщение без текста]"


def attachment_lines(event: dict) -> list[str]:
    msg = event.get("message")
    if not isinstance(msg, dict):
        return []
    attachments = msg.get("attachments")
    if not isinstance(attachments, list):
        return []
    out: list[str] = []
    for a in attachments:
        if not isinstance(a, dict):
            continue
        url = str(a.get("url") or "").strip()
        download_id = str(a.get("downloadID") or a.get("downloadId") or "").strip()
        if url:
            out.append(f"Вложение: {url}")
        elif download_id:
            out.append(f"Вложение WB, downloadID: {download_id}")
    return out


def fmt_chat(event: dict) -> str:
    client = str(event.get("clientName") or event.get("clientID") or "Покупатель").strip()
    chat_id = str(event.get("chatID") or event.get("chatId") or "").strip()
    when = str(event.get("addTime") or "").strip()
    lines = [
        f"Покупатель: {client}",
        f"Время: {when or 'не указано'}",
        f"Сообщение: {message_text(event)}",
    ]
    if chat_id:
        lines.append(f"Chat ID: {chat_id}")
    lines.extend(attachment_lines(event))
    return "\n".join(lines)


def fmt_claim(claim: dict) -> str:
    lines = [
        f"Товар: {str(claim.get('imt_name') or '').strip() or 'не указано'}",
        f"Артикул WB (nm_id): {claim.get('nm_id', '')}",
        f"SRID/заказ: {claim.get('srid', '')}",
        f"Дата заявки: {claim.get('dt', '')}",
        f"Комментарий покупателя: {str(claim.get('user_comment') or '').strip() or 'нет комментария'}",
        f"Статус: {claim.get('status', '')}",
        f"Тип заявки: {claim.get('claim_type', '')}",
        f"ID заявки: {claim.get('id', '')}",
    ]
    photos = claim.get("photos")
    if isinstance(photos, list) and photos:
        lines.append(f"Фото: {len(photos)}")
        for p in photos[:10]:
            if isinstance(p, str) and p.strip():
                lines.append(p.strip())
    return "\n".join(lines)


def make_report(chat_events: list[dict], new_claims: list[dict]) -> tuple[str, str]:
    parts: list[str] = []
    if chat_events:
        parts.append("НОВЫЕ СООБЩЕНИЯ В ЧАТАХ WILDBERRIES\n")
        for i, event in enumerate(chat_events, 1):
            parts.append(f"--- Сообщение {i} ---\n{fmt_chat(event)}\n")

    if new_claims:
        parts.append("НОВЫЕ ЗАЯВКИ НА ВОЗВРАТ WILDBERRIES\n")
        for i, claim in enumerate(new_claims, 1):
            parts.append(f"--- Возврат {i} ---\n{fmt_claim(claim)}\n")

    subject_bits = []
    if new_claims:
        subject_bits.append(f"возвратов: {len(new_claims)}")
    if chat_events:
        subject_bits.append(f"новых сообщений: {len(chat_events)}")
    subject = "[WB] " + ", ".join(subject_bits)
    body = "\n".join(parts).strip() + "\n"
    return subject, body


def append_outbox(subject: str, body: str, incoming: list[dict], claims: list[dict]) -> str:
    outbox = load_json(OUTBOX_FILE, {"version": 1, "items": []})
    if not isinstance(outbox, dict):
        outbox = {"version": 1, "items": []}
    items = outbox.get("items")
    if not isinstance(items, list):
        items = []

    seed = subject + "\n" + body
    batch_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    if not any(isinstance(x, dict) and x.get("id") == batch_id for x in items):
        items.append({
            "id": batch_id,
            "created_at": iso_now(),
            "recipient": "shop@office-mag.com",
            "subject": subject,
            "body": body,
            "new_client_messages": len(incoming),
            "new_return_claims": len(claims),
            "sent": False,
            "sent_at": None,
        })
    outbox["items"] = items[-200:]
    save_json(OUTBOX_FILE, outbox)
    return batch_id


def main() -> int:
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    now = utc_now()
    now_ms = int(now.timestamp() * 1000)

    prior_event_ids = [str(x) for x in (state.get("seen_event_ids") or []) if str(x).strip()]
    prior_claim_ids = [str(x) for x in (state.get("seen_claim_ids") or []) if str(x).strip()]
    seen_event_ids = set(prior_event_ids)
    seen_claim_ids = set(prior_claim_ids)

    initialized = bool(state.get("initialized_at"))
    if initialized:
        try:
            cursor = int(state.get("chat_next") or 0)
        except (TypeError, ValueError):
            cursor = 0
    else:
        cursor = int((now - timedelta(hours=INITIAL_LOOKBACK_HOURS)).timestamp() * 1000)

    events, next_cursor = fetch_chat_events(cursor)
    incoming = []
    for event in events:
        if is_client_message(event):
            k = event_key(event)
            if k not in seen_event_ids:
                incoming.append(event)

    claims = fetch_active_claims()
    new_claims = []
    for claim in claims:
        cid = str(claim.get("id") or "").strip()
        if cid and cid not in seen_claim_ids:
            new_claims.append(claim)

    incoming.sort(key=lambda x: int(x.get("addTimestamp") or 0))
    new_claims.sort(key=lambda x: str(x.get("dt") or ""))

    batch_id = ""
    if incoming or new_claims:
        subject, body = make_report(incoming, new_claims)
        batch_id = append_outbox(subject, body, incoming, new_claims)

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

    if events:
        saved_cursor = max(int(next_cursor or 0), cursor)
    elif initialized:
        saved_cursor = max(int(next_cursor or 0), cursor)
    else:
        saved_cursor = now_ms

    new_state = {
        "version": 2,
        "initialized_at": state.get("initialized_at") or iso_now(),
        "last_run_at": iso_now(),
        "chat_next": saved_cursor,
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
        "outbox_batch_id": batch_id,
        "recipient": "shop@office-mag.com",
        "delivery_channel": "ChatGPT Gmail connector",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MONITOR_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        raise
