#!/usr/bin/env python3
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://api-seller.ozon.ru"
CERTIFICATE_ID = 3123534
CERTIFICATE_SUFFIX = "22429/25"
TARGET_BRAND = "norden"
REPORT_PATH = "ozon/certificates/norden_bind_report.json"

CID = os.environ["OZON_CLIENT_ID"].strip()
KEY = os.environ["OZON_API_KEY"].strip()
HEADERS = {
    "Client-Id": CID,
    "Api-Key": KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "megapolis-norden-certificate-bind/1.0",
}

def req(method, path, payload=None, attempts=5):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(attempts):
        request = urllib.request.Request(BASE + path, data=data, headers=HEADERS, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            last = {"http_status": exc.code, "body": body[:5000]}
            if (exc.code == 429 or 500 <= exc.code < 600) and attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 20))
                continue
            return {"__error__": last}
        except Exception as exc:
            last = {"exception": repr(exc)}
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 20))
                continue
            return {"__error__": last}
    return {"__error__": last or {"message": "unknown request error"}}

def post(path, payload):
    return req("POST", path, payload)

def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]

def list_products():
    products = []
    last_id = ""
    seen = set()
    while True:
        body = {"filter": {"visibility": "ALL"}, "limit": 1000}
        if last_id:
            body["last_id"] = last_id
        data = post("/v3/product/list", body)
        if "__error__" in data:
            raise RuntimeError("product/list failed: " + json.dumps(data, ensure_ascii=False))
        result = data.get("result") or {}
        items = result.get("items") or []
        products.extend(items)
        next_id = result.get("last_id") or ""
        total = int(result.get("total") or 0)
        if not items or (total and len(products) >= total) or not next_id or next_id == last_id or next_id in seen:
            break
        seen.add(last_id)
        last_id = next_id
    return products

def get_attributes(product_ids):
    result = []
    for batch in chunks(product_ids, 1000):
        data = post(
            "/v4/product/info/attributes",
            {"filter": {"product_id": batch, "visibility": "ALL"}, "limit": 1000},
        )
        if "__error__" in data:
            raise RuntimeError("attributes failed: " + json.dumps(data, ensure_ascii=False))
        result.extend(data.get("result") or [])
    return result

def get_brand(product):
    for attr in product.get("attributes") or []:
        attr_id = int(attr.get("id") or attr.get("attribute_id") or 0)
        if attr_id == 85:
            values = []
            for item in attr.get("values") or []:
                value = str(item.get("value") or "").strip()
                if value:
                    values.append(value)
            return " | ".join(values)
    return ""

def list_certificates():
    out = []
    for page in range(1, 101):
        data = post("/v1/product/certificate/list", {"page": page, "page_size": 100})
        if "__error__" in data:
            raise RuntimeError("certificate/list failed: " + json.dumps(data, ensure_ascii=False))
        result = data.get("result")
        if isinstance(result, dict):
            items = result.get("items") or result.get("certificates") or result.get("result") or []
            total = int(result.get("total") or 0)
        else:
            items = result or data.get("items") or data.get("certificates") or []
            total = int(data.get("total") or 0)
        if not isinstance(items, list):
            items = []
        out.extend(items)
        if not items or len(items) < 100 or (total and len(out) >= total):
            break
    return out

def list_bound_products():
    out = []
    for page in range(1, 1001):
        data = post(
            "/v1/product/certificate/products/list",
            {"certificate_id": CERTIFICATE_ID, "page": page, "page_size": 1000},
        )
        if "__error__" in data:
            raise RuntimeError("certificate/products/list failed: " + json.dumps(data, ensure_ascii=False))
        result = data.get("result") or {}
        items = result.get("items") or []
        out.extend(items)
        count = int(result.get("count") or 0)
        if not items or len(items) < 1000 or (count and len(out) >= count):
            break
    return out

