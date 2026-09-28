import json
import math
import os
from pathlib import Path

import requests

OUT = Path("ozon/catalog_3x4/selection.json")
CLIENT_ID = os.environ["OZON_CLIENT_ID"].strip()
API_KEY = os.environ["OZON_API_KEY"].strip()

s = requests.Session()
s.headers.update({
    "Client-Id": CLIENT_ID,
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
})

def post(path, body):
    r = s.post("https://api-seller.ozon.ru" + path, json=body, timeout=90)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    if r.status_code >= 400:
        raise RuntimeError(f"{path} HTTP {r.status_code}: {json.dumps(data, ensure_ascii=False)[:3000]}")
    return data

def list_visibility(visibility):
    rows = []
    last_id = ""
    for _ in range(1000):
        d = post("/v3/product/list", {
            "filter": {"visibility": visibility},
            "last_id": last_id,
            "limit": 1000,
        })
        res = d.get("result") or {}
        batch = res.get("items") or []
        rows.extend(batch)
        new_last = res.get("last_id") or ""
        if not batch or not new_last or new_last == last_id:
            break
        last_id = new_last
        if len(batch) < 1000:
            break
    return rows

all_rows = list_visibility("ALL")
arch_rows = list_visibility("ARCHIVED")

merged = {}
for source, rows in (("ALL", all_rows), ("ARCHIVED", arch_rows)):
    for x in rows:
        pid = int(x.get("product_id") or x.get("id") or 0)
        offer = (x.get("offer_id") or "").strip()
        if not pid or not offer:
            continue
        row = merged.setdefault(pid, {
            "offer_id": offer,
            "product_id": pid,
            "sources": [],
        })
        if source not in row["sources"]:
            row["sources"].append(source)

offers = [x["offer_id"] for x in merged.values()]
infos = {}
for i in range(0, len(offers), 1000):
    d = post("/v3/product/info/list", {"offer_id": offers[i:i+1000]})
    for x in d.get("items") or []:
        pid = int(x.get("id") or x.get("product_id") or 0)
        if pid:
            infos[pid] = x

selected = []
for pid, base in merged.items():
    info = infos.get(pid, {})
    created = None
    for key in ("created_at", "created", "create_date", "created_time"):
        if info.get(key):
            created = info[key]
            break
    selected.append({
        "offer_id": base["offer_id"],
        "product_id": pid,
        "created_at": created,
        "archived": "ARCHIVED" in base["sources"],
        "sources": base["sources"],
    })

selected.sort(key=lambda x: (
    x.get("created_at") or "9999-12-31T23:59:59Z",
    x.get("product_id") or 0,
    x["offer_id"],
))

batch_size = 20
out = {
    "catalog_count": len(selected),
    "all_count": len({int(x.get("product_id") or x.get("id") or 0) for x in all_rows if x.get("product_id") or x.get("id")}),
    "archived_count": len({int(x.get("product_id") or x.get("id") or 0) for x in arch_rows if x.get("product_id") or x.get("id")}),
    "archived_in_final_queue": sum(1 for x in selected if x["archived"]),
    "batch_size": batch_size,
    "batch_count": math.ceil(len(selected) / batch_size),
    "ordering": "created_at_then_product_id",
    "scope": "ALL + ARCHIVED; no stock or visibility exclusion",
    "selected": selected,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k:v for k,v in out.items() if k != "selected"}, ensure_ascii=False, indent=2))
print("first20", json.dumps(selected[:20], ensure_ascii=False, indent=2))
print("last5", json.dumps(selected[-5:], ensure_ascii=False, indent=2))
