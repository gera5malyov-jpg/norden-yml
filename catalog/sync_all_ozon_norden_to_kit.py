#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, time, requests, importlib.util
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OZON_BASE="https://api-seller.ozon.ru"
BRAND_ATTR_ID=85
REPORT=ROOT/"catalog"/"ozon_norden_to_kit_report.json"

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

BRIDGE=load(Path("norden-kit")/"webasyst_n100_to_kit_once.py","kit_bridge")
POST=load(Path("catalog")/"sync_ozon_readback_to_kit.py","ozon_post")
MOD=load(Path("norden-kit")/"sync_norden_kit.py","norden_mod")

def s(v): return str(v or "").strip()
def norm(v): return re.sub(r"\s+"," ",s(v)).casefold().strip()
def dec(v):
    try:
        x=float(str(v).replace(",","."))
        return x if x > 0 else None
    except: return None

class Ozon:
    def __init__(self):
        self.session=requests.Session()
        self.headers={
            "Client-Id":os.environ["OZON_CLIENT_ID"],
            "Api-Key":os.environ["OZON_API_KEY"],
            "Content-Type":"application/json",
            "Accept":"application/json",
        }
    def post(self,path,body,attempts=8):
        last=None
        for attempt in range(attempts):
            r=self.session.post(OZON_BASE+path,headers=self.headers,json=body,timeout=120)
            last=r
            if r.status_code==429 or r.status_code>=500:
                time.sleep(float(r.headers.get("Retry-After") or min(30,2**attempt)))
                continue
            if not r.ok:
                raise RuntimeError(f"{path}: HTTP {r.status_code}: {r.text[:1500]}")
            return r.json() if r.content else {}
        raise RuntimeError(f"{path}: retries exhausted HTTP {last.status_code if last else 'N/A'}")

def chunks(xs,n):
    for i in range(0,len(xs),n): yield xs[i:i+n]

def list_visibility(oz,visibility):
    out=[]; last_id=""; seen=set()
    while True:
        body={"filter":{"visibility":visibility},"limit":1000}
        if last_id: body["last_id"]=last_id
        d=oz.post("/v3/product/list",body)
        r=d.get("result") or {}; items=r.get("items") or []
        out.extend(items)
        nxt=s(r.get("last_id")); total=int(r.get("total") or 0)
        if not items or len(out)>=total or not nxt or nxt==last_id or nxt in seen: break
        seen.add(last_id); last_id=nxt
    return out

def get_attrs(oz,ids):
    out=[]
    for batch in chunks(ids,1000):
        d=oz.post("/v4/product/info/attributes",{"filter":{"product_id":batch,"visibility":"ALL"},"limit":1000})
        out.extend(d.get("result") or [])
    return out

def get_info(oz,ids):
    out=[]
    for batch in chunks(ids,1000):
        d=oz.post("/v3/product/info/list",{"product_id":batch})
        out.extend(d.get("items") or (d.get("result") or {}).get("items") or [])
    return out

def get_prices(oz,ids):
    out=[]
    for batch in chunks(ids,100):
        cursor=""
        while True:
            body={"filter":{"product_id":batch,"visibility":"ALL"},"limit":100}
            if cursor: body["cursor"]=cursor
            d=oz.post("/v5/product/info/prices",body)
            items=d.get("items") or []; out.extend(items)
            nxt=s(d.get("cursor"))
            if not items or not nxt or nxt==cursor: break
            cursor=nxt
    return out

def brand_values(card):
    for a in card.get("attributes") or []:
        if int(a.get("id") or a.get("attribute_id") or 0)==BRAND_ATTR_ID:
            return [s(v.get("value")) for v in a.get("values") or [] if s(v.get("value"))]
    return []

def images(card,info):
    vals=[]
    for x in [card.get("primary_image")]:
        if s(x) and s(x) not in vals: vals.append(s(x))
    for x in card.get("images") or []:
        u=s(x.get("file_name") if isinstance(x,dict) else x)
        if u and u not in vals: vals.append(u)
    for x in info.get("images") or []:
        u=s(x.get("file_name") if isinstance(x,dict) else x)
        if u and u not in vals: vals.append(u)
    return vals

def description(card,info):
    for key in ("description","description_text"):
        if s(info.get(key)): return s(info.get(key))
        if s(card.get(key)): return s(card.get(key))
    # Ozon rich content is not copied as technical JSON to KIT.
    return ""

def pricing_from_ozon(row):
    p=row.get("price") or {}
    current=dec(p.get("price"))
    old=dec(p.get("old_price"))
    if current is None:
        current=dec(p.get("marketing_seller_price"))
    if current is None: return None
    if old and old>current:
        return {"price":str(old),"manual_discount_price":str(current)}
    return {"price":str(current)}

