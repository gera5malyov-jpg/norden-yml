#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import requests

WB_TOKEN = (os.getenv("WB_API_TOKEN") or "").strip()
GMAIL_USER = (os.getenv("GMAIL_SMTP_USER") or os.getenv("GMAIL_USER") or "gera5malyov@gmail.com").strip()
GMAIL_APP_PASSWORD = (os.getenv("GMAIL_APP_PASSWORD") or "").strip()
MAIL_TO = (os.getenv("MAIL_TO") or "shop@office-mag.com").strip()
STATE_FILE = Path(os.getenv("STATE_FILE") or "state/wb_chat_return_monitor_v2.json")
SUMMARY_FILE = Path(os.getenv("SUMMARY_FILE") or "state/wb_chat_return_summary_v2.json")
LOOKBACK_HOURS = max(3, int(os.getenv("LOOKBACK_HOURS") or "24"))

EVENTS_URL = "https://buyer-chat-api.wildberries.ru/api/v1/seller/events"
RETURNS_URL = "https://returns-api.wildberries.ru/api/v1/claims"

if not WB_TOKEN:
    raise SystemExit("WB_API_TOKEN is missing")

HEADERS = {"Authorization": WB_TOKEN, "Accept": "application/json"}


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wb_get(url: str, params: dict):
    import time
    last = None
    for attempt in range(4):
        r = requests.get(url, headers=HEADERS, params=params, timeout=90)
        last = r
        if r.status_code == 429 and attempt < 3:
            raw = r.headers.get("X-RateLimit-Retry") or r.headers.get("Retry-After") or "65"
            try:
                wait = float(raw)
            except (TypeError, ValueError):
                wait = 65.0
            time.sleep(min(180.0, max(12.0, wait + 2.0)))
            continue
        if not r.ok:
            raise RuntimeError(f"WB HTTP {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}
    raise RuntimeError(f"WB HTTP {last.status_code}: {last.text[:400]}")


def get_root(payload):
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("result"), dict):
        return payload["result"]
    return payload


def event_id(event: dict) -> str:
    eid = str(event.get("eventID") or event.get("eventId") or "").strip()
    if eid:
        return h(eid)
    msg = event.get("message") if isinstance(event.get("message"), dict) else {}
    raw = "|".join([
        str(event.get("chatID") or event.get("chatId") or ""),
        str(event.get("addTimestamp") or ""),
        str(event.get("sender") or ""),
        str(msg.get("text") or ""),
    ])
    return h(raw)


def chat_id(event: dict) -> str:
    raw = str(event.get("chatID") or event.get("chatId") or "").strip()
    return h(raw) if raw else ""


def is_message(event: dict) -> bool:
    if isinstance(event.get("message"), dict):
        return True
    return str(event.get("eventType") or event.get("type") or "").strip().lower() == "message"


def message_text(event: dict) -> str:
    msg = event.get("message") if isinstance(event.get("message"), dict) else {}
    text = str(msg.get("text") or "").strip()
    if text:
        return text
    if isinstance(msg.get("attachments"), list) and msg["attachments"]:
        return "[вложение/фото]"
    return "[без текста]"


def fetch_events(cursor: int):
    payload = wb_get(EVENTS_URL, {"next": cursor})
    root = get_root(payload)
    events = root.get("events") or []
    events = [x for x in events if isinstance(x, dict)]
    try:
        nxt = int(root.get("next") or 0)
    except (TypeError, ValueError):
        nxt = 0
    max_ts = cursor
    for e in events:
        try:
            max_ts = max(max_ts, int(e.get("addTimestamp") or 0))
        except (TypeError, ValueError):
            pass
    if nxt <= cursor:
        nxt = max_ts + 1 if max_ts >= cursor else cursor
    return events, nxt


def fetch_returns():
    payload = wb_get(RETURNS_URL, {"is_archive": "false", "limit": 100, "offset": 0})
    rows = payload.get("claims") if isinstance(payload, dict) else []
    return [x for x in (rows or []) if isinstance(x, dict)]


def send_email(subject: str, body: str):
    if not GMAIL_APP_PASSWORD:
        raise RuntimeError("GitHub secret GMAIL_APP_PASSWORD is missing")
    msg = EmailMessage()
    msg["From"] = GMAIL_USER
    msg["To"] = MAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=45) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


