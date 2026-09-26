#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import gspread
import requests
from google.oauth2.service_account import Credentials

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "catalog" / "norden_price_push_report.json"
SHEET_ID = os.environ.get("CATALOG_SPREADSHEET_ID", "1DvMYvKRr8fX8mfLFaeJCXCccQofPViPcIP2CgIctc8w").strip()
SHEET_NAME = os.environ.get("CATALOG_SHEET", "Норден").strip()
OZON_BASE = "https://api-seller.ozon.ru"
YANDEX_BASE = "https://api.partner.market.yandex.ru"

MONEY = Decimal("0.01")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def s(v):
    return str(v or "").strip()


def money(v):
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if d <= 0:
        return None
    return d


def money_str(v):
    d = money(v)
    if d is None:
        return None
    return f"{d.quantize(MONEY, rounding=ROUND_HALF_UP):.2f}"


def ceil_rub(v):
    d = money(v)
    if d is None:
        return None
    return int(math.ceil(float(d) - 1e-9))


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i+n]


def listify(payload, keys=()):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            return [x for x in value.values() if isinstance(x, dict)]
    if payload and all(isinstance(v, dict) for v in payload.values()):
        return list(payload.values())
    return []


def norm(v):
    import re, unicodedata
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", s(v)).casefold())


def req_json(session, method, url, *, headers=None, params=None, body=None, attempts=6, timeout=120):
    last = None
    for attempt in range(attempts):
        try:
            r = session.request(method, url, headers=headers, params=params, json=body, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                last = RuntimeError(f"HTTP {r.status_code}: {r.text[:700]}")
                if attempt + 1 < attempts:
                    time.sleep(min(20, 2 ** attempt))
                    continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:1500]}")
            return r.json() if r.content else {}
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(min(20, 2 ** attempt))
                continue
            raise
    raise last or RuntimeError("request failed")


def load_sheet():
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    creds = Credentials.from_service_account_info(
        json.loads(raw),
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
    )
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
    vals = ws.get_all_values()
    if not vals:
        raise RuntimeError("Каталог/Норден пуст")
    headers = vals[0]
    idx = {h: i for i, h in enumerate(headers)}
    required = [
        "Артикул", "Бренд", "YML ID",
        "KIT Цена до скидки", "KIT Цена со скидкой", "KIT Минимальная цена",
        "Цена Webasyst", "Старая цена Webasyst", "Закупочная цена Webasyst",
        "Yandex цена", "Yandex зачёркнутая цена", "Yandex offer_id", "Yandex business_id",
        "Ozon Предельная цена без акций", "Ozon Зачёркнутая цена",
        "Ozon Ограничение для акций и стратегий", "Ozon offer_id", "Ozon product_id",
    ]
    missing = [x for x in required if x not in idx]
    if missing:
        raise RuntimeError("В таблице отсутствуют обязательные столбцы: " + ", ".join(missing))

    rows = []
    for rn, row in enumerate(vals[1:], start=2):
        def get(name):
            i = idx[name]
            return row[i] if i < len(row) else ""
        art = s(get("Артикул"))
        if not art:
            continue
        brand = s(get("Бренд"))
        if brand and norm(brand) != "norden":
            continue
        rows.append({
            "row": rn,
            "article": art,
            "yml_id": s(get("YML ID")),
            "kit_old": money(get("KIT Цена до скидки")),
            "kit_sale": money(get("KIT Цена со скидкой")),
            "kit_min": money(get("KIT Минимальная цена")),
            "wa_sale": money(get("Цена Webasyst")),
            "wa_old": money(get("Старая цена Webasyst")),
            "wa_purchase": money(get("Закупочная цена Webasyst")),
            "ya_sale": money(get("Yandex цена")),
            "ya_old": money(get("Yandex зачёркнутая цена")),
            "ya_offer": s(get("Yandex offer_id")),
            "ya_business": s(get("Yandex business_id")),
            "oz_sale": money(get("Ozon Предельная цена без акций")),
            "oz_old": money(get("Ozon Зачёркнутая цена")),
            "oz_min": money(get("Ozon Ограничение для акций и стратегий")),
            "oz_offer": s(get("Ozon offer_id")),
            "oz_product_id": s(get("Ozon product_id")),
        })
    if len(rows) < 100:
        raise RuntimeError(f"Safety stop: найдено только {len(rows)} строк Norden")
    return rows


