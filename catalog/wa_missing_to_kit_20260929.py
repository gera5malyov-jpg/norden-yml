#!/usr/bin/env python3
from __future__ import annotations
import json, runpy, shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"norden-kit"/"webasyst_n100_to_kit_once.py"
SRC_REPORT=ROOT/"norden-kit"/"webasyst_n100_to_kit_import_report.json"
OUT=ROOT/"catalog"/"wa_missing_to_kit_report.json"

code=0
try:
    runpy.run_path(str(SRC), run_name="__main__")
except SystemExit as exc:
    try:
        code=int(exc.code or 0)
    except Exception:
        code=1
finally:
    if SRC_REPORT.exists():
        shutil.copyfile(SRC_REPORT, OUT)
    elif not OUT.exists():
        OUT.write_text(json.dumps({"status":"ОШИБКА","complete":False,"error":"source report missing"},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
raise SystemExit(code)
