#!/usr/bin/env python3
import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import gspread
import requests
from google.oauth2.service_account import Credentials

KIT_BASE = "https://api.kit.yandex.net"
REPORT = Path("norden-kit/restore_from_catalog_report.json")
SPREADSHEET_ID = os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET = os.environ.get("CATALOG_SHEET", "Норден").strip()
TOKEN = os.environ["YANDEX_KIT_TOKEN"].strip()
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

IDENTITY_TITLES = (
    "Артикул",
    "Код продавца",
    "Код Norden",
    "Внешний ID",
    "Внешний идентификатор",
    "External ID",
    "Код для сайта",
)
RESTORABLE = {"HIDDEN", "ARCHIVED"}
BRAND = "Norden"
CONFUSABLES = str.maketrans({
    "а":"a","в":"b","с":"c","е":"e","н":"h","к":"k","м":"m","о":"o","р":"p","т":"t","х":"x","у":"y",
    "А":"a","В":"b","С":"c","Е":"e","Н":"h","К":"k","М":"m","О":"o","Р":"p","Т":"t","Х":"x","У":"y",
})

def s(v):
    return str(v or "").strip()

def norm(v):
    x = unicodedata.normalize("NFKC", s(v)).translate(CONFUSABLES).casefold()
    return re.sub(r"[^0-9a-z]+", "", x)

def now():
    return datetime.now(timezone.utc).isoformat()

class Kit:
    def __init__(self, token):
        if not token:
            raise RuntimeError("YANDEX_KIT_TOKEN is empty")
        self.session = requests.Session()
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.last = 0.0

    def request(self, method, path, *, params=None, body=None):
        url = KIT_BASE + path
        for attempt in range(12):
            delay = 0.43 - (time.monotonic() - self.last)
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()
            headers = dict(self.headers)
            if body is not None:
                headers["Content-Type"] = "application/merge-patch+json" if method == "PATCH" else "application/json"
            r = self.session.request(method, url, params=params, json=body, headers=headers, timeout=120)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After") or min(45, 4 * (attempt + 1))))
                continue
            if r.status_code >= 500:
                time.sleep(min(20, 2 ** attempt))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code} {r.url}: {r.text[:700]}")
            if not r.content:
                return {}
            return r.json()
        raise RuntimeError(f"KIT retries exhausted: {method} {path}")

    @staticmethod
    def items(payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for k in ("items","results","variants","characteristics","products"):
            if isinstance(payload.get(k), list):
                return payload[k]
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        return []

    @staticmethod
    def total(payload):
        if not isinstance(payload, dict):
            return None
        for k in ("total","total_count"):
            if isinstance(payload.get(k), int):
                return payload[k]
        meta = payload.get("meta")
        if isinstance(meta, dict):
            for k in ("total","total_count"):
                if isinstance(meta.get(k), int):
                    return meta[k]
        return None

    def collection(self, path, params=None):
        out=[]
        page=1
        while True:
            q=dict(params or {})
            q.update({"page":page,"per_page":100})
            payload=self.request("GET",path,params=q)
            rows=[x for x in self.items(payload) if isinstance(x,dict)]
            out.extend(rows)
            total=self.total(payload)
            if not rows or (total is not None and len(out)>=total) or (total is None and len(rows)<100):
                break
            page += 1
            if page % 25 == 0:
                print(f"{path}: read {len(out)} rows", flush=True)
        return out

    def patch_status(self, variant_id, status):
        return self.request("PATCH", f"/v1/variants/{variant_id}", body={"status":status})

    def get_variant(self, variant_id):
        return self.request("GET", f"/v1/variants/{variant_id}")

def current_char_value(row, char_id):
    for c in row.get("characteristics") or []:
        if s(c.get("characteristic_id")) == char_id:
            vals = c.get("values") or []
            return s(c.get("value") or (vals[0] if vals else ""))
    return ""

def load_catalog():
    creds=json.loads(SA_JSON)
    gc=gspread.authorize(Credentials.from_service_account_info(
        creds,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly","https://www.googleapis.com/auth/drive.readonly"],
    ))
    ws=gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET)
    values=ws.get("A:E")
    if not values:
        raise RuntimeError("Catalog sheet is empty")
    header=[s(x) for x in values[0]]
    expected=["Артикул","Название","YML ID","Бренд","Архивный"]
    if header[:5] != expected:
        raise RuntimeError(f"Unexpected catalog headers A:E: {header[:5]}")
    targets=[]
    seen=set()
    duplicate_keys=[]
    for rowno,row in enumerate(values[1:], start=2):
        row=list(row)+[""]*(5-len(row))
        article,name,yml_id,brand,archive_flag=[s(x) for x in row[:5]]
        if not yml_id or brand.casefold()!=BRAND.casefold():
            continue
        key=norm(yml_id)
        if not key:
            continue
        if key in seen:
            duplicate_keys.append({"row":rowno,"yml_id":yml_id})
            continue
        seen.add(key)
        targets.append({
            "row":rowno,
            "article":article,
            "article_key":norm(article),
            "name":name,
            "yml_id":yml_id,
            "yml_key":key,
            "sheet_archive_flag":archive_flag,
        })
    if duplicate_keys:
        raise RuntimeError(f"Duplicate normalized YML IDs in catalog: {duplicate_keys[:20]}")
    if not (500 <= len(targets) <= 2000):
        raise RuntimeError(f"Safety guard: unexpected Norden target count {len(targets)}")
    return targets

