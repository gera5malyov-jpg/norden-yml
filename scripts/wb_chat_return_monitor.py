#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests

WB_TOKEN = (os.getenv("WB_API_TOKEN") or "").strip()
STATE_FILE = Path((os.getenv("STATE_FILE") or "state/wb_chat_return_monitor.json").strip())
SUMMARY_FILE = Path((os.getenv("SUMMARY_FILE") or "state/wb_chat_return_summary.json").strip())
INITIAL_LOOKBACK_HOURS = max(1, int((os.getenv("INITIAL_LOOKBACK_HOURS") or "24").strip()))
CHAT_PAGE_DELAY_SECONDS = max(10.2, float((os.getenv("CHAT_PAGE_DELAY_SECONDS") or "10.2").strip()))
MAX_CHAT_PAGES = max(1, int((os.getenv("MAX_CHAT_PAGES") or "30").strip()))

GMAIL_TO = (os.getenv("GMAIL_TO") or "shop@office-mag.com").strip()
GMAIL_FROM = (os.getenv("GMAIL_FROM") or "gera5malyov@gmail.com").strip()
GMAIL_OAUTH_JSON = (os.getenv("GMAIL_OAUTH_JSON") or "").strip()
GMAIL_CLIENT_ID = (os.getenv("GMAIL_CLIENT_ID") or "").strip()
GMAIL_CLIENT_SECRET = (os.getenv("GMAIL_CLIENT_SECRET") or "").strip()
GMAIL_REFRESH_TOKEN = (os.getenv("GMAIL_REFRESH_TOKEN") or "").strip()

CHAT_EVENTS_URL = "https://buyer-chat-api.wildberries.ru/api/v1/seller/events"
RETURNS_URL = "https://returns-api.wildberries.ru/api/v1/claims"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

if not WB_TOKEN:
    raise SystemExit("WB_API_TOKEN is missing")

HEADERS = {"Authorization": WB_TOKEN, "Accept": "application/json"}
session = requests.Session()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def fetch_chat_events(start_cursor: int) -> tuple[list[dict], int, bool, int]:
    cursor = max(0, int(start_cursor))
    all_events: list[dict] = []
    exhausted = False
    pages = 0

    for page_index in range(MAX_CHAT_PAGES):
        payload = request_json(CHAT_EVENTS_URL, params={"next": cursor})
        pages += 1
        events, nxt, total = parse_event_page(payload)
        all_events.extend(events)

        max_ts = cursor
        for event in events:
            try:
                max_ts = max(max_ts, int(event.get("addTimestamp") or 0))
            except (TypeError, ValueError):
                pass

        if total == 0 or not events:
            exhausted = True
            if nxt is not None:
                cursor = max(cursor, nxt)
            else:
                cursor = max_ts + 1 if max_ts >= cursor else cursor
            break

        if nxt is None:
            nxt = max_ts + 1 if max_ts >= cursor else cursor

        if nxt <= cursor:
            exhausted = True
            cursor = max_ts + 1 if max_ts >= cursor else cursor
            break

        cursor = nxt
        if page_index + 1 < MAX_CHAT_PAGES:
            time.sleep(CHAT_PAGE_DELAY_SECONDS)

    return all_events, cursor, exhausted, pages


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


def event_key(event: dict) -> str:
    event_id = str(event.get("eventID") or event.get("eventId") or "").strip()
    if event_id:
        return stable_hash(event_id)
    raw = "|".join([
        str(event.get("chatID") or event.get("chatId") or ""),
        str(event.get("addTimestamp") or ""),
        str(event.get("sender") or ""),
        str(event.get("eventType") or event.get("type") or ""),
        str((event.get("message") or {}).get("text") if isinstance(event.get("message"), dict) else ""),
    ])
    return stable_hash(raw)


def chat_key(event: dict) -> str:
    return stable_hash(str(event.get("chatID") or event.get("chatId") or ""))


def event_is_message(event: dict) -> bool:
    kind = str(event.get("eventType") or event.get("type") or "").strip().lower()
    return kind in {"", "message"}


def event_timestamp_ms(event: dict) -> int:
    try:
        return int(event.get("addTimestamp") or 0)
    except (TypeError, ValueError):
        return 0


def message_text(event: dict) -> str:
    msg = event.get("message")
    if isinstance(msg, dict):
        text = str(msg.get("text") or "").strip()
        if text:
            return text
        attachments = msg.get("attachments")
        if isinstance(attachments, list) and attachments:
            return "[сообщение содержит вложение/фото]"
    return "[сообщение без текста]"


