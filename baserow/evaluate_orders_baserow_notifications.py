#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from notify_baserow import send_notification

REPORT = Path("orders-baserow/runtime/last_report.json")
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
    warnings = [str(x) for x in (data.get("warnings") or []) if str(x).strip()]

    if warnings:
        send_notification(
            "red",
            "orders_source_warning",
            "Не все площадки отдали заказы",
            "; ".join(warnings[:8]),
            "orders:baserow:source-warning",
            ORDERS_URL,
            quiet=True,
        )

    created = n(data.get("created"))
    if created:
        send_notification(
            "green",
            "orders_added",
            f"Новых заказов добавлено в Baserow — {created}",
            f"Источники активных заказов: {data.get('counts_by_source') or {}}",
            "orders:baserow:new-orders",
            ORDERS_URL,
            quiet=True,
        )

    print(json.dumps({"warnings": len(warnings), "created": created}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
