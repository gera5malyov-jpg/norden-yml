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
            delay = 0.35 - (time.monotonic() - self.last)
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
                raise RuntimeError(f"HTTP {r.status_code} {r.url}: {r.text[:800]}")
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

    def search(self, value):
        payload=self.request("GET","/v1/variants",params={"name":value,"page":1,"per_page":100})
        return [x for x in self.items(payload) if isinstance(x,dict)]

    def search_all(self, value):
        out=[]
        page=1
        while True:
            payload=self.request("GET","/v1/variants",params={"name":value,"page":page,"per_page":100})
            rows=[x for x in self.items(payload) if isinstance(x,dict)]
            out.extend(rows)
            total=None
            if isinstance(payload,dict):
                total=payload.get("total_count")
                if not isinstance(total,int):
                    total=payload.get("total")
            if not rows or (isinstance(total,int) and len(out)>=total) or (total is None and len(rows)<100):
                break
            page += 1
            if page % 25 == 0:
                print(f"Search {value}: {len(out)} rows",flush=True)
        return out

    def characteristics(self):
        payload=self.request("GET","/v1/characteristics",params={"status":["ACTIVE"],"page":1,"per_page":1000})
        return [x for x in self.items(payload) if isinstance(x,dict)]

    def get_variant(self, variant_id):
        return self.request("GET",f"/v1/variants/{variant_id}")

    def publish(self, variant_id):
        return self.request("PATCH",f"/v1/variants/{variant_id}",body={"status":"PUBLISHED"})

def current_char_value(row, char_id):
    for c in row.get("characteristics") or []:
        if s(c.get("characteristic_id")) == char_id:
            vals=c.get("values") or []
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
    for rowno,row in enumerate(values[1:],start=2):
        row=list(row)+[""]*(5-len(row))
        article,name,yml_id,brand,archive_flag=[s(x) for x in row[:5]]
        if brand.casefold()!=BRAND.casefold():
            continue
        if not article and not yml_id:
            continue
        targets.append({
            "row":rowno,
            "article":article,
            "article_key":norm(article),
            "name":name,
            "yml_id":yml_id,
            "yml_key":norm(yml_id),
            "sheet_archive_flag":archive_flag,
        })
    if not (500 <= len(targets) <= 2000):
        raise RuntimeError(f"Safety guard: unexpected Norden target row count {len(targets)}")
    return targets

def compact(row):
    return {
        "variant_id":s(row.get("id")),
        "kit_id":row.get("kit_id"),
        "sku":s(row.get("sku")),
        "brand":s(row.get("brand")),
        "name":s(row.get("name")),
        "status":s(row.get("status")).upper(),
    }

