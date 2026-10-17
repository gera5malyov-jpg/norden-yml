#!/usr/bin/env python3
"""Safe bulk external-gallery restoration for EXACT Webasyst types supplied 2026-10-17.
Only shop_product.summary, strict supplier feed/name matches. Two-phase durable backup.
"""
import argparse
import collections
import datetime
import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from webasyst.client import WebasystClient
from supplier_engine.webasyst_sync import extimg_summary
from scripts.refresh_selected_product_external_images import (
    listify, products_for_type, key, summary_urls, extract_urls
)

NAMES=[
 "1-Обычный товар","33 Кровати-333","4sis.ru","afinalux","ALAVANN",
 "aletan-provence","aletan-мрамор","aletan.ru","Arimpex","Arredo Classic",
 "art-zerkalo","Artikul-mebel","avalonmebel.ru","barcelonadesign","Belsi-home",
 "blago-mebel.ru","bobox.ru","Bogacho","bradexhome.ru","Camelgroup",
 "championtool.ru","College","Deccon.ru","decorumdesign","deephouse-336",
]
FEEDS={
 "33 Кровати-333":"4s-mebel.yml",
 "4sis.ru":"4s-mebel.yml",
 "ALAVANN":"alavann/alavann.yml",
 "aletan.ru":"aletan-yml/aletan.yml",
 "Artikul-mebel":"artikul-mebel/catalog.yml",
 "avalonmebel.ru":"avalon-podstolya/catalog.yml",
 "Belsi-home":"belsi-home/catalog.yml",
 "College":"college-chair/catalog-content.yml",
 "Deccon.ru":"deccon/catalog-content.yml",
}
REPORT=ROOT/"reports/bulk_screenshot_type_extimg_20261017_report.json"
PLAN=ROOT/"reports/bulk_screenshot_type_extimg_20261017_plan.json.gz"
BACKUP=ROOT/"reports/bulk_screenshot_type_extimg_20261017_backup.json.gz"
PHOTO_PREFIX="http://5.35.127.160/images/"
PHOTO_PROXY="https://profikompany.ru/4sis-images/"

def normalize_pics(pics):
    good=[]
    for src in pics:
        s=str(src or "").strip()
        if s.startswith(PHOTO_PREFIX):s=PHOTO_PROXY+s[len(PHOTO_PREFIX):]
        if not s.startswith("https://"):continue
        if s not in good and len(s)<=1600:good.append(s)
    return good[:65]

def load_feed(relative):
    path=ROOT/relative
    if not path.is_file() or path.stat().st_size<1000:
        raise RuntimeError("Missing required supplier feed: "+relative)
    by_id=collections.defaultdict(list)
    by_name=collections.defaultdict(list)
    count=0
    for event,node in ET.iterparse(path,events=("end",)):
        if node.tag.rsplit("}",1)[-1]!="offer":continue
        name=(node.findtext("name") or "").strip()
        oid=(node.get("id") or "").strip()
        pics=normalize_pics(extract_urls("\n".join(x.text or "" for x in node.findall("picture"))))
        if name and pics:
            record={"name":name,"oid":oid,"pics":pics,"code":(node.findtext("vendorCode") or "").strip()}
            by_name[key(name)].append(record)
            if oid:by_id[oid].append(record)
            count+=1
        node.clear()
    if count<20:raise RuntimeError("Suspicious supplier feed: "+relative+" offers="+str(count))
    return {"by_id":by_id,"by_name":by_name,"count":count}

def supplier_pics(source,product):
    if not source:return [],"no_feed"
    name=key(product.get("name"))
    oid=str(product.get("yml_id") or "").strip()
    exact=source["by_name"].get(name,[])
    unique={x["oid"]:x for x in exact}
    if len(unique)==1:
        return next(iter(unique.values()))["pics"],"exact_name"
    if oid:
        found=source["by_id"].get(oid,[])
        if len(found)==1 and name==key(found[0]["name"]):
            return found[0]["pics"],"exact_id_and_name"
    return [],"no_exact_source_match"

def validate_summary_edit(summary,new_pics):
    old_urls,quality=summary_urls(summary)
    if quality in ("mixed_content","prose_without_shortcode"):
        return None,"protected_prose"
    if not new_pics:return None,"no_source"
    new=extimg_summary(new_pics)
    if not new or "[extimg]" not in new.lower() or new.count("[extimg]")!=len(new_pics):
        raise RuntimeError("Invalid external-image gallery encoding")
    if new==str(summary or "").strip():return None,"already_correct"
    return new,quality

