#!/usr/bin/env python3
"""Read-only diagnostics for visible broken external image links on profikompany.ru."""
from collections import Counter
from urllib.parse import urlparse
import json,re,requests,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"webasyst"))
from client import WebasystClient

sample="http://5.35.127.160/images/1c/38342043352038452045372033452033-001.jpg"
pages=[
"https://profikompany.ru/barselona-kofeynyy-stolik-iz-iskusstvennogo-rotanga-tsvet-korichnevyy/",
"https://4sis.ru/",
"https://4s-mebel.ru/",
"http://5.35.127.160/",
"https://5.35.127.160/",
sample,
sample.replace("http:","https:",1),
]
for url in pages:
    try:
        r=requests.get(url,timeout=(5,15),verify=False,headers={"User-Agent":"Mozilla/5.0"})
        d={"url":url,"status":r.status_code,"final_url":r.url,"length":len(r.content),
           "content_type":r.headers.get("Content-Type",""),"csp":r.headers.get("Content-Security-Policy","")[:200]}
        if "profikompany" in url:
            s=r.text
            for name,pattern in [
              ("main_ext_image",r'<img[^>]+class="[^"]*js-extimg-main-img[^"]*"[^>]*>'),
              ("thumb",r'<img[^>]+class="[^"]*js-extimg-thumb[^"]*"[^>]*>'),
              ("all_ip_urls",r'(?:https?:)?//5\.35\.127\.160[^"\s<>]+')]:
                a=re.findall(pattern,s,re.I)
                d[name]=a[:5] if name!="all_ip_urls" else len(a)
            d["has_shortcodes"]="[extimg]" in s
        print("URL_CHECK",json.dumps(d,ensure_ascii=False),flush=True)
    except Exception as e:print("URL_ERROR",json.dumps({"url":url,"error":repr(e)[:250]},ensure_ascii=False),flush=True)
try:
    wa=WebasystClient()
    r=wa.call("shop.product.search",params={"hash":"search/query=AF-31668995","limit":10,
           "fields":"id,name,type_id,summary,skus,url,image_id"})
    if isinstance(r,dict):
        v=r.get("products",r.get("items",r))
        if isinstance(v,dict):v=list(v.values())
    else:v=r
    print("WA_PRODUCTS",json.dumps([
       {"id":p.get("id"),"name":p.get("name"),"summary":str(p.get("summary") or "")[:1800],
         "image_id":p.get("image_id"),"type_id":p.get("type_id")} for p in v if isinstance(p,dict)][:5],
        ensure_ascii=False),flush=True)
except Exception as e:print("WA_ERROR",repr(e)[:300])
try:
    url="https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/4s-mebel.yml"
    r=requests.get(url,timeout=(10,60))
    print("FEED_STATUS",r.status_code,len(r.content),r.headers.get("Content-Type"))
    if r.ok:
        txt=r.text
        hosts=Counter(re.findall(r'<picture>https?://([^/<]+)',txt))
        idx=txt.lower().find("барселона")
        print("FEED_PIC_HOSTS",hosts.most_common(20))
        print("FEED_BARCELONA",txt[max(0,idx-400):idx+800] if idx!=-1 else "NOT_FOUND")
except Exception as e:print("FEED_ERROR",repr(e)[:300])
