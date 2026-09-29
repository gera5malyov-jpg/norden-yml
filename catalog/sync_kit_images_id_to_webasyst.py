#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "catalog" / "kit_images_id_to_webasyst_report.json"
PILOT_ARTICLE = os.environ.get("PILOT_ARTICLE", "AF-31662421").strip()
BRAND = "Norden"
TYPE_CANDIDATES = ("Norden", "NORDEN-100")

sys.path.insert(0, str(ROOT / "webasyst"))
from client import WebasystClient

def load(path, name):
    p = ROOT / path
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

BRIDGE = load(Path("norden-kit") / "webasyst_n100_to_kit_once.py", "kit_bridge")

def s(v):
    return str(v or "").strip()

def norm(v):
    return re.sub(r"\s+", " ", s(v)).casefold().strip()

def now():
    return datetime.now(timezone.utc).isoformat()

def listify(payload, keys=()):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        v = payload.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            return [x for x in v.values() if isinstance(x, dict)]
    if payload and all(isinstance(v, dict) for v in payload.values()):
        return list(payload.values())
    return []

def skus(product):
    rows = product.get("skus") or {}
    if isinstance(rows, dict):
        return [x for x in rows.values() if isinstance(x, dict)]
    if isinstance(rows, list):
        return [x for x in rows if isinstance(x, dict)]
    return []

def extimgs(urls):
    return "\n".join(f"[extimg]\n{u}\n[/extimg]" for u in urls if s(u))

def resolve_type(wa):
    types = listify(wa.call("shop.type.getList"))
    for wanted in TYPE_CANDIDATES:
        rows = [x for x in types if norm(x.get("name") or x.get("title")) == norm(wanted)]
        if len(rows) == 1:
            return s(rows[0].get("id")), s(rows[0].get("name") or rows[0].get("title"))
        if len(rows) > 1:
            raise RuntimeError(f"Webasyst type {wanted!r}: duplicate matches={len(rows)}")
    available = [s(x.get("name") or x.get("title")) for x in types]
    raise RuntimeError(f"Webasyst type Norden/NORDEN-100 not found; available sample={available[:30]}")

def load_wa_products(wa, type_id):
    out = []
    offset = 0
    while True:
        payload = wa.call(
            "shop.product.search",
            params={
                "hash": f"type/{type_id}",
                "offset": offset,
                "limit": 1000,
                "fields": "*,skus,stock_counts",
            },
        )
        batch = listify(payload, ("products", "items"))
        out.extend(batch)
        total = None
        if isinstance(payload, dict):
            total = payload.get("count") or payload.get("total_count")
        if not batch or len(batch) < 1000:
            break
        if total not in (None, "") and len(out) >= int(total):
            break
        offset += len(batch)
    return out

def scan_kit_norden(kit):
    by_sku = defaultdict(list)
    total = 0
    norden = 0
    for row in kit.scan_all_variants_parallel(workers=10):
        total += 1
        if norm(row.get("brand")) != norm(BRAND):
            continue
        sku = s(row.get("sku"))
        if not sku:
            continue
        by_sku[sku].append(row)
        norden += 1
    return by_sku, total, norden

def full_variant(kit, row):
    vid = s(row.get("id"))
    if not vid:
        raise RuntimeError("KIT variant id missing")
    return kit.request("GET", f"/v1/variants/{vid}")

def kit_public_image_urls(kit, variant, file_cache):
    media = [
        m for m in (variant.get("media") or [])
        if isinstance(m, dict)
        and s(m.get("type")).upper() == "IMAGE"
        and s(m.get("image_id"))
    ]
    def seq(m):
        try:
            return int(m.get("display_sequence") or 0)
        except Exception:
            return 0
    media.sort(key=seq)
    urls = []
    for m in media:
        fid = s(m.get("image_id"))
        if fid not in file_cache:
            meta = kit.request("GET", f"/v1/files/{fid}")
            file_cache[fid] = s(meta.get("url")) if isinstance(meta, dict) else ""
        u = file_cache[fid]
        if u and u not in urls:
            urls.append(u)
    return urls

def get_numeric_kit_id(variant):
    kid = s(variant.get("kit_id"))
    if not kid or not kid.isdigit():
        raise RuntimeError(f"numeric KIT kit_id missing: {kid!r}")
    return kid

