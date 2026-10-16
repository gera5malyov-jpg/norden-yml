#!/usr/bin/env python3
"""Repair external image galleries for only the specified Webasyst product types.

Safeguards:
* no creation/deletion or edits outside shop_product.summary;
* link photos to existing products only via exact supplier identity;
* keep valid external picture sources; no media uploads to Webasyst/KIT;
* write recoverable gzip backup to GitHub Actions artifact before modifying data;
* skip products with mixed prose and unsafe/ambiguous image mappings.
"""
from __future__ import annotations
import argparse
import collections
import gzip
import html
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from webasyst.client import WebasystClient
from supplier_engine.webasyst_sync import extimg_summary

TARGETS={
    "1-Обычный товар":1,
    "33 Кровати-333":79,
    "4sis.ru":11,
    "afinalux":63,
    "ALAVANN":59,
    "aletan-provence":46,
    "aletan-мрамор":122,
    "aletan.ru":202,
}
LOG=Path("reports/external_image_refresh_20261016.json")
BACKUP=Path("reports/external_image_refresh_20261016_backup.json.gz")
SRC={"33 Кровати-333":ROOT/"4s-mebel.yml",
     "ALAVANN":ROOT/"alavann/alavann.yml",
     "aletan.ru":ROOT/"aletan-yml/aletan.yml"}
URL=re.compile(r'https?://[^\s<>\[\]"\'\u201c\u201d]+',re.I)
BLOCK=re.compile(r"\[extimg\](.*?)\[/extimg\]",re.I|re.S)
TAG=re.compile(r"\[/?extimg\]",re.I)
MAX_ITEMS=7000

def key(value):
    return re.sub(r"[^0-9a-zа-яё]+","",unicodedata.normalize("NFKC",str(value or "")).casefold())
def listify(obj,keys=("products","items","skus")):
    if isinstance(obj,list):return [x for x in obj if isinstance(x,dict)]
    if isinstance(obj,dict):
        for field in keys:
            val=obj.get(field)
            if isinstance(val,list):return [x for x in val if isinstance(x,dict)]
            if isinstance(val,dict):return [x for x in val.values() if isinstance(x,dict)]
        if obj and all(isinstance(x,dict) for x in obj.values()):return list(obj.values())
    return []
def sku_list(obj):
    x=obj.get("skus")
    if isinstance(x,dict):x=list(x.values())
    return [str(v.get("sku") or "") for v in x if isinstance(v,dict)] if isinstance(x,list) else []
def extract_urls(value):
    urls=[];seen=set()
    for match in URL.findall(html.unescape(str(value or ""))):
        url=match.strip().rstrip(".,;")
        host=urlsplit(url).hostname
        if not host or url in seen or len(url)>1600:continue
        seen.add(url);urls.append(url)
    return urls
def summary_urls(summary):
    value=str(summary or "").strip()
    if not value:return [],"empty"
    if "[extimg]" not in value.lower():return [],"prose_without_shortcode"
    matches=BLOCK.findall(value)
    if matches:
        urls=[]
        for block in matches:urls+=extract_urls(block)
        # Source may have accidentally emitted an opening [extimg] without a closing tag.
        tail=BLOCK.sub("",value)
        if "[extimg]" in tail.lower():
            urls+=extract_urls(tail)
        elif URL.search(tail):
            return [],"mixed_content"
        residual=URL.sub("",TAG.sub("",BLOCK.sub("",value))).strip()
        if residual and residual not in (".",):
            return [],"mixed_content"
        return list(dict.fromkeys(urls)),"normal"
    # Malformed legacy open-only tag with one or more links.
    if value.lower().startswith("[extimg]"):
        urls=extract_urls(value)
        rest=URL.sub("",TAG.sub("",value)).strip()
        if rest and rest not in (".",):return [],"mixed_content"
        return urls,"open_only"
    return [],"mixed_content"

def parse_feed(path):
    if not path.is_file() or path.stat().st_size<1000:
        raise RuntimeError("Missing supplier YML source: "+str(path))
    catalog=collections.defaultdict(list)
    for event,node in ET.iterparse(path,events=("end",)):
        if node.tag.rsplit("}",1)[-1]!="offer":continue
        oid=str(node.get("id") or "").strip()
        code=(node.findtext("vendorCode") or "").strip()
        name=(node.findtext("name") or "").strip()
        pics=extract_urls("\n".join(x.text or "" for x in node.findall("picture")))
        if pics:
            entry={"id":oid,"code":code,"name":name,"pics":pics}
            if oid:catalog[oid].append(entry)
            if code and code!=oid:catalog["code:"+code].append(entry)
        node.clear()
    return catalog

