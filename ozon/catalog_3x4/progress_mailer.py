#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

import requests

TOKEN = (os.getenv("GITHUB_TOKEN") or "").strip()
REPO = (os.getenv("GITHUB_REPOSITORY") or "").strip()
RUN_ID = (os.getenv("GITHUB_RUN_ID") or "").strip()
BASE_SHA = (os.getenv("GITHUB_SHA") or "").strip()

SMTP_USER = (os.getenv("GMAIL_SMTP_USER") or "").strip()
SMTP_PASSWORD = "".join((os.getenv("GMAIL_APP_PASSWORD") or "").split())
MAIL_TO = (os.getenv("MAIL_TO") or "shop@office-mag.com").strip()

if not TOKEN or not REPO or not RUN_ID:
    raise SystemExit("Missing GITHUB_TOKEN/GITHUB_REPOSITORY/GITHUB_RUN_ID")

API = "https://api.github.com"
BRANCH = f"ozon-progress-{RUN_ID}"
STATE_PATH = ".ozon-progress/state.json"

gh = requests.Session()
gh.headers.update(
    {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Megapolis-Ozon-3x4-Progress/1.0",
    }
)


def gh_request(method: str, path: str, **kwargs):
    response = gh.request(method, API + path, timeout=60, **kwargs)
    return response


def ensure_branch() -> None:
    encoded = requests.utils.quote(BRANCH, safe="")
    r = gh_request("GET", f"/repos/{REPO}/git/ref/heads/{encoded}")
    if r.status_code == 200:
        return
    if r.status_code != 404:
        raise RuntimeError(f"GitHub branch lookup HTTP {r.status_code}: {r.text[:1000]}")
    if not BASE_SHA:
        raise RuntimeError("GITHUB_SHA is missing; cannot create progress branch")
    r = gh_request(
        "POST",
        f"/repos/{REPO}/git/refs",
        json={"ref": f"refs/heads/{BRANCH}", "sha": BASE_SHA},
    )
    if r.status_code not in (201, 422):
        raise RuntimeError(f"GitHub branch create HTTP {r.status_code}: {r.text[:1000]}")


def default_state() -> dict:
    return {
        "version": 1,
        "run_id": RUN_ID,
        "processed_total": 0,
        "success_total": 0,
        "error_total": 0,
        "images_converted_total": 0,
        "first_offer": None,
        "last_offer": None,
        "failed_skus": [],
        "last_batch_index": None,
        "last_reported_milestone": 0,
    }


def load_state() -> tuple[dict, str | None]:
    ensure_branch()
    r = gh_request(
        "GET",
        f"/repos/{REPO}/contents/{STATE_PATH}",
        params={"ref": BRANCH},
    )
    if r.status_code == 404:
        return default_state(), None
    if r.status_code != 200:
        raise RuntimeError(f"GitHub state read HTTP {r.status_code}: {r.text[:1000]}")
    obj = r.json()
    raw = base64.b64decode(obj.get("content") or "").decode("utf-8")
    state = json.loads(raw)
    return state, obj.get("sha")


def save_state(state: dict, content_sha: str | None) -> str:
    raw = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    body = {
        "message": f"progress: Ozon 3x4 run {RUN_ID} [{state.get('processed_total', 0)}] [skip ci]",
        "content": base64.b64encode(raw).decode("ascii"),
        "branch": BRANCH,
    }
    if content_sha:
        body["sha"] = content_sha
    r = gh_request("PUT", f"/repos/{REPO}/contents/{STATE_PATH}", json=body)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub state write HTTP {r.status_code}: {r.text[:1200]}")
    return (r.json().get("content") or {}).get("sha") or ""


