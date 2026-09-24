import base64
import json
import os
import sys
import urllib.error
import urllib.request

OFFER = "SAMS-641933"
DOC = "ЕАЭС N RU Д-CN.РА06.В.58347/26"
PRODUCT_ID = 6224919367
OZON_SKU = "5703283437"
YANDEX_BUSINESS_ID = 20806099
ISSUE_DATE = "2026-07-30"
EXPIRE_DATE = "2031-07-28"
CERT_URLS = [
    "https://s3.ibta.ru/certificates/21155/21155_0000.JPG",
    "https://s3.ibta.ru/certificates/21155/21155_0001.JPG",
]

OZON = "https://api-seller.ozon.ru"
YANDEX = "https://api.partner.market.yandex.ru"

oz_headers = {
    "Client-Id": os.environ["OZON_CLIENT_ID"],
    "Api-Key": os.environ["OZON_API_KEY"],
    "Content-Type": "application/json",
}
ya_headers = {
    "Api-Key": os.environ["YANDEX_MARKET_API_KEY"],
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def call(method, url, headers, body=None, timeout=60):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"raw": raw[:5000]}
        return e.code, parsed


def download_b64(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Megapolis-Cert-Sync/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        content = r.read()
    if not content:
        raise RuntimeError("empty certificate file: " + url)
    return base64.b64encode(content).decode("ascii")


def ozon_apply():
    print("=== OZON APPLY ===")

    # Verify target before mutation.
    st, prod = call("POST", OZON + "/v3/product/list", oz_headers, {
        "filter": {"offer_id": [OFFER], "visibility": "ALL"},
        "limit": 10,
    })
    items = ((prod.get("result") or {}).get("items") or []) if st == 200 else []
    target = next((x for x in items if str(x.get("offer_id") or "") == OFFER), None)
    if not target or int(target.get("product_id") or 0) != PRODUCT_ID:
        raise RuntimeError(f"Ozon target mismatch: HTTP {st} {json.dumps(prod, ensure_ascii=False)[:2000]}")

    # If declaration already exists, reuse it.
    st, info = call("POST", OZON + "/v1/product/certificate/info", oz_headers, {
        "certificate_number": DOC
    })
    cert_id = None
    if st == 200:
        result = info.get("result") or info
        cert_id = result.get("id") or result.get("certificate_id")
        print("OZON_CERT_ALREADY_EXISTS", json.dumps(info, ensure_ascii=False)[:5000])
    elif st != 404:
        raise RuntimeError(f"Ozon certificate info failed: HTTP {st} {json.dumps(info, ensure_ascii=False)}")

    if not cert_id:
        st, accordance = call("GET", OZON + "/v2/product/certificate/accordance-types/list", oz_headers)
        if st != 200:
            raise RuntimeError(f"Ozon accordance types failed: HTTP {st} {json.dumps(accordance, ensure_ascii=False)}")
        codes = {
            str(x.get("code") or "")
            for group in ((accordance.get("result") or {}).values())
            if isinstance(group, list)
            for x in group
            if isinstance(x, dict)
        }
        if "technical_regulations_cu" not in codes:
            raise RuntimeError("Ozon accordance type technical_regulations_cu is not available")

        files = []
        for idx, url in enumerate(CERT_URLS, 1):
            files.append({
                "file_content": download_b64(url),
                "name": f"{DOC} page {idx}.jpg",
            })

        create_body = {
            "params": {
                "accordance_type": "technical_regulations_cu",
                "certificate_country": "RU",
                "certificate_type": "DECLARATION",
                "expired_date": {"date": {"day": 28, "month": 7, "year": 2031}},
                "files": files,
                "issue_date": "2026-07-30T00:00:00Z",
                "name": "Декларация о соответствии",
                "number": DOC,
                "skus": [OZON_SKU],
            }
        }
        st, created = call("POST", OZON + "/v2/product/certificate/create", oz_headers, create_body, timeout=120)
        print("OZON_CREATE_STATUS", st)
        print("OZON_CREATE_RESPONSE", json.dumps(created, ensure_ascii=False)[:12000])
        if st != 200:
            raise RuntimeError("Ozon certificate create HTTP error")
        cert_id = created.get("certificate_id")
        create_status = str(created.get("status") or "")
        if not cert_id or create_status != "COMPLETED":
            # Do not bind an incomplete document. If Ozon made a draft, remove it.
            if cert_id:
                dst, deleted = call("POST", OZON + "/v1/product/certificate/delete", oz_headers, {
                    "certificate_id": int(cert_id)
                })
                print("OZON_INCOMPLETE_DELETE", dst, json.dumps(deleted, ensure_ascii=False)[:3000])
            raise RuntimeError("Ozon certificate was not created completely")

    # Bind to the exact product. Treat an already-bound response as non-fatal only
    # after verification below.
    st, bound = call("POST", OZON + "/v1/product/certificate/bind", oz_headers, {
        "certificate_id": int(cert_id),
        "product_id": int(PRODUCT_ID),
    })
    print("OZON_BIND_STATUS", st)
    print("OZON_BIND_RESPONSE", json.dumps(bound, ensure_ascii=False)[:6000])
    if st not in (200, 409):
        raise RuntimeError("Ozon certificate bind failed")

    # Verify using both certificate info and offer certificate list.
    st, lst = call("POST", OZON + "/v1/product/certificate/list", oz_headers, {
        "offer_id": OFFER,
        "page": 1,
        "page_size": 100,
    })
    print("OZON_VERIFY_STATUS", st)
    print("OZON_VERIFY", json.dumps(lst, ensure_ascii=False)[:12000])
    certs = ((lst.get("result") or {}).get("certificates") or []) if st == 200 else []
    numbers = {str(x.get("number") or x.get("certificate_number") or "") for x in certs if isinstance(x, dict)}
    ids = {int(x.get("id") or x.get("certificate_id") or 0) for x in certs if isinstance(x, dict)}
    if DOC not in numbers and int(cert_id) not in ids:
        # Some versions don't return the number in the filtered list; verify linked products.
        st2, linked = call("POST", OZON + "/v1/product/certificate/products/list", oz_headers, {
            "certificate_id": int(cert_id),
            "limit": 1000,
        })
        print("OZON_LINKED_VERIFY_STATUS", st2)
        print("OZON_LINKED_VERIFY", json.dumps(linked, ensure_ascii=False)[:12000])
        linked_items = ((linked.get("result") or {}).get("items") or []) if st2 == 200 else []
        if not any(int(x.get("product_id") or 0) == PRODUCT_ID for x in linked_items if isinstance(x, dict)):
            raise RuntimeError("Ozon verification did not confirm the product binding")

    print("OZON_OK certificate_id=", cert_id)
    return int(cert_id)


def yandex_apply():
    print("=== YANDEX APPLY ===")

    # Read exact offer and preserve any documents already attached.
    st, current = call(
        "POST",
        f"{YANDEX}/v2/businesses/{YANDEX_BUSINESS_ID}/offer-mappings",
        ya_headers,
        {"offerIds": [OFFER]},
    )
    if st != 200:
        raise RuntimeError(f"Yandex offer read failed: HTTP {st} {json.dumps(current, ensure_ascii=False)}")
    rows = ((current.get("result") or {}).get("offerMappings") or [])
    if not rows:
        raise RuntimeError("Yandex target offer not found")
    offer = rows[0].get("offer") or {}
    existing = [str(x) for x in (offer.get("certificates") or []) if str(x).strip()]
    print("YANDEX_EXISTING_CERTIFICATES", json.dumps(existing, ensure_ascii=False))

    # Create document metadata. Already existing is acceptable.
    st, created = call(
        "POST",
        f"{YANDEX}/v1/businesses/{YANDEX_BUSINESS_ID}/offers/documents/create",
        ya_headers,
        {
            "documents": [{
                "number": DOC,
                "type": "CONFORMITY_DECLARATION",
                "activeFromDate": ISSUE_DATE,
                "activeToDate": EXPIRE_DATE,
            }]
        },
    )
    print("YANDEX_CREATE_STATUS", st)
    print("YANDEX_CREATE_RESPONSE", json.dumps(created, ensure_ascii=False)[:8000])
    if st != 200:
        raise RuntimeError("Yandex document create failed")
    errors = ((created.get("result") or {}).get("errors") or [])
    bad = [e for e in errors if str(e.get("code") or "") != "DOCUMENT_ALREADY_EXISTS"]
    if bad:
        raise RuntimeError("Yandex document validation failed: " + json.dumps(bad, ensure_ascii=False))

    certs = list(dict.fromkeys(existing + [DOC]))
    if len(certs) > 6:
        raise RuntimeError("Yandex offer already has 6 documents; refusing to overwrite any existing document")

    st, upd = call(
        "POST",
        f"{YANDEX}/v2/businesses/{YANDEX_BUSINESS_ID}/offer-mappings/update",
        ya_headers,
        {"offerMappings": [{"offer": {"offerId": OFFER, "certificates": certs}}]},
    )
    print("YANDEX_UPDATE_STATUS", st)
    print("YANDEX_UPDATE_RESPONSE", json.dumps(upd, ensure_ascii=False)[:10000])
    if st != 200 or str(upd.get("status") or "OK") not in ("OK", ""):
        raise RuntimeError("Yandex certificate binding update failed")

    st, verify = call(
        "POST",
        f"{YANDEX}/v2/businesses/{YANDEX_BUSINESS_ID}/offer-mappings",
        ya_headers,
        {"offerIds": [OFFER]},
    )
    print("YANDEX_VERIFY_STATUS", st)
    print("YANDEX_VERIFY", json.dumps(verify, ensure_ascii=False)[:12000])
    rows = ((verify.get("result") or {}).get("offerMappings") or []) if st == 200 else []
    vcerts = ((rows[0].get("offer") or {}).get("certificates") or []) if rows else []
    if DOC not in [str(x) for x in vcerts]:
        raise RuntimeError("Yandex verification did not confirm the document number on the offer")

    print("YANDEX_OK")
    return True


errors = []
try:
    ozon_apply()
except Exception as e:
    errors.append("OZON: " + repr(e))
    print("OZON_ERROR", repr(e))

try:
    yandex_apply()
except Exception as e:
    errors.append("YANDEX: " + repr(e))
    print("YANDEX_ERROR", repr(e))

if errors:
    print("APPLY_ERRORS", json.dumps(errors, ensure_ascii=False))
    sys.exit(1)

print("ALL_OK")
