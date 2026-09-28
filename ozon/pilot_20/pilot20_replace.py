import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ozon.replace_images_3x4_batch as core

SEL = Path("ozon/pilot_20/selection.json")
OUT = Path("ozon/pilot_20/run")

data = json.loads(SEL.read_text(encoding="utf-8"))
offers = [x["offer_id"] for x in data.get("selected", []) if x.get("offer_id")]
if len(offers) != 20:
    raise SystemExit(f"Expected exactly 20 selected offers, got {len(offers)}")

core.SKUS = offers
core.ROOT = OUT
core.MANIFEST = OUT / "manifest.json"
core.BATCH_REPORT = OUT / "batch_report.json"

stage = sys.argv[1] if len(sys.argv) > 1 else ""
if stage == "prepare":
    core.prepare()
elif stage == "apply":
    core.apply()
elif stage == "wait-materialized":
    core.wait_materialized()
else:
    raise SystemExit("Usage: pilot20_replace.py prepare|apply|wait-materialized")
