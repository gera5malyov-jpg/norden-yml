import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import requests
import ozon.replace_images_3x4_batch as core

ROOT = Path("ozon/catalog_3x4")
SELECTION = ROOT / "selection.json"
BATCH_SIZE = 20

CLIENT_ID = os.environ.get("OZON_CLIENT_ID", "").strip()
API_KEY = os.environ.get("OZON_API_KEY", "").strip()
if not CLIENT_ID or not API_KEY:
    raise SystemExit("Missing OZON_CLIENT_ID/OZON_API_KEY")

api = requests.Session()
api.headers.update({
    "Client-Id": CLIENT_ID,
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "Megapolis-Ozon-Catalog-3x4/1.0",
})

def post(path, body):
    r = api.post("https://api-seller.ozon.ru" + path, json=body, timeout=90)
    try:
        data = r.json() if r.content else {}
    except Exception:
        data = {"raw": r.text}
    if r.status_code >= 400:
        raise RuntimeError(f"Ozon HTTP {r.status_code} {path}: {json.dumps(data, ensure_ascii=False)[:4000]}")
    return data

def current_positive_stock(offers):
    if not offers:
        return {}
    data = post("/v4/product/info/stocks", {
        "filter": {"offer_id": offers, "visibility": "ALL"},
        "limit": 100,
    })
    rows = data.get("items") or (data.get("result") or {}).get("items") or []
    out = {}
    for x in rows:
        offer = x.get("offer_id")
        present = sum(int(s.get("present") or 0) for s in (x.get("stocks") or []))
        reserved = sum(int(s.get("reserved") or 0) for s in (x.get("stocks") or []))
        if offer:
            out[offer] = {"present": present, "reserved": reserved}
    return out

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def load_batch(batch_index):
    data = json.loads(SELECTION.read_text(encoding="utf-8"))
    items = data.get("selected") or []
    start = batch_index * BATCH_SIZE
    return items[start:start+BATCH_SIZE]

def configure_core(batch_index, offers):
    batch_root = ROOT / "run" / f"batch_{batch_index:04d}"
    core.SKUS = offers
    core.ROOT = batch_root
    core.MANIFEST = batch_root / "manifest.json"
    core.BATCH_REPORT = batch_root / "batch_report.json"
    return batch_root

def prepare(batch_index):
    requested = load_batch(batch_index)
    requested_offers = [x["offer_id"] for x in requested if x.get("offer_id")]
    stocks = current_positive_stock(requested_offers)
    active = [o for o in requested_offers if stocks.get(o, {}).get("present", 0) > 0]
    skipped = [o for o in requested_offers if o not in active]
    batch_root = configure_core(batch_index, active)
    write_json(batch_root / "active.json", {
        "batch_index": batch_index,
        "requested": requested_offers,
        "active": active,
        "skipped_zero_stock_before_prepare": skipped,
        "stock_snapshot": stocks,
    })
    if active:
        core.prepare()
    else:
        write_json(core.MANIFEST, {"offers": {}, "repo": core.REPO, "branch": core.BRANCH})
    print(json.dumps({"batch": batch_index, "requested": requested_offers, "active": active, "skipped": skipped}, ensure_ascii=False))

def apply(batch_index):
    batch_root = ROOT / "run" / f"batch_{batch_index:04d}"
    state = json.loads((batch_root / "active.json").read_text(encoding="utf-8"))
    before = state.get("active") or []
    stocks = current_positive_stock(before)
    active = [o for o in before if stocks.get(o, {}).get("present", 0) > 0]
    newly_zero = [o for o in before if o not in active]
    state["active_for_apply"] = active
    state["skipped_zero_stock_before_apply"] = newly_zero
    state["apply_stock_snapshot"] = stocks
    write_json(batch_root / "active.json", state)
    configure_core(batch_index, active)
    if active:
        core.apply()
    else:
        write_json(core.BATCH_REPORT, {"offers": {}, "summary": {"requested": 0, "success": 0, "error": 0}})

def wait_materialized(batch_index):
    batch_root = ROOT / "run" / f"batch_{batch_index:04d}"
    state = json.loads((batch_root / "active.json").read_text(encoding="utf-8"))
    active = state.get("active_for_apply", state.get("active", []))
    configure_core(batch_index, active)
    if active:
        core.wait_materialized()
    else:
        write_json(batch_root / "materialization_report.json", {"status": "SUCCESS", "offers": {}})