def load_kit_module():
    path = ROOT / "norden-kit" / "sync_norden_kit.py"
    spec = importlib.util.spec_from_file_location("norden_kit_sync_module", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sync_kit(rows, report):
    part = report["channels"]["KIT"]
    try:
        mod = load_kit_module()
        kit = mod.KitClient(os.environ["YANDEX_KIT_TOKEN"].strip())
        mapping_path = ROOT / "norden-kit" / "kit_mapping.json"
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        by_sku = defaultdict(list)
        by_supplier = defaultdict(list)
        for supplier, variants in (mapping.get("variants") or {}).items():
            for v in variants or []:
                vid = s(v.get("variant_id"))
                if not vid:
                    continue
                row = {"variant_id": vid}
                by_supplier[s(supplier)].append(row)
                sku = s(v.get("sku"))
                if sku:
                    by_sku[sku].append(row)

        updates = []
        missing = []
        seen = set()
        for r in rows:
            if not (r["kit_old"] and r["kit_sale"] and r["kit_min"]):
                continue
            matches = by_sku.get(r["article"]) or by_supplier.get(r["yml_id"]) or []
            if not matches:
                missing.append(r["article"])
                continue
            for m in matches:
                vid = m["variant_id"]
                if vid in seen:
                    continue
                seen.add(vid)
                updates.append({
                    "variant_id": vid,
                    "old": money_str(r["kit_old"]),
                    "sale": money_str(r["kit_sale"]),
                    "minimum": money_str(r["kit_min"]),
                    "article": r["article"],
                })
        part["targeted"] = len(updates)
        part["missing_mapping"] = len(missing)
        part["missing_mapping_sample"] = missing[:50]
        if not updates:
            raise RuntimeError("Нет сопоставленных вариантов KIT")

        minimum_field = kit.discover_minimum_price_field(updates[0])
        part["minimum_price_field"] = minimum_field
        if not minimum_field:
            part["warnings"].append("KIT API не подтвердил отдельное поле минимальной цены; цена и зачёркнутая цена обновлены, минимум не подтверждён.")
        skipped = kit.bulk_prices(updates, minimum_field=minimum_field)
        part["skipped_stale_variant_ids"] = skipped[:100]
        part["updated"] = len(updates) - len(skipped)

        verified = 0
        verify_errors = []
        for u in updates[:25]:
            try:
                v = kit.get_variant(u["variant_id"])
                current_old = money(v.get("price") or (v.get("pricing") or {}).get("price"))
                current_sale = money(v.get("manual_discount_price") or (v.get("pricing") or {}).get("manual_discount_price"))
                ok = current_old == money(u["old"]) and current_sale == money(u["sale"])
                if minimum_field:
                    current_min = money(v.get(minimum_field) or (v.get("pricing") or {}).get(minimum_field))
                    ok = ok and current_min == money(u["minimum"])
                if ok:
                    verified += 1
                else:
                    verify_errors.append({"article": u["article"], "variant_id": u["variant_id"]})
            except Exception as exc:
                verify_errors.append({"article": u["article"], "error": str(exc)[:500]})
        part["verification_sample_size"] = min(25, len(updates))
        part["verified_sample"] = verified
        part["verification_mismatches"] = verify_errors[:25]
        part["status"] = "УСПЕШНО" if not skipped and not verify_errors else "ЧАСТИЧНО"
    except Exception as exc:
        part["status"] = "ОШИБКА"
        part["errors"].append(str(exc)[:2000])


def load_webasyst_client():
    sys.path.insert(0, str(ROOT / "webasyst"))
    from client import WebasystClient
    return WebasystClient


def load_wa_products(wa, type_id):
    out = []
    offset = 0
    while True:
        payload = wa.call(
            "shop.product.search",
            params={"hash": f"type/{type_id}", "offset": offset, "limit": 1000, "fields": "*,skus,stock_counts"},
        )
        batch = listify(payload, ("products", "items"))
        total = payload.get("count") or payload.get("total_count") if isinstance(payload, dict) else None
        out.extend(batch)
        if not batch or len(batch) < 1000:
            break
        if total not in (None, "") and len(out) >= int(total):
            break
        offset += len(batch)
    return out


def product_skus(product):
    rows = product.get("skus")
    if isinstance(rows, dict):
        return [x for x in rows.values() if isinstance(x, dict)]
    if isinstance(rows, list):
        return [x for x in rows if isinstance(x, dict)]
    return []


def sync_webasyst(rows, report):
    part = report["channels"]["Webasyst"]
    try:
        WebasystClient = load_webasyst_client()
        wa = WebasystClient(min_request_interval=0.45)
        types = listify(wa.call("shop.type.getList"))
        matches = [x for x in types if norm(x.get("name") or x.get("title")) == norm("NORDEN-100")]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one Webasyst type NORDEN-100, found {len(matches)}")
        type_id = s(matches[0].get("id"))
        products = load_wa_products(wa, type_id)
        sku_index = {}
        duplicates = set()
        for p in products:
            for sku in product_skus(p):
                code = s(sku.get("sku"))
                if not code:
                    continue
                if code in sku_index:
                    duplicates.add(code)
                else:
                    sku_index[code] = sku
        part["webasyst_products"] = len(products)
        part["duplicate_skus"] = sorted(duplicates)[:50]

        targeted = 0
        updated = 0
        unchanged = 0
        missing = []
        errors = []
        for r in rows:
            if not (r["wa_purchase"] and r["wa_sale"] and r["wa_old"]):
                continue
            sku = sku_index.get(r["article"])
            if not sku or r["article"] in duplicates:
                missing.append(r["article"])
                continue
            targeted += 1
            desired = {
                "purchase_price": money_str(r["wa_purchase"]),
                "price": money_str(r["wa_sale"]),
                "compare_price": money_str(r["wa_old"]),
            }
            changes = {}
            for key, val in desired.items():
                if money(sku.get(key)) != money(val):
                    changes[key] = val
            if not changes:
                unchanged += 1
                continue
            try:
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": s(sku.get("id"))},
                    data=changes,
                )
                updated += 1
            except Exception as exc:
                errors.append({"article": r["article"], "error": str(exc)[:700]})
        part["targeted"] = targeted
        part["updated"] = updated
        part["unchanged"] = unchanged
        part["missing"] = len(missing)
        part["missing_sample"] = missing[:50]
        part["errors"] = errors[:100]

        verify_products = load_wa_products(wa, type_id)
        verify_index = {}
        for p in verify_products:
            for sku in product_skus(p):
                code = s(sku.get("sku"))
                if code and code not in verify_index:
                    verify_index[code] = sku
        verified = 0
        verify_mismatch = []
        for r in rows:
            if not (r["wa_purchase"] and r["wa_sale"] and r["wa_old"]):
                continue
            sku = verify_index.get(r["article"])
            if not sku:
                continue
            if (
                money(sku.get("purchase_price")) == r["wa_purchase"].quantize(MONEY, rounding=ROUND_HALF_UP)
                and money(sku.get("price")) == r["wa_sale"].quantize(MONEY, rounding=ROUND_HALF_UP)
                and money(sku.get("compare_price")) == r["wa_old"].quantize(MONEY, rounding=ROUND_HALF_UP)
            ):
                verified += 1
            elif len(verify_mismatch) < 50:
                verify_mismatch.append(r["article"])
        part["verified"] = verified
        part["verification_mismatch_sample"] = verify_mismatch
        part["status"] = "УСПЕШНО" if not errors and not verify_mismatch else "ЧАСТИЧНО"
    except Exception as exc:
        part["status"] = "ОШИБКА"
        part["errors"].append(str(exc)[:2000])