def product_articles(product):
    return list(dict.fromkeys(s(x.get("sku")) for x in skus(product) if s(x.get("sku"))))

def choose_match(product, kit_by_sku):
    articles = product_articles(product)
    hits = []
    for article in articles:
        rows = kit_by_sku.get(article) or []
        if len(rows) > 1:
            raise RuntimeError(f"multiple KIT Norden variants for exact article {article}: {[s(x.get('id')) for x in rows]}")
        if len(rows) == 1:
            hits.append((article, rows[0]))
    uniq = {}
    for article, row in hits:
        uniq[s(row.get("id"))] = (article, row)
    if len(uniq) > 1:
        raise RuntimeError(f"Webasyst product has multiple different KIT matches: {[(a,s(r.get('id'))) for a,r in uniq.values()]}")
    if not uniq:
        return None, None
    return next(iter(uniq.values()))

def verify_unrelated_features(before, after):
    bf = before.get("features") or {} if isinstance(before, dict) else {}
    af = after.get("features") or {} if isinstance(after, dict) else {}
    if isinstance(bf, dict) and isinstance(af, dict):
        lost = [
            k for k, v in bf.items()
            if k != "kit_id" and v not in (None, "", [])
            and af.get(k) in (None, "", [])
        ]
        if lost:
            raise RuntimeError("unrelated Webasyst features lost: " + ", ".join(lost[:20]))

def sync_product(wa, kit, product, article, kit_row, file_cache):
    pid = s(product.get("id"))
    if not pid:
        raise RuntimeError("Webasyst product id missing")
    full = full_variant(kit, kit_row)
    if norm(full.get("brand")) != norm(BRAND):
        raise RuntimeError(f"KIT brand mismatch: {full.get('brand')!r}")
    if s(full.get("sku")) != article:
        raise RuntimeError(f"KIT SKU mismatch: expected {article}, got {full.get('sku')!r}")

    kid = get_numeric_kit_id(full)
    urls = kit_public_image_urls(kit, full, file_cache)
    summary = extimgs(urls)

    before = wa.call("shop.product.getInfo", params={"id": pid})
    before_features = before.get("features") or {} if isinstance(before, dict) else {}
    old_kid = s(before_features.get("kit_id")) if isinstance(before_features, dict) else ""
    old_summary = s(before.get("summary")) if isinstance(before, dict) else ""

    data = {"features": {"kit_id": kid}}
    if summary:
        data["summary"] = summary
    wa.call("shop.product.update", http_method="POST", params={"id": pid}, data=data)

    after = wa.call("shop.product.getInfo", params={"id": pid})
    af = after.get("features") or {} if isinstance(after, dict) else {}
    got_kid = s(af.get("kit_id")) if isinstance(af, dict) else ""
    if got_kid != kid:
        raise RuntimeError(f"KIT ID readback mismatch: expected {kid}, got {got_kid!r}")
    if summary:
        got_summary = s(after.get("summary"))
        if got_summary.replace("\r\n", "\n").strip() != summary.replace("\r\n", "\n").strip():
            raise RuntimeError("summary extimg readback mismatch")
    verify_unrelated_features(before, after)

    return {
        "article": article,
        "webasyst_product_id": pid,
        "kit_variant_uuid": s(full.get("id")),
        "kit_id": kid,
        "images": len(urls),
        "kit_id_changed": old_kid != kid,
        "summary_changed": bool(summary) and old_summary.replace("\r\n", "\n").strip() != summary.replace("\r\n", "\n").strip(),
        "no_kit_images": not bool(urls),
    }

