#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deephouse-kit"))
from sync_deephouse_kit import KitClient, s

kit = KitClient(os.environ["YANDEX_KIT_TOKEN"])
rows = kit.list_all("/v1/variants", {"name": "RED-"}, "variants")
red = [v for v in rows if s(v.get("sku")).upper().startswith("RED-")]
media = []
for v in red:
    imgs = [m for m in (v.get("media") or []) if isinstance(m, dict) and s(m.get("type")).upper() == "IMAGE"]
    media.append(len(imgs))
print(json.dumps({
    "query_rows": len(rows),
    "red_variants": len(red),
    "with_images": sum(1 for n in media if n),
    "without_images": sum(1 for n in media if not n),
    "total_images": sum(media),
    "max_images": max(media) if media else 0,
    "avg_images": (sum(media)/len(media)) if media else 0,
}, ensure_ascii=False, indent=2))

print("\nFILES_PROBE")
for params in ({"page":1,"per_page":3}, {"page":1,"per_page":100}):
    try:
        payload = kit.request("GET", "/v1/files", params=params)
        print(json.dumps({
            "params": params,
            "keys": list(payload.keys()) if isinstance(payload, dict) else None,
            "total_count": payload.get("total_count") if isinstance(payload, dict) else None,
            "total": payload.get("total") if isinstance(payload, dict) else None,
            "sample": (payload.get("files") or [])[:3] if isinstance(payload, dict) else None,
        }, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"params":params,"error":str(exc)},ensure_ascii=False))
