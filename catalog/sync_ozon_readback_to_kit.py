#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, time, requests, importlib.util
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(path,name):
    p=ROOT/path
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

BRIDGE=load(Path("norden-kit")/"webasyst_n100_to_kit_once.py","kit_bridge")
MOD=load(Path("norden-kit")/"sync_norden_kit.py","kit_sync")

OZON_BASE="https://api-seller.ozon.ru"

def s(v): return str(v or "").strip()
def norm(v): return re.sub(r"\s+"," ",s(v)).casefold().strip()

class OzonClient:
    def __init__(self):
        self.s=requests.Session()
        self.h={
            "Client-Id":os.environ["OZON_CLIENT_ID"],
            "Api-Key":os.environ["OZON_API_KEY"],
            "Content-Type":"application/json",
            "Accept":"application/json",
        }
    def post(self,path,body):
        r=self.s.post(OZON_BASE+path,headers=self.h,json=body,timeout=120)
        if not r.ok:
            raise RuntimeError(f"{path}: HTTP {r.status_code}: {r.text[:1500]}")
        return r.json() if r.content else {}

def exact_ozon_product(oz,offer_id):
    d=oz.post("/v3/product/list",{"filter":{"offer_id":[offer_id],"visibility":"ALL"},"limit":100})
    items=((d.get("result") or {}).get("items") or [])
    exact=[x for x in items if s(x.get("offer_id"))==offer_id]
    if len(exact)!=1:
        raise RuntimeError(f"Ozon exact offer_id {offer_id}: found {len(exact)}")
    pid=int(exact[0].get("product_id") or 0)
    if not pid:
        raise RuntimeError(f"Ozon exact offer_id {offer_id}: product_id missing")
    return pid,exact[0]

def ozon_readback(oz,offer_id):
    pid,list_row=exact_ozon_product(oz,offer_id)
    attrs=oz.post("/v4/product/info/attributes",{"filter":{"product_id":[pid],"visibility":"ALL"},"limit":1000})
    rows=attrs.get("result") or []
    exact=[x for x in rows if int(x.get("id") or x.get("product_id") or 0)==pid and s(x.get("offer_id"))==offer_id]
    if len(exact)!=1:
        raise RuntimeError(f"Ozon attributes readback {offer_id}: found {len(exact)}")
    card=exact[0]
    info=oz.post("/v3/product/info/list",{"product_id":[pid]})
    infos=info.get("items") or (info.get("result") or {}).get("items") or []
    exact_info=[x for x in infos if int(x.get("id") or x.get("product_id") or 0)==pid and s(x.get("offer_id"))==offer_id]
    if len(exact_info)!=1:
        raise RuntimeError(f"Ozon info readback {offer_id}: found {len(exact_info)}")
    info_row=exact_info[0]
    st=info_row.get("statuses") or {}
    created=bool(st.get("is_created")) or bool(info_row.get("id"))
    if not created:
        raise RuntimeError(f"Ozon {offer_id}: card not confirmed created")
    return {"product_id":pid,"list":list_row,"attributes":card,"info":info_row}

def attribute_schema(oz,description_category_id,type_id):
    d=oz.post("/v1/description-category/attribute",{
        "description_category_id":int(description_category_id),
        "type_id":int(type_id),
        "language":"DEFAULT",
    })
    return {int(x.get("id") or 0):x for x in (d.get("result") or []) if int(x.get("id") or 0)}

def exact_kit_variant(kit,offer_id):
    payload=kit.request("GET","/v1/variants",params={"name":offer_id,"page":1,"per_page":100})
    rows=kit.items(payload)
    exact=[x for x in rows if s(x.get("sku"))==offer_id]
    if len(exact)!=1:
        raise RuntimeError(f"KIT exact SKU {offer_id}: found {len(exact)}")
    vid=s(exact[0].get("id"))
    if not vid: raise RuntimeError(f"KIT {offer_id}: variant id missing")
    return vid

def characteristic_index(kit):
    rows=kit.characteristics()
    by_title=defaultdict(list)
    for x in rows:
        if s(x.get("title")):
            by_title[MOD.norm_title(x["title"])].append(x)
    return rows,by_title

def characteristic_id(kit,rows,by_title,title):
    key=MOD.norm_title(title)
    matches=list(by_title.get(key) or [])
    if len(matches)==1:
        return s(matches[0].get("id"))
    if len(matches)>1:
        # KIT may contain legacy duplicate characteristic definitions with the same visible title.
        # Reuse one deterministic ACTIVE definition instead of failing or creating yet another duplicate.
        def rank(x):
            status=0 if s(x.get("status")).upper()=="ACTIVE" else 1
            seq=x.get("display_sequence")
            try: seq=int(seq)
            except: seq=10**9
            return (status,seq,s(x.get("id")))
        chosen=sorted(matches,key=rank)[0]
        cid=s(chosen.get("id"))
        if not cid:
            raise RuntimeError(f"KIT characteristic {title!r}: selected duplicate has no id")
        return cid
    return MOD.characteristic_id(kit,rows,by_title,title)

def is_ozon_characteristic_title(title):
    return s(title).startswith("Ozon ")

CUSTOMER_EXCLUDE_IDS={
    9048,   # Название модели для объединения
    12141,  # Название модели для шаблона
    9024,   # Код продавца
    4180,   # Название карточки
    23171,  # Хештеги
    4191,   # Аннотация
    22232,  # ТН ВЭД
    11650,  # Количество заводских упаковок
    22073,  # Планирование нескольких упаковок
    23536,  # Нужен код маркировки
    8790,   # Документ PDF
}
CUSTOMER_EXCLUDE_NAME_PARTS=(
    "для объединения",
    "для шаблона",
    "код продавца",
    "хештег",
    "аннотация",
    "тн вэд",
    "код маркировки",
    "заводских упаков",
    "планирую доставлять",
    "документ pdf",
)

