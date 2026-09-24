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

    dup_variants = int(data.get("duplicate_variant_ids_total") or 0)
    by_field_counts = data.get("duplicate_groups_by_field") or {}
    source_groups = int(data.get("duplicate_groups_by_source_article") or 0)
    total_groups = source_groups + sum(int(v or 0) for v in by_field_counts.values())

    # Daily runs stay quiet if everything is clean. Initial/resume runs always send a result.
    if daily and dup_variants == 0 and total_groups == 0:
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
        f"Групп дублей по сопоставленному товару Norden: {source_groups}",
        f"Карточек, попавших хотя бы в одну группу дублей: {dup_variants}",
        f"Конфликтов идентификаторов: {data.get('identity_conflicts', '—')}",
        f"Активных Norden без уверенного сопоставления с источником: {data.get('unresolved_active_norden', '—')}",
        f"Сопоставление по названию: {data.get('name_matching_policy', 'NO')}",
        "",
        "Группы дублей по полям:",
    ]
    if by_field_counts:
        for field, count in sorted(by_field_counts.items()):
            lines.append(f"- {field}: {count}")
    else:
        lines.append("- нет")

    lines += [
        "",
        "Автоматическое удаление не выполнялось. Неканонические дубли должны оставаться с остатком 0 и быть скрыты/архивированы, если KIT это позволяет.",
        f"GitHub Actions: {os.getenv('RUN_URL', '—')}",
    ]

    if dup_variants:
        lines += ["", "Первые найденные группы:"]
        shown = 0

        for group in data.get("duplicates_by_source_article") or []:
            if shown >= 15:
                break
            items = group.get("items") or []
            details = "; ".join(
                f"{x.get('sku') or 'без SKU'} / KIT {x.get('kit_id') or '—'} / {x.get('status') or '—'}"
                for x in items[:6]
            )
            lines.append(f"Товар Norden: {group.get('key')} — {details}")
            shown += 1

        for field, groups in (data.get("duplicates_by_field") or {}).items():
            for group in groups or []:
                if shown >= 15:
                    break
                items = group.get("items") or []
                details = "; ".join(
                    f"{x.get('sku') or 'без SKU'} / KIT {x.get('kit_id') or '—'} / {x.get('status') or '—'}"
                    for x in items[:6]
                )
                lines.append(f"{field}: {group.get('key')} — {details}")
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