def get_category_paths(oz):
    d=oz.post("/v1/description-category/tree",{"language":"DEFAULT"})
    paths={}
    def walk(nodes,prefix):
        for n in nodes or []:
            if not isinstance(n,dict): continue
            name=s(n.get("category_name")); cid=n.get("description_category_id")
            path=prefix+([name] if name else [])
            if cid: paths[int(cid)]=path
            walk(n.get("children") or [],path)
    walk(d.get("result") or [],[])
    return paths

def main():
    oz=Ozon(); kit=BRIDGE.KitClient()
    active=list_visibility(oz,"ALL")
    archived=list_visibility(oz,"ARCHIVED")
    archived_ids={int(x.get("product_id") or 0) for x in archived if int(x.get("product_id") or 0)}
    by_id={}
    for x in active+archived:
        pid=int(x.get("product_id") or 0)
        if pid: by_id[pid]=x
    attrs=get_attrs(oz,sorted(by_id))
    attr_by_id={int(x.get("id") or x.get("product_id") or 0):x for x in attrs}
    norden_ids=sorted(pid for pid,x in attr_by_id.items() if any(v.upper()=="NORDEN" for v in brand_values(x)))
    infos=get_info(oz,norden_ids)
    prices=get_prices(oz,norden_ids)
    info_by_id={int(x.get("id") or x.get("product_id") or 0):x for x in infos}
    price_by_id={int(x.get("product_id") or 0):x for x in prices}
    cat_paths=get_category_paths(oz)

    # KIT characteristic metadata and exact article index.
    chars=kit.characteristics()
    by_title=defaultdict(list)
    for x in chars:
        if s(x.get("title")): by_title[MOD.norm_title(x["title"])].append(x)
    article_rows=by_title.get(MOD.norm_title("Артикул"),[])
    if article_rows:
        article_id=s(sorted(article_rows,key=lambda x:(s(x.get("status")).upper()!="ACTIVE",s(x.get("id"))))[0].get("id"))
    else:
        cr=kit.create_characteristic("Артикул"); article_id=s(cr.get("id"))
        chars.append(cr); by_title[MOD.norm_title("Артикул")].append(cr)
    char_title={s(x.get("id")):s(x.get("title")) for x in chars}

    sku_index=defaultdict(list); article_index=defaultdict(list)
    variants=[]
    for row in kit.scan_all_variants_parallel(workers=10):
        variants.append(row)
        vid=s(row.get("id")); sku=s(row.get("sku"))
        if sku: sku_index[norm(sku)].append(row)
        for c in row.get("characteristics") or []:
            if s(c.get("characteristic_id"))==article_id and s(c.get("value")):
                article_index[norm(c.get("value"))].append(row)

    # KIT categories: create Ozon hierarchy below one dedicated Norden root.
    categories=kit.categories()
    cat_index=defaultdict(list)
    for x in categories:
        cat_index[(s(x.get("parent_id")),norm(x.get("title")))].append(x)
    def ensure_cat(parent,title):
        key=(s(parent),norm(title)); rows=cat_index.get(key) or []
        if rows: return s(rows[0].get("id"))
        cr=kit.create_category(title,parent or None)
        cid=s(cr.get("id"))
        if not cid: raise RuntimeError(f"KIT category create returned no id: {title}")
        cat_index[key].append(cr); return cid
    root_id=ensure_cat("","Norden")
    def ensure_path(path):
        parent=root_id
        # Ozon category tree often begins with broad roots; preserve it under Norden.
        for title in [x for x in path if s(x)]:
            parent=ensure_cat(parent,s(title))
        return parent

    # Warehouse needed only for newly created variants; stock remains 0 until supplier/stock sync.
    whs=kit.warehouses()
    wh_by_title={norm(x.get("title") or x.get("name")):s(x.get("id")) for x in whs}
    wh_id=wh_by_title.get(norm("МСК")) or next((s(x.get("id")) for x in whs if s(x.get("id"))),"")

    report={
        "started_at":datetime.now(timezone.utc).isoformat(),
        "ozon_norden_total":len(norden_ids),
        "ozon_norden_archived":sum(1 for x in norden_ids if x in archived_ids),
        "kit_variants_scanned":len(variants),
        "created":0,"updated":0,"skipped":0,"errors":0,
        "created_items":[],"updated_items":[],"skipped_items":[],"error_items":[]
    }
    REPORT.parent.mkdir(parents=True,exist_ok=True)

    for idx,pid in enumerate(norden_ids,1):
        card=attr_by_id.get(pid) or {}
        info=info_by_id.get(pid) or {}
        pr=price_by_id.get(pid) or {}
        offer=s(card.get("offer_id") or by_id.get(pid,{}).get("offer_id") or info.get("offer_id"))
        name=s(card.get("name") or info.get("name") or offer)
        if not offer:
            report["skipped"]+=1; report["skipped_items"].append({"product_id":pid,"reason":"offer_id missing"}); continue
        key=norm(offer)
        try:
            sku_rows=sku_index.get(key) or []
            art_rows=article_index.get(key) or []
            ids={s(x.get("id")) for x in sku_rows+art_rows if s(x.get("id"))}
            if len(ids)>1:
                raise RuntimeError(f"Multiple KIT matches for exact article {offer}: {sorted(ids)}")

            existing=next(iter(sku_rows or art_rows),None)
            created=False
            if existing is None:
                dc=int(card.get("description_category_id") or info.get("description_category_id") or 0)
                cat_id=ensure_path(cat_paths.get(dc) or ["Ozon"])
                prod=kit.create_product(cat_id)
                product_id=s(prod.get("id"))
                if not product_id: raise RuntimeError("KIT create product returned no id")
                body={"sku":offer,"name":name,"status":"PUBLISHED","product_id":product_id,"brand":"Norden"}
                price_patch=pricing_from_ozon(pr)
                if price_patch: body["pricing"]=price_patch
                if wh_id: body["stocks"]=[{"warehouse_id":wh_id,"quantity":0,"reserved":0}]
                v=kit.create_variant(body)
                vid=s(v.get("id"))
                if not vid: raise RuntimeError("KIT create variant returned no id")
                existing={"id":vid,"sku":offer}; created=True
                sku_index[key].append(existing)
            vid=s(existing.get("id"))
            current=kit.request("GET",f"/v1/variants/{vid}")

            # Customer-facing Ozon characteristics only; preserve non-Ozon KIT characteristics.
            dc=card.get("description_category_id") or info.get("description_category_id")
            tid=card.get("type_id") or info.get("type_id")
            schema={}
            if dc and tid:
                schema=POST.attribute_schema(oz,int(dc),int(tid))
            customer=POST.customer_attribute_values(card,schema)

            # Refresh characteristic definitions after any newly-created ones.
            chars_now=kit.characteristics()
            title_map={s(x.get("id")):s(x.get("title")) for x in chars_now}
            title_index=defaultdict(list)
            for x in chars_now:
                if s(x.get("title")): title_index[MOD.norm_title(x["title"])].append(x)
            canonical=POST.build_customer_rows(kit,chars_now,title_index,customer)
            canonical_titles={norm(x.get("title")) for x in customer}
            keep=[]
            for c in current.get("characteristics") or []:
                cid=s(c.get("characteristic_id")); title=title_map.get(cid,"")
                if cid==article_id: continue
                if POST.is_ozon_characteristic_title(title): continue
                if norm(title) in canonical_titles: continue
                keep.append(c)
            characteristics=keep+[{"characteristic_id":article_id,"value":offer,"values":[offer]}]+canonical

            patch={
                "name":name,
                "status":"PUBLISHED",
                "brand":"Norden",
                "characteristics":characteristics,
            }
            pp=pricing_from_ozon(pr)
            if pp: patch["pricing"]=pp
            desc=description(card,info)
            if desc: patch["description"]=desc

            media=[]
            for url in images(card,info):
                try:
                    up=kit.upload_image_url(url); fid=s(up.get("id"))
                    if fid: media.append({"type":"IMAGE","display_sequence":len(media),"image_id":fid})
                except Exception as exc:
                    report["error_items"].append({"offer_id":offer,"stage":"image","error":str(exc)[:500]})
            if media: patch["media"]=media

            kit.patch_variant(vid,patch)
            check=kit.request("GET",f"/v1/variants/{vid}")
            if s(check.get("sku"))!=offer:
                raise RuntimeError(f"KIT readback SKU mismatch: {check.get('sku')}")
            if s(check.get("status")).upper()!="PUBLISHED":
                raise RuntimeError(f"KIT readback status is not PUBLISHED: {check.get('status')}")

            row={"offer_id":offer,"product_id":pid,"kit_variant_id":vid,"archived_in_ozon":pid in archived_ids,
                 "price":(pr.get("price") or {}).get("price"),"old_price":(pr.get("price") or {}).get("old_price"),
                 "images":len(media),"characteristics":len(customer)}
            if created:
                report["created"]+=1; report["created_items"].append(row)
            else:
                report["updated"]+=1; report["updated_items"].append(row)
        except Exception as exc:
            report["errors"]+=1
            report["error_items"].append({"offer_id":offer,"product_id":pid,"stage":"sync","error":str(exc)[:1200]})
        if idx%25==0:
            REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            print(f"Processed {idx}/{len(norden_ids)} created={report['created']} updated={report['updated']} errors={report['errors']}",flush=True)

    report["finished_at"]=datetime.now(timezone.utc).isoformat()
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("ozon_norden_total","ozon_norden_archived","created","updated","skipped","errors")},ensure_ascii=False))
    if report["errors"]:
        raise SystemExit(2)

if __name__=="__main__":
    main()
