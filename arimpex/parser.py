#!/usr/bin/env python3
"""arimpex.ru -> YML Pro. Only public pages; no KIT/Webasyst API."""
import ast, base64, hashlib, html, json, logging, os, re, sys, threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
import xml.etree.ElementTree as ET
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SITE = "https://arimpex.ru"
OUT = Path("arimpex/arimpex.yml")
WORKERS = int(os.getenv("ARIMPEX_WORKERS", "5"))
LOG = logging.getLogger("arimpex")
LOCAL = threading.local()
STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*JSON\.parse\(\s*'((?:\\.|[^'\\])*)'\s*\)\s*;", re.S)

def clean(x):
    return " ".join(html.unescape(str(x or "")).replace("\xa0", " ").split())

def safe(x):
    return "".join(c for c in str(x or "") if c in "\n\r\t" or 32 <= ord(c) <= 55295 or 57344 <= ord(c) <= 65533 or 65536 <= ord(c) <= 1114111)

def request(url, min_size=300):
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname not in ("arimpex.ru", "www.arimpex.ru"):
        raise ValueError("Refused unrelated URL: " + url)
    s = getattr(LOCAL, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ArimpexYMLPro/1.0)", "Accept-Language": "ru"})
        policy = Retry(total=4, backoff_factor=1.2, status_forcelist=(429,500,502,503,504),
                       allowed_methods=frozenset(["GET"]), respect_retry_after_header=True)
        s.mount("https://", HTTPAdapter(max_retries=policy))
        LOCAL.session = s
    r = s.get(url, timeout=(15, 45))
    r.raise_for_status()
    if urlsplit(r.url).hostname not in ("arimpex.ru", "www.arimpex.ru") or len(r.content) < min_size:
        raise ValueError("Unexpected page: " + url)
    return r.text

def state(url):
    match = STATE_RE.search(request(url))
    if not match:
        raise ValueError("No initial-state on " + url)
    data = json.loads(ast.literal_eval("'" + match.group(1) + "'"))
    if not isinstance(data.get("detail", {}).get("product"), dict):
        raise ValueError("No product in " + url)
    return data

def sitemap():
    root = ET.fromstring(request(SITE + "/sitemap/sitemap_products.xml"))
    urls = sorted(set(clean(e.text) for e in root.iter() if e.tag.endswith("loc")
                      and re.fullmatch(r"https://(?:www\.)?arimpex\.ru/product/[^/]+/", clean(e.text))))
    if len(urls) < int(os.getenv("ARIMPEX_MIN_MODELS", "120")):
        raise ValueError("Sitemap too small: %d" % len(urls))
    return urls

def category(st):
    v = tuple(clean(x.get("label")) for x in st.get("breadcrumbs",{}).get("items",[])
              if str(x.get("link") or "").startswith("/catalog/"))
    return v or ("Мебель",)

def id_of(p):
    return int(p["id"])

def load_model(url):
    st = state(url); p = st["detail"]["product"]
    if not clean(p.get("article")) or not p.get("pid"):
        raise ValueError("No article or product id on " + url)
    # The initial HTML includes only a small preview of offers. The configurator
    # fetches the actual complete list from /api/product/<model-eid>/offers/.
    # Never create an artificial Cartesian product of colors: only real supplier SKUs.
    options = st.get("detail", {}).get("options") or []
    configurable = any(x.get("name") == "configurator" and x.get("enabled") for x in options)
    offers = list((p.get("offers") or {}).values())
    eid = str(p.get("eid") or "").split("#")[0]
    if eid:
        api_url = SITE + "/api/product/" + quote(eid, safe="") + "/offers/"
        payload = json.loads(request(api_url, min_size=2))
        if not isinstance(payload, dict) or not isinstance(payload.get("offers"), (dict, list)):
            raise ValueError("Invalid full offers API response: " + url)
        full = payload["offers"]
        complete = list(full.values()) if isinstance(full, dict) else full
        if complete:
            offers = complete
        elif configurable:
            raise ValueError("Configurator returned no SKUs: " + url)
    elif configurable:
        raise ValueError("Configurator missing model EID: " + url)
    if id_of(p) not in [id_of(x) for x in offers]:
        if configurable:
            raise ValueError("Main SKU missing from complete configurator: " + url)
        offers.append({"id": p["id"], "pid": p["pid"], "link": p.get("link")})
    if not offers or len(set(id_of(x) for x in offers)) != len(offers):
        raise ValueError("Invalid or duplicated SKU list: " + url)
    for offer in offers:
        if str(offer.get("pid")) != str(p["pid"]):
            raise ValueError("Cross-model SKU in full configurator: " + url)
        if configurable and not offer.get("configuration"):
            raise ValueError("SKU without configuration: " + url)
    LOG.info("Model %s: %d actual supplier SKUs", p["article"], len(offers))
    return {"url":url, "base":p, "category":category(st), "offers":offers}

