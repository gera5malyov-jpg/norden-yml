#!/usr/bin/env python3
import json, os, re, time, unicodedata
from pathlib import Path
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests
import gspread
from google.oauth2.service_account import Credentials

TYPE_NAME = "NORDEN-100"
ROOT = Path(__file__).resolve().parents[1]
LOCAL_YML = ROOT / "norden.yml"
REPORT = ROOT / "catalog" / "norden_yml_id_report.json"

SPREADSHEET_ID = os.environ["CATALOG_SPREADSHEET_ID"].strip()
SHEET_NAME = os.environ.get("CATALOG_SHEET", "Норден").strip()
WA_BASE = (os.environ.get("WEBASYST_BASE_URL") or "https://profikompany.ru").rstrip("/")
WA_TOKEN = os.environ["WEBASYST_API_TOKEN"].strip()
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

def s(v): return str(v or "").strip()
def norm(v): return re.sub(r"[^0-9a-zа-яё]+","",unicodedata.normalize("NFKC",s(v)).casefold())

def listify(payload, keys=()):
    if isinstance(payload,list): return [x for x in payload if isinstance(x,dict)]
    if not isinstance(payload,dict): return []
    for k in keys:
        v=payload.get(k)
        if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
        if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
    if payload and all(isinstance(v,dict) for v in payload.values()):
        return list(payload.values())
    return []

def product_skus(product):
    v=product.get("skus")
    if isinstance(v,dict): return [x for x in v.values() if isinstance(x,dict)]
    if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
    return []

class WA:
    def __init__(self):
        self.session=requests.Session()
        self.session.headers.update({"Accept":"application/json","User-Agent":"megapolis-catalog-yml-id/1.0"})
        self.last=0.0
    def call(self,method,params=None):
        p=dict(params or {})
        p["format"]="json"; p["access_token"]=WA_TOKEN
        for attempt in range(10):
            delay=0.18-(time.monotonic()-self.last)
            if delay>0: time.sleep(delay)
            self.last=time.monotonic()
            r=self.session.get(f"{WA_BASE}/api.php/{method}",params=p,timeout=90)
            if r.status_code==429:
                time.sleep(float(r.headers.get("Retry-After") or min(30,2*(attempt+1)))); continue
            if r.status_code>=500:
                time.sleep(min(20,2**attempt)); continue
            try: data=r.json()
            except Exception: raise RuntimeError(f"{method}: non-JSON HTTP {r.status_code}")
            if r.status_code>=400: raise RuntimeError(f"{method}: HTTP {r.status_code}: {str(data)[:800]}")
            if isinstance(data,dict) and data.get("error"):
                raise RuntimeError(f"{method}: {data.get('error')}: {data.get('error_description') or ''}")
            return data
        raise RuntimeError(f"{method}: retries exhausted")

def load_wa_products(wa,type_id):
    out=[]; offset=0
    while True:
        d=wa.call("shop.product.search",{
            "hash":f"type/{type_id}",
            "offset":offset,
            "limit":1000,
            "fields":"*,skus,stock_counts",
        })
        batch=listify(d,("products","items"))
        out.extend(batch)
        total=(d.get("count") or d.get("total_count")) if isinstance(d,dict) else None
        if not batch or len(batch)<1000: break
        if total not in (None,"") and len(out)>=int(total): break
        offset+=len(batch)
    return out

def load_yml_offers():
    offers=[]
    for event,elem in ET.iterparse(LOCAL_YML,events=("end",)):
        if elem.tag=="offer":
            oid=s(elem.attrib.get("id"))
            vendor=s(elem.findtext("vendorCode"))
            name=s(elem.findtext("name"))
            if oid:
                offers.append({
                    "id":oid,
                    "vendor":vendor,
                    "name":name,
                    "id_norm":norm(oid),
                    "vendor_norm":norm(vendor),
                })
            elem.clear()
    return offers

def match_offer_by_name(name,offers):
    n=norm(name)
    if not n: return "", "no-name"
    candidates=[]
    for o in offers:
        tokens=[x for x in (o["id_norm"],o["vendor_norm"]) if x]
        best=max([len(x) for x in tokens if x in n] or [0])
        if best:
            suffix=0
            for raw in (o["id"],o["vendor"]):
                if raw and s(name).casefold().endswith(raw.casefold()):
                    suffix=max(suffix,len(norm(raw)))
            candidates.append((suffix,best,o["id"]))
    if not candidates: return "", "no-match"
    candidates.sort(reverse=True)
    top=candidates[0]
    tied=[x for x in candidates if x[:2]==top[:2]]
    if len({x[2] for x in tied})==1:
        return top[2], "yml-name-match"
    return "", "ambiguous"

