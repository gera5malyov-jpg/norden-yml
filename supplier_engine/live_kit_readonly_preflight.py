from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

from webasyst.client import WebasystClient
from .kit_sync import plan_manifest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "norden-kit" / "webasyst_n100_to_kit_once.py"


def _load_helpers():
    spec = importlib.util.spec_from_file_location("norden_webasyst_kit_helpers", BRIDGE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load existing Norden/KIT helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _category_rows(payload, listify):
    rows = []
    seen = set()

    def walk(node, parent_id=0):
        if not isinstance(node, dict):
            return
        cid = str(node.get("id") or "").strip()
        if not cid:
            return
        if cid not in seen:
            rows.append({
                "id": int(cid) if cid.isdigit() else cid,
                "name": str(node.get("name") or node.get("title") or "").strip(),
                "parent_id": int(parent_id) if str(parent_id).isdigit() else parent_id,
            })
            seen.add(cid)
        children = node.get("children") or node.get("childs") or node.get("categories") or []
        if isinstance(children, dict):
            children = list(children.values())
        for child in children if isinstance(children, list) else []:
            walk(child, cid)

    for row in listify(payload, ("categories", "items")):
        walk(row, 0)
    return rows


def _feature_rows(info, title_by_code, feature_value):
    raw = info.get("features") or {} if isinstance(info, dict) else {}
    if not isinstance(raw, dict):
        return []
    out = []
    for code, value in raw.items():
        text = feature_value(value)
        if not text:
            continue
        out.append({
            "code": str(code),
            "name": title_by_code.get(str(code), str(code)),
            "values": [text],
        })
    return out


def _summary_urls(summary):
    summary = str(summary or "")
    return list(dict.fromkeys(
        url.rstrip(".,;)")
        for url in re.findall(r"https?://[^\s<>\]\[\"']+", summary)
        if url
    ))


def build_manifest(wa, helpers, type_name):
    types = helpers.listify(wa.call("shop.type.getList"))
    matches = [
        row for row in types
        if helpers.nt(row.get("name") or row.get("title")) == helpers.nt(type_name)
    ]
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one Webasyst type %s, found %d" % (type_name, len(matches)))
    type_id = helpers.s(matches[0].get("id"))

    basic_products = helpers.load_wa_products(wa, type_id)
    category_payload = wa.call("shop.category.getTree")
    categories = _category_rows(category_payload, helpers.listify)
    category_ids_known = {str(row["id"]) for row in categories}

    feature_defs = helpers.listify(wa.call("shop.feature.getList"), ("features", "items"))
    title_by_code = {
        helpers.s(row.get("code")): helpers.s(row.get("name") or row.get("title") or row.get("code"))
        for row in feature_defs
        if helpers.s(row.get("code"))
    }

    items = []
    skipped_no_supplier_article = []
    for idx, basic in enumerate(basic_products, 1):
        product_id = helpers.s(basic.get("id"))
        if not product_id:
            continue
        info = wa.call("shop.product.getInfo", params={"id": product_id})
        sku_rows = helpers.product_skus(info if isinstance(info, dict) and info.get("skus") else basic)
        if not sku_rows:
            continue

        # Supplier importer uses one Webasyst SKU per supplier article. If a
        # historical product contains more, keep only the primary SKU here;
        # live plugin manifest remains the authoritative apply source.
        sku = sku_rows[0]
        supplier_sku = helpers.s(sku.get("name"))
        if not supplier_sku:
            skipped_no_supplier_article.append(product_id)
            continue

        cids = [
            cid for cid in helpers.extract_category_ids(info, basic)
            if str(cid) in category_ids_known
        ]
        cids = list(dict.fromkeys(cids))
        summary = (info or {}).get("summary") if isinstance(info, dict) else basic.get("summary")
        stock = helpers.stock_total(sku, basic)
        items.append({
            "supplier_sku": supplier_sku,
            "product_id": int(product_id) if product_id.isdigit() else product_id,
            "sku_id": sku.get("id"),
            "sku": helpers.s(sku.get("sku")),
            "sku_name": supplier_sku,
            "name": helpers.s((info or {}).get("name") if isinstance(info, dict) else "") or helpers.s(basic.get("name")),
            "description": helpers.s((info or {}).get("description") if isinstance(info, dict) else "") or helpers.s(basic.get("description")),
            "summary": helpers.s(summary),
            "status": int((info or {}).get("status") or basic.get("status") or 0) if isinstance(info, dict) else int(basic.get("status") or 0),
            "purchase_price": sku.get("purchase_price"),
            "stock": stock,
            "category_ids": [int(cid) if str(cid).isdigit() else cid for cid in cids],
            "features": _feature_rows(info or {}, title_by_code, helpers.feature_value),
            "image_urls": _summary_urls(summary),
        })
        if idx % 100 == 0:
            print("WEBASYST_READONLY_SCAN=%d/%d" % (idx, len(basic_products)), flush=True)

    return {
        "type_id": type_id,
        "type_name": type_name,
        "webasyst_products": len(basic_products),
        "skipped_no_supplier_article": skipped_no_supplier_article,
        "items": items,
        "categories": categories,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type-name", default="NORDEN-100")
    ap.add_argument("--brand", default="Norden")
    ap.add_argument("--output", default="kit-live-preflight.json")
    args = ap.parse_args()

    helpers = _load_helpers()
    wa = WebasystClient()
    manifest = build_manifest(wa, helpers, args.type_name)
    result = plan_manifest(
        manifest,
        {
            "identity": {"brand": args.brand},
            "rules": {"export_to_kit": True},
        },
    )
    payload = {
        "readonly": True,
        "source": {
            "type_id": manifest["type_id"],
            "type_name": manifest["type_name"],
            "webasyst_products": manifest["webasyst_products"],
            "manifest_items": len(manifest["items"]),
            "skipped_no_supplier_article": len(manifest["skipped_no_supplier_article"]),
        },
        "result": result,
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "readonly": True,
        "source": payload["source"],
        "status": result.get("status"),
        "eligible": result.get("eligible"),
        "skipped_no_category": result.get("skipped_no_category"),
        "would_create": result.get("would_create"),
        "would_update": result.get("would_update"),
        "category_paths": len(result.get("required_category_paths") or []),
        "missing_category_paths": len(result.get("missing_category_paths") or []),
        "identity_conflicts": (result.get("preflight") or {}).get("conflicts"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