def main():
    wa = WebasystClient(min_request_interval=0.45)
    kit = BRIDGE.KitClient()
    type_id, type_name = resolve_type(wa)

    report = {
        "started_at": now(),
        "status": "ВЫПОЛНЯЕТСЯ",
        "brand": BRAND,
        "webasyst_type_id": type_id,
        "webasyst_type_name": type_name,
        "pilot_article": PILOT_ARTICLE,
        "webasyst_products": 0,
        "kit_variants_scanned": 0,
        "kit_norden_variants": 0,
        "matched": 0,
        "updated": 0,
        "kit_id_changed": 0,
        "summary_changed": 0,
        "unchanged": 0,
        "missing_in_kit": 0,
        "missing_images": 0,
        "ambiguous": 0,
        "errors": [],
        "items": [],
    }

    products = load_wa_products(wa, type_id)
    report["webasyst_products"] = len(products)
    kit_by_sku, total_kit, norden_kit = scan_kit_norden(kit)
    report["kit_variants_scanned"] = total_kit
    report["kit_norden_variants"] = norden_kit

    by_article_wa = defaultdict(list)
    for p in products:
        for a in product_articles(p):
            by_article_wa[a].append(p)

    # Canary: exact user-provided example must be resolvable before mass write.
    pilot_products = by_article_wa.get(PILOT_ARTICLE) or []
    if not pilot_products:
        raise RuntimeError(f"Pilot {PILOT_ARTICLE}: no Webasyst product in type {type_name}")
    pilot_rows = kit_by_sku.get(PILOT_ARTICLE) or []
    if len(pilot_rows) != 1:
        raise RuntimeError(f"Pilot {PILOT_ARTICLE}: expected 1 KIT Norden match, found {len(pilot_rows)}")

    file_cache = {}
    done_products = set()
    pilot_result = sync_product(wa, kit, pilot_products[0], PILOT_ARTICLE, pilot_rows[0], file_cache)
    report["pilot_result"] = pilot_result
    done_products.add(s(pilot_products[0].get("id")))

    for idx, product in enumerate(products, 1):
        pid = s(product.get("id"))
        if pid in done_products:
            result = pilot_result
            report["matched"] += 1
            report["updated"] += 1
            report["kit_id_changed"] += int(result["kit_id_changed"])
            report["summary_changed"] += int(result["summary_changed"])
            report["missing_images"] += int(result["no_kit_images"])
            if not result["kit_id_changed"] and not result["summary_changed"]:
                report["unchanged"] += 1
            report["items"].append(result)
            continue
        try:
            article, row = choose_match(product, kit_by_sku)
            if row is None:
                report["missing_in_kit"] += 1
                if len(report["errors"]) < 500:
                    report["errors"].append({
                        "webasyst_product_id": pid,
                        "articles": product_articles(product),
                        "stage": "match",
                        "error": "No exact KIT Norden article match",
                    })
                continue
            report["matched"] += 1
            result = sync_product(wa, kit, product, article, row, file_cache)
            report["updated"] += 1
            report["kit_id_changed"] += int(result["kit_id_changed"])
            report["summary_changed"] += int(result["summary_changed"])
            report["missing_images"] += int(result["no_kit_images"])
            if not result["kit_id_changed"] and not result["summary_changed"]:
                report["unchanged"] += 1
            report["items"].append(result)
        except Exception as exc:
            msg = str(exc)
            if "multiple KIT" in msg or "multiple different KIT" in msg:
                report["ambiguous"] += 1
            report["errors"].append({
                "webasyst_product_id": pid,
                "articles": product_articles(product),
                "stage": "sync",
                "error": msg[:1500],
            })

        if idx % 25 == 0:
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({
                "processed": idx,
                "total": len(products),
                "matched": report["matched"],
                "updated": report["updated"],
                "missing_in_kit": report["missing_in_kit"],
                "errors": len(report["errors"]),
            }, ensure_ascii=False), flush=True)

    report["finished_at"] = now()
    # Missing KIT matches mean the requested all-products scope is incomplete, but all resolvable products were processed.
    report["complete"] = report["missing_in_kit"] == 0 and report["ambiguous"] == 0 and not [
        e for e in report["errors"] if e.get("stage") == "sync"
    ]
    report["status"] = "УСПЕШНО" if report["complete"] else "ЗАВЕРШЕНО С ОШИБКАМИ"
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "webasyst_type": type_name,
        "webasyst_products": report["webasyst_products"],
        "kit_norden_variants": report["kit_norden_variants"],
        "matched": report["matched"],
        "updated": report["updated"],
        "kit_id_changed": report["kit_id_changed"],
        "summary_changed": report["summary_changed"],
        "missing_in_kit": report["missing_in_kit"],
        "missing_images": report["missing_images"],
        "ambiguous": report["ambiguous"],
        "errors": len(report["errors"]),
    }, ensure_ascii=False))
    return 0 if report["complete"] else 2

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        failure = {
            "started_at": now(),
            "finished_at": now(),
            "status": "ОШИБКА",
            "complete": False,
            "fatal_error": str(exc),
        }
        REPORT.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False))
        raise