def load_afina():
    # Optional fresh source: on failure still normalize currently held links.
    url="https://afinalux.ru/index.php?route=feed/yandex_yml"
    try:
        r=requests.get(url,timeout=(15,100),headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        root=ET.fromstring(r.content)
        by_code=collections.defaultdict(list)
        for offer in root.iter("offer"):
            vendor=key(offer.findtext("vendor"))
            if vendor!=key("Afina Garden"):continue
            code=key(offer.findtext("vendorCode"))
            pics=extract_urls("\n".join(x.text or "" for x in offer.findall("picture")))
            if code and pics:by_code[code].append({
                "code":code,"name":offer.findtext("name") or "","pics":pics})
        return by_code
    except Exception as e:
        print("AFINA_SOURCE_READ_FAILED",type(e).__name__,str(e)[:130],flush=True)
        return {}

def products_for_type(wa,type_id):
    collected=[];offset=0
    while True:
        result=wa.call("shop.product.search",params={
            "hash":"type/"+str(type_id),"offset":offset,"limit":1000,
            "fields":"id,name,type_id,summary,skus,yml_id,images,image_id"})
        batch=listify(result)
        collected.extend(batch)
        if len(batch)<1000:break
        offset+=len(batch)
        if offset>MAX_ITEMS:raise RuntimeError("Too many items for selected type "+str(type_id))
    if len(collected)!=len({str(x.get("id")) for x in collected}):
        raise RuntimeError("Duplicate Webasyst product IDs in listing")
    if any(int(x.get("type_id") or 0)!=int(type_id) for x in collected):
        raise RuntimeError("Cross-type product unexpectedly included")
    return collected

def source_for_product(typ,product,index,afina):
    original_id=str(product.get("yml_id") or "").strip()
    psname=key(product.get("name"))
    if typ in ("ALAVANN","aletan.ru"):
        if not original_id:return [],"missing_supplier_id"
        entries=index.get(original_id) or []
        if len(entries)!=1:return [],"source_missing_or_ambiguous"
        return entries[0]["pics"],"supplier_yml"
    if typ=="33 Кровати-333":
        entries=index.get(original_id) or []
        if len(entries)==1 and (not entries[0]["name"] or key(entries[0]["name"])==psname):
            return entries[0]["pics"],"supplier_yml"
        return [],"existing_summary_only"
    if typ=="afinalux":
        matches=[]
        for sku in sku_list(product):
            matches+=afina.get(key(sku),[])
        unique={tuple(e["pics"]):e for e in matches}
        if len(unique)==1:return next(iter(unique.values()))["pics"],"supplier_yml"
        return [],"existing_summary_only"
    return [],"existing_summary_only"

def make_plan(wa):
    found=listify(wa.call("shop.type.getList"),keys=("types","items"))
    for n,tid in TARGETS.items():
        exact=[t for t in found if str(t.get("name") or "").casefold()==n.casefold()]
        if len(exact)!=1 or int(exact[0]["id"])!=tid:raise RuntimeError("Unexpected Webasyst type: "+n)
    feeds={name:parse_feed(path) for name,path in SRC.items()}
    afina=load_afina()
    print("SUPPLIER_FEEDS",json.dumps({n:len(v) for n,v in feeds.items()}),flush=True)
    changes=[];summary={}
    for typ,type_id in TARGETS.items():
        products=products_for_type(wa,type_id)
        stats=collections.Counter()
        stats["count"]=len(products)
        for product in products:
            before=str(product.get("summary") or "")
            old_urls,quality=summary_urls(before)
            fresh_urls,method=source_for_product(typ,product,feeds.get(typ,{}),afina)
            stats["source_"+method]+=1
            # Preserve the full source gallery where valid; do not replace with empty images.
            chosen=fresh_urls or old_urls
            if not chosen:
                stats["unresolved_"+quality]+=1
                continue
            # Never destroy manually authored prose or captions.
            if quality in ("mixed_content","prose_without_shortcode"):
                stats["protected_mixed_content"]+=1
                continue
            # Some feeds contain identical URL but different line breaks: normalize safely.
            new=extimg_summary(chosen)
            if not new:raise RuntimeError("Bad gallery encoding")
            if new==before.strip():
                stats["already_correct"]+=1;continue
            changed={
                "type_name":typ,"type_id":type_id,"product_id":int(product["id"]),
                "old_summary":before,"new_summary":new,
                "old_image_count":len(old_urls),"new_image_count":len(chosen),
                "strategy":method if fresh_urls else quality,
                "name":str(product.get("name") or "")[:130],
                "yml_id":str(product.get("yml_id") or ""),
            }
            changes.append(changed)
            stats["needs_repair"]+=1
            if not before:stats["created_from_empty"]+=1
        summary[typ]=dict(stats)
        print("TYPE_IMAGE_PLAN",json.dumps({"name":typ,"type_id":type_id,**dict(stats)},
                      ensure_ascii=False),flush=True)
    if len(changes)>5500:raise RuntimeError("Too many proposed changes")
    if summary["aletan.ru"]["count"]<800:raise RuntimeError("Aletan catalog disappeared")
    if summary["ALAVANN"]["count"]<200:raise RuntimeError("ALAVANN catalog disappeared")
    if summary["4sis.ru"]["count"]<800:raise RuntimeError("4sis catalog disappeared")
    if summary["33 Кровати-333"]["count"]<2500:raise RuntimeError("4S catalog disappeared")
    if summary["afinalux"]["count"]<250:raise RuntimeError("Afina catalog disappeared")
    return changes,summary

def save_plan(changes,summary):
    LOG.parent.mkdir(parents=True,exist_ok=True)
    record={"mode":"preview","summary":summary,"selected":len(changes),
      "updated":0,"failed":[],"verified":0}
    LOG.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
    with gzip.open(BACKUP,"wt",encoding="utf-8",compresslevel=6) as file:
        json.dump({"summary":summary,"pending_changes":changes},file,ensure_ascii=False)
    print("BACKUP_READY",json.dumps({"path":str(BACKUP),"bytes":BACKUP.stat().st_size,
         "items":len(changes)}),flush=True)
    return record

def apply(wa,changes,report):
    # Only summary is sent. No supplier sync, KIT writes, product creations, or image uploads.
    print("APPLY_START",len(changes),flush=True)
    failures=[];updated=0
    for i,row in enumerate(changes,1):
        try:
            wa.call("shop.product.update",http_method="POST",
                    params={"id":str(row["product_id"])},
                    data={"summary":row["new_summary"]})
            updated+=1
        except Exception as e:
            failures.append({"id":row["product_id"],"type":row["type_name"],
                "reason":type(e).__name__,"message":str(e)[:180]})
            if len(failures)>=20:
                print("SAFETY_STOP",json.dumps(failures[-5:],ensure_ascii=False),flush=True)
                break
        if i%100==0 or i==len(changes):
            print("APPLY_PROGRESS",json.dumps({"processed":i,"total":len(changes),
                "updated":updated,"errors":len(failures)}),flush=True)
            report.update({"mode":"applied_partial","updated":updated,"failed":failures})
            LOG.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    report.update({"mode":"applied" if not failures else "partial_failure",
                   "updated":updated,"failed":failures})
    LOG.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    # Spot-check output from product detail endpoint (returns authoritative summary).
    samples=[]
    by_type=collections.defaultdict(list)
    for row in changes[:min(len(changes),updated)]:
        by_type[row["type_name"]].append(row)
    for typ,entries in by_type.items():
        picks=[entries[0],entries[len(entries)//2],entries[-1]]
        for item in picks:
            data=wa.call("shop.product.getInfo",params={"id":str(item["product_id"])})
            actual=str(data.get("summary") or "") if isinstance(data,dict) else ""
            samples.append({"type":typ,"id":item["product_id"],
                            "correct":actual==item["new_summary"],
                            "blocks":actual.count("[extimg]")})
    report["sample_validation"]=samples
    report["verified"]=sum(int(s["correct"]) for s in samples)
    LOG.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("VERIFY_SAMPLES",json.dumps(samples,ensure_ascii=False),flush=True)
    if failures or any(not x["correct"] for x in samples):
        raise RuntimeError("Image restoration had errors or failed verification")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    wa=WebasystClient(min_request_interval=.17)
    changes,summary=make_plan(wa)
    report=save_plan(changes,summary)
    if args.apply:apply(wa,changes,report)
    print("FINAL_IMAGE_REPORT",json.dumps({
        "mode":report["mode"],"selected":report["selected"],"updated":report["updated"],
        "failed":len(report["failed"]),"by_type":summary,
        "backup":str(BACKUP)},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
