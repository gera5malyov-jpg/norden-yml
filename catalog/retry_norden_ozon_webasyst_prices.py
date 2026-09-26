#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"catalog"/"push_norden_prices_to_channels.py"
REPORT=ROOT/"catalog"/"norden_price_retry_ozon_webasyst_report.json"

def now():
    return datetime.now(timezone.utc).isoformat()

spec=importlib.util.spec_from_file_location("norden_price_all", BASE)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load {BASE}")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def main():
    rep={
        "started_at":now(),
        "source":{"spreadsheet_id":mod.SHEET_ID,"sheet":mod.SHEET_NAME},
        "Ozon":{"status":"НЕ ЗАПУЩЕНО","errors":[],"warnings":[]},
        "Webasyst":{"status":"НЕ ЗАПУЩЕНО","errors":[],"warnings":[]},
    }
    try:
        rows=mod.load_sheet()
        rep["catalog_rows"]=len(rows)
        wrapper={"channels":{"Ozon":rep["Ozon"],"Webasyst":rep["Webasyst"]}}
        mod.sync_webasyst(rows,wrapper)
        mod.sync_ozon(rows,wrapper)
        rep["Ozon"]=wrapper["channels"]["Ozon"]
        rep["Webasyst"]=wrapper["channels"]["Webasyst"]
        rep["status"]="УСПЕШНО" if all(rep[x].get("status")=="УСПЕШНО" for x in ("Ozon","Webasyst")) else "ЗАВЕРШЕНО С ОШИБКАМИ"
    except Exception as e:
        rep["status"]="ОШИБКА"
        rep["fatal_error"]=str(e)[:2500]
    rep["finished_at"]=now()
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(rep,ensure_ascii=False,indent=2))
    raise SystemExit(0 if rep["status"]=="УСПЕШНО" else 1)

if __name__=="__main__":
    main()
