#!/usr/bin/env python3
import json, requests
from urllib.parse import urljoin
urls=[
"https://norden.group/images/detailed/719/00-00010488___B1816_3S_fabric_LE8100-07_47b22bfc-3002-11f0-bb0d-c5a378e5bbc4.jpg",
"https://norden.group/images/detailed/719/00-00010488___B1816_3S_fabric_LE8100-07_47b22bfd-3002-11f0-bb0d-c5a378e5bbc4.jpg",
"https://norden.group/images/detailed/719/00-00010488___B1816_3S_fabric_LE8100-07_47b22bfe-3002-11f0-bb0d-c5a378e5bbc4.jpg",
"https://norden.group/images/detailed/719/00-00010488___B1816_3S_fabric_LE8100-07_47b22bfb-3002-11f0-bb0d-c5a378e5bbc4.jpg"]
out=[]
for start in urls:
    u=start; chain=[]
    sess=requests.Session()
    for i in range(12):
        r=sess.get(u,headers={"User-Agent":"Mozilla/5.0","Referer":"https://norden.group/"},allow_redirects=False,timeout=30)
        chain.append({"url":u,"status":r.status_code,"location":r.headers.get("Location"),"content_type":r.headers.get("Content-Type"),"len":len(r.content)})
        if r.status_code not in (301,302,303,307,308) or not r.headers.get("Location"):
            break
        u=urljoin(u,r.headers["Location"])
    out.append({"start":start,"chain":chain})
open("catalog/corfu_redirect_diag.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps(out,ensure_ascii=False,indent=2))
