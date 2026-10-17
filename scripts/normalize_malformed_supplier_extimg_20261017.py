#!/usr/bin/env python3
"""Normalize malformed URL-only external image summaries for selected Shop-Script types.

Only products lacking native images and lacking a parsed external-image gallery.
No content text replacement, no stock/price/SKU changes, no image downloads.
Requires an archived backup and exact optimistic preflight before writes.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from webasyst.client import WebasystClient
from scripts.refresh_selected_product_external_images import (
    listify,products_for_type,summary_urls,extract_urls
)
from supplier_engine.webasyst_sync import extimg_summary

TARGETS={
 "deephouse-336":{"type_id":24,"hosts":{"deephouse.pro","www.deephouse.pro"}},
 "bradexhome.ru":{"type_id":161,"hosts":{"bradexhome.ru","www.bradexhome.ru"}},
 "championtool.ru":{"type_id":195,"hosts":{"championtool.ru","www.championtool.ru"}},
}
REPORT=ROOT/"reports/malformed_external_photo_links_20261017.json"
PLAN=ROOT/"reports/malformed_external_photo_links_20261017_plan.json.gz"
BACKUP=ROOT/"reports/malformed_external_photo_links_20261017_backup.json.gz"
TAG=re.compile(r"\[/?extimg\]",re.I)
URL=re.compile(r"https?://[^\s<>\[\]\"'\u201c\u201d]+",re.I)
IMAGE=re.compile(r"\.(?:jpe?g|png|webp)(?:$|[?#])",re.I)

def photos_from_url_only(summary,hosts):
    raw=html.unescape(str(summary or "")).replace("\\n","\n").strip()
    if not raw:return [],"empty"
    cleaned=TAG.sub("",raw)
    # All non-URL text must be whitespace: preserve any genuinely authored prose.
    remainder=URL.sub("",cleaned).strip()
    if remainder:return [],"protected_prose"
    pics=[];bad=0
    for val in extract_urls(raw):
        parts=urlsplit(val)
        if parts.scheme!="https" or parts.hostname not in hosts or not IMAGE.search(parts.path):
            bad+=1;continue
        if val not in pics:pics.append(val)
    if bad:return [],"unsafe_or_nonimage_links"
    return pics[:65],("eligible" if pics else "no_valid_photo_url")

def fetch_targets(wa):
    types=listify(wa.call("shop.type.getList"),keys=("types","items"))
    data={}
    for name,spec in TARGETS.items():
        found=[t for t in types if str(t.get("name") or "").casefold()==name.casefold()]
        if len(found)!=1 or int(found[0]["id"])!=spec["type_id"]:
            raise RuntimeError("Unsafe Webasyst type identity for "+name)
        data[name]=products_for_type(wa,spec["type_id"])
    return data

def prepare():
    wa=WebasystClient(min_request_interval=.15)
    data=fetch_targets(wa);changes=[];before=[];summary={}
    for name,products in data.items():
        counts=collections.Counter()
        for p in products:
            counts["total"]+=1
            native=bool(p.get("image_id")) or bool(p.get("images"))
            urls,_=summary_urls(str(p.get("summary") or ""))
            if native or urls:
                counts["already_has_gallery_or_native"]+=1
                continue
            raw=str(p.get("summary") or "")
            pics,status=photos_from_url_only(raw,TARGETS[name]["hosts"])
            counts[status]+=1
            if not pics:continue
            new=extimg_summary(pics)
            if not new or new.count("[extimg]")!=len(pics):
                raise RuntimeError("Invalid rendered shortcodes for "+str(p["id"]))
            if new==raw.strip():
                counts["already_normalized"]+=1
                continue
            row={"id":int(p["id"]),"type_id":TARGETS[name]["type_id"],
                "type_name":name,"name":str(p.get("name") or ""),
                "old":raw,"new":new,"pictures":len(pics)}
            before.append({k:row[k] for k in ("id","type_id","type_name","name","old")})
            changes.append(row)
            counts["planned"]+=1
        summary[name]=dict(counts)
        print("MALFORMED_PHOTO_TYPE",json.dumps({"name":name,**dict(counts)},ensure_ascii=False),flush=True)
    if len(changes)>1300:raise RuntimeError("Unexpectedly large normalization plan")
    PLAN.parent.mkdir(exist_ok=True,parents=True)
    manifest={"mode":"preview","planned":len(changes),"by_type":summary,"written":0,"verified":0}
    with gzip.open(BACKUP,"wt",encoding="utf-8",compresslevel=6) as f:
        json.dump({"backup":before,"metadata":manifest},f,ensure_ascii=False)
    with gzip.open(PLAN,"wt",encoding="utf-8",compresslevel=6) as f:
        json.dump({"changes":changes,"metadata":manifest},f,ensure_ascii=False)
    REPORT.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print("MALFORMED_PHOTO_BACKUP",json.dumps({"count":len(changes),"size_bytes":BACKUP.stat().st_size,
        "backup":str(BACKUP.relative_to(ROOT))},ensure_ascii=False),flush=True)

def apply():
    if not BACKUP.is_file() or not PLAN.is_file():raise RuntimeError("Backup/plan missing")
    with gzip.open(BACKUP,"rt",encoding="utf-8") as f:back=json.load(f)
    with gzip.open(PLAN,"rt",encoding="utf-8") as f:plan=json.load(f)
    changes=plan["changes"]
    if len(changes)!=plan["metadata"]["planned"] or len(changes)!=len(back["backup"]):
        raise RuntimeError("Plan count differs from backup")
    originals={x["id"]:x for x in back["backup"]}
    wa=WebasystClient(min_request_interval=.15)
    data=fetch_targets(wa)
    current={int(p["id"]):p for products in data.values() for p in products}
    candidates=[];skipped=[]
    for c in changes:
        before=originals.get(c["id"])
        if not before or before["old"]!=c["old"] or before["type_id"]!=c["type_id"]:
            raise RuntimeError("Backed-up data inconsistent")
        p=current.get(c["id"])
        if not p or int(p.get("type_id") or 0)!=c["type_id"] or str(p.get("name") or "")!=c["name"] or str(p.get("summary") or "")!=c["old"]:
            skipped.append(c["id"]);continue
        if p.get("image_id") or p.get("images"):
            skipped.append(c["id"]);continue
        pics,state=photos_from_url_only(c["old"],TARGETS[c["type_name"]]["hosts"])
        if not pics or state!="eligible" or extimg_summary(pics)!=c["new"]:
            raise RuntimeError("Preflight safety condition failed for "+str(c["id"]))
        candidates.append(c)
    print("MALFORMED_PHOTO_PREFLIGHT",json.dumps({"eligible":len(candidates),"skipped":len(skipped)}),flush=True)
    errors=[];updated=0
    for i,c in enumerate(candidates,1):
        try:
            wa.call("shop.product.update",http_method="POST",params={"id":str(c["id"])},
                data={"summary":c["new"]})
            updated+=1
        except Exception as e:
            errors.append({"id":c["id"],"error":str(e)[:180]})
            if len(errors)>=5:break
        if i%80==0 or i==len(candidates):
            print("MALFORMED_PHOTO_PROGRESS",json.dumps({"done":i,"updated":updated,"errors":len(errors)}),flush=True)
    reread=fetch_targets(wa)
    actual={int(p["id"]):str(p.get("summary") or "") for products in reread.values() for p in products}
    verified=sum(actual.get(c["id"])==c["new"] for c in candidates)
    mismatches=[c["id"] for c in candidates if actual.get(c["id"])!=c["new"]]
    result={**plan["metadata"],"mode":"complete" if not errors and verified==len(candidates) else "partial",
        "eligible":len(candidates),"skipped":len(skipped),"written":updated,"verified":verified,
        "errors":errors,"mismatches":mismatches[:100],"backup":str(BACKUP.relative_to(ROOT))}
    REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("MALFORMED_PHOTO_RESULT",json.dumps(result,ensure_ascii=False),flush=True)
    if result["mode"]!="complete":raise RuntimeError("Partial image normalization: inspect report")

if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--apply",action="store_true")
    args=cli.parse_args()
    if args.apply:apply()
    else:prepare()
