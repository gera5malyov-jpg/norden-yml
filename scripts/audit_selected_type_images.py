#!/usr/bin/env python3
"""Read-only image-source audit for seven selected Webasyst product types."""
import collections,json,re,sys,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"webasyst"))
from client import WebasystClient

WANTED=["1-Обычный товар","33 Кровати-333","4sis.ru","afinalux","ALAVANN","aletan-provence","aletan-мрамор","aletan.ru"]
def listify(o,keys=()):
    if isinstance(o,list):return [v for v in o if isinstance(v,dict)]
    if isinstance(o,dict):
        for k in keys:
            v=o.get(k)
            if isinstance(v,list):return v
            if isinstance(v,dict):return [x for x in v.values() if isinstance(x,dict)]
        if o and all(isinstance(v,dict) for v in o.values()):return list(o.values())
    return []
def skus(o):
    x=o.get("skus")
    if isinstance(x,dict):x=list(x.values())
    return [str(r.get("sku","")) for r in x if isinstance(r,dict)] if isinstance(x,list) else []
def extimg_count(s):return str(s or "").lower().count("[extimg]")
def audit(wa,t):
    out=[];offset=0;limit=1000
    while True:
        r=wa.call("shop.product.search",params={"hash":"type/"+str(t["id"]),
              "offset":offset,"limit":limit,"fields":"id,name,type_id,summary,skus,images,image_id,url,yml_id"})
        batch=listify(r,("products","items"))
        out.extend(batch)
        if len(batch)<limit:break
        offset+=len(batch)
        if offset>12000:raise RuntimeError("Pagination unsafe "+str(t))
    photos=collections.Counter();records=[]
    for p in out:
        s=str(p.get("summary") or "")
        b=extimg_count(s)
        photos["with_extimg"]+=int(b>0)
        photos["multi_extimg"]+=int(b>1)
        photos["any_summary"]+=int(bool(s.strip()))
        photos["bad_literal_backslash_n"]+=int("\\n" in s and b>0)
        photos["image_id"]+=int(bool(p.get("image_id")))
        photos["summary_http"]+=int("http" in s)
        photos["links_total"]+=b
        records.append({"id":p.get("id"),"sku":skus(p)[:2],"name":str(p.get("name",""))[:100],
             "extimg":b,"summary":s[:300],"image_id":p.get("image_id"),"yml_id":p.get("yml_id")})
    print("TYPE_REPORT",json.dumps({"name":t["name"],"id":t["id"],"count":len(out),
         "counts":dict(photos),"first":records[:5],
         "without_extimg":next((x for x in records if x["extimg"]==0),None),
         "with_extimg":next((x for x in records if x["extimg"]>0),None)},ensure_ascii=False),flush=True)
def main():
    wa=WebasystClient(min_request_interval=.1)
    types=listify(wa.call("shop.type.getList"))
    print("TYPES_ALL",json.dumps([{"id":x["id"],"name":x["name"]} for x in types
      if any(q.lower().replace(" ","") in str(x.get("name","")).lower().replace(" ","") for q in
      ("кроват","4sis","afinalux","alavann","aletan","обычн"))],ensure_ascii=False))
    found=[]
    for n in WANTED:
        ts=[x for x in types if str(x.get("name","")).casefold()==n.casefold()]
        if len(ts)!=1:
            print("TYPE_NOT_UNIQUE",n,[{"id":x["id"],"name":x["name"]} for x in ts]);continue
        found.append(ts[0])
    for t in found:audit(wa,t)
if __name__=="__main__":main()
