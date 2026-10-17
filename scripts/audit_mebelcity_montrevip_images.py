#!/usr/bin/env python3
"""Read-only external and native image diagnostics for all requested screenshot types."""
import collections,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from webasyst.client import WebasystClient
from scripts.refresh_selected_product_external_images import listify,products_for_type,summary_urls
NAMES=["mebelcity стулья","montre-vip"]

def main():
 wa=WebasystClient(min_request_interval=0.13)
 types=listify(wa.call("shop.type.getList"),keys=("types","items"))
 rows=[]
 for name in NAMES:
  match=[t for t in types if str(t.get("name") or "").casefold()==name.casefold()]
  if len(match)!=1:
   print("TYPE_NATIVE_AUDIT",json.dumps({"type":name,"not_found":len(match)==0,"ambiguous":len(match)>1},ensure_ascii=False),flush=True)
   continue
  p=products_for_type(wa,int(match[0]["id"]))
  c=collections.Counter();examples={}
  for item in p:
   summary=str(item.get("summary") or "")
   urls,q=summary_urls(summary)
   native=bool(item.get("image_id")) or bool(item.get("images"))
   other_http=bool(re.search(r"https?://",summary,re.I)) and not urls
   flags={"native_image":native,"extimg_gallery":bool(urls),"missing_both":not native and not urls,
          "summary_text_without_shortcode":bool(summary.strip()) and not urls,
          "other_http_without_extimg":other_http,
          "external_only":bool(urls) and not native,
          "native_only":native and not urls,
          "native_and_external":native and bool(urls),
          "no_native_but_text":not native and bool(summary.strip()) and not urls}
   for k,v in flags.items():
    if v:
     c[k]+=1
     if k not in examples:examples[k]={"id":item.get("id"),"name":str(item.get("name"))[:100],
              "summary_head":summary[:190],"image_id":str(item.get("image_id"))[:50],
              "url":str(item.get("url") or "")[:140]}
  c["products"]=len(p)
  entry={"type":name,"type_id":match[0]["id"],**dict(c),"examples":examples}
  rows.append(entry)
  print("TYPE_NATIVE_AUDIT",json.dumps(entry,ensure_ascii=False),flush=True)
 Path("reports").mkdir(exist_ok=True)
 Path("reports/mebelcity_montrevip_native_images_20261017.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
 print("TYPE_NATIVE_AUDIT_FINAL",json.dumps({"types":len(rows),
  "products":sum(x["products"] for x in rows),
  "missing_both":sum(x.get("missing_both",0) for x in rows),
  "native_only":sum(x.get("native_only",0) for x in rows),
  "extimg_gallery":sum(x.get("extimg_gallery",0) for x in rows)},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