def main():
    started=now()
    targets=load_catalog()
    kit=Kit(TOKEN)

    chars=kit.characteristics()
    title_to_ids=defaultdict(list)
    for c in chars:
        cid=s(c.get("id")); title=s(c.get("title"))
        if cid and title:
            title_to_ids[norm(title)].append(cid)
    identity_ids={}
    for title in IDENTITY_TITLES:
        ids=list(dict.fromkeys(title_to_ids.get(norm(title),[])))
        if ids:
            identity_ids[title]=ids

    af_rows=kit.search_all("AF-")
    af_index=defaultdict(list)
    for r in af_rows:
        sku_key=norm(r.get("sku"))
        if sku_key:
            af_index[sku_key].append(r)

    cache={}
    def search_cached(q):
        q=s(q)
        if not q:
            return []
        if q not in cache:
            cache[q]=kit.search(q)
        return cache[q]

    chosen_rows=[]
    already=[]
    unresolved=[]
    ambiguous=[]
    query_errors=[]

    for i,t in enumerate(targets,start=1):
        article=t["article"]
        yml=t["yml_id"]
        exact=[]

        # Primary key: exact KIT SKU == Catalog article (AF-...) from one AF index.
        if article:
            exact=list(af_index.get(t["article_key"],[]))

        # Fallback: search by supplier/YML ID and require exact identity value
        # from a live variant read. Name-only matches are never enough to restore.
        fallback=[]
        if not exact and yml:
            try:
                rows=search_cached(yml)
            except Exception as exc:
                query_errors.append({"row":t["row"],"article":article,"query":yml,"error":str(exc)[:800]})
                rows=[]
            for r in rows:
                vid=s(r.get("id"))
                if not vid:
                    continue
                try:
                    live=kit.get_variant(vid)
                except Exception as exc:
                    query_errors.append({"row":t["row"],"article":article,"variant_id":vid,"error":str(exc)[:800]})
                    continue
                values=[]
                sku=s(live.get("sku"))
                if sku:
                    values.append(("SKU",sku))
                for title,ids in identity_ids.items():
                    for cid in ids:
                        v=current_char_value(live,cid)
                        if v:
                            values.append((title,v))
                keys={norm(v) for _,v in values if norm(v)}
                if t["yml_key"] and t["yml_key"] in keys:
                    fallback.append(live)

        candidates=exact or fallback
        unique={}
        for r in candidates:
            vid=s(r.get("id"))
            if vid:
                unique[vid]=r
        candidates=list(unique.values())

        if not candidates:
            unresolved.append({
                "row":t["row"],"article":article,"name":t["name"],"yml_id":yml
            })
            if i % 100 == 0 or i==len(targets):
                print(f"Resolve {i}/{len(targets)}",flush=True)
            continue

        published=[r for r in candidates if s(r.get("status")).upper()=="PUBLISHED"]
        if published:
            published.sort(key=lambda r:(int(r.get("kit_id")) if str(r.get("kit_id","")).isdigit() else 10**18,s(r.get("sku"))))
            already.append({
                "target":{"row":t["row"],"article":article,"yml_id":yml,"name":t["name"]},
                "published":[compact(r) for r in published],
                "other_candidates":[compact(r) for r in candidates if s(r.get("status")).upper()!="PUBLISHED"],
            })
            if i % 100 == 0 or i==len(targets):
                print(f"Resolve {i}/{len(targets)}",flush=True)
            continue

        restorables=[r for r in candidates if s(r.get("status")).upper() in RESTORABLE]
        if not restorables:
            unresolved.append({
                "row":t["row"],"article":article,"name":t["name"],"yml_id":yml,
                "reason":"matched candidate is neither PUBLISHED nor HIDDEN/ARCHIVED",
                "candidates":[compact(r) for r in candidates],
            })
            if i % 100 == 0 or i==len(targets):
                print(f"Resolve {i}/{len(targets)}",flush=True)
            continue

        def rank(r):
            exact_sku=0 if article and norm(r.get("sku"))==t["article_key"] else 1
            legacy=0 if s(r.get("sku")) and not s(r.get("sku")).startswith("100-") else 1
            try: kid=int(r.get("kit_id"))
            except Exception: kid=10**18
            return (exact_sku,legacy,kid,s(r.get("sku")),s(r.get("id")))

        restorables.sort(key=rank)
        chosen=restorables[0]
        chosen_rows.append({
            "target":{"row":t["row"],"article":article,"yml_id":yml,"name":t["name"]},
            "chosen":compact(chosen),
            "other_restorable_candidates":[compact(r) for r in restorables[1:]],
            "match_mode":"exact_sku" if exact else "exact_identity_fallback",
        })

        if i % 100 == 0 or i==len(targets):
            print(f"Resolve {i}/{len(targets)}",flush=True)

    # Do not restore the same KIT variant twice if duplicate table rows point to it.
    by_variant={}
    duplicate_table_targets=[]
    for p in chosen_rows:
        vid=p["chosen"]["variant_id"]
        if vid not in by_variant:
            by_variant[vid]=p
        else:
            duplicate_table_targets.append({
                "variant_id":vid,
                "kept_target":by_variant[vid]["target"],
                "duplicate_target":p["target"],
            })
    plans=list(by_variant.values())

    restored=[]
    errors=[]
    for i,p in enumerate(plans,start=1):
        vid=p["chosen"]["variant_id"]
        try:
            before=kit.get_variant(vid)
            before_status=s(before.get("status")).upper()
            if before_status=="PUBLISHED":
                already.append({
                    "target":p["target"],
                    "published":[compact(before)],
                    "other_candidates":[],
                    "note":"became published before write",
                })
                continue
            if before_status not in RESTORABLE:
                errors.append({
                    "target":p["target"],"variant_id":vid,
                    "error":f"live status changed to {before_status}",
                })
                continue
            kit.publish(vid)
            live=kit.get_variant(vid)
            after=s(live.get("status")).upper()
            item={
                "target":p["target"],
                "match_mode":p["match_mode"],
                "variant_id":vid,
                "kit_id":live.get("kit_id"),
                "sku":s(live.get("sku")),
                "before_status":before_status,
                "after_status":after,
                "other_restorable_candidates_left_untouched":len(p["other_restorable_candidates"]),
            }
            if after=="PUBLISHED":
                restored.append(item)
            else:
                item["error"]="verification status is not PUBLISHED"
                errors.append(item)
        except Exception as exc:
            errors.append({
                "target":p["target"],
                "variant_id":vid,
                "error":str(exc)[:1000],
            })
        if i % 50 == 0 or i==len(plans):
            print(f"Restore {i}/{len(plans)}",flush=True)

    report={
        "started_at":started,
        "finished_at":now(),
        "operation":"restore Norden KIT products from Catalog sheet using targeted search",
        "spreadsheet_id":SPREADSHEET_ID,
        "sheet":SHEET,
        "brand":BRAND,
        "stop_all_norden_untouched":True,
        "identity_titles_found":identity_ids,
        "counts":{
            "catalog_rows":len(targets),
            "af_index_rows":len(af_rows),
            "af_index_unique_skus":len(af_index),
            "fallback_search_queries_cached":len(cache),
            "already_published":len(already),
            "restore_candidates_before_dedupe":len(chosen_rows),
            "restore_planned_unique_variants":len(plans),
            "restored_verified":len(restored),
            "restore_errors":len(errors),
            "unresolved":len(unresolved),
            "ambiguous":len(ambiguous),
            "duplicate_table_targets_same_variant":len(duplicate_table_targets),
            "query_errors":len(query_errors),
        },
        "restored":restored,
        "already_published":already,
        "unresolved":unresolved,
        "ambiguous":ambiguous,
        "duplicate_table_targets_same_variant":duplicate_table_targets,
        "query_errors":query_errors,
        "errors":errors,
        "complete":len(errors)==0 and len(query_errors)==0,
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["counts"],ensure_ascii=False,indent=2))
    if errors:
        raise SystemExit(1)

if __name__=="__main__":
    main()