wa=WA()
types=listify(wa.call("shop.type.getList"))
matches=[x for x in types if norm(x.get("name") or x.get("title"))==norm(TYPE_NAME)]
if len(matches)!=1:
    raise RuntimeError(f"Expected exactly one Webasyst type {TYPE_NAME}, found {len(matches)}")
type_id=s(matches[0].get("id"))
products=load_wa_products(wa,type_id)

offers=load_yml_offers()

wa_by_article={}
wa_name_by_article={}
wa_yml_nonempty=0
wa_yml_blank=0
sample=[]
for p in products:
    yml_id=s(p.get("yml_id"))
    pname=s(p.get("name"))
    for sku in product_skus(p):
        article=s(sku.get("sku"))
        if not article: continue
        k=norm(article)
        if yml_id:
            wa_by_article[k]=yml_id
            wa_yml_nonempty+=1
            if len(sample)<20:
                sample.append({"article":article,"yml_id":yml_id,"product_name":pname})
        else:
            wa_yml_blank+=1
        wa_name_by_article[k]=pname

creds=json.loads(SA_JSON)
gc=gspread.authorize(Credentials.from_service_account_info(
    creds,scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
))
sh=gc.open_by_key(SPREADSHEET_ID)
ws=sh.worksheet(SHEET_NAME)
vals=ws.get_all_values()
if not vals: raise RuntimeError("Норден sheet is empty")
headers=list(vals[0])
if "YML ID" not in headers: headers.append("YML ID")
if ws.col_count<len(headers): ws.resize(cols=len(headers))
ws.update(
    range_name=f"A1:{gspread.utils.rowcol_to_a1(1,len(headers))}",
    values=[headers],value_input_option="RAW"
)
idx={h:i for i,h in enumerate(headers)}
a_col=idx["Артикул"]; n_col=idx["Название"]; y_col=idx["YML ID"]

result_values=[]
source_counts={"webasyst_yml_id":0,"webasyst_name_fallback":0,"ozon_yml_name_fallback":0,"unmatched":0}
unmatched=[]
ambiguous=[]
for row in vals[1:]:
    article=s(row[a_col] if a_col<len(row) else "")
    name=s(row[n_col] if n_col<len(row) else "")
    if not article:
        result_values.append([""]); continue
    k=norm(article)
    yml_id=wa_by_article.get(k,"")
    if yml_id:
        source_counts["webasyst_yml_id"]+=1
        result_values.append([yml_id]); continue

    candidate_name=wa_name_by_article.get(k) or name
    yml_id,reason=match_offer_by_name(candidate_name,offers)
    if yml_id:
        if k in wa_name_by_article:
            source_counts["webasyst_name_fallback"]+=1
        else:
            source_counts["ozon_yml_name_fallback"]+=1
        result_values.append([yml_id]); continue

    if reason=="ambiguous":
        ambiguous.append({"article":article,"name":candidate_name})
    else:
        unmatched.append({"article":article,"name":candidate_name})
    source_counts["unmatched"]+=1
    result_values.append([""])

# Authoritative rewrite of the whole YML ID column clears any earlier wrong values.
start=2
end=1+len(result_values)
if result_values:
    ws.update(
        range_name=f"{gspread.utils.rowcol_to_a1(start,y_col+1)}:{gspread.utils.rowcol_to_a1(end,y_col+1)}",
        values=result_values,
        value_input_option="RAW"
    )

report={
    "ok":True,
    "definition":"YML ID = Webasyst product.yml_id when available; otherwise exact/unique <offer id> from repository norden.yml.",
    "webasyst_type":TYPE_NAME,
    "webasyst_type_id":type_id,
    "webasyst_products":len(products),
    "webasyst_yml_nonempty":wa_yml_nonempty,
    "webasyst_yml_blank":wa_yml_blank,
    "yml_offers":len(offers),
    "catalog_rows":len(result_values),
    "source_counts":source_counts,
    "unmatched_count":len(unmatched),
    "unmatched":unmatched[:100],
    "ambiguous_count":len(ambiguous),
    "ambiguous":ambiguous[:100],
    "sample_webasyst_yml":sample,
}
REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False))
