#!/usr/bin/env python3
import json
import os
import re
import sys

import requests

from client import WebasystAPIError, WebasystClient


BASE_URL = os.getenv("WEBASYST_BASE_URL", "https://profikompany.ru").rstrip("/")
TOKEN = os.getenv("WEBASYST_API_TOKEN", "")


def safe_token_meta():
    return {
        "present": bool(TOKEN),
        "length": len(TOKEN),
        "has_leading_or_trailing_whitespace": TOKEN != TOKEN.strip(),
        "contains_newline": "\n" in TOKEN or "\r" in TOKEN,
        "looks_like_32_hex": bool(re.fullmatch(r"[0-9a-fA-F]{32}", TOKEN.strip())),
        "starts_with_access_token_label": TOKEN.strip().lower().startswith("access_token="),
        "starts_with_bearer_label": TOKEN.strip().lower().startswith("bearer "),
    }


def summarize_response(label, response):
    try:
        body = response.json()
    except ValueError:
        body = {"non_json": response.text[:300]}

    # Never print token-bearing URLs/query strings.
    history = []
    for h in response.history:
        history.append({
            "status": h.status_code,
            "host": requests.utils.urlparse(h.url).netloc,
            "location_host": requests.utils.urlparse(h.headers.get("Location", "")).netloc or None,
        })

    result = {
        "label": label,
        "status": response.status_code,
        "final_host": requests.utils.urlparse(response.url).netloc,
        "redirects": history,
        "error": body.get("error") if isinstance(body, dict) else None,
        "error_description": body.get("error_description") if isinstance(body, dict) else None,
        "ok": response.ok and not (isinstance(body, dict) and body.get("error")),
    }
    if result["ok"]:
        if isinstance(body, list):
            result["items"] = len(body)
        elif isinstance(body, dict):
            result["keys"] = len(body)
    return result


def main():
    print("Webasyst API diagnostic")
    print(json.dumps({"base_url": BASE_URL, "token_meta": safe_token_meta()}, ensure_ascii=False))

    if not TOKEN:
        print("ERROR: WEBASYST_API_TOKEN is missing", file=sys.stderr)
        return 1

    token = TOKEN.strip()
    endpoint = f"{BASE_URL}/api.php/shop.type.getList"

    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "norden-yml-webasyst-diagnostic/1.0"})

    # 1) Official secure Authorization: Bearer method.
    bearer = session.get(
        endpoint,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
        allow_redirects=True,
    )
    bearer_result = summarize_response("bearer", bearer)
    print(json.dumps(bearer_result, ensure_ascii=False))

    # 2) Official POST-field transfer. Preferred fallback because the token is not in the URL.
    post_form = session.post(
        endpoint,
        data={"access_token": token, "format": "json"},
        timeout=60,
        allow_redirects=True,
    )
    post_form_result = summarize_response("post_form", post_form)
    print(json.dumps(post_form_result, ensure_ascii=False))

    # 3) Query-parameter fallback, used only for diagnostics.
    query = session.get(
        endpoint,
        params={"access_token": token, "format": "json"},
        timeout=60,
        allow_redirects=True,
    )
    query_result = summarize_response("query_parameter", query)
    print(json.dumps(query_result, ensure_ascii=False))

    if not (bearer_result["ok"] or post_form_result["ok"] or query_result["ok"]):
        print("ERROR: token was rejected by Webasyst with all tested transports.", file=sys.stderr)
        return 1

    print("Webasyst API connection is working.")

    # Confirm the reusable client and key read permissions without printing business data.
    client = WebasystClient(base_url=BASE_URL, token=token)
    probes = [
        ("product_types", "shop.type.getList", {}),
        ("stocks", "shop.stock.getList", {}),
        ("categories", "shop.category.getTree", {"max_level": 1}),
        ("products", "shop.product.search", {"limit": 1, "fields": "id"}),
        ("orders", "shop.order.search", {"limit": 1, "fields": "id"}),
    ]

    capability_results = []
    for label, method, params in probes:
        try:
            payload = client.call(method, params=params)
            entry = {"capability": label, "method": method, "ok": True}
            if isinstance(payload, list):
                entry["returned_items"] = len(payload)
            elif isinstance(payload, dict):
                if "count" in payload:
                    entry["count"] = payload.get("count")
                elif "products" in payload and isinstance(payload.get("products"), list):
                    entry["returned_items"] = len(payload["products"])
                elif "orders" in payload and isinstance(payload.get("orders"), list):
                    entry["returned_items"] = len(payload["orders"])
            capability_results.append(entry)
        except WebasystAPIError as exc:
            capability_results.append({
                "capability": label,
                "method": method,
                "ok": False,
                "error": str(exc),
            })

    print(json.dumps({"capabilities": capability_results}, ensure_ascii=False))
    if not all(x["ok"] for x in capability_results):
        print("ERROR: API token works, but one or more requested Shop-Script read permissions are unavailable.", file=sys.stderr)
        return 2

    print("Reusable Webasyst client and Shop-Script read permissions are verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
