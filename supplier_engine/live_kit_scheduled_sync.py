from __future__ import annotations

import argparse
import json
from pathlib import Path

from .kit_sync import KitClient, plan_manifest, sync_manifest
from .live_kit_readonly_preflight import _load_helpers, build_manifest


def _write(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type-name", default="NORDEN-100")
    ap.add_argument("--brand", default="Norden")
    ap.add_argument("--mode", choices=("dry-run", "apply"), default="apply")
    ap.add_argument("--max-create", type=int, default=100)
    ap.add_argument("--max-eligible", type=int, default=5000)
    ap.add_argument("--output", default="scheduled-kit-sync.json")
    args = ap.parse_args()

    helpers = _load_helpers()
    wa = helpers.WebasystClient()
    kit = KitClient()
    config = {
        "identity": {"brand": args.brand},
        "rules": {"export_to_kit": True},
    }

    payload = {
        "status": "failed",
        "mode": args.mode,
        "type_name": args.type_name,
        "brand": args.brand,
    }

    try:
        manifest = build_manifest(wa, helpers, args.type_name)
        preflight = plan_manifest(manifest, config, kit=kit)
        payload["source"] = {
            "type_id": manifest["type_id"],
            "type_name": manifest["type_name"],
            "webasyst_products": manifest["webasyst_products"],
            "manifest_items": len(manifest["items"]),
            "skipped_no_supplier_article": len(manifest["skipped_no_supplier_article"]),
        }
        payload["preflight"] = preflight

        errors = list(preflight.get("errors") or [])
        identity = preflight.get("preflight") or {}
        eligible = int(preflight.get("eligible") or 0)
        would_create = int(preflight.get("would_create") or 0)
        would_update = int(preflight.get("would_update") or 0)

        if preflight.get("status") != "ok":
            errors.append({"error": "KIT preflight status is not ok"})
        if int(identity.get("conflicts") or 0) != 0:
            errors.append({"error": "KIT identity conflicts detected"})
        if eligible <= 0:
            errors.append({"error": "No categorized Webasyst products are eligible"})
        if eligible > args.max_eligible:
            errors.append({
                "error": "Eligible product count exceeds safety limit",
                "eligible": eligible,
                "max_eligible": args.max_eligible,
            })
        if would_create > args.max_create:
            errors.append({
                "error": "Planned KIT creates exceed safety limit",
                "would_create": would_create,
                "max_create": args.max_create,
            })
        if would_create + would_update != eligible:
            errors.append({
                "error": "Preflight identity accounting mismatch",
                "eligible": eligible,
                "would_create": would_create,
                "would_update": would_update,
            })

        if errors:
            payload["status"] = "blocked"
            payload["errors"] = errors
            _write(args.output, payload)
            raise SystemExit(2)

        if args.mode == "dry-run":
            payload["status"] = "ok"
            payload["readonly"] = True
            _write(args.output, payload)
            return

        result = sync_manifest(manifest, config, kit=kit, wa=wa)
        payload["apply"] = result
        payload["status"] = result.get("status") or "failed"
        if payload["status"] != "ok" or result.get("errors"):
            _write(args.output, payload)
            raise SystemExit(2)

        _write(args.output, payload)
    except SystemExit:
        raise
    except Exception as exc:
        payload["status"] = "failed"
        payload.setdefault("errors", []).append(str(exc))
        _write(args.output, payload)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