def main():
    started=now()
    targets=load_catalog()
    by_yml={t["yml_key"]:t for t in targets}
    by_article={t["article_key"]:t for t in targets if t["article_key"]}

    kit=Kit(TOKEN)
    chars=kit.collection("/v1/characteristics", {"status":["ACTIVE"]})
    by_title=defaultdict(list)
    for c in chars:
        title=s(c.get("title"))
        cid=s(c.get("id"))
        if title and cid:
            by_title[norm(title)].append(cid)
    identity_ids={}
    for title in IDENTITY_TITLES:
        ids=list(dict.fromkeys(by_title.get(norm(title), [])))
        if ids:
            identity_ids[title]=ids

    variants=kit.collection("/v1/variants")
    candidates=defaultdict(dict)
    variant_conflicts=[]

    for row in variants:
        if s(row.get("brand")).casefold()!=BRAND.casefold():
            continue
        vid=s(row.get("id"))
        if not vid:
            continue
        values=[]
        sku=s(row.get("sku"))
        if sku:
            values.append(("SKU",sku))
        for title,ids in identity_ids.items():
            for cid in ids:
                v=current_char_value(row,cid)
                if v:
                    values.append((title,v))

        matched={}
        for field,value in values:
            k=norm(value)
            if not k:
                continue
            if k in by_yml:
                t=by_yml[k]
                matched[t["yml_key"]]=t
            if k in by_article:
                t=by_article[k]
                matched[t["yml_key"]]=t
        if len(matched)>1:
            variant_conflicts.append({
                "variant_id":vid,
                "kit_id":row.get("kit_id"),
                "sku":sku,
                "status":s(row.get("status")).upper(),
                "matched_targets":[{"yml_id":t["yml_id"],"article":t["article"]} for t in matched.values()],
                "identity_values":[{"field":f,"value":v} for f,v in values],
            })
            continue
        if len(matched)==1:
            key=next(iter(matched))
            candidates[key][vid]={
                "variant_id":vid,
                "kit_id":row.get("kit_id"),
                "sku":sku,
                "name":s(row.get("name")),
                "status":s(row.get("status")).upper(),
                "identity_values":[{"field":f,"value":v} for f,v in values],
            }

    plans=[]
    already=[]
    unresolved=[]
    nonarchive_only=[]
    ambiguous=[]
    for t in targets:
        rows=list(candidates.get(t["yml_key"],{}).values())
        if not rows:
            unresolved.append({k:t[k] for k in ("row","article","name","yml_id")})
            continue
        published=[r for r in rows if r["status"]=="PUBLISHED"]
        if published:
            published.sort(key=lambda r:(int(r["kit_id"]) if str(r.get("kit_id","")).isdigit() else 10**18, r["sku"]))
            already.append({
                "target":{k:t[k] for k in ("row","article","name","yml_id")},
                "published":published,
                "other_candidates":[r for r in rows if r["status"]!="PUBLISHED"],
            })
            continue
        archived=[r for r in rows if r["status"] in RESTORABLE]
        if not archived:
            nonarchive_only.append({
                "target":{k:t[k] for k in ("row","article","name","yml_id")},
                "candidates":rows,
            })
            continue

        def canonical(r):
            exact_sheet_sku = 0 if t["article"] and norm(r["sku"])==t["article_key"] else 1
            legacy = 0 if r["sku"] and not r["sku"].startswith("100-") else 1
            try: kid=int(r.get("kit_id"))
            except Exception: kid=10**18
            return (exact_sheet_sku, legacy, kid, r["sku"], r["variant_id"])

        archived.sort(key=canonical)
        chosen=archived[0]
        same_rank=[r for r in archived if canonical(r)[:2]==canonical(chosen)[:2]]
        # Multiple archived candidates are expected after historical duplicate cleanup.
        # Restore exactly one canonical item; leave duplicate archive rows untouched.
        plans.append({
            "target":{k:t[k] for k in ("row","article","name","yml_id")},
            "chosen":chosen,
            "other_archived_candidates":[r for r in archived[1:]],
            "all_candidates":rows,
        })

    restored=[]
    errors=[]
    for i,p in enumerate(plans, start=1):
        chosen=p["chosen"]
        vid=chosen["variant_id"]
        try:
            kit.patch_status(vid,"PUBLISHED")
            live=kit.get_variant(vid)
            live_status=s(live.get("status")).upper()
            item={
                "target":p["target"],
                "variant_id":vid,
                "kit_id":chosen.get("kit_id"),
                "sku":chosen.get("sku"),
                "before_status":chosen.get("status"),
                "after_status":live_status,
                "other_archived_candidates_left_untouched":len(p["other_archived_candidates"]),
            }
            if live_status=="PUBLISHED":
                restored.append(item)
            else:
                item["error"]="verification status is not PUBLISHED"
                errors.append(item)
        except Exception as exc:
            errors.append({
                "target":p["target"],
                "variant_id":vid,
                "kit_id":chosen.get("kit_id"),
                "sku":chosen.get("sku"),
                "error":str(exc)[:1000],
            })
        if i % 50 == 0 or i==len(plans):
            print(f"Restore progress {i}/{len(plans)}", flush=True)

    report={
        "started_at":started,
        "finished_at":now(),
        "operation":"restore Norden KIT products from Catalog sheet",
        "spreadsheet_id":SPREADSHEET_ID,
        "sheet":SHEET,
        "brand":BRAND,
        "stop_all_norden_untouched":True,
        "identity_titles_found":identity_ids,
        "counts":{
            "catalog_targets":len(targets),
            "kit_variants_scanned":len(variants),
            "variant_identity_conflicts":len(variant_conflicts),
            "already_published":len(already),
            "restore_planned":len(plans),
            "restored_verified":len(restored),
            "restore_errors":len(errors),
            "unresolved_no_kit_match":len(unresolved),
            "matched_but_not_published_or_archived":len(nonarchive_only),
        },
        "restored":restored,
        "already_published":already,
        "unresolved":unresolved,
        "nonarchive_only":nonarchive_only,
        "variant_conflicts":variant_conflicts,
        "errors":errors,
        "complete":len(errors)==0,
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["counts"],ensure_ascii=False,indent=2))
    if errors:
        raise SystemExit(1)

if __name__=="__main__":
    main()