def get_variant(task):
    parent, offer = task; base = parent["base"]
    if id_of(base) == id_of(offer):
        p = base
    else:
        link = offer.get("link")
        if not str(link or "").startswith("/product/"):
            raise ValueError("Bad SKU link: " + str(link))
        p = state(urljoin(SITE, link))["detail"]["product"]
    if id_of(p) != id_of(offer) or str(p.get("pid")) != str(base["pid"]):
        raise ValueError("Mismatched supplier SKU id=%s" % offer["id"])
    if clean(p.get("article")) != clean(base.get("article")):
        raise ValueError("Wrong article for id=%s" % offer["id"])
    if not current_price(p):
        raise ValueError("Missing price for id=%s" % offer["id"])
    return (p, parent, offer)

def current_price(p, kind="current"):
    for i in p.get("prices") or []:
        if i.get("type") == kind:
            try:
                n = Decimal(str(i.get("value")))
                if n.is_finite() and n > 0:
                    return format(n, "f")
            except (InvalidOperation, TypeError, ValueError):
                pass
    return None

def picture(src):
    src = str(src or "")
    parsed = urlsplit(src)
    if parsed.scheme != "https" or parsed.hostname not in ("arimpex.ru","www.arimpex.ru","imgproxy.arimpex.ru"):
        return None
    if parsed.hostname == "imgproxy.arimpex.ru":
        parts = parsed.path.split("/")
        start = next((j for j,v in enumerate(parts) if v.startswith("aHR0cHM6")), None)
        if start is not None:
            encoded = re.sub(r"\.(?:jpg|jpeg|png|webp)$", "", "".join(parts[start:]), flags=re.I)
            try:
                original = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded)%4)).decode("utf-8")
                q = urlsplit(original)
                if q.scheme == "https" and q.hostname in ("arimpex.ru","www.arimpex.ru"):
                    return original
            except (ValueError, UnicodeDecodeError):
                pass
    return src

def pictures(p):
    result = []
    for img in p.get("images") or []:
        value = picture(img.get("src"))
        if value and value not in result:
            result.append(value)
    return result

def availability(p):
    a = p.get("availability") or {}
    status = str(a.get("state") or "unknown")
    sums = {str(x.get("type")): x.get("value") for x in a.get("quantities") or [] if isinstance(x,dict)}
    try:
        count = max(0, min(100000, int(sums.get("available", sums.get("store",0)))))
    except (TypeError, ValueError):
        count = 0
    return status, count if status=="buy" else 0

def category_id(parts):
    return str(1000 + int(hashlib.sha256("/".join(parts).encode()).hexdigest()[:12],16) % 1999000000)

def variant_name(p):
    """Independent product title describing the supplier's actual configuration."""
    title = clean(p.get("name"))
    parts = []
    for group in p.get("configuration") or []:
        label = clean(group.get("name"))
        values = [clean(" ".join(str(z or "") for z in (item.get("name"), item.get("value"))))
                  for item in group.get("items") or []]
        value = ", ".join(x for x in values if x)
        if label and value:
            parts.append(label + ": " + value)
    return title + (" (" + "; ".join(parts) + ")" if parts else "")