def ozon_post(session, path, body):
    headers = {
        "Client-Id": os.environ["OZON_CLIENT_ID"].strip(),
        "Api-Key": os.environ["OZON_API_KEY"].strip(),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "megapolis-norden-catalog-price-sync/1.0",
    }
    return req_json(session, "POST", OZON_BASE + path, headers=headers, body=body)


def sync_ozon(rows, report):
    part = report["channels"]["Ozon"]
    try:
        session = requests.Session()
        targets = []
        for r in rows:
            if not (r["oz_offer"] and r["oz_sale"] and r["oz_old"] and r["oz_min"]):
                continue
            price = ceil_rub(r["oz_sale"])
            old = ceil_rub(r["oz_old"])
            minp = ceil_rub(r["oz_min"])
            if not (price and old and minp):
                continue
            if old <= price or minp > price:
                part["errors"].append(f'{r["article"]}: invalid Ozon price tuple {price}/{old}/{minp}')
                continue
            x = {
                "offer_id": r["oz_offer"],
                "price": str(price),
                "old_price": str(old),
                "min_price": str(minp),
                "currency_code": "RUB",
                "min_price_for_auto_actions_enabled": True,
                "price_strategy_enabled": "DISABLED",
            }
            if r["oz_product_id"].isdigit():
                x["product_id"] = int(r["oz_product_id"])
            targets.append((r["article"], x))
        part["targeted"] = len(targets)
        updated = 0
        api_errors = []
        for batch in chunks(targets, 100):
            payload = {"prices": [x for _, x in batch]}
            d = ozon_post(session, "/v1/product/import/prices", payload)
            result = d.get("result") or []
            by_offer = {s(x.get("offer_id")): x for x in result if isinstance(x, dict)}
            for art, x in batch:
                rr = by_offer.get(x["offer_id"])
                if rr and rr.get("updated") and not rr.get("errors"):
                    updated += 1
                else:
                    api_errors.append({"article": art, "offer_id": x["offer_id"], "response": rr})
        part["updated"] = updated
        part["api_error_count"] = len(api_errors)
        part["api_errors"] = api_errors[:50]

        wanted = {x["offer_id"]: x for _, x in targets}
        verified = 0
        mismatch = []
        offers = list(wanted)
        for ids in chunks(offers, 100):
            d = ozon_post(session, "/v5/product/info/prices", {
                "cursor": "",
                "filter": {"offer_id": ids, "visibility": "ALL"},
                "limit": 100,
            })
            for item in d.get("items") or []:
                oid = s(item.get("offer_id"))
                if oid not in wanted:
                    continue
                p = item.get("price") or {}
                cur = ceil_rub(p.get("price"))
                old = ceil_rub(p.get("old_price"))
                minp = ceil_rub(p.get("min_price"))
                w = wanted[oid]
                if cur == int(w["price"]) and old == int(w["old_price"]) and minp == int(w["min_price"]):
                    verified += 1
                elif len(mismatch) < 50:
                    mismatch.append({
                        "offer_id": oid,
                        "expected": [w["price"], w["old_price"], w["min_price"]],
                        "actual": [cur, old, minp],
                    })
        part["verified"] = verified
        part["verification_mismatch_sample"] = mismatch
        part["status"] = "УСПЕШНО" if updated == len(targets) and verified == len(targets) else "ЧАСТИЧНО"
    except Exception as exc:
        part["status"] = "ОШИБКА"
        part["errors"].append(str(exc)[:2000])