def main():
    state = read_json(STATE_FILE, {})
    try:
        cursor = int(state.get("next") or 0)
    except (TypeError, ValueError):
        cursor = 0
    if cursor <= 0:
        cursor = int((now_utc() - timedelta(hours=LOOKBACK_HOURS)).timestamp() * 1000)

    seen_events = set(state.get("seen_events") or [])
    seen_chats = set(state.get("seen_chats") or [])
    seen_returns = set(state.get("seen_returns") or [])

    events, next_cursor = fetch_events(cursor)

    fresh = []
    event_hashes = []
    chat_hashes = []
    for e in events:
        eh = event_id(e)
        event_hashes.append(eh)
        ch = chat_id(e)
        if ch:
            chat_hashes.append(ch)
        if is_message(e) and eh not in seen_events:
            fresh.append((e, bool(ch and ch not in seen_chats)))

    claims = fetch_returns()
    fresh_returns = []
    return_hashes = []
    for row in claims:
        rid = str(row.get("id") or "").strip()
        if not rid:
            continue
        rh = h(rid)
        return_hashes.append(rh)
        if rh not in seen_returns:
            fresh_returns.append(row)

    fresh.sort(key=lambda p: int(p[0].get("addTimestamp") or 0))
    fresh_returns.sort(key=lambda x: str(x.get("dt") or ""))

    client_n = sum(1 for e, _ in fresh if str(e.get("sender") or "").lower() == "client")
    seller_n = sum(1 for e, _ in fresh if str(e.get("sender") or "").lower() == "seller")
    new_chat_n = sum(1 for _, new_chat in fresh if new_chat)

    if fresh or fresh_returns:
        lines = [
            "Wildberries — новые события.",
            f"Новых сообщений: {len(fresh)}",
            f"От покупателей: {client_n}",
            f"От продавца: {seller_n}",
            f"Новых чатов: {new_chat_n}",
            f"Новых возвратов: {len(fresh_returns)}",
            "",
        ]
        for i, (e, new_chat) in enumerate(fresh, 1):
            sender = {"client": "Покупатель", "seller": "Продавец"}.get(
                str(e.get("sender") or "").lower(), "Не указан"
            )
            lines += [
                f"--- Чат {i} ---",
                f"Тип: {'НОВЫЙ ЧАТ' if new_chat else 'новое сообщение'}",
                f"Отправитель: {sender}",
                f"Время: {e.get('addTime') or ''}",
                f"Текст: {message_text(e)}",
                "",
            ]
        for i, row in enumerate(fresh_returns, 1):
            lines += [
                f"--- Возврат {i} ---",
                f"Товар: {row.get('imt_name') or ''}",
                f"Артикул WB: {row.get('nm_id') or ''}",
                f"SRID: {row.get('srid') or ''}",
                f"Дата: {row.get('dt') or ''}",
                f"Комментарий: {row.get('user_comment') or ''}",
                f"Статус: {row.get('status') or ''}",
                "",
            ]
            photos = row.get("photos")
            if isinstance(photos, list):
                for photo in photos[:10]:
                    if isinstance(photo, str) and photo.strip():
                        lines.append(f"Фото: {photo.strip()}")
        subject = f"[WB] сообщения {len(fresh)}, чаты {new_chat_n}, возвраты {len(fresh_returns)}"
        send_email(subject, "\n".join(lines))

    new_state = {
        "version": 2,
        "updated_at": iso_now(),
        "next": next_cursor,
        "seen_events": list(dict.fromkeys(list(seen_events) + event_hashes))[-10000:],
        "seen_chats": list(dict.fromkeys(list(seen_chats) + chat_hashes))[-10000:],
        "seen_returns": list(dict.fromkeys(list(seen_returns) + return_hashes))[-10000:],
    }
    write_json(STATE_FILE, new_state)
    write_json(SUMMARY_FILE, {
        "run_at": iso_now(),
        "events_read": len(events),
        "new_messages": len(fresh),
        "new_messages_client": client_n,
        "new_messages_seller": seller_n,
        "new_chats": new_chat_n,
        "active_returns": len(claims),
        "new_returns": len(fresh_returns),
        "email_sent": bool(fresh or fresh_returns),
    })


if __name__ == "__main__":
    main()