def join_values(values):
    out=[]
    for v in values or []:
        if not isinstance(v,dict):
            continue
        val=s(v.get("value"))
        if val and val not in out:
            out.append(val)
    return ", ".join(out)

def is_customer_attribute(aid,name):
    if int(aid or 0) in CUSTOMER_EXCLUDE_IDS:
        return False
    n=norm(name)
    if not n or n.startswith("attribute_"):
        return False
    return not any(x in n for x in CUSTOMER_EXCLUDE_NAME_PARTS)

def customer_attribute_values(card,schema):
    out=[]
    seen=set()
    for a in card.get("attributes") or []:
        aid=int(a.get("id") or a.get("attribute_id") or 0)
        if not aid:
            continue
        meta=schema.get(aid) or {}
        name=s(meta.get("name")) or f"attribute_{aid}"
        if not is_customer_attribute(aid,name):
            continue
        value=join_values(a.get("values") or [])
        if not value:
            continue
        key=norm(name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"attribute_id":aid,"title":name,"value":value})
    return out

def build_customer_rows(kit,rows,by_title,customer_values):
    result=[]
    for x in customer_values:
        title=s(x.get("title"))
        value=s(x.get("value"))
        if not title or not value:
            continue
        cid=characteristic_id(kit,rows,by_title,title)
        result.append({"characteristic_id":cid,"value":value,"values":[value]})
    return result

def sync_offer_to_kit(offer_id, expected_kit_variant_id=None):
    oz=OzonClient(); kit=BRIDGE.KitClient()
    rb=ozon_readback(oz,offer_id)
    card=rb["attributes"]; info=rb["info"]
    dc=card.get("description_category_id") or info.get("description_category_id")
    tid=card.get("type_id") or info.get("type_id")
    if not dc or not tid:
        raise RuntimeError(f"Ozon {offer_id}: description_category_id/type_id missing")
    schema=attribute_schema(oz,dc,tid)

    vid=exact_kit_variant(kit,offer_id)
    if expected_kit_variant_id and vid!=expected_kit_variant_id:
        raise RuntimeError(f"KIT {offer_id}: variant mismatch expected {expected_kit_variant_id}, got {vid}")
    current=kit.request("GET",f"/v1/variants/{vid}")
    rows,by_title=characteristic_index(kit)
    char_meta={s(x.get("id")):s(x.get("title")) for x in rows}

    customer_values=customer_attribute_values(card,schema)
    customer_titles={norm(x["title"]) for x in customer_values}

    keep=[]
    removed=[]
    replaced=[]
    for c in current.get("characteristics") or []:
        title=char_meta.get(s(c.get("characteristic_id")),"")
        if is_ozon_characteristic_title(title):
            removed.append({"title":title,"value":c.get("value")})
            continue
        if norm(title) in customer_titles:
            replaced.append({"title":title,"value":c.get("value")})
            continue
        keep.append(c)

    # KIT storefront must contain only normal human-readable product characteristics.
    # Ozon API IDs, dictionary IDs, product IDs, statuses, readback timestamps and other
    # integration metadata stay in the report/Google Sheet and are never written as KIT characteristics.
    canonical=build_customer_rows(kit,rows,by_title,customer_values)
    patch={"characteristics":keep+canonical}
    kit.patch_variant(vid,patch)

    verify=kit.request("GET",f"/v1/variants/{vid}")
    rows2=kit.characteristics()
    titles2={s(x.get("id")):s(x.get("title")) for x in rows2}
    assigned=[]
    for c in verify.get("characteristics") or []:
        title=titles2.get(s(c.get("characteristic_id")),"")
        assigned.append({"title":title,"value":s(c.get("value"))})

    leftovers=[x for x in assigned if is_ozon_characteristic_title(x["title"])]
    if leftovers:
        raise RuntimeError(f"KIT {offer_id}: technical Ozon characteristics still assigned: {leftovers[:10]}")

    actual_by_title={norm(x["title"]):x["value"] for x in assigned if x["title"]}
    missing=[]
    for x in customer_values:
        if actual_by_title.get(norm(x["title"])) != s(x["value"]):
            missing.append({"title":x["title"],"expected":x["value"],"actual":actual_by_title.get(norm(x["title"]))})
    if missing:
        raise RuntimeError(f"KIT {offer_id}: customer Ozon characteristics readback mismatch: {missing[:10]}")

    return {
        "offer_id":offer_id,
        "ozon_product_id":rb["product_id"],
        "kit_variant_id":vid,
        "removed_technical_ozon_count":len(removed),
        "replaced_same_name_count":len(replaced),
        "customer_characteristic_count":len(customer_values),
        "status":"КЛИЕНТСКИЕ ХАРАКТЕРИСТИКИ ИЗ OZON",
    }

def main():
    offer=s(os.environ.get("OZON_POSTPUBLISH_OFFER_ID"))
    if not offer:
        raise RuntimeError("OZON_POSTPUBLISH_OFFER_ID is required")
    expected=s(os.environ.get("KIT_VARIANT_ID"))
    result=sync_offer_to_kit(offer,expected or None)
    out=ROOT/"catalog"/"ozon_postpublish_to_kit_report.json"
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__":
    main()
