#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

TOKEN=(os.getenv("WB_API_TOKEN") or "").strip()
if not TOKEN:
    raise SystemExit("WB_API_TOKEN is missing")

OUT=Path("wb-return-latest")
OUT.mkdir(parents=True, exist_ok=True)
headers={"Authorization":TOKEN,"Accept":"application/json"}

def fetch_claims(is_archive: bool):
    r=requests.get(
        "https://returns-api.wildberries.ru/api/v1/claims",
        headers=headers,
        params={"is_archive":"true" if is_archive else "false","limit":100,"offset":0},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"claims archive={is_archive} HTTP {r.status_code}: {r.text[:500]}")
    d=r.json() if r.content else {}
    rows=d.get("claims") or []
    return [x for x in rows if isinstance(x,dict)]

active=fetch_claims(False)
archived=fetch_claims(True)
all_claims=[]
for source, rows in (("active",active),("archive",archived)):
    for x in rows:
        row=dict(x)
        row["_source"]=source
        all_claims.append(row)

if not all_claims:
    print(json.dumps({"ok":True,"claims":0,"message":"No return claims found"},ensure_ascii=False))
    raise SystemExit(3)

def parse_dt(v):
    if not v:
        return datetime.min
    s=str(v).strip().replace("Z","+00:00")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except Exception:
        return datetime.min

latest=max(all_claims,key=lambda x:(parse_dt(x.get("dt")),parse_dt(x.get("dt_update"))))
photos=latest.get("photos") or []
if not isinstance(photos,list):
    photos=[]

safe_meta={k:v for k,v in latest.items() if k!="photos"}
safe_meta["photos_count"]=len(photos)
(OUT/"claim.json").write_text(json.dumps(safe_meta,ensure_ascii=False,indent=2),encoding="utf-8")

downloaded=[]
errors=[]
for i,u in enumerate(photos,1):
    if not isinstance(u,str) or not u.strip():
        continue
    url=u.strip()
    if url.startswith("//"):
        url="https:"+url
    elif url.startswith("/"):
        url="https://photos.wbstatic.net"+url
    try:
        rr=requests.get(url,timeout=90)
        rr.raise_for_status()
        ctype=(rr.headers.get("Content-Type") or "").lower()
        ext=".webp"
        if "jpeg" in ctype or "jpg" in ctype:
            ext=".jpg"
        elif "png" in ctype:
            ext=".png"
        elif "webp" in ctype:
            ext=".webp"
        else:
            path=urlparse(url).path.lower()
            for candidate in (".webp",".jpg",".jpeg",".png"):
                if path.endswith(candidate):
                    ext=candidate
                    break
        name=f"photo_{i:02d}{ext}"
        (OUT/name).write_bytes(rr.content)
        downloaded.append(name)
    except Exception as e:
        errors.append({"index":i,"error":f"{type(e).__name__}: {str(e)[:300]}"})

summary={
    "ok":True,
    "active_claims":len(active),
    "archived_claims":len(archived),
    "claim_id":latest.get("id"),
    "claim_source":latest.get("_source"),
    "dt":latest.get("dt"),
    "dt_update":latest.get("dt_update"),
    "nm_id":latest.get("nm_id"),
    "srid":latest.get("srid"),
    "status":latest.get("status"),
    "status_ex":latest.get("status_ex"),
    "claim_type":latest.get("claim_type"),
    "imt_name":latest.get("imt_name"),
    "user_comment":latest.get("user_comment"),
    "photos_count":len(photos),
    "downloaded":downloaded,
    "errors":errors,
}
print(json.dumps(summary,ensure_ascii=False,indent=2))
if len(downloaded)!=len(photos):
    raise SystemExit(4)
