from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from .norden import load_norden_source
from webasyst.client import WebasystClient


TYPE_NAME = "NORDEN-100"
MAIN_STOCK_NAMES = ("Основной склад", "Основной")


def _s(value):
    return str(value or "").strip()


def _norm(value):
    return re.sub(
        r"\s+",
        " ",
        unicodedata.normalize("NFKC", _s(value)).casefold(),
    ).strip()


def _listify(payload, keys=("products", "items", "skus")):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                return [x for x in value.values() if isinstance(x, dict)]
        if payload and all(isinstance(v, dict) for v in payload.values()):
            return list(payload.values())
    return []


def _skus(product):
    rows = product.get("skus") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    return [x for x in rows if isinstance(x, dict)]


def _number(value):
    try:
        return Decimal(_s(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _money(value):
    number = _number(value)
    if number is None:
        return ""
    return format(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _quantity(value):
    number = _number(value)
    if number is None or number <= 0:
        return 0
    return int(number)


def _load_type_and_stocks(wa, type_name):
    types = _listify(wa.call("shop.type.getList"), ("types", "items"))
    type_matches = [
        row for row in types
        if _norm(row.get("name") or row.get("title")) == _norm(type_name)
    ]
    if len(type_matches) != 1:
        raise RuntimeError(
            "Expected exactly one Webasyst type %r, found %d"
            % (type_name, len(type_matches))
        )

    stocks = _listify(wa.call("shop.stock.getList"), ("stocks", "items"))
    if not stocks:
        raise RuntimeError("Webasyst returned no stocks")

    main = []
    for wanted in MAIN_STOCK_NAMES:
        main = [
            row for row in stocks
            if _norm(row.get("name") or row.get("title")) == _norm(wanted)
        ]
        if main:
            break
    if len(main) != 1:
        raise RuntimeError(
            "Expected exactly one Webasyst main stock (%s), found %d"
            % (" / ".join(MAIN_STOCK_NAMES), len(main))
        )

    stock_ids = [
        _s(row.get("id")) for row in stocks
        if _s(row.get("id")).isdigit()
    ]
    if not stock_ids:
        raise RuntimeError("Webasyst stock IDs are unavailable")

    return _s(type_matches[0].get("id")), _s(main[0].get("id")), stock_ids


def _load_products(wa, type_id):
    out = []
    offset = 0
    while True:
        payload = wa.call(
            "shop.product.search",
            params={
                "hash": "type/%s" % type_id,
                "offset": offset,
                "limit": 1000,
                "fields": "*,skus,stock_counts",
            },
        )
        rows = _listify(payload, ("products", "items"))
        out.extend(rows)
        if len(rows) < 1000:
            break
        offset += len(rows)
    return out


def _source_index(rows):
    by_article = defaultdict(list)
    for row in rows:
        article = _s(row.get("product_code"))
        if article:
            by_article[_norm(article)].append(row)
    duplicates = {
        key: values for key, values in by_article.items()
        if len(values) > 1
    }
    if duplicates:
        sample = [values[0].get("product_code") for values in list(duplicates.values())[:20]]
        raise RuntimeError(
            "Norden source contains duplicate normalized articles: %s" % sample
        )
    return {key: values[0] for key, values in by_article.items()}


def _build_plan(products, source_by_article, main_stock_id, stock_ids):
    candidates = []
    by_supplier_article = defaultdict(list)
    skipped_no_supplier_article = []

    for product in products:
        product_id = _s(product.get("id"))
        for sku in _skus(product):
            sku_id = _s(sku.get("id"))
            supplier_article = _s(sku.get("name"))
            if not sku_id:
                continue
            if not supplier_article:
                skipped_no_supplier_article.append({
                    "product_id": product_id,
                    "sku_id": sku_id,
                    "sku": _s(sku.get("sku")),
                })
                continue
            row = {
                "product_id": product_id,
                "sku_id": sku_id,
                "sku": _s(sku.get("sku")),
                "supplier_article": supplier_article,
                "source": source_by_article.get(_norm(supplier_article)),
            }
            candidates.append(row)
            by_supplier_article[_norm(supplier_article)].append(row)

    duplicates = {
        key: rows for key, rows in by_supplier_article.items()
        if len(rows) > 1
    }
    if duplicates:
        sample = [
            {
                "supplier_article": rows[0]["supplier_article"],
                "sku_ids": [row["sku_id"] for row in rows],
            }
            for rows in list(duplicates.values())[:20]
        ]
        raise RuntimeError(
            "Duplicate supplier articles inside Webasyst NORDEN-100: %s" % sample
        )

    matched = sum(1 for row in candidates if row["source"] is not None)
    labeled = len(candidates)
    if labeled >= 100 and matched / float(labeled or 1) < 0.50:
        raise RuntimeError(
            "Safety stop: only %d/%d Webasyst supplier articles match current Norden source"
            % (matched, labeled)
        )

    plan = []
    for row in candidates:
        source = row["source"]
        qty = _quantity(source.get("qty")) if source is not None else 0
        stock = {str(stock_id): "0" for stock_id in stock_ids}
        stock[str(main_stock_id)] = str(qty)
        data = {
            "stock": stock,
            "available": 1 if qty > 0 else 0,
        }
        purchase = _money(source.get("price")) if source is not None else ""
        if purchase and _number(purchase) and _number(purchase) > 0:
            data["purchase_price"] = purchase
        plan.append(dict(row, quantity=qty, data=data))

    return {
        "items": plan,
        "matched": matched,
        "missing_from_source": labeled - matched,
        "skipped_no_supplier_article": skipped_no_supplier_article,
    }


def _write(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type-name", default=TYPE_NAME)
    ap.add_argument("--mode", choices=("dry-run", "apply"), default="apply")
    ap.add_argument("--output", default="norden-webasyst-refresh.json")
    args = ap.parse_args()

    payload = {
        "status": "failed",
        "mode": args.mode,
        "type_name": args.type_name,
    }

    try:
        source_rows, _source_bytes, source_meta = load_norden_source()
        if len(source_rows) < 1000:
            raise RuntimeError(
                "Safety stop: Norden source is unexpectedly small (%d rows)"
                % len(source_rows)
            )
        source = _source_index(source_rows)

        wa = WebasystClient()
        type_id, main_stock_id, stock_ids = _load_type_and_stocks(wa, args.type_name)
        products = _load_products(wa, type_id)
        if len(products) < 1000:
            raise RuntimeError(
                "Safety stop: Webasyst %s catalog is unexpectedly small (%d products)"
                % (args.type_name, len(products))
            )

        plan = _build_plan(products, source, main_stock_id, stock_ids)
        items = plan["items"]
        payload.update({
            "source": {
                "origin": source_meta.get("origin"),
                "fallback_used": bool(source_meta.get("fallback_used")),
                "rows": len(source_rows),
            },
            "webasyst": {
                "type_id": type_id,
                "products": len(products),
                "sku_candidates": len(items),
                "matched_source": plan["matched"],
                "missing_from_source_zero_stock": plan["missing_from_source"],
                "skipped_no_supplier_article": len(plan["skipped_no_supplier_article"]),
                "main_stock_id": main_stock_id,
                "stock_ids": stock_ids,
            },
            "readonly": args.mode == "dry-run",
        })

        if args.mode == "dry-run":
            payload["status"] = "ok"
            payload["sample"] = [
                {
                    "product_id": row["product_id"],
                    "sku_id": row["sku_id"],
                    "sku": row["sku"],
                    "supplier_article": row["supplier_article"],
                    "quantity": row["quantity"],
                    "purchase_price": row["data"].get("purchase_price"),
                }
                for row in items[:30]
            ]
            _write(args.output, payload)
            return

        updated = 0
        zeroed_missing = 0
        purchase_updated = 0
        errors = []
        for row in items:
            try:
                wa.call(
                    "shop.product.skus.update",
                    http_method="POST",
                    params={"id": row["sku_id"]},
                    data=row["data"],
                )
                updated += 1
                if row["source"] is None:
                    zeroed_missing += 1
                if row["data"].get("purchase_price"):
                    purchase_updated += 1
            except Exception as exc:
                errors.append({
                    "product_id": row["product_id"],
                    "sku_id": row["sku_id"],
                    "sku": row["sku"],
                    "supplier_article": row["supplier_article"],
                    "error": str(exc)[:1000],
                })
                if len(errors) >= 100:
                    break

        payload["apply"] = {
            "updated": updated,
            "purchase_updated": purchase_updated,
            "zeroed_missing": zeroed_missing,
            "errors": errors,
        }
        payload["status"] = "ok" if not errors else "partial_failure"
        _write(args.output, payload)
        if errors:
            raise SystemExit(2)

    except SystemExit:
        raise
    except Exception as exc:
        payload.setdefault("errors", []).append(str(exc))
        _write(args.output, payload)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