def send_email(state: dict, milestone: int) -> None:
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("Missing GMAIL_SMTP_USER/GMAIL_APP_PASSWORD")

    failed = list(state.get("failed_skus") or [])
    failed_preview = failed[:200]
    lines = [
        "Автоматический отчёт обработки изображений Ozon 3:4.",
        "",
        f"Порог: {milestone} товаров",
        f"Обработано всего: {state.get('processed_total', 0)}",
        f"Успешно: {state.get('success_total', 0)}",
        f"С ошибками: {state.get('error_total', 0)}",
        f"Сконвертировано изображений: {state.get('images_converted_total', 0)}",
        f"Диапазон карточек: {state.get('first_offer') or '-'} — {state.get('last_offer') or '-'}",
        f"Последний batch: {state.get('last_batch_index')}",
        f"Ошибочных артикулов: {len(failed)}",
    ]
    if failed_preview:
        lines += ["", "Артикулы с ошибками:", ", ".join(failed_preview)]
        if len(failed) > len(failed_preview):
            lines.append(
                f"В письме показаны первые {len(failed_preview)}. Полный список — в JSON-вложении."
            )
    else:
        lines += ["", "Артикулы с ошибками: нет."]

    lines += [
        "",
        f"GitHub run: https://github.com/{REPO}/actions/runs/{RUN_ID}",
    ]

    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = MAIL_TO
    msg["Subject"] = f"[Ozon 3:4] Отчёт после {milestone} товаров"
    msg.set_content("\n".join(lines) + "\n")

    attachment = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
    msg.add_attachment(
        attachment,
        maintype="application",
        subtype="json",
        filename=f"ozon_3x4_cumulative_{milestone}.json",
    )

    context = ssl.create_default_context()
    errors = []
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
        return
    except Exception as exc:
        errors.append(f"587/STARTTLS: {type(exc).__name__}: {exc}")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=60) as smtp:
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
        return
    except Exception as exc:
        errors.append(f"465/SSL: {type(exc).__name__}: {exc}")

    raise RuntimeError("Gmail send failed: " + " | ".join(errors))


def update(summary_path: str) -> None:
    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    state, sha = load_state()

    requested = [str(x) for x in (summary.get("requested") or []) if str(x)]
    metrics = summary.get("summary") or {}
    offers = summary.get("offers") or []

    state["processed_total"] = int(state.get("processed_total") or 0) + len(requested)
    state["success_total"] = int(state.get("success_total") or 0) + int(metrics.get("success") or 0)
    state["error_total"] = int(state.get("error_total") or 0) + int(metrics.get("error") or 0)
    state["images_converted_total"] = int(state.get("images_converted_total") or 0) + sum(
        int((o or {}).get("converted_count") or 0) for o in offers
    )

    if requested:
        state["first_offer"] = state.get("first_offer") or requested[0]
        state["last_offer"] = requested[-1]

    batch_index = summary.get("batch_index")
    if batch_index is not None:
        state["last_batch_index"] = int(batch_index)

    failed = list(state.get("failed_skus") or [])
    failed_seen = set(failed)
    for row in offers:
        row = row or {}
        result = str(row.get("result") or "").strip().upper()
        is_error = bool(row.get("error")) or int(row.get("ozon_error_count") or 0) > 0 or result in {
            "ERROR",
            "FAILED",
            "FAILURE",
        }
        offer_id = str(row.get("offer_id") or "").strip()
        if is_error and offer_id and offer_id not in failed_seen:
            failed.append(offer_id)
            failed_seen.add(offer_id)
    state["failed_skus"] = failed

    # Persist the cumulative counters before attempting email, so a transient
    # mail problem never loses processing progress.
    sha = save_state(state, sha)

    processed = int(state.get("processed_total") or 0)
    last_reported = int(state.get("last_reported_milestone") or 0)
    milestone = (processed // 1000) * 1000

    if milestone >= 1000 and milestone > last_reported:
        send_email(state, milestone)
        state["last_reported_milestone"] = milestone
        save_state(state, sha)
        print(
            json.dumps(
                {
                    "email_sent": True,
                    "milestone": milestone,
                    "processed_total": processed,
                    "recipient": MAIL_TO,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(
            json.dumps(
                {
                    "email_sent": False,
                    "processed_total": processed,
                    "next_milestone": ((processed // 1000) + 1) * 1000,
                },
                ensure_ascii=False,
            )
        )


def cleanup() -> None:
    encoded = requests.utils.quote(BRANCH, safe="")
    r = gh_request("DELETE", f"/repos/{REPO}/git/refs/heads/{encoded}")
    if r.status_code in (204, 404):
        print(json.dumps({"cleanup": "ok", "branch": BRANCH}))
        return
    raise RuntimeError(f"GitHub progress branch delete HTTP {r.status_code}: {r.text[:1000]}")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: progress_mailer.py update <summary.json> | cleanup")
    cmd = sys.argv[1]
    if cmd == "update":
        if len(sys.argv) != 3:
            raise SystemExit("Usage: progress_mailer.py update <summary.json>")
        update(sys.argv[2])
        return 0
    if cmd == "cleanup":
        cleanup()
        return 0
    raise SystemExit(f"Unknown command: {cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
