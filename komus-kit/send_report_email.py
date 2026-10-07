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


def yesno(value) -> str:
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
    status = str(report.get("status") or "unknown")
    complete = bool(report.get("catalog_complete"))
    created = int(report.get("new_products_created") or 0)
    existing = int(report.get("existing_variants_seen") or 0)
    price_changes = int(report.get("price_changes") or 0)
    stock_changes = int(report.get("stock_changes") or 0)
    zeroed = int(report.get("absent_to_zero") or 0)
    errors = int(report.get("error_count") or 0)
    warnings = int(report.get("warning_count") or 0)
    eligible = int(report.get("eligible_in_stock") or 0)
    source_seen = int(report.get("source_rows_seen") or 0)
    offset = int(report.get("next_offset") or 0)
    price_rows = int(report.get("price_rows") or 0)
    special_used = int(report.get("special_price_used") or 0)
    fallback = int(report.get("base_price_fallback") or 0)
    duplicates = int(report.get("duplicate_kit_skus") or 0)

    if kind == "intermediate":
        subject = f"[Komus KIT] Промежуточный отчет - создано {created}"
        heading = "Очередная партия Komus -> KIT завершена."
        tail = "Следующая партия поставлена в очередь автоматически."
    elif kind == "scheduled":
        subject = f"[Komus KIT] Ежедневное обновление - {status}"
        heading = "Ежедневная синхронизация Komus -> KIT завершена."
        tail = ""
    else:
        if status == "ok" and errors == 0 and complete:
            subject = "[Komus KIT] Итоговый отчет - загрузка завершена"
            heading = "Первичная загрузка Komus -> KIT полностью завершена."
        elif errors:
            subject = f"[Komus KIT] Итоговый отчет - ошибок {errors}"
            heading = "Цепочка Komus -> KIT завершилась с ошибками и требует проверки."
        else:
            subject = f"[Komus KIT] Итоговый отчет - {status}"
            heading = "Цепочка Komus -> KIT завершила текущий проход."
        tail = "Новая партия автоматически не запланирована."

    lines = [
        heading,
        "",
        f"Статус: {status}",
        f"Каталог полностью пройден: {yesno(complete)}",
        f"Строк источника обработано: {source_seen}",
        f"Товаров в наличии: {eligible}",
        f"Существующих карточек KIT найдено: {existing}",
        f"Новых карточек создано: {created}",
        f"Цен изменено: {price_changes}",
        f"Остатков изменено: {stock_changes}",
        f"Остатков обнулено: {zeroed}",
        f"Строк прайса спеццен: {price_rows}",
        f"Использовано Спец.ЦЕНА: {special_used}",
        f"Использована базовая цена как резерв: {fallback}",
        f"Дублирующихся kom-SKU в KIT: {duplicates}",
        f"Следующая позиция источника: {offset}",
        f"Ошибок: {errors}",
        f"Предупреждений: {warnings}",
    ]

    if tail:
        lines += ["", tail]
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
    print(f"KOMUS_REPORT_EMAIL_SENT kind={args.kind} to={TO_EMAIL}")


if __name__ == "__main__":
    main()
