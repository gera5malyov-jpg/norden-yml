#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBASYST_DIR = ROOT / "webasyst"
import sys
sys.path.insert(0, str(WEBASYST_DIR))
from client import WebasystClient  # noqa: E402

SKU_PREFIX = "RED-"
TYPE_NAME = "RED-Black1"
STOCK_NAME = "Red МСК"
SNAPSHOT_PATH = ROOT / "red-kit" / "runtime" / "red_black_snapshot.json"
REPORT_PATH = ROOT / "webasyst" / "last_red_black_daily_sync.json"
WRITE_DELAY = float(os.getenv("RED_WEBASYST_WRITE_DELAY", "0.15"))


def s(value):
    return str(value or "").strip()


def norm(value):
    return re.sub(
        r"[^0-9a-zа-яё]+",
        "",
        unicodedata.normalize("NFKC", s(value)).casefold(),
    )


def sku_key(value):
    return re.sub(r"\s+", "", s(value)).casefold()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def money(value):
    try:
        d = Decimal(s(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if d < 0:
        return None
    return d.quantize(Decimal("0.01"))


def money_str(value):
    d = money(value)
    return None if d is None else f"{d:.2f}"


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


def product_skus(product):
    skus = product.get("skus")
    if isinstance(skus, dict):
        return [x for x in skus.values() if isinstance(x, dict)]
    if isinstance(skus, list):
        return [x for x in skus if isinstance(x, dict)]
    return []


def exact_one(rows, wanted, label):
    matches = [
        row for row in rows
        if norm(row.get("name") or row.get("title")) == norm(wanted)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Ожидался ровно один {label} {wanted!r}; найдено {len(matches)}")
    return matches[0]


def extimg_summary(urls):
    urls = list(dict.fromkeys(s(x) for x in urls if s(x)))
    return "\n".join(f"[extimg]\n{url}\n[/extimg]" for url in urls)


TRANSLIT = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y",
    "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
    "х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
})


def product_url(name, sku):
    text = unicodedata.normalize("NFKC", s(name)).casefold().translate(TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    code = re.sub(r"[^a-z0-9]+", "-", s(sku).casefold()).strip("-")
    return ((text[:140].strip("-") or "red-black") + "-" + code).strip("-")


def load_wa_products(wa):
    out = []
    offset = 0
    while True:
        payload = wa.call(
            "shop.product.search",
            params={
                "hash": "search/query=RED-",
                "offset": offset,
                "limit": 1000,
                "fields": "id,name,type_id,summary,skus,stock_counts",
            },
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


def extract_product_id(payload):
    if isinstance(payload, dict):
        for key in ("id", "product_id"):
            if payload.get(key) not in (None, ""):
                return s(payload.get(key))
        product = payload.get("product")
        if isinstance(product, dict):
            return s(product.get("id"))
    return ""


def feature_code(title):
    return "redblack_" + hashlib.sha1(norm(title).encode("utf-8")).hexdigest()[:14]


def is_scalar_feature(row):
    ftype = s(row.get("type")).lower()
    try:
        selectable = bool(int(row.get("selectable") or 0))
    except Exception:
        selectable = bool(row.get("selectable"))
    return bool(s(row.get("code"))) and not selectable and (
        not ftype or any(x in ftype for x in ("varchar", "text", "double", "float", "int", "decimal"))
    )


def write_report(report):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def load_snapshot():
    if not SNAPSHOT_PATH.exists():
        raise RuntimeError(
            f"Нет {SNAPSHOT_PATH}. Сначала должен успешно выполниться Red Black -> KIT."
        )
    payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    offers = payload.get("offers")
    if not isinstance(offers, list) or not offers:
        raise RuntimeError("Snapshot Red Black пуст")
    return payload, [x for x in offers if isinstance(x, dict)]


def run(dry_run=False):
    snapshot, offers = load_snapshot()
    wa = WebasystClient(min_request_interval=WRITE_DELAY)

    report = {
        "started_at": now_iso(),
        "dry_run": bool(dry_run),
        "source": "Red Black + актуализированный Yandex KIT",
        "target": "Webasyst",
        "type_name": TYPE_NAME,
        "stock_name": STOCK_NAME,
        "price_rule": "<rrc_price> -> цена продажи Webasyst",
        "purchase_rule": "<price> -> purchase_price Webasyst",
        "missing_rule": "нет в полной выгрузке -> Red МСК = 0; цены не менять",
        "images_rule": "для новых карточек каждое фото отдельным [extimg]URL[/extimg] в кратком описании",
        "source_offers": len(offers),
        "webasyst_red_products": 0,
        "webasyst_red_skus": 0,
        "matched_existing": 0,
        "created_new": 0,
        "planned_new": 0,
        "updated_price_purchase_stock": 0,
        "missing_zeroed": 0,
        "type_updates": 0,
        "features_created": 0,
        "errors": [],
        "warnings": [],
        "samples_new": [],
        "samples_updated": [],
        "complete": False,
    }

    types = listify(wa.call("shop.type.getList"))
    target_type = exact_one(types, TYPE_NAME, "тип товара Webasyst")
    type_id = s(target_type.get("id"))
    report["type_id"] = type_id

    stocks = listify(wa.call("shop.stock.getList"))
    target_stock = exact_one(stocks, STOCK_NAME, "склад Webasyst")
    stock_id = s(target_stock.get("id"))
    report["stock_id"] = stock_id

    products = load_wa_products(wa)
    wa_by_sku = defaultdict(list)
    red_rows = []
    product_by_id = {}
    for product in products:
        pid = s(product.get("id"))
        if pid:
            product_by_id[pid] = product
        for sku_row in product_skus(product):
            sku = s(sku_row.get("sku"))
            if not sku.upper().startswith(SKU_PREFIX):
                continue
            row = (product, sku_row)
            red_rows.append(row)
            wa_by_sku[sku_key(sku)].append(row)

    report["webasyst_red_products"] = len({s(p.get("id")) for p, _ in red_rows})
    report["webasyst_red_skus"] = len(red_rows)

    duplicate_wa = {
        key: rows for key, rows in wa_by_sku.items()
        if len(rows) > 1
    }
    if duplicate_wa:
        report["warnings"].append(
            f"В Webasyst найдено {len(duplicate_wa)} дублирующихся RED-SKU; конфликтующие SKU пропускаются."
        )

    current_match_sku_ids = set()
    current_match_product_ids = set()
    resolved = []

    for item in offers:
        preferred = s(item.get("kit_sku")) or s(item.get("sku"))
        preferred_rows = wa_by_sku.get(sku_key(preferred), []) if preferred else []
        if len(preferred_rows) > 1:
            matches = {
                s(sku_row.get("id")): (product, sku_row)
                for product, sku_row in preferred_rows
                if s(sku_row.get("id"))
            }
        elif len(preferred_rows) == 1:
            product, sku_row = preferred_rows[0]
            matches = {s(sku_row.get("id")): (product, sku_row)}
        else:
            candidates = list(dict.fromkeys(
                [item.get("sku")] + list(item.get("sku_candidates") or [])
            ))
            matches = {}
            for candidate in candidates:
                if not s(candidate) or sku_key(candidate) == sku_key(preferred):
                    continue
                for product, sku_row in wa_by_sku.get(sku_key(candidate), []):
                    sid = s(sku_row.get("id"))
                    if sid:
                        matches[sid] = (product, sku_row)

        if len(matches) > 1:
            report["errors"].append({
                "sku": s(item.get("sku")),
                "stage": "match",
                "message": "Несколько SKU Webasyst соответствуют одному товару: "
                           + ", ".join(sorted(s(row[1].get("sku")) for row in matches.values())),
            })
            resolved.append((item, None))
            continue

        match = next(iter(matches.values())) if matches else None
        if match:
            product, sku_row = match
            current_match_sku_ids.add(s(sku_row.get("id")))
            current_match_product_ids.add(s(product.get("id")))
            report["matched_existing"] += 1
        else:
            report["planned_new"] += 1
        resolved.append((item, match))

    all_features = listify(wa.call("shop.feature.getList"), ("features", "items"))
    global_by_title = defaultdict(list)
    for row in all_features:
        global_by_title[norm(row.get("name") or row.get("title"))].append(row)
    feature_cache = {}

    def ensure_feature(title):
        key = norm(title)
        if key in feature_cache:
            return feature_cache[key]

        usable = {}
        for row in global_by_title.get(key, []):
            if is_scalar_feature(row):
                usable[s(row.get("code"))] = row
        if len(usable) == 1:
            code = next(iter(usable))
            feature_cache[key] = code
            return code

        code = feature_code(title)
        existing = next((x for x in all_features if s(x.get("code")) == code), None)
        if existing is None and not dry_run:
            created = wa.call(
                "shop.feature.add",
                http_method="POST",
                data={
                    "code": code,
                    "type": "varchar",
                    "name": title,
                    "selectable": 0,
                    "multiple": 0,
                    "available_for_sku": 0,
                },
            )
            row = created if isinstance(created, dict) else {}
            if not s(row.get("code")):
                row = {"code": code, "name": title, "type": "varchar", "selectable": 0}
            all_features.append(row)
            global_by_title[key].append(row)
            report["features_created"] += 1

        feature_cache[key] = code
        return code

    def source_features(item):
        pairs = []

        def add(title, value):
            value = s(value)
            if not title or not value:
                return
            if any(norm(t) == norm(title) for t, _ in pairs):
                return
            pairs.append((title, value))

        add("Артикул", item.get("sku"))
        add("Код для сайта", item.get("sku"))
        add("Артикул поставщика", item.get("code") or item.get("article"))
        for title, values in (item.get("params") or {}).items():
            if isinstance(values, list):
                add(title, " / ".join(s(x) for x in values if s(x)))
            else:
                add(title, values)
        return {ensure_feature(title): value for title, value in pairs}

    for item, match in resolved:
        sku = s(item.get("sku"))
        if any(e.get("sku") == sku and e.get("stage") == "match" for e in report["errors"]):
            continue

        sale = money_str(item.get("rrc_price"))
        purchase = money_str(item.get("purchase_price"))
        stock = item.get("stock")
        try:
            stock = max(0, int(stock))
        except Exception:
            report["errors"].append({"sku": sku, "stage": "source", "message": "Некорректный stock"})
            continue

        if match:
            product, sku_row = match
            pid = s(product.get("id"))
            sid = s(sku_row.get("id"))

            if not dry_run and s(product.get("type_id")) != type_id:
                wa.call(
                    "shop.product.update",
                    http_method="POST",
                    params={"id": pid},
                    data={"type_id": type_id},
                )
            if s(product.get("type_id")) != type_id:
                report["type_updates"] += 1

            sku_changes = {"stock": {stock_id: str(stock)}}
            if sale:
                sku_changes["price"] = sale
            if purchase:
                sku_changes["purchase_price"] = purchase

            if not dry_run:
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": sid},
                    data=sku_changes,
                )
            report["updated_price_purchase_stock"] += 1

            if len(report["samples_updated"]) < 30:
                report["samples_updated"].append({
                    "sku": s(sku_row.get("sku")),
                    "product_id": pid,
                    "sale_price": sale,
                    "purchase_price": purchase,
                    "stock_red_msk": stock,
                    "type_id": type_id,
                })
            continue

        if not sale:
            report["errors"].append({
                "sku": sku,
                "stage": "create",
                "message": "Нельзя создать новую карточку Webasyst без rrc_price",
            })
            continue
        if not purchase:
            report["errors"].append({
                "sku": sku,
                "stage": "create",
                "message": "Нельзя создать новую карточку Webasyst без закупочной цены <price>",
            })
            continue

        features = source_features(item)
        summary = extimg_summary(item.get("pictures") or [])
        if dry_run:
            report["created_new"] += 1
            if len(report["samples_new"]) < 30:
                report["samples_new"].append({
                    "sku": sku,
                    "name": s(item.get("name")),
                    "sale_price": sale,
                    "purchase_price": purchase,
                    "stock_red_msk": stock,
                    "images": len(item.get("pictures") or []),
                    "features": len(features),
                })
            continue

        created = wa.call(
            "shop.product.add",
            http_method="POST",
            data={
                "name": s(item.get("name")) or sku,
                "url": product_url(item.get("name"), sku),
                "type_id": type_id,
                "currency": "RUB",
                "summary": summary,
                "description": s(item.get("description")),
                "status": 1,
                "features": features,
                "skus": [{
                    "price": sale,
                    "purchase_price": purchase,
                    "stock": {stock_id: str(stock)},
                    "available": 1,
                    "status": 1,
                }],
            },
        )
        pid = extract_product_id(created)
        if not pid:
            raise RuntimeError(f"shop.product.add не вернул product_id: {str(created)[:500]}")

        skus = listify(
            wa.call("shop.product.skus.getList", params={"product_id": pid}),
            ("skus", "items"),
        )
        if len(skus) != 1:
            raise RuntimeError(f"У новой карточки {pid} неожиданно {len(skus)} SKU")

        sid = s(skus[0].get("id"))
        wa.call(
            "shop.product.skus.update",
            http_method="POST",
            params={"id": sid},
            data={
                "sku": sku,
                "price": sale,
                "purchase_price": purchase,
                "stock": {stock_id: str(stock)},
                "available": 1,
                "status": 1,
            },
        )
        report["created_new"] += 1
        if len(report["samples_new"]) < 30:
            report["samples_new"].append({
                "sku": sku,
                "product_id": pid,
                "name": s(item.get("name")),
                "sale_price": sale,
                "purchase_price": purchase,
                "stock_red_msk": stock,
                "images": len(item.get("pictures") or []),
                "features": len(features),
            })

    # Товары RED-, исчезнувшие из полной выгрузки: только остаток = 0.
    # Цену продажи и закупку здесь намеренно не передаем.
    zeroed_products = set()
    for product, sku_row in red_rows:
        sid = s(sku_row.get("id"))
        if not sid or sid in current_match_sku_ids:
            continue
        sku = s(sku_row.get("sku"))
        if sku_key(sku) in duplicate_wa:
            continue

        pid = s(product.get("id"))
        if not dry_run:
            if s(product.get("type_id")) != type_id and pid not in zeroed_products:
                wa.call(
                    "shop.product.update",
                    http_method="POST",
                    params={"id": pid},
                    data={"type_id": type_id},
                )
            wa.call(
                "shop.product.skus.update",
                http_method="POST",
                params={"id": sid},
                data={"stock": {stock_id: "0"}},
            )
        if s(product.get("type_id")) != type_id and pid not in zeroed_products:
            report["type_updates"] += 1
        zeroed_products.add(pid)
        report["missing_zeroed"] += 1

    report["complete"] = (
        not report["errors"]
        and report["created_new"] == report["planned_new"]
    )
    report["status"] = "УСПЕШНО" if report["complete"] else "ЗАВЕРШЕНО С ОШИБКАМИ"
    report["finished_at"] = now_iso()
    write_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


def main():
    parser = argparse.ArgumentParser(description="Red Black: KIT/source -> Webasyst")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        return run(dry_run=args.dry_run)
    except Exception as exc:
        report = {
            "started_at": now_iso(),
            "finished_at": now_iso(),
            "dry_run": bool(args.dry_run),
            "status": "ОШИБКА",
            "complete": False,
            "error": str(exc)[:3000],
        }
        write_report(report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
