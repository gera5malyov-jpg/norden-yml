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
