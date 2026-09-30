#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from notify_baserow import send_notification

CATALOG_URL = "/database/49/table/156/609"
SYNC_REPORT = Path("baserow/norden_sync_report.json")
COMM_REPORT = Path("baserow/norden_marketplace_commissions_report.json")
PUBLISH_REPORT = Path("baserow/norden_stock_publish_report.json")


def load(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def n(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manual", action="store_true")
    a = p.parse_args()

    sync = load(SYNC_REPORT)
    comm = load(COMM_REPORT)
    pub = load(PUBLISH_REPORT)
    sent = 0

    api_error = str(sync.get("api_error") or "").strip()
    if api_error:
        sent += send_notification(
            "yellow",
            "norden_source_fallback",
            "Norden: основной API дал ошибку",
            "Синхронизация продолжилась из резервного прайса. " + api_error[:700],
            "norden:source:fallback",
            CATALOG_URL,
            quiet=True,
        )

    zeroed = n(sync.get("missing_source_set_zero")) + n(sync.get("existing_out_of_stock_updates"))
    if zeroed:
        sent += send_notification(
            "red",
            "norden_stock_zero",
            f"Norden: остаток стал 0 у {zeroed} товаров",
            "Товары отсутствуют в текущих остатках поставщика или получили нулевой остаток.",
            "norden:stock:became-zero",
            CATALOG_URL,
            quiet=True,
        )

    new_rows = n(sync.get("new_rows_created"))
    if new_rows:
        sent += send_notification(
            "green",
            "norden_new_products",
            f"Norden: добавлено новых товаров — {new_rows}",
            "Новые карточки добавлены в базу из актуального каталога поставщика.",
            "norden:new-products",
            CATALOG_URL,
            quiet=True,
        )

    no_images = n(sync.get("excluded_without_images"))
    no_chars = n(sync.get("eligible_without_characteristics"))
    if no_images or no_chars:
        sent += send_notification(
            "yellow",
            "norden_content_missing",
            "Norden: не хватает контента у новых товаров",
            f"Без изображений: {no_images}. Без характеристик: {no_chars}.",
            "norden:content-missing",
            CATALOG_URL,
            quiet=True,
        )

    wa = pub.get("webasyst") or {}
    kit = pub.get("kit") or {}
    unmatched = n(wa.get("unmatched"))
    wa_amb = n(wa.get("ambiguous"))
    kit_missing = n(kit.get("missing_mapping"))
    kit_amb = n(kit.get("ambiguous_mapping"))
    if unmatched or wa_amb or kit_missing or kit_amb:
        sent += send_notification(
            "yellow",
            "norden_mapping_problem",
            "Norden: есть несопоставленные товары",
            (
                f"Webasyst не найдено: {unmatched}, неоднозначно: {wa_amb}. "
                f"KIT не найдено: {kit_missing}, неоднозначно: {kit_amb}."
            ),
            "norden:mapping-problem",
            CATALOG_URL,
            quiet=True,
        )

    significant = comm.get("significant_commission_changes") or []
    if significant:
        examples = ", ".join(
            f"{x.get('marketplace')} {x.get('offer')}: {x.get('old')}%→{x.get('new')}%"
            for x in significant[:5]
        )
        sent += send_notification(
            "yellow",
            "marketplace_commission_change",
            f"Комиссия маркетплейса изменилась у {len(significant)} товаров",
            examples,
            "norden:commission-change",
            CATALOG_URL,
            quiet=True,
        )

    low_margin = comm.get("low_margin_items") or []
    if low_margin:
        examples = ", ".join(
            f"{x.get('marketplace')} {x.get('offer')}: {x.get('margin')}%"
            for x in low_margin[:5]
        )
        sent += send_notification(
            "red",
            "marketplace_low_margin",
            f"Маржа ниже 18% у {len(low_margin)} товаров Norden",
            examples,
            "norden:low-margin",
            CATALOG_URL,
            quiet=True,
        )

    if a.manual:
        sent += send_notification(
            "green",
            "norden_manual_sync_ok",
            "Norden: ручная синхронизация завершена",
            (
                f"Новых товаров: {new_rows}; обнулено: {zeroed}; "
                f"обновлений комиссий: {n(comm.get('rows_updated'))}."
            ),
            "norden:manual-sync-ok",
            CATALOG_URL,
            quiet=True,
        )

    print(json.dumps({"notifications_sent": int(sent)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
