#!/usr/bin/env python3
"""Marketplace API checks every 6 hours; email only on confirmed outage."""
from daily_api_health_check import (
    RESULTS, ozon, wildberries, yandex_market, verify
)


def main():
    verify("Ozon", ("OZON_CLIENT_ID", "OZON_API_KEY"), ozon)
    verify("Wildberries", ("WB_API_TOKEN",), wildberries)
    verify("Яндекс Маркет", ("YANDEX_MARKET_API_KEY",), yandex_market)
    for r in RESULTS:
        print(r["service"], r["status"], r["reason"])
    failures = [r for r in RESULTS if r["status"] == "FAIL"]
    # Emails are sent once by the central daily notifier only.
    if failures:
        print("::error::Marketplace API failures:", len(failures))
    else:
        print("Marketplaces healthy; no email sent")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
