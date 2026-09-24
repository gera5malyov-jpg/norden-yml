#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests

WB_TOKEN = (os.getenv("WB_API_TOKEN") or "").strip()
GMAIL_OAUTH_JSON = (os.getenv("GMAIL_OAUTH_JSON") or "").strip()
GMAIL_FROM = (os.getenv("GMAIL_FROM") or "gera5malyov@gmail.com").strip()
MAIL_TO = (os.getenv("MAIL_TO") or "shop@office-mag.com").strip()
STATE_FILE = Path(os.getenv("STATE_FILE") or "state/wb_chat_return_gmail_api.json")
LOOKBACK_HOURS = max(3, int(os.getenv("LOOKBACK_HOURS") or "6"))

CHATS_URL = "https://buyer-chat-api.wildberries.ru/api/v1/seller/chats"
CLAIMS_URL = "https://returns-api.wildberries.ru/api/v1/claims"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

if not WB_TOKEN:
    raise SystemExit("WB_API_TOKEN is missing")

wb = requests.Session()
wb.headers.update({"Authorization": WB_TOKEN, "Accept": "application/json"})


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def get_json(url: str, params: dict | None = None) -> Any:
    response = wb.get(url, params=params, timeout=90)
    if response.status_code == 429:
        retry = response.headers.get("X-RateLimit-Retry") or response.headers.get("Retry-After") or ""
        raise RuntimeError(f"WB API rate limit; retry_after={retry}")
    if not response.ok:
        raise RuntimeError(f"GET {url} HTTP {response.status_code}: {response.text[:500]}")
    return response.json() if response.content else {}


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(STATE_FILE)


