#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests

WB_TOKEN = (os.getenv("WB_API_TOKEN") or "").strip()
SMTP_HOST = (os.getenv("SMTP_HOST") or "smtp.yandex.ru").strip()
SMTP_PORT = int((os.getenv("SMTP_PORT") or "465").strip())
SMTP_USER = (os.getenv("SMTP_USER") or "shop@office-mag.com").strip()
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").strip()
ALERT_EMAIL_TO = (os.getenv("ALERT_EMAIL_TO") or "shop@office-mag.com").strip()
STATE_FILE = Path((os.getenv("STATE_FILE") or "state/wb_chat_return_monitor.json").strip())
INITIAL_LOOKBACK_HOURS = max(1, int((os.getenv("INITIAL_LOOKBACK_HOURS") or "24").strip()))

CHAT_EVENTS_URL = "https://buyer-chat-api.wildberries.ru/api/v1/seller/events"
RETURNS_URL = "https://returns-api.wildberries.ru/api/v1/claims"

if not WB_TOKEN:
    raise SystemExit("WB_API_TOKEN is missing")
if not SMTP_PASSWORD:
    raise SystemExit(
        "SMTP_PASSWORD is missing. Add GitHub secret YANDEX_SMTP_APP_PASSWORD; "
        "state is not advanced, so alerts will not be lost."
    )

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


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_FILE)


def extract_events(payload: Any) -> tuple[list[dict], int | None]:
    events: list[dict] = []
    nxt: int | None = None

    if isinstance(payload, list):
        events = [x for x in payload if isinstance(x, dict)]
        return events, None
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

    candidates = [payload.get("events"), payload.get("result"), payload.get("data")]
    for c in candidates:
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
    return "|".join(
        [
            str(event.get("chatID") or event.get("chatId") or ""),
            str(event.get("addTimestamp") or ""),
            str(event.get("sender") or ""),
            str((event.get("message") or {}).get("text") if isinstance(event.get("message"), dict) else ""),
        ]
    )


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


def send_alert(chat_events: list[dict], new_claims: list[dict]) -> None:
    parts: list[str] = []
    if chat_events:
        parts.append("НОВЫЕ СООБЩЕНИЯ В ЧАТАХ WILDBERRIES\n")
        for i, event in enumerate(chat_events, 1):
            parts.append(f"--- Сообщение {i} ---\n{fmt_chat(event)}\n")

    if new_claims:
        parts.append(
            "НОВЫЕ ЗАЯВКИ НА ВОЗВРАТ WILDBERRIES\n"
            "Важно: по данным WB, для DBS/EDBS срок рассмотрения заявки — 1 день, "
            "для других моделей — 10 дней.\n"
        )
        for i, claim in enumerate(new_claims, 1):
            parts.append(f"--- Возврат {i} ---\n{fmt_claim(claim)}\n")

    body = "\n".join(parts).strip() + "\n"
    subject_bits = []
    if new_claims:
        subject_bits.append(f"ВОЗВРАТ: {len(new_claims)}")
    if chat_events:
        subject_bits.append(f"новых сообщений: {len(chat_events)}")
    subject = "[WB] " + ", ".join(subject_bits)

    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_EMAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=45, context=ctx) as smtp:
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)


def main() -> int:
    state = load_state()
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
        if not is_client_message(event):
            continue
        k = event_key(event)
        if k in seen_event_ids:
            continue
        incoming.append(event)

    claims = fetch_active_claims()
    new_claims = []
    for claim in claims:
        cid = str(claim.get("id") or "").strip()
        if not cid or cid in seen_claim_ids:
            continue
        new_claims.append(claim)

    incoming.sort(key=lambda x: int(x.get("addTimestamp") or 0))
    new_claims.sort(key=lambda x: str(x.get("dt") or ""))

    print(
        json.dumps(
            {
                "initialized": initialized,
                "chat_events_read": len(events),
                "new_client_messages": len(incoming),
                "active_claims": len(claims),
                "new_claims": len(new_claims),
                "chat_next_before": cursor,
                "chat_next_after": next_cursor,
                "alert_to": ALERT_EMAIL_TO,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if incoming or new_claims:
        send_alert(incoming, new_claims)
        print("EMAIL_SENT=1")
    else:
        print("EMAIL_SENT=0")

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

    # Keep enough IDs to protect against inclusive cursors / API replays without growing forever.
    event_ids_sorted = ordered_event_ids[-5000:]
    claim_ids_sorted = ordered_claim_ids[-10000:]

    if events:
        saved_cursor = max(int(next_cursor or 0), cursor)
    elif initialized:
        saved_cursor = max(int(next_cursor or 0), cursor)
    else:
        # On an empty first run, baseline from "now" so the lookback window is not rescanned forever.
        saved_cursor = now_ms

    new_state = {
        "version": 1,
        "initialized_at": state.get("initialized_at") or iso_now(),
        "last_run_at": iso_now(),
        "chat_next": saved_cursor,
        "seen_event_ids": event_ids_sorted,
        "seen_claim_ids": claim_ids_sorted,
    }
    save_state(new_state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MONITOR_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        raise
