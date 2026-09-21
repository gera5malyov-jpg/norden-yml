#!/usr/bin/env python3
import json, math, os, time, urllib.error, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OZON_BASE = "https://api-seller.ozon.ru"
OFFER_ID = os.environ.get("OFFER_ID", "AF-31646769").strip()
CLIENT_ID = os.environ.get("OZON_CLIENT_ID", "").strip()
API_KEY = os.environ.get("OZON_API_KEY", "").strip()
PROFIT_PCT = float(os.environ.get("PROFIT_PCT", "20"))
MIN_PROFIT_PCT = float(os.environ.get("MIN_PROFIT_PCT", "18"))
ACQUIRING_FALLBACK_PCT = float(os.environ.get("ACQUIRING_FALLBACK_PCT", "2"))
PROBE_ONLY = os.environ.get("PROBE_ONLY", "false").strip().lower() in {"1", "true", "yes", "on"}
REPORT = ROOT / "ozon" / f"norden-price-test-{OFFER_ID}.json"

if not CLIENT_ID or not API_KEY:
    raise SystemExit("OZON_CLIENT_ID / OZON_API_KEY are not configured")

HEADERS = {
    "Client-Id": CLIENT_ID,
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "megapolis-norden-rfbs-price/1.1",
}

def post(path, payload, attempts=5):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(attempts):
        req = urllib.request.Request(OZON_BASE + path, data=data, headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if (exc.code == 429 or 500 <= exc.code < 600) and attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 20))
                continue
            raise RuntimeError(f"Ozon API {path}: HTTP {exc.code}: {body[:1500]}") from exc
        except Exception:
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 20))
                continue
            raise
    raise RuntimeError(f"Ozon API {path}: retries exhausted")

