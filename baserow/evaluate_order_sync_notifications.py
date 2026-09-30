#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from notify_baserow import send_notification

REPORT = Path("webasyst/runtime/marketplace_order_sync_report.json")
ORDERS_URL = "/database/49/table/494"


def n(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def main():
    if not REPORT.exists():
        return 0
    data = json.loads(REPORT.read_text(encoding="utf-8"))

    errors = n(data.get("errors"))
    unmatched = n(data.get("skipped_unmatched_sku"))
    chat_errors = n(data.get("chat_errors"))
    chat_blocked = n(data.get("chat_blocked"))

    if unmatched:
        samples = []
        for x in data.get("unmatched") or []:
            samples.append(
                f"{x.get('source')} {x.get('external_id')}: SKU {x.get('sku')}"
            )
        send_notification(
            "red",
            "order_not_imported_webasyst",
            f"Заказы не созданы в Webasyst — {unmatched}",
            "; ".join(samples[:8]) or "Не найден товар/SKU в Webasyst.",
            "orders:webasyst:unmatched",
            ORDERS_URL,
            quiet=True,
        )

    if errors:
        source_errors = []
        for source, stat in (data.get("sources") or {}).items():
            count = n((stat or {}).get("errors"))
            if count:
                source_errors.append(f"{source}: {count}")
        send_notification(
            "red",
            "order_sync_error",
            f"Ошибки синхронизации заказов — {errors}",
            "Источники: " + (", ".join(source_errors) or "см. GitHub Actions"),
            "orders:webasyst:sync-error",
            ORDERS_URL,
            quiet=True,
        )

    if chat_errors or chat_blocked:
        send_notification(
            "yellow",
            "order_chat_problem",
            "Есть проблемы с сообщениями покупателям",
            f"Временных ошибок: {chat_errors}. Недоступных чатов: {chat_blocked}.",
            "orders:chat-problem",
            ORDERS_URL,
            quiet=True,
        )

    print(json.dumps({
        "errors": errors,
        "unmatched": unmatched,
        "chat_errors": chat_errors,
        "chat_blocked": chat_blocked,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
