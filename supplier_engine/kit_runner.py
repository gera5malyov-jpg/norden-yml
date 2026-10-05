from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .bridge import MegasuppliersBridge
from .kit_export import KitExportError, sync_supplier_to_kit
from .runner import fetch_source, load_config, load_norden_source, normalize, parse_source
from webasyst.client import WebasystClient


def _write(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--supplier-id", type=int, required=True)
    ap.add_argument("--request-id", required=True)
    ap.add_argument("--approved-source-sha256", required=True)
    ap.add_argument("--output", default="kit-run.json")
    args = ap.parse_args()

    payload = {"status": "failed", "supplier_id": args.supplier_id, "request_id": args.request_id}
    bridge = None
    try:
        config = load_config(args.config)
        if not (config.get("rules") or {}).get("export_to_kit", False):
            payload = {"status": "disabled", "supplier_id": args.supplier_id, "request_id": args.request_id}
            _write(args.output, payload)
            return

        if config.get("source", {}).get("format", "").lower() == "norden":
            rows, source_data, source_meta = load_norden_source()
        else:
            source_data = fetch_source(config)
            rows = parse_source(config, source_data)
            source_meta = None

        source_sha = hashlib.sha256(source_data).hexdigest()
        if source_sha.lower() != args.approved_source_sha256.strip().lower():
            raise KitExportError("Источник изменился после Apply; выгрузка в KIT заблокирована.")

        products = normalize(config, rows)
        wa = WebasystClient()
        result = sync_supplier_to_kit(wa, products, config)
        payload = {
            "status": "ok",
            "supplier_id": args.supplier_id,
            "request_id": args.request_id,
            "source_sha256": source_sha,
            "source": source_meta or {},
            "result": result,
        }
    except KitExportError as exc:
        payload = {
            "status": "partial_failure" if exc.result else "failed",
            "supplier_id": args.supplier_id,
            "request_id": args.request_id,
            "errors": [str(exc)],
            "result": exc.result or {},
        }
    except Exception as exc:
        payload = {
            "status": "failed",
            "supplier_id": args.supplier_id,
            "request_id": args.request_id,
            "errors": [str(exc)],
        }

    try:
        bridge = MegasuppliersBridge()
        bridge.set_kit_result(args.supplier_id, args.request_id, payload)
    except Exception as exc:
        payload.setdefault("warnings", []).append("Не удалось записать статус KIT в Webasyst: %s" % exc)

    _write(args.output, payload)
    if payload.get("status") not in ("ok", "disabled"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