def format_message(event: dict, *, is_new_chat: bool) -> str:
    sender_raw = str(event.get("sender") or "").strip().lower()
    sender = {
        "client": "Покупатель",
        "seller": "Продавец",
    }.get(sender_raw, sender_raw or "Не указан")

    when = str(event.get("addTime") or "").strip()
    client_name = str(event.get("clientName") or "").strip()
    chat_id = str(event.get("chatID") or event.get("chatId") or "").strip()

    lines = [
        f"Тип: {'новый чат' if is_new_chat else 'новое сообщение'}",
        f"Отправитель: {sender}",
        f"Время: {when or 'не указано'}",
    ]
    if client_name:
        lines.append(f"Покупатель: {client_name}")
    lines.append(f"Сообщение: {message_text(event)}")
    if chat_id:
        lines.append(f"Chat ID: {chat_id}")

    msg = event.get("message")
    if isinstance(msg, dict):
        attachments = msg.get("attachments")
        if isinstance(attachments, list):
            for attachment in attachments[:10]:
                if not isinstance(attachment, dict):
                    continue
                url = str(attachment.get("url") or "").strip()
                download_id = str(attachment.get("downloadID") or attachment.get("downloadId") or "").strip()
                if url:
                    lines.append(f"Вложение: {url}")
                elif download_id:
                    lines.append(f"Вложение WB: {download_id}")
    return "\n".join(lines)


def format_claim(claim: dict) -> str:
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


def gmail_credentials() -> tuple[str, str, str]:
    if GMAIL_OAUTH_JSON:
        try:
            data = json.loads(GMAIL_OAUTH_JSON)
        except json.JSONDecodeError as exc:
            raise RuntimeError("GMAIL_OAUTH_JSON is not valid JSON") from exc
        client_id = str(data.get("client_id") or "").strip()
        client_secret = str(data.get("client_secret") or "").strip()
        refresh_token = str(data.get("refresh_token") or "").strip()
    else:
        client_id = GMAIL_CLIENT_ID
        client_secret = GMAIL_CLIENT_SECRET
        refresh_token = GMAIL_REFRESH_TOKEN

    if not client_id or not client_secret or not refresh_token:
        raise RuntimeError(
            "Gmail API credentials are missing. Add GitHub secret GMAIL_OAUTH_JSON "
            "with client_id, client_secret and refresh_token."
        )
    return client_id, client_secret, refresh_token


def gmail_access_token() -> str:
    client_id, client_secret, refresh_token = gmail_credentials()
    r = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=45,
    )
    if not r.ok:
        raise RuntimeError(f"Google OAuth token refresh failed HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Google OAuth response has no access_token")
    return token


def send_gmail(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = GMAIL_FROM
    msg["To"] = GMAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
    token = gmail_access_token()
    r = requests.post(
        GMAIL_SEND_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"raw": raw},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"Gmail API send failed HTTP {r.status_code}: {r.text[:700]}")


def build_email(messages: list[tuple[dict, bool]], claims: list[dict]) -> tuple[str, str]:
    client_count = sum(
        1 for event, _ in messages if str(event.get("sender") or "").strip().lower() == "client"
    )
    seller_count = sum(
        1 for event, _ in messages if str(event.get("sender") or "").strip().lower() == "seller"
    )
    new_chat_count = sum(1 for _, is_new_chat in messages if is_new_chat)

    subject = (
        f"[WB] сообщения: {len(messages)}, новые чаты: {new_chat_count}, возвраты: {len(claims)}"
    )

    blocks = [
        "Автоматическая проверка Wildberries через API.",
        "",
        f"Новых сообщений: {len(messages)}",
        f"Из них от покупателей: {client_count}",
        f"Из них от продавца: {seller_count}",
        f"Новых чатов: {new_chat_count}",
        f"Новых заявок на возврат: {len(claims)}",
        "",
    ]

    if messages:
        blocks.append("=== ЧАТЫ ===")
        for idx, (event, is_new_chat) in enumerate(messages, 1):
            blocks.append(f"\n--- {idx} ---\n{format_message(event, is_new_chat=is_new_chat)}")

    if claims:
        blocks.append("\n=== ВОЗВРАТЫ ===")
        for idx, claim in enumerate(claims, 1):
            blocks.append(f"\n--- {idx} ---\n{format_claim(claim)}")

    return subject, "\n".join(blocks).strip() + "\n"