def cdn_check(batch_index, attempts=6, delay=10):
    batch_root = ROOT / "run" / f"batch_{batch_index:04d}"
    state = json.loads((batch_root / "active.json").read_text(encoding="utf-8"))
    active = state.get("active_for_apply", state.get("active", []))
    last = {}
    if not active:
        out = {"status": "SUCCESS", "offers": {}}
        write_json(batch_root / "materialization_report.json", out)
        print(json.dumps(out, ensure_ascii=False))
        return

    import time
    for attempt in range(1, attempts + 1):
        all_ready = True
        last = {}
        for offer_id in active:
            data = post("/v3/product/info/list", {"offer_id": [offer_id]})
            items = data.get("items") or []
            if not items:
                last[offer_id] = {"error": "not_found"}
                all_ready = False
                continue
            item = items[0]
            urls = []
            for key in ("primary_image", "images"):
                val = item.get(key) or []
                if isinstance(val, str):
                    val = [val]
                for x in val:
                    if isinstance(x, dict):
                        x = x.get("url") or x.get("file_name") or x.get("src")
                    if isinstance(x, str) and x not in urls:
                        urls.append(x)
            raw = sum("raw.githubusercontent.com" in u for u in urls)
            ready = raw == 0
            last[offer_id] = {"count": len(urls), "raw_github_count": raw, "ready": ready}
            if not ready:
                all_ready = False
        if all_ready:
            out = {"status": "SUCCESS", "attempt": attempt, "offers": last}
            write_json(batch_root / "materialization_report.json", out)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return
        if attempt < attempts:
            time.sleep(delay)

    out = {"status": "PENDING", "attempt": attempts, "offers": last}
    write_json(batch_root / "materialization_report.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=2))

def compact(batch_index):
    batch_root = ROOT / "run" / f"batch_{batch_index:04d}"
    state = json.loads((batch_root / "active.json").read_text(encoding="utf-8"))
    report_path = batch_root / "batch_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {"offers": {}, "summary": {}}
    mat_path = batch_root / "materialization_report.json"
    mat = json.loads(mat_path.read_text(encoding="utf-8")) if mat_path.exists() else {}
    rows = []
    for offer_id, x in (report.get("offers") or {}).items():
        v = x.get("verification") or {}
        rows.append({
            "offer_id": offer_id,
            "result": x.get("result"),
            "source_image_count": x.get("source_image_count"),
            "converted_count": x.get("converted_count"),
            "already_3x4_count": x.get("already_3x4_count"),
            "final_image_count": x.get("final_image_count"),
            "count_correct": v.get("count_correct"),
            "order_preserved": v.get("order_preserved"),
            "primary_ok": v.get("primary_is_expected_first_image"),
            "all_images_3x4": v.get("all_images_3x4"),
            "ozon_error_count": len(v.get("ozon_errors") or []),
            "error": x.get("error"),
        })
    compact = {
        "batch_index": batch_index,
        "requested": state.get("requested") or [],
        "processed": state.get("active_for_apply", state.get("active", [])),
        "skipped_zero_stock": sorted(set((state.get("skipped_zero_stock_before_prepare") or []) + (state.get("skipped_zero_stock_before_apply") or []))),
        "summary": report.get("summary") or {},
        "materialization_status": mat.get("status"),
        "offers": rows,
    }
    write_json(batch_root / "summary.json", compact)
    print(json.dumps(compact, ensure_ascii=False, indent=2))

def main():
    if len(sys.argv) < 3:
        raise SystemExit("Usage: process_batch.py <prepare|apply|wait-materialized|cdn-check|compact> <batch_index>")
    stage = sys.argv[1]
    batch_index = int(sys.argv[2])
    if stage == "prepare":
        prepare(batch_index)
    elif stage == "apply":
        apply(batch_index)
    elif stage == "wait-materialized":
        wait_materialized(batch_index)
    elif stage == "cdn-check":
        cdn_check(batch_index)
    elif stage == "compact":
        compact(batch_index)
    else:
        raise SystemExit(f"Unknown stage: {stage}")

if __name__ == "__main__":
    main()
