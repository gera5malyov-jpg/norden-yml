#!/usr/bin/env python3
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://api-seller.ozon.ru"
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
    "User-Agent": "megapolis-norden-certificate-bind/1.1",
}

def normalize_certificate_number(value):
    text = str(value or "").upper().replace(" ", "")
    table = str.maketrans({
        "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н",
        "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х",
    })
    return text.translate(table)

TARGET_CERT_NORMALIZED = normalize_certificate_number("ЕАЭС N RU Д-CN.РА05.В.22429/25")

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

def resolve_certificate():
    matches = []
    for item in list_certificates():
        number = str(item.get("certificate_number") or "")
        if CERTIFICATE_SUFFIX in number and normalize_certificate_number(number) == TARGET_CERT_NORMALIZED:
            matches.append(item)
    if not matches:
        raise RuntimeError("Target declaration was not found in seller account")
    matches.sort(
        key=lambda item: (
            str(item.get("status_code") or "") == "approved",
            str(item.get("issue_date") or ""),
            int(item.get("certificate_id") or 0),
        ),
        reverse=True,
    )
    certificate = matches[0]
    certificate_id = int(certificate.get("certificate_id") or 0)
    if not certificate_id:
        raise RuntimeError("Target declaration has no certificate_id")
    return certificate_id, certificate

def list_bound_products(certificate_id):
    data = post(
        "/v1/product/certificate/products/list",
        {"certificate_id": certificate_id, "limit": 1000},
    )
    if "__error__" in data:
        raise RuntimeError("certificate/products/list failed: " + json.dumps(data, ensure_ascii=False))
    result = data.get("result") or {}
    return result.get("items") or []

