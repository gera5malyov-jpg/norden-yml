#!/usr/bin/env python3
"""Sanitize already published Artikul-Mebel YML without accessing supplier's blocked HTTP endpoint."""
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import re
import sys
import xml.etree.ElementTree as ET

from parser import sanitize_description, clean_params

PATH = Path("catalog.yml")
IMAGE_PATH = re.compile(r"^/upload/resize_cache/(iblock|uf)/([^/]+)/[^/]+/(.+\.(?:jpe?g|png|webp))$",re.I)

def source_url(raw):
    url=(raw or "").strip()
    p=urlsplit(url)
    if p.scheme!="https" or p.hostname not in ("artikul-mebel.ru","www.artikul-mebel.ru"):
        return url
    m=IMAGE_PATH.fullmatch(p.path)
    if m:
        return urlunsplit(p._replace(path="/upload/"+m[1]+"/"+m[2]+"/"+m[3],query="",fragment=""))
    return url

def main():
    if not PATH.exists():
        raise SystemExit("No previous catalog, refusing to generate empty output")
    tree=ET.parse(PATH)
    root=tree.getroot()
    if root.tag!="yml_catalog":
        raise RuntimeError("Unexpected XML root")
    offers=root.findall("./shop/offers/offer")
    if len(offers)<100:
        raise RuntimeError(f"Existing YML has too few offers: {len(offers)}")
    ids=[o.attrib["id"] for o in offers]
    if len(set(ids))!=len(ids):
        raise RuntimeError("Duplicate offer IDs - refuse to rewrite")
    image_total=image_changed=description_changed=removed_params=0
    for o in offers:
        for pic in o.findall("picture"):
            image_total+=1
            old=pic.text or ""
            new=source_url(old)
            if old!=new:
                pic.text=new
                image_changed+=1
        for descr in o.findall("description"):
            old=descr.text or ""
            new=sanitize_description(old)
            if old!=new:
                descr.text=new
                description_changed+=1
        for param in o.findall("param"):
            if not clean_params({param.get("name") or "":param.text or ""}):
                o.remove(param)
                removed_params+=1
    if not image_total:
        raise RuntimeError("No images in existing feed, refusing rewriting")
    ET.indent(root,space="  ")
    payload=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    ET.fromstring(payload)
    if len(payload)<10000:
        raise RuntimeError("Rewritten feed too small")
    tmp=PATH.with_suffix(".yml.tmp")
    tmp.write_bytes(payload)
    tmp.replace(PATH)
    print(f"EXISTING_YML_CLEANED offers={len(offers)} images={image_total} image_urls_changed={image_changed} sanitized_descriptions={description_changed} removed_contact_params={removed_params} bytes={len(payload)}")
    if image_changed==0:
        print("WARNING: No resize_cache photo links were present in the previous YML")

if __name__=="__main__":
    try: main()
    except Exception as e:
        print("FIX_YML_FAILURE="+str(e),file=sys.stderr)
        raise
