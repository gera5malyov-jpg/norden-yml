#!/usr/bin/env python3
import json
import os
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

ENDPOINTS = {
    "spb": "https://spbapi.dalli-service.com/v1/",
    "msk": "https://api.dalli-service.com/v1/",
}

def post_status(account: str, token: str, days: int = 14):
    root = ET.Element("statusreq")
    ET.SubElement(root, "auth", {"token": token})
    ET.SubElement(root, "orderno")
    ET.SubElement(root, "datefrom").text = (date.today() - timedelta(days=days)).isoformat()
    ET.SubElement(root, "dateto").text = date.today().isoformat()

    body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    req = urllib.request.Request(
        ENDPOINTS[account],
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/xml; charset=utf-8",
            "Accept": "application/xml, text/xml, */*",
            "User-Agent": "megapolis-dalli-status-check/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Dalli HTTP {exc.code}: {raw[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Dalli connection error: {exc}") from exc

    parsed = ET.fromstring(raw)
    if parsed.get("error"):
        raise RuntimeError(
            f"Dalli API error={parsed.get('error')}: {parsed.get('errormsg', 'unknown')}"
        )

    orders = []
    for order in parsed.findall("order"):
        status = order.find("status")
        receiver = order.find("receiver")
        orders.append({
            "orderno": order.get("orderno", ""),
            "ordercode": order.get("ordercode", ""),
            "awb": order.get("awb", "") or order.get("givencode", ""),
            "createdate": order.get("createdate", ""),
            "status": (status.text or "").strip() if status is not None else "",
            "status_title": status.get("title", "") if status is not None else "",
            "status_eventtime": status.get("eventtime", "") if status is not None else "",
            "status_store": status.get("eventstore", "") if status is not None else "",
            "delivery_date": (receiver.findtext("date", "") if receiver is not None else ""),
        })

    def sort_key(item):
        return (item.get("status_eventtime", ""), item.get("createdate", ""), item.get("orderno", ""))

    orders.sort(key=sort_key, reverse=True)
    return {
        "account": account.upper(),
        "date_from": (date.today() - timedelta(days=days)).isoformat(),
        "date_to": date.today().isoformat(),
        "count": len(orders),
        "orders": orders,
    }

def main():
    out_dir = Path("dalli")
    out_dir.mkdir(parents=True, exist_ok=True)

    configs = {
        "spb": os.environ.get("DALLI_TOKEN_SPB", "") or os.environ.get("DALLI_TOKEN", ""),
        "msk": os.environ.get("DALLI_TOKEN_MSK", ""),
    }
    for account, token in configs.items():
        if not token.strip():
            raise RuntimeError(f"Missing Dalli token for {account}")
        result = post_status(account, token)
        path = out_dir / f"status_{account}_latest.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{account.upper()}: {result['count']} orders -> {path}")

if __name__ == "__main__":
    main()