def bind_batch(product_ids, failures, responses):
    if not product_ids:
        return
    data = post(
        "/v1/product/certificate/bind",
        {"certificate_id": CERTIFICATE_ID, "product_id": product_ids},
    )
    responses.append({"product_ids": product_ids, "response": data})
    if "__error__" not in data and data.get("result") is True:
        return
    if len(product_ids) == 1:
        failures.append({"product_id": product_ids[0], "response": data})
        return
    midpoint = len(product_ids) // 2
    bind_batch(product_ids[:midpoint], failures, responses)
    bind_batch(product_ids[midpoint:], failures, responses)

def main():
    certificates = list_certificates()
    certificate = next(
        (item for item in certificates if int(item.get("certificate_id") or 0) == CERTIFICATE_ID),
        None,
    )
    if not certificate:
        raise RuntimeError(f"Certificate id {CERTIFICATE_ID} not found in seller account")
    number = str(certificate.get("certificate_number") or "")
    if CERTIFICATE_SUFFIX not in number:
        raise RuntimeError(
            f"Certificate id {CERTIFICATE_ID} has unexpected number: {number!r}"
        )

    products = list_products()
    product_ids = [
        int(item.get("product_id") or 0)
        for item in products
        if int(item.get("product_id") or 0)
    ]
    attributes = get_attributes(product_ids)

    norden = []
    brand_counts = {}
    for product in attributes:
        brand = get_brand(product).strip()
        if brand:
            brand_counts[brand] = brand_counts.get(brand, 0) + 1
        if brand.casefold() == TARGET_BRAND:
            pid = int(product.get("id") or product.get("product_id") or 0)
            if pid:
                norden.append(
                    {
                        "product_id": pid,
                        "offer_id": str(product.get("offer_id") or ""),
                        "name": str(product.get("name") or ""),
                        "brand": brand,
                    }
                )

    if not norden:
        raise RuntimeError(
            "No products with exact Ozon brand Norden were found. No changes made."
        )

    bound_before_rows = list_bound_products()
    bound_before = {int(item.get("product_id") or 0) for item in bound_before_rows}
    target_ids = sorted({item["product_id"] for item in norden})
    missing_before = [pid for pid in target_ids if pid not in bound_before]

    failures = []
    responses = []
    for batch in chunks(missing_before, 100):
        bind_batch(batch, failures, responses)
        time.sleep(0.25)

    bound_after_rows = list_bound_products()
    bound_after = {int(item.get("product_id") or 0) for item in bound_after_rows}
    still_missing = [pid for pid in target_ids if pid not in bound_after]
    newly_bound = [pid for pid in target_ids if pid in bound_after and pid not in bound_before]

    by_id = {item["product_id"]: item for item in norden}
    report = {
        "certificate": certificate,
        "certificate_id": CERTIFICATE_ID,
        "target_brand": "Norden",
        "catalog_product_count": len(product_ids),
        "norden_product_count": len(target_ids),
        "certificate_products_before": len(bound_before),
        "norden_already_bound_before": len(target_ids) - len(missing_before),
        "norden_missing_before": len(missing_before),
        "norden_newly_bound": len(newly_bound),
        "norden_still_missing": len(still_missing),
        "all_norden_bound": len(still_missing) == 0,
        "missing_before_products": [by_id[pid] for pid in missing_before],
        "newly_bound_products": [by_id[pid] for pid in newly_bound],
        "still_missing_products": [by_id[pid] for pid in still_missing],
        "failures": failures,
        "bind_responses": responses,
        "brand_counts_matching_nord": {
            brand: count for brand, count in brand_counts.items()
            if "nord" in brand.casefold()
        },
    }
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(json.dumps({
        "certificate_id": CERTIFICATE_ID,
        "certificate_number": number,
        "norden_product_count": len(target_ids),
        "already_bound_before": len(target_ids) - len(missing_before),
        "missing_before": len(missing_before),
        "newly_bound": len(newly_bound),
        "still_missing": len(still_missing),
        "all_norden_bound": len(still_missing) == 0,
    }, ensure_ascii=False))

    if still_missing:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
