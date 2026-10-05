from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .norden import load_norden_source
from .runner import load_config, normalize, _unique_image_aliases
from .validators import validate_run
from .webasyst_sync import (
    apply_plan,
    build_plan,
    index_by_supplier_sku_name,
    previous_prices,
    validate_apply_plan,
)
from .live_norden_webasyst_refresh import _load_products, _skus, _s
from webasyst.client import WebasystClient


def _load_links(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("Supplier link dump is invalid")
    out = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        supplier_sku = _s(row.get("supplier_sku"))
        product_id = _s(row.get("product_id"))
        sku_id = _s(row.get("sku_id"))
        if not supplier_sku or not product_id or not sku_id:
            continue
        key = (supplier_sku, product_id, sku_id)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "supplier_sku": supplier_sku,
            "product_id": product_id,
            "sku_id": sku_id,
            "purchase_price": row.get("purchase_price"),
            "stock": row.get("stock"),
        })
    return out


def _current_links(wa, type_id):
    rows = _load_products(wa, type_id)
    out = []
    seen = set()
    for product in rows:
        product_id = _s(product.get("id"))
        for sku in _skus(product):
            supplier_sku = _s(sku.get("name"))
            sku_id = _s(sku.get("id"))
            if not supplier_sku or not product_id or not sku_id:
                continue
            key = (supplier_sku, product_id, sku_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "supplier_sku": supplier_sku,
                "product_id": product_id,
                "sku_id": sku_id,
                "purchase_price": sku.get("purchase_price"),
                "stock": sku.get("count"),
            })
    return out


def _merge_links(*groups):
    out = []
    seen = set()
    for rows in groups:
        for row in rows or []:
            key = (
                _s(row.get("supplier_sku")),
                _s(row.get("product_id")),
                _s(row.get("sku_id")),
            )
            if not all(key) or key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out


def _write(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="supplier_engine/configs/norden_scheduled.json")
    ap.add_argument("--links", default="norden-links.json")
    ap.add_argument("--mode", choices=("dry-run", "apply"), default="apply")
    ap.add_argument("--max-create", type=int, default=3000)
    ap.add_argument("--max-repair", type=int, default=500)
    ap.add_argument("--output", default="norden-webasyst-reconcile.json")
    args = ap.parse_args()

    payload = {"status": "failed", "mode": args.mode}

    try:
        config = load_config(args.config)
        rows, source_bytes, source_meta = load_norden_source()
        products = normalize(config, rows)
        if len(products) < 1000:
            raise RuntimeError(
                "Safety stop: Norden source is unexpectedly small (%d products)"
                % len(products)
            )

        wa = WebasystClient()
        type_id = int((config.get("webasyst") or {}).get("type_id") or 0)
        if not type_id:
            raise RuntimeError("Norden Webasyst type_id is missing")

        by_supplier = index_by_supplier_sku_name(
            wa,
            type_id,
            [product.supplier_sku for product in products],
            {product.sku: product.supplier_sku for product in products},
            _unique_image_aliases(products),
        )
        existing = {
            product.sku: by_supplier[product.supplier_sku]
            for product in products
            if product.supplier_sku in by_supplier
        }

        saved_links = _load_links(args.links)
        current_links = _current_links(wa, type_id)
        links = _merge_links(saved_links, current_links)

        validation = validate_run(
            products,
            previous_count=None,
            max_price_change_pct=float((config.get("safety") or {}).get("max_price_change_pct", 100)),
            min_count_ratio=float((config.get("safety") or {}).get("min_source_count_ratio", 0.6)),
            previous_by_sku=previous_prices(existing),
        )
        plan = build_plan(products, existing, links, config.get("rules") or {})
        for error in validate_apply_plan(plan, config):
            if error not in validation.errors:
                validation.errors.append(error)

        create_count = len(plan.get("create") or [])
        repair_count = len(plan.get("repair_sku") or [])
        if create_count > args.max_create:
            validation.errors.append(
                "Safety stop: planned creates %d exceed limit %d"
                % (create_count, args.max_create)
            )
        if repair_count > args.max_repair:
            validation.errors.append(
                "Safety stop: planned SKU repairs %d exceed limit %d"
                % (repair_count, args.max_repair)
            )

        validation.blocked = bool(validation.errors)
        payload.update({
            "source": {
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
                "origin": source_meta.get("origin"),
                "fallback_used": bool(source_meta.get("fallback_used")),
                "api_error": source_meta.get("api_error") or "",
                "products": len(products),
            },
            "links": {
                "saved": len(saved_links),
                "current": len(current_links),
                "combined": len(links),
            },
            "plan": {
                "create": create_count,
                "repair_sku": repair_count,
                "update": len(plan.get("update") or []),
                "zero": len(plan.get("zero") or []),
                "skipped": len(plan.get("skipped") or []),
                "blocked": len(plan.get("blocked") or []),
                "blocked_sample": (plan.get("blocked") or [])[:50],
            },
            "errors": list(validation.errors),
            "warnings": list(validation.warnings),
            "readonly": args.mode == "dry-run",
        })

        if validation.blocked:
            payload["status"] = "blocked"
            _write(args.output, payload)
            raise SystemExit(2)

        if args.mode == "dry-run":
            payload["status"] = "ok"
            _write(args.output, payload)
            return

        result = apply_plan(wa, plan, config)
        payload["apply"] = result
        payload["status"] = "ok"
        _write(args.output, payload)
    except SystemExit:
        raise
    except Exception as exc:
        payload.setdefault("errors", []).append(str(exc))
        _write(args.output, payload)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
