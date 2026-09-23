#!/usr/bin/env python3
import json, re, sys
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

URLS = [
    "https://fh-mebel.ru/shop/krovati/dvuspalnye/adel-krovat-dvuspalnaya/",
    "https://fh-mebel.ru/shop/shkaf/shkafy-uglovye/shkaf-uglovoy-tiffani/",
    "https://fh-mebel.ru/shop/spalnya/modulnye/adel-spalnya/",
]

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/153 Safari/537.36 FH-Mebel-KIT-Probe"

def text(x):
    return " ".join((x or "").split())

def main():
    s=requests.Session()
    s.headers.update({"User-Agent":UA,"Accept-Language":"ru-RU,ru;q=0.9"})
    out=[]
    for url in URLS:
        r=s.get(url,timeout=60)
        print("\n=== URL",url,"status",r.status_code,"len",len(r.content),"===")
        if r.status_code != 200:
            out.append({"url":url,"status":r.status_code,"error":"HTTP"})
            continue
        html=r.text
        soup=BeautifulSoup(html,"html.parser")
        page={
            "url":url,
            "title": text(soup.title.get_text(" ",strip=True) if soup.title else ""),
            "h1": text(soup.h1.get_text(" ",strip=True) if soup.h1 else ""),
            "selects":[],
            "inputs":[],
            "price_nodes":[],
            "scripts":[],
            "links":[],
            "images":[],
            "param_controls":[],
        }
        for el in soup.find_all("select"):
            page["selects"].append({
                "attrs":dict(el.attrs),
                "text":text(el.get_text(" ",strip=True)),
                "options":[{"attrs":dict(o.attrs),"text":text(o.get_text(" ",strip=True))} for o in el.find_all("option")]
            })
        for el in soup.find_all("input"):
            attrs={k:v for k,v in el.attrs.items() if k not in {"style"}}
            blob=json.dumps(attrs,ensure_ascii=False).lower()
            parent=text(el.parent.get_text(" ",strip=True) if el.parent else "")
            if any(k in blob+" "+parent.lower() for k in ["price","mod","variant","size","razmer","mirror","zerkal","spal","color","cvet","id"]):
                page["inputs"].append({"attrs":attrs,"parent_text":parent[:500]})
                if "js_affect_param_value" in (el.get("class") or []) and str(el.get("name") or "").startswith("param"):
                    par=el.parent
                    page["param_controls"].append({"input":attrs,"parent_html":str(par)[:5000],"parent_text":parent[:1200]})
        for el in soup.find_all(True):
            cls=" ".join(el.get("class",[])).lower()
            eid=(el.get("id") or "").lower()
            tx=text(el.get_text(" ",strip=True))
            if ("price" in cls or "price" in eid or re.search(r"\b\d[\d\s,.]{2,}\s*руб",tx,re.I)) and len(tx)<500:
                page["price_nodes"].append({"tag":el.name,"id":el.get("id"),"class":el.get("class"),"text":tx[:450],"attrs":dict(el.attrs)})
                if len(page["price_nodes"])>=80: break
        for node in soup.find_all(True):
            d={k:v for k,v in node.attrs.items() if str(k).startswith("data-param")}
            if d:
                page["param_controls"].append({"tag":node.name,"attrs":dict(node.attrs),"data_params":d,"text":text(node.get_text(" ",strip=True))[:700],"html":str(node)[:5000]})
        for sc in soup.find_all("script"):
            body=sc.string or sc.get_text("\n")
            if not body: continue
            low=body.lower()
            if any(k in low for k in ["price","variant","modif","offer","product","razmer","размер"]):
                snippets=[]
                for m in re.finditer(r".{0,200}(?:price|variant|modif|offer|razmer|размер).{0,500}", body, re.I|re.S):
                    snippets.append(text(m.group(0))[:700])
                    if len(snippets)>=8: break
                page["scripts"].append({"attrs":dict(sc.attrs),"snippets":snippets})
                if len(page["scripts"])>=20: break
        for a in soup.find_all("a",href=True):
            href=urljoin(url,a["href"])
            if "/shop/cart/" in href or "/shop/" in href:
                page["links"].append({"href":href,"text":text(a.get_text(" ",strip=True))[:200],"attrs":dict(a.attrs)})
        for im in soup.find_all("img"):
            src=im.get("data-src") or im.get("data-original") or im.get("src")
            if src and "/userfiles/shop/" in src:
                page["images"].append(urljoin(url,src))
        # Raw snippets around likely modification markers
        raws=[]
        for pat in [r"modif.{0,2000}",r"variant.{0,2000}",r"price.{0,2000}",r"spal.{0,2000}",r"razmer.{0,2000}"]:
            for m in re.finditer(pat,html,re.I|re.S):
                raws.append(text(m.group(0))[:1800])
                if len(raws)>=20: break
            if len(raws)>=20: break
        page["raw_snippets"]=raws
        out.append(page)
    print("\n=== JSON ===")
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
