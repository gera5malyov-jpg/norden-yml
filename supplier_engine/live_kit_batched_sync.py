from __future__ import annotations

import argparse
import json
from pathlib import Path

from .kit_sync import KitClient, sync_manifest
from .live_kit_readonly_preflight import _load_helpers, build_manifest


def _s(value):
    return str(value or "").strip()


def _feature_kit_id(features):
    for row in features or []:
        if not isinstance(row, dict):
            continue
        if _s(row.get("code")).casefold() != "kit_id":
            continue
        values = row.get("values") or []
        if not isinstance(values, list):
            values = [values]
        for value in values:
            value = _s(value)
            if value.isdigit():
                return value
        value = _s(row.get("value"))
        if value.isdigit():
            return value
    return ""


def _write(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type-name", default="NORDEN-100")
    ap.add_argument("--brand", default="Norden")
    ap.add_argument("--batch-size", type=int, default=100)
    ap.add_argument("--max-eligible", type=int, default=5000)
    ap.add_argument("--max-create", type=int, default=5000)
    ap.add_argument("--output", default="scheduled-kit-batched-sync.json")
    args = ap.parse_args()

    payload = {
        "status": "failed",
        "mode": "apply",
        "type_name": args.type_name,
        "brand": args.brand,
        "batch_size": args.batch_size,
        "batches": [],
    }

    try:
        helpers = _load_helpers()
        wa = helpers.WebasystClient()
        kit = KitClient()
        config = {
            "identity": {"brand": args.brand},
            "rules": {"export_to_kit": True},
        }

        manifest = build_manifest(wa, helpers, args.type_name)
        products = [
            row for row in (manifest.get("items") or [])
            if isinstance(row, dict) and row.get("category_ids")
        ]
        skipped_no_category = len(manifest.get("items") or []) - len(products)
        if not products:
            raise RuntimeError("No categorized Webasyst products are eligible")
        if len(products) > args.max_eligible:
            raise RuntimeError(
                "Eligible product count %d exceeds limit %d"
                % (len(products), args.max_eligible)
            )

        # Prioritize products without a saved KIT ID, because these are the
        # cards most likely to need creation. This makes new KIT cards visible
        # in the first batches instead of after the whole catalog is checked.
        products.sort(
            key=lambda row: (
                1 if _feature_kit_id(row.get("features")) else 0,
                _s(row.get("sku")),
            )
        )

        # Source-level duplicate guard before any writes.
        seen_sku = {}
        seen_supplier = {}
        duplicate_errors = []
        for row in products:
            sku = _s(row.get("sku"))
            supplier = _s(row.get("supplier_sku"))
            pid = _s(row.get("product_id"))
            if sku:
                previous = seen_sku.get(sku)
                if previous and previous != pid:
                    duplicate_errors.append(
                        "Webasyst duplicate SKU %s: products %s and %s"
                        % (sku, previous, pid)
                    )
                else:
                    seen_sku[sku] = pid
            if supplier:
                previous = seen_supplier.get(supplier)
                if previous and previous != pid:
                    duplicate_errors.append(
                        "Webasyst duplicate supplier article %s: products %s and %s"
                        % (supplier, previous, pid)
                    )
                else:
                    seen_supplier[supplier] = pid
        if duplicate_errors:
            raise RuntimeError(
                "Source duplicate guard blocked writes: %s"
                % " | ".join(duplicate_errors[:20])
            )

        payload["source"] = {
            "type_id": manifest.get("type_id"),
            "webasyst_products": manifest.get("webasyst_products"),
            "eligible": len(products),
            "skipped_no_category": skipped_no_category,
            "without_kit_id_first": sum(
                1 for row in products
                if not _feature_kit_id(row.get("features"))
            ),
        }

        batch_size = max(1, min(250, int(args.batch_size or 100)))
        totals = {
            "created": 0,
            "updated": 0,
            "unchanged_variants": 0,
            "categories_created": 0,
            "characteristics_created": 0,
            "webasyst_updated": 0,
            "webasyst_kit_id_skipped": 0,
            "images_deferred_products": sum(
                1 for row in products if row.get("image_urls")
            ),
            "errors": [],
        }

        for offset in range(0, len(products), batch_size):
            source_batch = products[offset : offset + batch_size]
            # Core first: defer media until all cards exist.
            core_batch = []
            for row in source_batch:
                item = dict(row)
                item["image_urls"] = []
                core_batch.append(item)

            batch_manifest = dict(manifest)
            batch_manifest["items"] = core_batch
            result = sync_manifest(batch_manifest, config, kit=kit, wa=wa)

            batch_no = (offset // batch_size) + 1
            batch_report = {
                "batch": batch_no,
                "from": offset + 1,
                "to": offset + len(source_batch),
                "status": result.get("status"),
                "created": int(result.get("created") or 0),
                "updated": int(result.get("updated") or 0),
                "unchanged_variants": int(result.get("unchanged_variants") or 0),
                "webasyst_updated": int(result.get("webasyst_updated") or 0),
                "errors": list(result.get("errors") or []),
            }
            payload["batches"].append(batch_report)

            for key in (
                "created",
                "updated",
                "unchanged_variants",
                "categories_created",
                "characteristics_created",
                "webasyst_updated",
                "webasyst_kit_id_skipped",
            ):
                totals[key] += int(result.get(key) or 0)
            totals["errors"].extend(list(result.get("errors") or []))

            print(
                "KIT BATCH %d: %d-%d created=%d updated=%d errors=%d"
                % (
                    batch_no,
                    offset + 1,
                    offset + len(source_batch),
                    batch_report["created"],
                    batch_report["updated"],
                    len(batch_report["errors"]),
                ),
                flush=True,
            )

            if totals["created"] > args.max_create:
                raise RuntimeError(
                    "Created count %d exceeds limit %d"
                    % (totals["created"], args.max_create)
                )
            if result.get("status") != "ok" or batch_report["errors"]:
                payload["totals"] = totals
                payload["status"] = "partial_failure"
                _write(args.output, payload)
                raise SystemExit(2)

            # Keep an on-disk checkpoint after every successful batch.
            payload["totals"] = totals
            payload["status"] = "in_progress"
            _write(args.output, payload)

        payload["totals"] = totals
        payload["status"] = "ok"
        payload["media_phase"] = "deferred"
        _write(args.output, payload)

    except SystemExit:
        raise
    except Exception as exc:
        payload.setdefault("errors", []).append(str(exc))
        payload["status"] = "failed"
        _write(args.output, payload)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
