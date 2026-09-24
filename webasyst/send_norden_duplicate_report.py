#!/usr/bin/env python3
import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

REPORT = Path("norden-kit/norden_duplicate_audit_report.json")


def main():
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    workflow = (os.getenv("SOURCE_WORKFLOW") or "").strip()
    daily = workflow == "Norden KIT -> Webasyst daily sync"

    groups_article = int(data.get("duplicate_groups_by_norden_article") or 0)
    groups_code = int(data.get("duplicate_groups_by_code_for_site") or 0)
    groups_sku = int(data.get("duplicate_groups_by_exact_sku") or 0)
    dup_variants = int(data.get("duplicate_variant_ids_total") or 0)

    # Daily runs stay quiet if everything is clean. Initial/resume runs always send a result.
    if daily and dup_variants == 0 and groups_article == 0 and groups_code == 0 and groups_sku == 0:
        print("Дубли Norden не найдены; отдельное ежедневное письмо не требуется.")
        return 0

    status = "НАЙДЕНЫ ДУБЛИ" if dup_variants else "ДУБЛЕЙ НЕ НАЙДЕНО"
    subject = f"[Norden] Проверка дублей — {status}"
    lines = [
        "Контроль дублей Norden после синхронизации",
        "",
        f"Результат: {status}",
        f"Исходный процесс: {workflow or '—'}",
        f"Активных карточек Norden в KIT: {data.get('active_norden_variants', '—')}",
        f"Групп дублей по артикулу Norden: {groups_article}",
        f"Групп дублей по «Код для сайта»: {groups_code}",
        f"Групп дублей по точному SKU: {groups_sku}",
        f"Карточек, попавших хотя бы в одну группу дублей: {dup_variants}",
        f"Активных Norden без уверенного сопоставления с источником: {data.get('unresolved_active_norden', '—')}",
        "",
        "Автоматическое удаление/архивация не выполнялись.",
        f"GitHub Actions: {os.getenv('RUN_URL', '—')}",
    ]

    if dup_variants:
        lines += ["", "Первые найденные группы:"]
        shown = 0
        for title, key in (
            ("Артикул Norden", "duplicates_by_norden_article"),
            ("Код для сайта", "duplicates_by_code_for_site"),
            ("SKU", "duplicates_by_exact_sku"),
        ):
            for group in data.get(key) or []:
                if shown >= 15:
                    break
                items = group.get("items") or []
                details = "; ".join(
                    f"{x.get('sku') or 'без SKU'} / KIT {x.get('kit_id') or '—'}"
                    for x in items[:6]
                )
                lines.append(f"{title}: {group.get('key')} — {details}")
                shown += 1
            if shown >= 15:
                break

    user = (os.getenv("SMTP_USER") or "").strip()
    password = "".join((os.getenv("SMTP_PASSWORD") or "").split())
    recipient = (os.getenv("RECIPIENT") or "shop@office-mag.com").strip()
    if not user or not password:
        raise SystemExit("Не настроены GMAIL_SMTP_USER/GMAIL_APP_PASSWORD")

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content("\n".join(lines))

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"Письмо отправлено на {recipient}: {subject}")
        return 0
    except Exception:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=60) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"Письмо отправлено на {recipient}: {subject}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