def yandex_headers():
    return {
        "Api-Key": os.environ["YANDEX_MARKET_API_KEY"].strip(),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "megapolis-norden-catalog-price-sync/1.0",
    }


def sync_yandex(rows, report):
    part = report["channels"]["Yandex"]
    try:
        session = requests.Session()
        groups = defaultdict(list)
        for r in rows:
            if not (r["ya_offer"] and r["ya_business"] and r["ya_sale"] and r["ya_old"]):
                continue
            sale = ceil_rub(r["ya_sale"])
            old = ceil_rub(r["ya_old"])
            if not (sale and old) or old <= sale:
                part["errors"].append(f'{r["article"]}: invalid Yandex price tuple {sale}/{old}')
                continue
            groups[r["ya_business"]].append((r["article"], {
                "offerId": r["ya_offer"],
                "price": {"value": sale, "currencyId": "RUR", "discountBase": old},
            }))
        part["targeted"] = sum(len(x) for x in groups.values())
        accepted = 0
        api_errors = []
        for bid, rows2 in groups.items():
            for batch in chunks(rows2, 200):
                d = req_json(
                    session,
                    "POST",
                    f"{YANDEX_BASE}/v2/businesses/{bid}/offer-prices/updates",
                    headers=yandex_headers(),
                    body={"offers": [x for _, x in batch]},
                )
                if s(d.get("status")).upper() == "OK" and not d.get("errors"):
                    accepted += len(batch)
                else:
                    api_errors.append({"business_id": bid, "response": d})
        part["accepted"] = accepted
        part["api_errors"] = api_errors[:50]
        part["api_error_count"] = len(api_errors)

        # Yandex notes that propagation can take several minutes. Verify immediately where possible,
        # but an accepted update is not marked failed solely because readback is still stale.
        time.sleep(5)
        wanted = {}
        for bid, rows2 in groups.items():
            wanted[bid] = {x["offerId"]: x["price"] for _, x in rows2}
        verified = 0
        stale = []
        for bid, mp in wanted.items():
            for ids in chunks(list(mp), 200):
                d = req_json(
                    session,
                    "POST",
                    f"{YANDEX_BASE}/v2/businesses/{bid}/offer-prices",
                    headers=yandex_headers(),
                    params={"limit": 500},
                    body={"offerIds": ids, "archived": False},
                )
                offers = ((d.get("result") or {}).get("offers") or [])
                for off in offers:
                    oid = s(off.get("offerId"))
                    if oid not in mp:
                        continue
                    p = off.get("price") or {}
                    exp = mp[oid]
                    if ceil_rub(p.get("value")) == int(exp["value"]) and ceil_rub(p.get("discountBase")) == int(exp["discountBase"]):
                        verified += 1
                    elif len(stale) < 50:
                        stale.append({
                            "offer_id": oid,
                            "expected": [exp["value"], exp["discountBase"]],
                            "actual": [p.get("value"), p.get("discountBase")],
                        })
        part["verified_immediately"] = verified
        part["pending_or_mismatch_sample"] = stale
        if accepted == part["targeted"] and not api_errors:
            part["status"] = "УСПЕШНО"
            if verified < accepted:
                part["warnings"].append("API принял цены; часть readback ещё может быть старой из-за задержки обновления Яндекс Маркета.")
        else:
            part["status"] = "ЧАСТИЧНО"
    except Exception as exc:
        part["status"] = "ОШИБКА"
        part["errors"].append(str(exc)[:2000])