def prepare():
    wa=WebasystClient(min_request_interval=0.11)
    types=listify(wa.call("shop.type.getList"),keys=("types","items"))
    matched=[]
    for name in NAMES:
        matches=[t for t in types if str(t.get("name") or "").casefold()==name.casefold()]
        if len(matches)!=1:raise RuntimeError("Webasyst type missing or ambiguous: "+name)
        matched.append((name,int(matches[0]["id"])))
    source_files={filename:load_feed(filename) for filename in set(FEEDS.values())}
    changes=[];backup=[];type_report={}
    for name,tid in matched:
        products=products_for_type(wa,tid)
        source=source_files.get(FEEDS.get(name))
        stat=collections.Counter()
        for p in products:
            summary=str(p.get("summary") or "")
            pid=int(p["id"])
            backup.append({"id":pid,"name":str(p.get("name") or ""), "type_id":tid,"summary":summary})
            urls,quality=summary_urls(summary)
            # Existing external links are the fallback; never fill with another SKU's picture.
            fresh,source_method=supplier_pics(source,p)
            chosen=fresh or normalize_pics(urls)
            new,reason=validate_summary_edit(summary,chosen)
            if new is None:stat[reason]+=1
            else:
                changes.append({"id":pid,"type_id":tid,"type_name":name,
                    "name":str(p.get("name") or ""), "before":summary,"after":new,
                    "picture_count":len(chosen),"from":source_method if fresh else quality})
                stat["planned"]+=1
            if fresh:stat["matched_feed"]+=1
            elif urls:stat["had_external_urls"]+=1
            else:stat["no_safe_external_source"]+=1
        stat["products"]=len(products)
        type_report[name]={"id":tid,**dict(stat)}
        print("TYPE_EXTIMG_PREVIEW",json.dumps({"name":name,**type_report[name]},ensure_ascii=False),flush=True)
    if len(backup)!=len({x["id"] for x in backup}):
        raise RuntimeError("Products shared across supposedly separate types")
    if len(changes)>len(backup):raise RuntimeError("Unsafe plan size")
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    manifest={"date_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
      "mode":"preview","total":len(backup),"planned":len(changes),
      "types":type_report,"written":0,"verified":0,"failed":[]}
    with gzip.open(BACKUP,"wt",encoding="utf-8",compresslevel=6) as f:
        json.dump({"original":backup,"metadata":manifest},f,ensure_ascii=False)
    with gzip.open(PLAN,"wt",encoding="utf-8",compresslevel=6) as f:
        json.dump({"changes":changes,"metadata":manifest},f,ensure_ascii=False)
    REPORT.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print("SAFE_BACKUP_READY",json.dumps({"backup":str(BACKUP.relative_to(ROOT)),
        "rows":len(backup),"plan_rows":len(changes),"backup_size":BACKUP.stat().st_size}),flush=True)

def apply():
    if not BACKUP.is_file() or not PLAN.is_file():raise RuntimeError("No durable plan and backup")
    with gzip.open(PLAN,"rt",encoding="utf-8") as f:payload=json.load(f)
    with gzip.open(BACKUP,"rt",encoding="utf-8") as f:archive=json.load(f)
    changes=payload["changes"];meta=payload["metadata"]
    if meta["planned"]!=len(changes) or meta["total"]!=len(archive["original"]):
        raise RuntimeError("Plan-backup count mismatch")
    originals={x["id"]:x for x in archive["original"]}
    if len(originals)!=len(archive["original"]):raise RuntimeError("Duplicate backup IDs")
    wa=WebasystClient(min_request_interval=0.11)
    # Full optimistic preflight: reject changed summary or type before first mutation.
    for i,c in enumerate(changes,1):
        before=originals.get(c["id"])
        if not before or any(before[k]!=c[k] for k in ("name","type_id")) or before["summary"]!=c["before"]:
            raise RuntimeError("Invalid backup relationship "+str(c["id"]))
        p=wa.call("shop.product.getInfo",params={"id":str(c["id"])})
        if not isinstance(p,dict) or str(p.get("summary") or "")!=c["before"] or str(p.get("name") or "")!=c["name"] or int(p.get("type_id") or 0)!=c["type_id"]:
            raise RuntimeError("Concurrent card edit: "+str(c["id"]))
        if i%250==0:print("PREFLIGHT_PROGRESS",i,len(changes),flush=True)
    print("PREFLIGHT_PASSED",len(changes),flush=True)
    failed=[];written=0;verified=0
    for i,c in enumerate(changes,1):
        try:
            wa.call("shop.product.update",http_method="POST",params={"id":str(c["id"])},
                data={"summary":c["after"]})
            written+=1
            if i<=5 or i%100==0:
                check=wa.call("shop.product.getInfo",params={"id":str(c["id"])})
                if str(check.get("summary") or "")!=c["after"]:
                    raise RuntimeError("Post-update summary mismatch")
                verified+=1
        except Exception as e:
            failed.append({"id":c["id"],"reason":type(e).__name__,"message":str(e)[:180]})
            if len(failed)>=5:break
        if i%100==0 or i==len(changes):
            result=dict(meta,mode="applying",written=written,verified=verified,failed=failed,
                       processed=i,expected=len(changes))
            REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            print("APPLY_PROGRESS",json.dumps({"processed":i,"total":len(changes),
                "written":written,"verified_samples":verified,"failures":len(failed)}),flush=True)
    result=dict(meta,mode="done" if not failed and written==len(changes) else "partial",
                written=written,verified=verified,failed=failed,expected=len(changes))
    REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("BULK_EXTIMG_FINAL",json.dumps({"mode":result["mode"],"planned":len(changes),
        "written":written,"verified_samples":verified,"failed":failed[:5]},ensure_ascii=False),flush=True)
    if result["mode"]!="done":raise RuntimeError("Some image-only updates failed")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--apply-plan",action="store_true")
    args=ap.parse_args()
    if args.apply_plan:apply()
    else:prepare()
