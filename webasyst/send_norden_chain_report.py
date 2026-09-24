#!/usr/bin/env python3
import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def ru_status(value):
    return {
        "success": "УСПЕШНО",
        "failure": "ЗАВЕРШЕНО С ОШИБКАМИ",
        "cancelled": "ОТМЕНЕНО",
        "skipped": "ПРОПУЩЕНО",
    }.get((value or "").strip().lower(), (value or "НЕИЗВЕСТНО").upper())


def main():
    zero = load("norden-kit/zero_norden_stocks_report.json")
    bootstrap = load("norden-kit/webasyst_bootstrap_report.json")
    kit = load("norden-kit/last_sync_report.json")
    wa = load("webasyst/last_norden_webasyst_sync.json")

    phase = (os.getenv("PHASE") or "daily").strip().lower()
    zero_outcome = os.getenv("ZERO_OUTCOME", "")
    bootstrap_outcome = os.getenv("BOOTSTRAP_OUTCOME", "")
    kit_outcome = os.getenv("KIT_OUTCOME", "")
    wa_outcome = os.getenv("WA_OUTCOME", "")

    outcomes = [x for x in (kit_outcome, wa_outcome) if x]
    if phase == "initial":
        outcomes = [x for x in (zero_outcome, bootstrap_outcome, kit_outcome, wa_outcome) if x]
    ok = bool(outcomes) and all(x == "success" for x in outcomes)

    status = "УСПЕШНО" if ok else "ЗАВЕРШЕНО С ОШИБКАМИ"
    subject = f"[Norden → KIT → Webasyst] {'Первичное' if phase == 'initial' else 'Ежедневное'} обновление — {status}"

    lines = [
        "Контур Norden: Norden → Яндекс KIT → Webasyst",
        "",
        f"Результат: {status}",
        f"Этап: {'первичная настройка и полная сверка' if phase == 'initial' else 'ежедневное обновление'}",
        "",
    ]

    if phase == "initial":
        lines += [
            "1. Обнуление Norden в KIT",
            f"результат: {ru_status(zero_outcome)}",
            f"активных карточек Norden: {zero.get('active_norden_variants', '—')}",
            f"обнулено строк остатков: {zero.get('stock_rows_zeroed', '—')}",
            f"ошибок: {len(zero.get('errors') or [])}",
            "",
            "2. Сверка Webasyst NORDEN-100 → KIT",
            f"результат: {ru_status(bootstrap_outcome)}",
            f"товаров Webasyst: {bootstrap.get('webasyst_products', '—')}",
            f"SKU Webasyst: {bootstrap.get('webasyst_skus', '—')}",
            f"совпало по артикулу: {bootstrap.get('matched_exact_sku', '—')}",
            f"совпало по коду Norden: {bootstrap.get('matched_by_norden_code', '—')}",
            f"отсутствовало перед переносом: {bootstrap.get('missing_before', '—')}",
            f"создано в KIT: {bootstrap.get('created_in_kit', '—')}",
            f"пропущено «только Москва»: {bootstrap.get('moscow_only_skipped', '—')}",
            f"ошибок: {len(bootstrap.get('errors') or [])}",
            "",
        ]

    lines += [
        f"{'3' if phase == 'initial' else '1'}. Обновление Norden → KIT",
        f"результат: {ru_status(kit_outcome)}",
        f"товаров в источнике: {kit.get('source_products', '—')}",
        f"обновлено цен: {kit.get('price_updates', '—')}",
        f"обновлено остатков: {kit.get('stock_updates', '—')}",
        f"создано новых карточек: {kit.get('new_products_created', '—')}",
        f"исключено «только Москва»: {kit.get('excluded_moscow_only_products', '—')}",
        f"ошибок: {len(kit.get('errors') or [])}",
        "",
        f"{'4' if phase == 'initial' else '2'}. KIT → Webasyst (только существующие NORDEN-100)",
        f"результат: {ru_status(wa_outcome)}",
        f"товаров Webasyst: {wa.get('webasyst_products', '—')}",
        f"SKU Webasyst: {wa.get('webasyst_skus', '—')}",
        f"совпало по артикулу: {wa.get('matched_exact_sku', '—')}",
        f"совпало по коду Norden: {wa.get('matched_by_code', '—')}",
        f"обновлено: {wa.get('updated', '—')}",
        f"без карточки в KIT: {wa.get('missing_in_kit', '—')}",
        f"без оптовой цены Norden: {wa.get('missing_source_price', '—')}",
        f"остаток обнулён из-за отсутствия в KIT: {wa.get('stock_zeroed_for_missing_kit', '—')}",
        f"ошибок: {len(wa.get('errors') or [])}",
        "",
        "Правила Webasyst:",
        "закупочная цена = оптовая цена Norden",
        "цена продажи = закупочная цена × 1,25",
        "зачёркнутая цена = закупочная цена × 1,60",
        "остаток = склад МСК в KIT",
        "новые товары из KIT в Webasyst не создаются",
        "",
        f"GitHub Actions: {os.getenv('RUN_URL','—')}",
        f"Время отчёта UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
    ]

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
    failures = []
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"Письмо отправлено на {recipient}: {subject}")
        return 0
    except Exception as exc:
        failures.append(f"587/STARTTLS: {type(exc).__name__}: {exc}")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=60) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"Письмо отправлено на {recipient}: {subject}")
        return 0
    except Exception as exc:
        failures.append(f"465/SSL: {type(exc).__name__}: {exc}")

    raise SystemExit("Не удалось отправить письмо. " + " | ".join(failures))


if __name__ == "__main__":
    raise SystemExit(main())