def main():
    report = {
        "started_at": now_iso(),
        "source": {"spreadsheet_id": SHEET_ID, "sheet": SHEET_NAME},
        "rounding": "Ozon/Yandex: округление вверх до целого рубля; KIT/Webasyst: значения из таблицы до копеек",
        "channels": {
            "Ozon": {"status": "НЕ ЗАПУЩЕНО", "errors": [], "warnings": []},
            "Yandex": {"status": "НЕ ЗАПУЩЕНО", "errors": [], "warnings": []},
            "KIT": {"status": "НЕ ЗАПУЩЕНО", "errors": [], "warnings": []},
            "Webasyst": {"status": "НЕ ЗАПУЩЕНО", "errors": [], "warnings": []},
        },
    }
    exit_code = 0
    try:
        rows = load_sheet()
        report["catalog_rows"] = len(rows)
        # Independent channels: one failure must not block the other three.
        sync_kit(rows, report)
        sync_webasyst(rows, report)
        sync_ozon(rows, report)
        sync_yandex(rows, report)
        bad = [k for k, v in report["channels"].items() if v["status"] not in ("УСПЕШНО",)]
        report["status"] = "УСПЕШНО" if not bad else "ЗАВЕРШЕНО С ОШИБКАМИ"
        if bad:
            exit_code = 1
    except Exception as exc:
        report["status"] = "ОШИБКА"
        report["fatal_error"] = str(exc)[:3000]
        exit_code = 1
    report["finished_at"] = now_iso()
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