def main() -> int:
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}

    now = utc_now()
    initialized = bool(state.get("initialized_at"))

    try:
        stored_cursor = int(state.get("chat_next") or 0)
    except (TypeError, ValueError):
        stored_cursor = 0

    if stored_cursor > 0:
        start_cursor = stored_cursor
    else:
        start_cursor = int((now - timedelta(hours=INITIAL_LOOKBACK_HOURS)).timestamp() * 1000)

    prior_event_ids = [str(x) for x in (state.get("seen_event_hashes") or []) if str(x).strip()]
    prior_claim_ids = [str(x) for x in (state.get("seen_claim_hashes") or []) if str(x).strip()]
    prior_chat_ids = [str(x) for x in (state.get("seen_chat_hashes") or []) if str(x).strip()]
    seen_event_ids = set(prior_event_ids)
    seen_claim_ids = set(prior_claim_ids)
    seen_chat_ids = set(prior_chat_ids)

    events, next_cursor, exhausted, pages = fetch_chat_events(start_cursor)

    fresh_messages: list[tuple[dict, bool]] = []
    current_event_hashes: list[str] = []
    current_chat_hashes: list[str] = []

    for event in events:
        ehash = event_key(event)
        current_event_hashes.append(ehash)

        ckey = chat_key(event)
        if ckey:
            current_chat_hashes.append(ckey)

        if not event_is_message(event):
            continue
        if ehash in seen_event_ids:
            continue

        is_new_chat = bool(ckey and ckey not in seen_chat_ids)
        fresh_messages.append((event, is_new_chat))

    claims = fetch_active_claims()
    fresh_claims: list[dict] = []
    current_claim_hashes: list[str] = []

    for claim in claims:
        cid = str(claim.get("id") or "").strip()
        if not cid:
            continue
        chash = stable_hash(cid)
        current_claim_hashes.append(chash)
        if chash not in seen_claim_ids:
            fresh_claims.append(claim)

    fresh_messages.sort(key=lambda pair: event_timestamp_ms(pair[0]))
    fresh_claims.sort(key=lambda claim: str(claim.get("dt") or ""))

    client_count = sum(
        1 for event, _ in fresh_messages if str(event.get("sender") or "").strip().lower() == "client"
    )
    seller_count = sum(
        1 for event, _ in fresh_messages if str(event.get("sender") or "").strip().lower() == "seller"
    )
    new_chat_count = sum(1 for _, is_new_chat in fresh_messages if is_new_chat)

    print(json.dumps({
        "initialized": initialized,
        "start_cursor": start_cursor,
        "next_cursor": next_cursor,
        "pages": pages,
        "cursor_exhausted": exhausted,
        "events_read": len(events),
        "new_messages": len(fresh_messages),
        "new_messages_client": client_count,
        "new_messages_seller": seller_count,
        "new_chats": new_chat_count,
        "active_returns": len(claims),
        "new_returns": len(fresh_claims),
        "gmail_to": GMAIL_TO,
    }, ensure_ascii=False, indent=2))

    email_sent = False
    if fresh_messages or fresh_claims:
        subject, body = build_email(fresh_messages, fresh_claims)
        try:
            send_gmail(subject, body)
            email_sent = True
            print("GMAIL_API_SENT=1")
        except Exception:
            # Do not advance state when an alert could not be delivered.
            print("GMAIL_API_SENT=0", file=sys.stderr)
            raise
    else:
        print("GMAIL_API_SENT=0")

    merged_event_hashes = (prior_event_ids + current_event_hashes)[-10000:]
    merged_claim_hashes = (prior_claim_ids + current_claim_hashes)[-10000:]
    merged_chat_hashes = (prior_chat_ids + current_chat_hashes)[-10000:]

    new_state = {
        "version": 4,
        "initialized_at": state.get("initialized_at") or iso_now(),
        "last_run_at": iso_now(),
        "chat_next": next_cursor,
        "seen_event_hashes": list(dict.fromkeys(merged_event_hashes)),
        "seen_claim_hashes": list(dict.fromkeys(merged_claim_hashes)),
        "seen_chat_hashes": list(dict.fromkeys(merged_chat_hashes)),
    }
    save_json(STATE_FILE, new_state)

    summary = {
        "version": 2,
        "run_at": iso_now(),
        "new_messages": len(fresh_messages),
        "new_messages_client": client_count,
        "new_messages_seller": seller_count,
        "new_chats": new_chat_count,
        "active_returns": len(claims),
        "new_returns": len(fresh_claims),
        "email_sent": email_sent,
        "privacy_note": "Message text, buyer names, chat IDs, order IDs and return details are never written to the public repository.",
    }
    save_json(SUMMARY_FILE, summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MONITOR_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        raise
