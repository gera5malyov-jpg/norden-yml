import json
import os
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://price.tetchair.ru"
LOGIN = os.environ["TETCHAIR_LOGIN"].strip()
PASSWORD = os.environ["TETCHAIR_PASSWORD"].strip()

s = requests.Session()
s.headers.update({"User-Agent": "Megapolis-Tetchair-Sync/1.0"})

r = s.get(BASE + "/catalog/", timeout=40)
r.raise_for_status()
r = s.post(
    BASE + "/login/",
    data={"login": LOGIN, "pass": PASSWORD},
    timeout=40,
    allow_redirects=True,
)
r.raise_for_status()

report = {
    "login_ok": False,
    "login_final_url": r.url,
    "pages": {},
}

soup = BeautifulSoup(r.text, "html.parser")
report["login_ok"] = not bool(soup.select_one("form.login_form"))

for path in ("/catalog/", "/sklad/"):
    resp = s.get(BASE + path, timeout=60)
    resp.raise_for_status()
    page = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a in page.find_all("a", href=True):
        href = urljoin(BASE, a["href"])
        if any(x in href for x in ("/catalog/", "/sklad/", "/product/", "#product")):
            links.append(href)
    items = []
    for el in page.select(".product_table_item, .product_item")[:5]:
        attrs = {k: v for k, v in el.attrs.items() if k.startswith("data-") or k in ("class", "id")}
        text = " ".join(el.get_text(" ", strip=True).split())[:1200]
        items.append({"attrs": attrs, "text": text})
    product_ids = sorted(set(re.findall(r"(?:data-p=[\"']|#product|/product/\?p=)(\d+)", resp.text)))[:30]
    report["pages"][path] = {
        "status": resp.status_code,
        "url": resp.url,
        "title": page.title.get_text(" ", strip=True) if page.title else "",
        "has_login_form": bool(page.select_one("form.login_form")),
        "link_count": len(links),
        "links_sample": links[:50],
        "product_ids_sample": product_ids,
        "product_items_sample": items,
        "classes_sample": sorted({
            cls
            for tag in page.find_all(class_=True)
            for cls in (tag.get("class") or [])
            if "product" in cls.lower() or "catalog" in cls.lower()
        })[:100],
    }

print(json.dumps(report, ensure_ascii=False, indent=2))
if not report["login_ok"]:
    raise SystemExit("Tetchair login failed")


catalog = s.get(BASE + "/catalog/", timeout=60)
catalog.raise_for_status()
catalog_soup = BeautifulSoup(catalog.text, "html.parser")
category_urls = []
for a in catalog_soup.find_all("a", href=True):
    href = urljoin(BASE, a["href"])
    if re.fullmatch(r"https://price\.tetchair\.ru/catalog/\d+/", href):
        if href not in category_urls:
            category_urls.append(href)

report["category_count"] = len(category_urls)
report["category_samples"] = []
first_product_id = None
hits = 0

for url in category_urls:
    resp = s.get(url, timeout=60)
    resp.raise_for_status()
    page = BeautifulSoup(resp.text, "html.parser")
    product_nodes = page.select(".product_table_item, .product_item, [data-p][data-pr], [data-p]")
    ids = sorted(set(re.findall(r"data-p=[\\\"'](\\d+)", resp.text)))[:100]
    if not product_nodes and not ids:
        continue
    sample_nodes = []
    for el in product_nodes[:8]:
        attrs = {
            k: v for k, v in el.attrs.items()
            if k.startswith("data-") or k in ("class", "id")
        }
        text_value = " ".join(el.get_text(" ", strip=True).split())[:1800]
        sample_nodes.append({"attrs": attrs, "text": text_value})
        if first_product_id is None:
            pid = el.get("data-p")
            if pid and str(pid).isdigit():
                first_product_id = str(pid)
    if first_product_id is None and ids:
        first_product_id = ids[0]
    report["category_samples"].append({
        "url": url,
        "title": page.title.get_text(" ", strip=True) if page.title else "",
        "product_nodes": len(product_nodes),
        "product_ids_sample": ids,
        "nodes_sample": sample_nodes,
        "text_sample": " ".join(page.get_text(" ", strip=True).split())[-3500:],
    })
    hits += 1
    if hits >= 5:
        break

if first_product_id:
    detail = s.get(BASE + "/product/?p=" + first_product_id, timeout=60)
    detail.raise_for_status()
    ds = BeautifulSoup(detail.text, "html.parser")
    report["product_detail_sample"] = {
        "product_id": first_product_id,
        "url": detail.url,
        "title": ds.title.get_text(" ", strip=True) if ds.title else "",
        "text": " ".join(ds.get_text(" ", strip=True).split())[:6000],
        "images": [
            urljoin(BASE, x.get("src"))
            for x in ds.find_all("img", src=True)
        ][:30],
        "data_attrs": [
            {
                "tag": el.name,
                "attrs": {
                    k: v for k, v in el.attrs.items()
                    if k.startswith("data-") or k in ("class", "id")
                },
                "text": " ".join(el.get_text(" ", strip=True).split())[:800],
            }
            for el in ds.find_all(attrs={"data-p": True})[:20]
        ],
        "tables": [
            [
                [" ".join(cell.get_text(" ", strip=True).split()) for cell in row.find_all(["th","td"])]
                for row in table.find_all("tr")[:30]
            ]
            for table in ds.find_all("table")[:5]
        ],
    }

print("DEEP_INSPECTION")
print(json.dumps({
    "category_count": report.get("category_count"),
    "category_samples": report.get("category_samples"),
    "product_detail_sample": report.get("product_detail_sample"),
}, ensure_ascii=False, indent=2))


sklad = s.get(BASE + "/sklad/", timeout=60)
sklad.raise_for_status()
ss = BeautifulSoup(sklad.text, "html.parser")
selects = []
for sel in ss.find_all("select"):
    selects.append({
        "name": sel.get("name"),
        "id": sel.get("id"),
        "class": sel.get("class"),
        "options": [
            {
                "value": opt.get("value"),
                "text": " ".join(opt.get_text(" ", strip=True).split()),
                "data": {k: v for k, v in opt.attrs.items() if k.startswith("data-")},
            }
            for opt in sel.find_all("option")[:300]
        ],
    })
forms = []
for form in ss.find_all("form"):
    forms.append({
        "action": form.get("action"),
        "method": form.get("method"),
        "inputs": [
            {"name": x.get("name"), "value": x.get("value"), "type": x.get("type")}
            for x in form.find_all(["input","select"])
            if x.get("name")
        ][:100],
    })
print("SKLAD_STRUCTURE")
print(json.dumps({
    "selects": selects,
    "forms": forms,
    "text_tail": " ".join(ss.get_text(" ", strip=True).split())[-5000:],
    "data_p_count": len(ss.select("[data-p]")),
    "data_pr_count": len(ss.select("[data-pr]")),
}, ensure_ascii=False, indent=2))