def money(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

def find_supplier_article():
    mapping = json.loads((ROOT / "norden-kit" / "kit_mapping.json").read_text(encoding="utf-8"))
    matches = []
    for article, variants in (mapping.get("variants") or {}).items():
        for row in variants or []:
            if str(row.get("sku") or "").strip() == OFFER_ID:
                matches.append(article)
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one Norden mapping for {OFFER_ID}, found {len(matches)}")
    return matches[0]

def purchase_from_github_feed(article):
    root = ET.parse(ROOT / "norden.yml").getroot()
    for offer in root.findall(".//offer"):
        if str(offer.get("id") or "").strip() == article:
            value = money(offer.findtext("price"))
            if value <= 0:
                raise RuntimeError(f"Invalid purchase price in norden.yml for {article}")
            return value
    raise RuntimeError(f"{article} not found in norden.yml")

def get_ozon():
    data = post("/v5/product/info/prices", {
        "cursor": "",
        "filter": {"offer_id": [OFFER_ID], "visibility": "ALL"},
        "limit": 100
    })
    items = data.get("items") or []
    exact = [x for x in items if str(x.get("offer_id") or "") == OFFER_ID]
    if len(exact) != 1:
        raise RuntimeError(f"Expected exactly one Ozon product {OFFER_ID}, found {len(exact)}")
    return exact[0]

def ceil_rub(x):
    return int(math.ceil(x - 1e-9))

article = find_supplier_article()
purchase = purchase_from_github_feed(article)
before = get_ozon()
if PROBE_ONLY:
    probe = {"status": "ДИАГНОСТИКА", "offer_id": OFFER_ID, "norden_article": article, "purchase_price_github_rub": purchase, "ozon_raw": before}
    REPORT.write_text(json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(probe, ensure_ascii=False, indent=2))
    raise SystemExit(0)
commissions = before.get("commissions") or {}
commission_pct = money(commissions.get("sales_percent_rfbs"))
if commission_pct <= 0:
    raise RuntimeError(f"Ozon did not return sales_percent_rfbs for {OFFER_ID}")

price_block = before.get("price") or {}
current_price = money(price_block.get("price"))
marketing_seller_price = money(price_block.get("marketing_seller_price"))
acquiring_amount = money(before.get("acquiring"))
acquiring_base = marketing_seller_price if marketing_seller_price > 0 else current_price
if acquiring_amount > 0 and acquiring_base > 0:
    acquiring_pct = acquiring_amount / acquiring_base * 100.0
    acquiring_source = "Ozon acquiring / marketing_seller_price"
else:
    acquiring_pct = ACQUIRING_FALLBACK_PCT
    acquiring_source = f"fallback {ACQUIRING_FALLBACK_PCT}%"

total_pct = commission_pct + acquiring_pct
denom = 1.0 - total_pct / 100.0
if denom <= 0:
    raise RuntimeError(f"Invalid total fee rate: {total_pct:.4f}%")

sale_price = ceil_rub(purchase * (1.0 + PROFIT_PCT / 100.0) / denom)
min_price = ceil_rub(purchase * (1.0 + MIN_PROFIT_PCT / 100.0) / denom)
if min_price > sale_price:
    raise RuntimeError("Safety stop: min_price > sale_price")

base_report = {
    "status": "ПОДГОТОВЛЕНО",
    "finished_at": datetime.now(timezone.utc).isoformat(),
    "offer_id": OFFER_ID,
    "norden_article": article,
    "scheme": "rFBS",
    "purchase_price_github_rub": purchase,
    "purchase_source": "norden.yml",
    "ozon_sales_percent_rfbs": commission_pct,
    "ozon_acquiring_current_amount_rub": acquiring_amount,
    "ozon_acquiring_base_rub": acquiring_base,
    "ozon_acquiring_effective_percent": round(acquiring_pct, 6),
    "acquiring_source": acquiring_source,
    "delivery_included": False,
    "target_profit_percent_of_purchase": PROFIT_PCT,
    "minimum_profit_percent_of_purchase": MIN_PROFIT_PCT,
    "old_ozon_price_rub": current_price,
    "old_marketing_seller_price_rub": marketing_seller_price,
    "calculated_sale_price_rub": sale_price,
    "calculated_min_price_rub": min_price,
}
REPORT.write_text(json.dumps(base_report, ensure_ascii=False, indent=2), encoding="utf-8")
print("PRECALC " + json.dumps(base_report, ensure_ascii=False))

payload = {
    "prices": [{
        "product_id": int(before.get("product_id") or 0),
        "offer_id": OFFER_ID,
        "price": str(sale_price),
        "old_price": str(int(round(money(price_block.get("old_price"))))),
        "min_price": str(min_price),
        "min_price_for_auto_actions_enabled": True,
        "net_price": str(int(round(purchase))),
        "currency_code": "RUB",
        "declared_price": "18221",
        "vat": "0",
        "auto_action_enabled": "UNKNOWN",
        "price_strategy_enabled": "DISABLED"
    }]
}
update = post("/v1/product/import/prices", payload)
rows = update.get("result") or []
ok = bool(rows) and bool(rows[0].get("updated")) and not rows[0].get("errors")
if not ok:
    base_report["status"] = "ОШИБКА"
    base_report["update_response"] = update
    REPORT.write_text(json.dumps(base_report, ensure_ascii=False, indent=2), encoding="utf-8")
    raise RuntimeError("Ozon rejected price update: " + json.dumps(update, ensure_ascii=False))

time.sleep(3)
after = get_ozon()
after_price = money((after.get("price") or {}).get("price"))
after_min = money((after.get("price") or {}).get("min_price"))
net_sale = sale_price * denom - purchase
net_min = min_price * denom - purchase

report = dict(base_report)
report.update({
    "status": "УСПЕШНО",
    "verified_ozon_price_rub": after_price,
    "verified_ozon_min_price_rub": after_min,
    "estimated_profit_after_commission_and_acquiring_rub": round(net_sale, 2),
    "estimated_profit_at_min_price_rub": round(net_min, 2),
    "update_response": update,
})
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
