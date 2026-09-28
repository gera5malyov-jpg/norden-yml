#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

REPORT = Path(os.environ.get("REPORT_FILE", "catalog/norden_chairs_stools_6h_report.json"))
RECIPIENT = os.environ.get("RECIPIENT", "shop@office-mag.com").strip()
SMTP_USER = os.environ["SMTP_USER"].strip()
SMTP_PASSWORD = "".join(os.environ["SMTP_PASSWORD"].split())
RUN_URL = os.environ.get("RUN_URL", "").strip()
SYNC_OUTCOME = os.environ.get("SYNC_OUTCOME", "").strip().lower()


def load_report():
    if not REPORT.exists():
        return {}
    try:
        return json.loads(REPORT.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "ОШИБКА", "fatal_error": f"Не удалось прочитать отчёт: {exc}"}


def count_errors(section):
    value = section.get("errors") if isinstance(section, dict) else None
    return len(value) if isinstance(value, list) else 0


data = load_report()
status = str(data.get("status") or ("УСПЕШНО" if SYNC_OUTCOME == "success" else "ЗАВЕРШЕНО С ОШИБКАМИ"))
if SYNC_OUTCOME and SYNC_OUTCOME != "success" and status == "УСПЕШНО":
    status = "ЗАВЕРШЕНО С ОШИБКАМИ"

source = data.get("source") if isinstance(data.get("source"), dict) else {}
fallback = source.get("category_fallback") if isinstance(source.get("category_fallback"), dict) else {}
sheet = data.get("sheet") if isinstance(data.get("sheet"), dict) else {}
wa = data.get("webasyst") if isinstance(data.get("webasyst"), dict) else {}
kit = data.get("kit") if isinstance(data.get("kit"), dict) else {}

lines = [
    f"Статус: {status}",
    "Процесс: Norden — кресла и стулья каждые 6 часов",
    "",
    "Источник Norden:",
    f"каталог: {source.get('catalog', '—')}",
    f"кресел/стульев: {source.get('target', '—')}",
    f"источник данных: {source.get('source', '—')}",
    f"источник категорий: {source.get('category_source', source.get('source', '—'))}",
    f"XML fallback категорий: {'ДА' if fallback.get('used') else 'НЕТ'}",
]
if fallback.get("used"):
    lines += [
        f"целевых до fallback: {fallback.get('target_before', '—')}",
        f"целевых после fallback: {fallback.get('target_after', '—')}",
        f"категорий восстановлено из XML: {fallback.get('enriched_from_xml', '—')}",
        f"общих категорий осталось: {fallback.get('generic_after', '—')}",
    ]

lines += [
    "",
    "Google Sheets «Норден»:",
    f"сопоставлено: {sheet.get('matched', '—')}",
    f"добавлено новых строк: {sheet.get('added', '—')}",
    f"обнулено отсутствующих: {sheet.get('zeroed_missing', '—')}",
    f"неоднозначных YML ID: {sheet.get('ambiguous_yml', '—')}",
    f"потенциальных дублей: {sheet.get('possible_duplicates', '—')}",
    "",
    "Webasyst:",
    f"обновлено: {wa.get('updated', '—')}",
    f"создано: {wa.get('created', '—')}",
    f"обнулено отсутствующих: {wa.get('zeroed_missing', '—')}",
    f"ошибок: {count_errors(wa)}",
    "",
    "Яндекс KIT:",
    f"обновлено: {kit.get('updated', '—')}",
    f"создано: {kit.get('created', '—')}",
    f"обнулено отсутствующих: {kit.get('zeroed_missing', '—')}",
    f"на согласование категории: {kit.get('category_review', '—')}",
    f"ошибок: {count_errors(kit)}",
]
if data.get("fatal_error"):
    lines += ["", f"Критическая ошибка: {data['fatal_error']}"]
if RUN_URL:
    lines += ["", f"GitHub Actions: {RUN_URL}"]

msg = EmailMessage()
msg["From"] = SMTP_USER
msg["To"] = RECIPIENT
msg["Subject"] = f"[Norden 6ч] кресла/стулья — {status}"
msg.set_content("\n".join(lines))

ctx = ssl.create_default_context()
failures = []
try:
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.ehlo()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)
    print(json.dumps({"status": "УСПЕШНО", "recipient": RECIPIENT, "subject": msg["Subject"]}, ensure_ascii=False))
    raise SystemExit(0)
except SystemExit:
    raise
except Exception as exc:
    failures.append(f"587/STARTTLS: {type(exc).__name__}: {exc}")

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60, context=ctx) as smtp:
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)
    print(json.dumps({"status": "УСПЕШНО", "recipient": RECIPIENT, "subject": msg["Subject"]}, ensure_ascii=False))
except Exception as exc:
    failures.append(f"465/SSL: {type(exc).__name__}: {exc}")
    raise SystemExit("Не удалось отправить отчёт: " + " | ".join(failures))
