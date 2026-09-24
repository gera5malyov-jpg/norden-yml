#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

TOKEN = (os.getenv("WB_API_TOKEN") or "").strip()
STATE_FILE = Path(os.getenv("STATE_FILE") or "state/wb_chat_return_monitor.json")
SUMMARY_FILE = Path(os.getenv("SUMMARY_FILE") or "state/wb_chat_return_summary.json")
LOOKBACK_HOURS = max(1, int(os.getenv("INITIAL_LOOKBACK_HOURS") or "24"))

CHATS_URL = "https://buyer-chat-api.wildberries.ru/api/v1/seller/chats"
CLAIMS_URL = "https://returns-api.wildberries.ru/api/v1/claims"

if not TOKEN:
    raise SystemExit("WB_API_TOKEN is missing")

session = requests.Session()
session.headers.update({"Authorization": TOKEN, "Accept": "application/json"})


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def request_json(url: str, params: dict | None = None, tries: int = 4) -> Any:
    for attempt in range(tries):
        response = session.get(url, params=params, timeout=90)
        if response.status_code == 429 and attempt + 1 < tries:
            raw = response.headers.get("X-RateLimit-Retry") or response.headers.get("Retry-After") or "65"
            try:
                wait = float(raw)
            except (TypeError, ValueError):
                wait = 65.0
            time.sleep(min(180.0, max(10.0, wait + 2.0)))
            continue
        if response.status_code >= 500 and attempt + 1 < tries:
            time.sleep(5 + attempt * 5)
            continue
        if not response.ok:
            raise RuntimeError(f"GET {url} HTTP {response.status_code}: {response.text[:500]}")
        return response.json() if response.content else {}
    raise RuntimeError(f"GET {url} failed")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


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


def last_timestamp(chat: dict) -> int:
    try:
        return int(last_message(chat).get("addTimestamp") or 0)
    except (TypeError, ValueError):
        return 0


def chat_signature(chat: dict) -> str:
    lm = last_message(chat)
    raw = "|".join(
        [
            chat_id(chat),
            str(lm.get("addTimestamp") or ""),
            str(lm.get("text") or ""),
        ]
    )
    return digest(raw)


def fetch_claims() -> list[dict]:
    rows: list[dict] = []
    offset = 0
    limit = 100

    for _ in range(100):
        payload = request_json(
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


def main() -> int:
    state = load_state()
    initialized = bool(state.get("initialized_at"))
    previous_chats = state.get("chats") if isinstance(state.get("chats"), dict) else {}
    previous_claims = set(str(x) for x in (state.get("claim_hashes") or []))

    chats = extract_chats(request_json(CHATS_URL))
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp() * 1000
    )

    current_chats: dict[str, dict] = {}
    changed_count = 0
    new_chat_count = 0
    recent_changed_count = 0

    for chat in chats:
        cid = chat_id(chat)
        if not cid:
            continue

        key = digest(cid)
        sig = chat_signature(chat)
        ts = last_timestamp(chat)
        current_chats[key] = {"sig": sig, "ts": ts}

        old = previous_chats.get(key)
        if isinstance(old, dict):
            if sig != str(old.get("sig") or "") or ts > int(old.get("ts") or 0):
                changed_count += 1
                if ts >= cutoff_ms:
                    recent_changed_count += 1
        else:
            if initialized:
                changed_count += 1
                new_chat_count += 1
                if ts >= cutoff_ms:
                    recent_changed_count += 1
            elif ts >= cutoff_ms:
                changed_count += 1
                new_chat_count += 1
                recent_changed_count += 1

    claims = fetch_claims()
    current_claim_hashes: list[str] = []
    new_claim_count = 0

    for claim in claims:
        claim_id = str(claim.get("id") or "").strip()
        if not claim_id:
            continue
        key = digest(claim_id)
        current_claim_hashes.append(key)
        if key not in previous_claims:
            new_claim_count += 1

    summary = {
        "version": 4,
        "run_at": iso_now(),
        "chats_total": len(chats),
        "new_or_changed_chats": changed_count,
        "new_chats": new_chat_count,
        "recent_changed_chats_lookback": recent_changed_count,
        "active_returns": len(claims),
        "new_returns": new_claim_count,
        "notification_pending": bool(changed_count or new_claim_count),
        "detection_source": "seller/chats.lastMessage + returns claims",
        "privacy_note": "No message text, buyer names, chat IDs, order IDs or return details are written to the public repository.",
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    save_json(
        STATE_FILE,
        {
            "version": 5,
            "initialized_at": state.get("initialized_at") or iso_now(),
            "last_run_at": iso_now(),
            "chats": current_chats,
            "claim_hashes": current_claim_hashes[-10000:],
        },
    )
    save_json(SUMMARY_FILE, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