def xml_feed(rows):
    root = ET.Element("yml_catalog", date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    shop = ET.SubElement(root, "shop")
    for k,v in (("name","А.Р. ИМПЭКС"),("company","А.Р. ИМПЭКС"),("url",SITE)):
        ET.SubElement(shop,k).text = v
    ET.SubElement(ET.SubElement(shop,"currencies"),"currency",{"id":"RUB","rate":"1"})
    cats = ET.SubElement(shop,"categories")
    ET.SubElement(cats,"category",{"id":"1"}).text="А.Р. ИМПЭКС"
    paths = {p[:i] for _,parent,_ in rows for p in [parent["category"]] for i in range(1,len(p)+1)}
    cat_ids = {p:category_id(p) for p in paths}
    if len(set(cat_ids.values())) != len(cat_ids):
        raise ValueError("Category hash collision")
    for p in sorted(paths,key=lambda v:(len(v),v)):
        ET.SubElement(cats,"category",{"id":cat_ids[p], "parentId":cat_ids[p[:-1]] if len(p)>1 else "1"}).text=safe(p[-1])
    offers=ET.SubElement(shop,"offers")
    seen=set()
    title_counts = Counter(variant_name(p).casefold() for p,_,_ in rows)
    for p,parent,off in sorted(rows,key=lambda x:id_of(x[0])):
        sku=id_of(p)
        if sku in seen: raise ValueError("Duplicate SKU "+str(sku))
        seen.add(sku)
        status,count=availability(p)
        elt=ET.SubElement(offers,"offer",{"id":"arimpex-"+str(sku),
            "available":"true" if status in ("buy","on-order") else "false"})
        link=urljoin(SITE,p.get("link") or off.get("link") or parent["url"])
        if not link.startswith(SITE+"/product/"): raise ValueError("Invalid variant URL "+link)
        data={"categoryId":cat_ids[parent["category"]],
              "name":variant_name(p) + (" [ARIMPEX-" + str(sku) + "]" if title_counts[variant_name(p).casefold()] > 1 else ""),
              "url":link,"price":current_price(p),"currencyId":"RUB",
              "vendorCode":"ARIMPEX-"+str(sku), "model":clean(p["article"]), "count":str(count)}
        brand=clean((p.get("brand") or {}).get("name"))
        if brand: data["vendor"]=brand
        for k,v in data.items(): ET.SubElement(elt,k).text=safe(v)
        old=current_price(p,"old")
        if old and Decimal(old)>Decimal(data["price"]): ET.SubElement(elt,"oldprice").text=old
        photos=pictures(p)
        if not photos: photos=pictures(parent["base"])
        for v in photos: ET.SubElement(elt,"picture").text=v
        desc=clean(p.get("description"))
        if desc: ET.SubElement(elt,"description").text=safe(desc)
        params=[("Артикул производителя",clean(p["article"])),
                ("ID модификации поставщика",str(sku)),
                ("Наличие у поставщика",{"buy":"В наличии","on-order":"Под заказ"}.get(status,status))]
        eid=str(p.get("eid") or "")
        if "#" in eid: params.append(("Внешний ID модификации",eid.split("#")[-1]))
        for s in (p.get("specifications") or {}).get("items") or []:
            params.append((clean(s.get("name")),clean(s.get("value"))))
        for cfg in p.get("configuration") or []:
            vals=[clean((x.get("name") or "")+" "+(x.get("value") or "")) for x in cfg.get("items") or []]
            params.append((clean(cfg.get("name")),", ".join(v for v in vals if v)))
        param_names=set()
        for k,v in params:
            if k and v and k not in param_names:
                param_names.add(k)
                ET.SubElement(elt,"param",{"name":safe(k)}).text=safe(v)
    ET.indent(root,space="  ")
    content=b'<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding="utf-8")+b"\n"
    ET.fromstring(content)
    return content,seen

def parallel(f,items):
    result=[]; errors=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures={pool.submit(f,x):x for x in items}
        for n,task in enumerate(as_completed(futures),1):
            try: result.append(task.result())
            except Exception as exc: errors.append((str(futures[task])[:180],str(exc)))
            if n%100==0: LOG.info("Processed %d/%d",n,len(items))
    if errors: raise RuntimeError("Failed %d records. Examples: %s" % (len(errors),errors[:7]))
    return result

def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    links=sitemap()
    LOG.info("Sitemap models: %d",len(links))
    parents=parallel(load_model,links)
    tasks=[(parent,offer) for parent in parents for offer in parent["offers"]]
    if len({id_of(o) for p,o in tasks})!=len(tasks): raise ValueError("SKU id shared between models")
    LOG.info("Actual variants: %d",len(tasks))
    rows=parallel(get_variant,tasks)
    if len(rows)!=len(tasks) or len(rows)<len(links): raise ValueError("Incomplete catalog")
    content,ids=xml_feed(rows)
    if OUT.exists():
        old={x.get("id") for x in ET.parse(OUT).findall("./shop/offers/offer")}
        cur={"arimpex-"+str(x) for x in ids}
        missing=len(old-cur)
        if old and os.getenv("ARIMPEX_ALLOW_SHRINK")!="1" and (missing>max(5,len(old)*.15) or len(cur)<len(old)*.8):
            raise ValueError("Unsafe catalog shrink: %d of %d old offers missing" % (missing,len(old)))
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix(".yml.tmp"); temp.write_bytes(content); temp.replace(OUT)
    LOG.info("PUBLISHED %d products, %d variants, %d bytes",len(links),len(ids),len(content))

if __name__=="__main__":
    try: main()
    except Exception:
        LOG.exception("Arimpex feed generation failed. Old feed preserved.")
        sys.exit(1)
