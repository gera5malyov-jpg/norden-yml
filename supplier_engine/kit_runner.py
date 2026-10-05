from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .bridge import MegasuppliersBridge
from .kit_sync import KitSyncError, plan_manifest, sync_manifest
from .runner import fetch_source, load_config, load_norden_source
from webasyst.client import WebasystClient


def _write(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _source_bytes(config):
    if config.get("source", {}).get("format", "").lower() == "norden":
        _rows, source_data, _meta = load_norden_source()
        return source_data
    return fetch_source(config)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--mode", choices=("dry-run", "apply"), default="apply")
    ap.add_argument("--supplier-id", type=int, required=True)
    ap.add_argument("--request-id", required=True)
    ap.add_argument("--approved-source-sha256", default="")
    ap.add_argument("--output", default="kit-run.json")
    args = ap.parse_args()

    payload = {
        "status": "failed",
        "mode": args.mode,
        "supplier_id": args.supplier_id,
        "request_id": args.request_id,
    }
    bridge = None
    try:
        config = load_config(args.config)
        if not (config.get("rules") or {}).get("export_to_kit", False):
            payload = {
                "status": "disabled",
                "mode": args.mode,
                "supplier_id": args.supplier_id,
                "request_id": args.request_id,
            }
            _write(args.output, payload)
            return

        source_sha = ""
        if args.mode == "apply":
            approved = args.approved_source_sha256.strip().lower()
            if not approved:
                raise KitSyncError("approved_source_sha256 is required for KIT apply")
            source_data = _source_bytes(config)
            source_sha = hashlib.sha256(source_data).hexdigest()
            if source_sha.lower() != approved:
                raise KitSyncError("Источник изменился после Apply; выгрузка в KIT заблокирована.")

        stock_id = (config.get("webasyst") or {}).get("stock_id")
        if not stock_id:
            raise KitSyncError("Для выгрузки в KIT не задан склад Webasyst.")

        bridge = MegasuppliersBridge()
        manifest = bridge.kit_manifest(
            args.supplier_id,
            args.request_id,
            stock_id,
        )

        if args.mode == "dry-run":
            result = plan_manifest(manifest, config)
        else:
            result = sync_manifest(
                manifest,
                config,
                wa=WebasystClient(),
            )

        payload = {
            "status": result.get("status") or "ok",
            "mode": args.mode,
            "supplier_id": args.supplier_id,
            "request_id": args.request_id,
            "source_sha256": source_sha,
            "manifest": {
                "products": len(manifest.get("items") or []),
                "categories": len(manifest.get("categories") or []),
            },
            "result": result,
        }
    except KitSyncError as exc:
        payload = {
            "status": "failed",
            "mode": args.mode,
            "supplier_id": args.supplier_id,
            "request_id": args.request_id,
            "errors": [str(exc)],
            "result": exc.report or {},
        }
    except Exception as exc:
        payload = {
            "status": "failed",
            "mode": args.mode,
            "supplier_id": args.supplier_id,
            "request_id": args.request_id,
            "errors": [str(exc)],
        }

    try:
        bridge = bridge or MegasuppliersBridge()
        bridge.set_kit_result(args.supplier_id, args.request_id, payload)
    except Exception as exc:
        payload.setdefault("warnings", []).append(
            "Не удалось записать статус KIT в Webasyst: %s" % exc
        )

    _write(args.output, payload)
    if payload.get("status") not in ("ok", "disabled"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
