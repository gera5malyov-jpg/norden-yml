from __future__ import annotations

import argparse
import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


TO_EMAIL = "shop@office-mag.com"


def load_report(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {
            "status": "failed",
            "catalog_complete": False,
            "error_count": 1,
            "errors": [{"sku": "REPORT", "message": f"Report file not found: {path}"}],
        }
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "failed",
            "catalog_complete": False,
            "error_count": 1,
            "errors": [{"sku": "REPORT", "message": f"Cannot read report: {exc}"}],
        }


def fmt_bool(value) -> str:
    return "да" if bool(value) else "нет"


def first_messages(rows, limit=10):
    out = []
    for row in rows or []:
        if isinstance(row, dict):
            sku = str(row.get("sku") or "").strip()
            msg = str(row.get("message") or "").strip()
            line = f"{sku}: {msg}" if sku else msg
        else:
            line = str(row).strip()
        if line:
            out.append(line)
        if len(out) >= limit:
            break
    return out


def build_message(report: dict, kind: str, run_url: str) -> EmailMessage:
    created = int(report.get("new_products_created") or 0)
    indexed = int(report.get("riva_variants_indexed") or 0)
    existing_seen = int(report.get("existing_variants_seen") or 0)
    errors = int(report.get("error_count") or 0)
    warnings = int(report.get("warning_count") or 0)
    status = str(report.get("status") or "unknown")
    complete = bool(report.get("catalog_complete"))

    if kind == "intermediate":
        subject = f"[Riva KIT] Промежуточный отчет - создано {created}"
        heading = "Промежуточная партия Riva -> KIT завершена."
        next_line = "Следующая партия поставлена в очередь автоматически."
    elif kind == "scheduled":
        subject = f"[Riva KIT] Плановая синхронизация - {status}"
        heading = "Плановая синхронизация Riva -> KIT завершена."
        next_line = ""
    else:
        if status == "ok" and errors == 0 and complete:
            subject = "[Riva KIT] Итоговый отчет - загрузка завершена"
            heading = "Полная дозагрузка Riva -> KIT завершена."
        elif errors:
            subject = f"[Riva KIT] Итоговый отчет - остановлено, ошибок {errors}"
            heading = "Цепочка Riva -> KIT остановлена и требует проверки."
        else:
            subject = f"[Riva KIT] Итоговый отчет - {status}"
            heading = "Цепочка Riva -> KIT завершила работу."
        next_line = "Новая партия автоматически не запланирована."

    total_after = indexed + created if indexed or created else 0
    lines = [
        heading,
        "",
        f"Статус: {status}",
        f"Каталог полностью пройден: {fmt_bool(complete)}",
        f"Riva-карточек в KIT до этой партии: {indexed}",
        f"Новых товаров создано в этой партии: {created}",
        f"Ориентировочно Riva-карточек в KIT после партии: {total_after}",
        f"Существующих карточек найдено при проходе: {existing_seen}",
        f"Ошибок: {errors}",
        f"Предупреждений: {warnings}",
        f"Пропущено неоднозначных существующих карточек: {int(report.get('ambiguous_existing_skipped') or 0)}",
        f"Дублирующихся SKU-бакетов Riva в KIT: {int(report.get('duplicate_article_buckets') or 0)}",
        f"Повторяющихся строк 'Код для сайта' в текущем проходе: {int(report.get('duplicate_site_code_rows') or 0)}",
    ]
    if next_line:
        lines += ["", next_line]
    if run_url:
        lines += ["", f"GitHub Actions: {run_url}"]

    err_lines = first_messages(report.get("errors"), 10)
    if err_lines:
        lines += ["", "Первые ошибки:"]
        lines.extend(f"- {x}" for x in err_lines)

    warn_lines = first_messages(report.get("warnings"), 10)
    if warn_lines:
        lines += ["", "Первые предупреждения:"]
        lines.extend(f"- {x}" for x in warn_lines)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = TO_EMAIL
    msg.set_content("\n".join(lines))
    return msg


def send(msg: EmailMessage):
    user = os.environ.get("GMAIL_SMTP_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not user:
        raise RuntimeError("GMAIL_SMTP_USER is not configured")
    if not password:
        raise RuntimeError("GMAIL_APP_PASSWORD is not configured")
    msg["From"] = user
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--kind", choices=("intermediate", "final", "scheduled"), required=True)
    ap.add_argument("--run-url", default="")
    args = ap.parse_args()

    report = load_report(args.report)
    msg = build_message(report, args.kind, args.run_url)
    send(msg)
    print(f"RIVA_REPORT_EMAIL_SENT kind={args.kind} to={TO_EMAIL}")


if __name__ == "__main__":
    main()