def extract_chats(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]
    for key in ("chats", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            rows = value.get("chats") or value.get("items") or value.get("result")
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
    return []


def chat_id(chat: dict) -> str:
    return str(chat.get("chatID") or chat.get("chatId") or chat.get("id") or "").strip()


def last_message(chat: dict) -> dict:
    value = chat.get("lastMessage")
    return value if isinstance(value, dict) else {}


def last_ts(chat: dict) -> int:
    try:
        return int(last_message(chat).get("addTimestamp") or 0)
    except (TypeError, ValueError):
        return 0


def signature(chat: dict) -> str:
    message = last_message(chat)
    return digest(
        "|".join(
            [
                chat_id(chat),
                str(message.get("addTimestamp") or ""),
                str(message.get("text") or ""),
            ]
        )
    )


def fetch_claims() -> list[dict]:
    rows: list[dict] = []
    offset = 0
    limit = 100
    for _ in range(100):
        payload = get_json(
            CLAIMS_URL,
            {"is_archive": "false", "limit": limit, "offset": offset},
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


def gmail_access_token() -> str:
    if not GMAIL_OAUTH_JSON:
        raise RuntimeError("GMAIL_OAUTH_JSON is missing")
    cfg = json.loads(GMAIL_OAUTH_JSON)
    client_id = str(cfg.get("client_id") or "").strip()
    client_secret = str(cfg.get("client_secret") or "").strip()
    refresh_token = str(cfg.get("refresh_token") or "").strip()
    if not client_id or not client_secret or not refresh_token:
        raise RuntimeError("GMAIL_OAUTH_JSON must contain client_id, client_secret, refresh_token")

    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=45,
    )
    if not response.ok:
        raise RuntimeError(f"Google OAuth refresh HTTP {response.status_code}: {response.text[:400]}")
    token = str(response.json().get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Google OAuth response has no access_token")
    return token


def send_gmail(subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = GMAIL_FROM
    message["To"] = MAIL_TO
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")

    response = requests.post(
        GMAIL_SEND_URL,
        headers={
            "Authorization": f"Bearer {gmail_access_token()}",
            "Content-Type": "application/json",
        },
        json={"raw": raw},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Gmail API send HTTP {response.status_code}: {response.text[:500]}")


def format_chat(chat: dict, is_new: bool) -> str:
    message = last_message(chat)
    text = str(message.get("text") or "").strip() or "[без текста]"
    lines = [
        f"Тип: {'новый чат' if is_new else 'новое сообщение'}",
        f"Время: {message.get('addTimestamp') or ''}",
        f"Сообщение: {text}",
    ]
    name = str(chat.get("clientName") or "").strip()
    if name:
        lines.insert(2, f"Покупатель: {name}")
    return "\n".join(lines)


def format_claim(claim: dict) -> str:
    lines = [
        f"Товар: {str(claim.get('imt_name') or '').strip() or 'не указано'}",
        f"Артикул WB: {claim.get('nm_id', '')}",
        f"SRID: {claim.get('srid', '')}",
        f"Дата: {claim.get('dt', '')}",
        f"Комментарий: {str(claim.get('user_comment') or '').strip() or 'нет'}",
        f"Статус: {claim.get('status', '')}",
        f"ID заявки: {claim.get('id', '')}",
    ]
    photos = claim.get("photos")
    if isinstance(photos, list):
        for photo in photos[:10]:
            if isinstance(photo, str) and photo.strip():
                lines.append(f"Фото: {photo.strip()}")
    return "\n".join(lines)


def main() -> int:
    state = load_state()
    initialized = bool(state.get("initialized_at"))
    previous_chats = state.get("chats") if isinstance(state.get("chats"), dict) else {}
    previous_claims = set(str(x) for x in (state.get("claim_hashes") or []))
    cutoff = int((now_utc() - timedelta(hours=LOOKBACK_HOURS)).timestamp() * 1000)

    chats = extract_chats(get_json(CHATS_URL))
    current_chats: dict[str, dict] = {}
    changed: list[tuple[dict, bool]] = []

    for chat in chats:
        cid = chat_id(chat)
        if not cid:
            continue
        key = digest(cid)
        sig = signature(chat)
        ts = last_ts(chat)
        current_chats[key] = {"sig": sig, "ts": ts}
        old = previous_chats.get(key)

        if isinstance(old, dict):
            if sig != str(old.get("sig") or "") or ts > int(old.get("ts") or 0):
                changed.append((chat, False))
        elif initialized or ts >= cutoff:
            changed.append((chat, True))

    claims = fetch_claims()
    current_claim_hashes: list[str] = []
    new_claims: list[dict] = []
    for claim in claims:
        claim_id = str(claim.get("id") or "").strip()
        if not claim_id:
            continue
        key = digest(claim_id)
        current_claim_hashes.append(key)
        if key not in previous_claims:
            new_claims.append(claim)

    changed.sort(key=lambda item: last_ts(item[0]))
    new_claims.sort(key=lambda item: str(item.get("dt") or ""))

    if changed or new_claims:
        new_chat_count = sum(1 for _, is_new in changed if is_new)
        subject = f"[WB] новые сообщения/чаты: {len(changed)}, возвраты: {len(new_claims)}"
        parts = [
            "Автоматический отчёт Wildberries.",
            f"Новых/изменившихся чатов: {len(changed)}",
            f"Новых чатов: {new_chat_count}",
            f"Новых возвратов: {len(new_claims)}",
        ]
        if changed:
            parts.append("\n=== ЧАТЫ ===")
            for index, (chat, is_new) in enumerate(changed, 1):
                parts.append(f"\n--- {index} ---\n{format_chat(chat, is_new)}")
        if new_claims:
            parts.append("\n=== ВОЗВРАТЫ ===")
            for index, claim in enumerate(new_claims, 1):
                parts.append(f"\n--- {index} ---\n{format_claim(claim)}")

        send_gmail(subject, "\n".join(parts).strip() + "\n")

    # State is advanced only after successful Gmail delivery.
    save_state(
        {
            "version": 1,
            "initialized_at": state.get("initialized_at") or iso_now(),
            "last_run_at": iso_now(),
            "chats": current_chats,
            "claim_hashes": current_claim_hashes[-10000:],
        }
    )

    print(
        json.dumps(
            {
                "ok": True,
                "chats_total": len(chats),
                "new_or_changed_chats": len(changed),
                "new_returns": len(new_claims),
                "email_sent": bool(changed or new_claims),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
