#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

BASE_URL = "https://api-seller.ozon.ru"
CLIENT_ID = os.environ.get("OZON_CLIENT_ID", "").strip()
API_KEY = os.environ.get("OZON_API_KEY", "").strip()
REPORT_JSON = Path(os.environ.get("BRAND_REPORT_JSON", "ozon/brand_sync_report.json"))
REPORT_MD = Path(os.environ.get("BRAND_REPORT_MD", "ozon/brand_sync_report.md"))

SOURCE_BRANDS = {"RIVA CHAIR", "RV DESIGN"}
TARGET_BRAND = "Мегаполис"
BATCH_SIZE = 50

HEADERS = {
    "Client-Id": CLIENT_ID,
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "megapolis-ozon-brand-sync/1.0",
}

def norm(v):
    return " ".join(str(v or "").replace("Ё", "Е").replace("ё", "е").upper().split())

def post(path, payload, attempts=6):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=data, headers=HEADERS, method="POST")
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 429 or 500 <= exc.code < 600:
                if attempt + 1 < attempts:
                    time.sleep(min(2 ** attempt, 30))
                    continue
            raise RuntimeError(f"Ozon API {path}: HTTP {exc.code}: {body[:2000]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 30))
                continue
            raise RuntimeError(f"Ozon API {path}: {exc}") from exc
    raise RuntimeError(f"Ozon API {path}: retries exhausted")

def chunks(values, size):
    for i in range(0, len(values), size):
        yield values[i:i + size]

def list_products():
    out = []
    last_id = ""
    seen = set()
    while True:
        body = {"filter": {"visibility": "ALL"}, "limit": 1000}
        if last_id:
            body["last_id"] = last_id
        data = post("/v3/product/list", body)
        result = data.get("result") or {}
        items = result.get("items") or []
        out.extend(items)
        new_last = result.get("last_id") or ""
        total = int(result.get("total") or 0)
        if not items or len(out) >= total or not new_last or new_last == last_id or new_last in seen:
            break
        seen.add(last_id)
        last_id = new_last
    return out

def get_product_attributes(product_ids):
    out = []
    for batch in chunks(product_ids, 1000):
        data = post("/v4/product/info/attributes", {
            "filter": {"product_id": batch, "visibility": "ALL"},
            "limit": 1000,
        })
        out.extend(data.get("result") or [])
    return out

def get_category_attributes(category_id, type_id):
    return (post("/v1/description-category/attribute", {
        "description_category_id": int(category_id),
        "type_id": int(type_id),
        "language": "DEFAULT",
    }).get("result") or [])

def get_dictionary_values(category_id, type_id, attribute_id):
    out = []
    last_value_id = 0
    while True:
        payload = {
            "attribute_id": int(attribute_id),
            "description_category_id": int(category_id),
            "type_id": int(type_id),
            "language": "DEFAULT",
            "limit": 5000,
            "last_value_id": int(last_value_id),
        }
        data = post("/v1/description-category/attribute/values", payload)
        values = data.get("result") or []
        out.extend(values)
        if not data.get("has_next") or not values:
            break
        new_last = int(values[-1].get("id") or 0)
        if not new_last or new_last == last_value_id:
            break
        last_value_id = new_last
    return out

def find_attr(product, attr_id):
    for attr in product.get("attributes") or []:
        if int(attr.get("id") or attr.get("attribute_id") or 0) == int(attr_id):
            return attr
    return None

def first_value(attr):
    if not attr:
        return None
    for value in attr.get("values") or []:
        text = str(value.get("value") or "").strip()
        dict_id = int(value.get("dictionary_value_id") or 0)
        if text or dict_id:
            return {"value": text, "dictionary_value_id": dict_id}
    return None

def get_daily_remaining():
    try:
        data = post("/v4/product/info/limit", {})
        daily = data.get("daily_update") or {}
        limit = int(daily.get("limit") or 0)
        usage = int(daily.get("usage") or 0)
        if limit > 0:
            return max(0, limit - usage), limit, usage, daily.get("reset_at")
    except Exception:
        pass
    return None, None, None, None

def write_report(report):
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Ozon — замена бренда",
        "",
        f"- Результат: **{'УСПЕШНО' if report.get('ok') else 'ЗАВЕРШЕНО С ОШИБКАМИ'}**",
        f"- Товаров в кабинете: **{report.get('products_loaded', 0)}**",
        f"- Найдено с брендом RIVA Chair / Riva Chair / RV DESIGN: **{report.get('matched', 0)}**",
        f"- Отправлено на изменение: **{report.get('submitted', 0)}**",
        f"- Подтверждено как «Мегаполис»: **{report.get('verified', 0)}**",
        f"- Не изменено: **{report.get('not_changed', 0)}**",
    ]
    if report.get("daily_limit") is not None:
        lines.append(
            f"- Лимит обновлений Ozon до запуска: **{report.get('daily_usage')}/{report.get('daily_limit')}**, "
            f"остаток **{report.get('daily_remaining')}**"
        )
    if report.get("task_ids"):
        lines += ["", "## Task ID Ozon", "", ", ".join(str(x) for x in report["task_ids"])]
    if report.get("changed"):
        lines += ["", "## Изменено", "", "| Артикул | Было | Стало |", "|---|---|---|"]
        for x in report["changed"][:500]:
            lines.append(f"| `{x['offer_id']}` | {x['old_brand']} | {TARGET_BRAND} |")
    if report.get("skipped"):
        lines += ["", "## Не изменено", "", "| Артикул | Причина |", "|---|---|"]
        for x in report["skipped"][:500]:
            lines.append(f"| `{x.get('offer_id','')}` | {x.get('reason','')} |")
    if report.get("error"):
        lines += ["", "## Ошибка", "", report["error"]]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main():
    report = {
        "ok": False,
        "source_brands": ["RIVA Chair", "Riva Chair", "RV DESIGN"],
        "target_brand": TARGET_BRAND,
        "products_loaded": 0,
        "matched": 0,
        "submitted": 0,
        "verified": 0,
        "not_changed": 0,
        "changed": [],
        "skipped": [],
        "task_ids": [],
    }
    try:
        if not CLIENT_ID or not API_KEY:
            raise RuntimeError("Не настроены OZON_CLIENT_ID / OZON_API_KEY")

        catalog = list_products()
        ids = [int(x.get("product_id") or 0) for x in catalog if int(x.get("product_id") or 0)]
        products = get_product_attributes(ids)
        report["products_loaded"] = len(products)

        groups = sorted({
            (int(p.get("description_category_id") or 0), int(p.get("type_id") or 0))
            for p in products
            if int(p.get("description_category_id") or 0) and int(p.get("type_id") or 0)
        })

        brand_attr_by_group = {}
        for category_id, type_id in groups:
            attrs = get_category_attributes(category_id, type_id)
            matches = [a for a in attrs if norm(a.get("name")) == "БРЕНД"]
            if len(matches) == 1:
                brand_attr_by_group[(category_id, type_id)] = int(matches[0].get("id") or 0)

        existing_target_by_group = {}
        candidates = []

        for p in products:
            category_id = int(p.get("description_category_id") or 0)
            type_id = int(p.get("type_id") or 0)
            group = (category_id, type_id)
            attr_id = brand_attr_by_group.get(group)
            if not attr_id:
                continue
            val = first_value(find_attr(p, attr_id))
            if not val:
                continue
            current = val["value"]
            if norm(current) == norm(TARGET_BRAND) and val["dictionary_value_id"]:
                existing_target_by_group[group] = {
                    "dictionary_value_id": val["dictionary_value_id"],
                    "value": current,
                }
            if norm(current) in SOURCE_BRANDS:
                candidates.append({
                    "product_id": int(p.get("id") or p.get("product_id") or 0),
                    "offer_id": str(p.get("offer_id") or ""),
                    "name": str(p.get("name") or ""),
                    "group": group,
                    "brand_attr_id": attr_id,
                    "old_brand": current,
                })

        report["matched"] = len(candidates)

        target_by_group = dict(existing_target_by_group)
        for group in sorted({x["group"] for x in candidates}):
            if group in target_by_group:
                continue
            category_id, type_id = group
            attr_id = brand_attr_by_group[group]
            values = get_dictionary_values(category_id, type_id, attr_id)
            exact = [v for v in values if norm(v.get("value")) == norm(TARGET_BRAND)]
            if len(exact) == 1 and int(exact[0].get("id") or 0):
                target_by_group[group] = {
                    "dictionary_value_id": int(exact[0].get("id") or 0),
                    "value": str(exact[0].get("value") or TARGET_BRAND),
                }

        ready = []
        for item in candidates:
            target = target_by_group.get(item["group"])
            if not target:
                report["skipped"].append({
                    "offer_id": item["offer_id"],
                    "reason": "В справочнике брендов этой категории Ozon не найдено точное значение «Мегаполис»",
                })
                continue
            item["target_dictionary_value_id"] = target["dictionary_value_id"]
            item["target_value"] = target["value"]
            ready.append(item)

        remaining, limit, usage, reset_at = get_daily_remaining()
        report["daily_remaining"] = remaining
        report["daily_limit"] = limit
        report["daily_usage"] = usage
        report["daily_reset_at"] = reset_at

        selected = ready
        if remaining is not None and len(selected) > remaining:
            deferred = selected[remaining:]
            selected = selected[:remaining]
            for item in deferred:
                report["skipped"].append({
                    "offer_id": item["offer_id"],
                    "reason": "Не хватило суточного лимита обновлений Ozon",
                })

        submitted = []
        for batch in chunks(selected, BATCH_SIZE):
            payload = {
                "items": [
                    {
                        "offer_id": x["offer_id"],
                        "attributes": [{
                            "complex_id": 0,
                            "id": x["brand_attr_id"],
                            "values": [{
                                "dictionary_value_id": x["target_dictionary_value_id"],
                                "value": x["target_value"],
                            }],
                        }],
                    }
                    for x in batch
                ]
            }
            try:
                response = post("/v1/product/attributes/update", payload)
                task_id = response.get("task_id")
                if task_id is not None:
                    report["task_ids"].append(task_id)
                submitted.extend(batch)
            except Exception as exc:
                for x in batch:
                    report["skipped"].append({
                        "offer_id": x["offer_id"],
                        "reason": f"Ошибка Ozon при обновлении: {str(exc)[:500]}",
                    })
            time.sleep(0.4)

        report["submitted"] = len(submitted)

        if submitted:
            time.sleep(8)
            verify_ids = [x["product_id"] for x in submitted if x["product_id"]]
            verified_products = get_product_attributes(verify_ids)
            by_offer = {str(p.get("offer_id") or ""): p for p in verified_products}
            for item in submitted:
                p = by_offer.get(item["offer_id"])
                if not p:
                    report["skipped"].append({
                        "offer_id": item["offer_id"],
                        "reason": "После обновления товар не вернулся в контрольной выборке Ozon",
                    })
                    continue
                attr = find_attr(p, item["brand_attr_id"])
                val = first_value(attr)
                if val and norm(val["value"]) == norm(TARGET_BRAND):
                    report["changed"].append({
                        "offer_id": item["offer_id"],
                        "name": item["name"],
                        "old_brand": item["old_brand"],
                        "new_brand": val["value"],
                    })
                else:
                    actual = val["value"] if val else ""
                    report["skipped"].append({
                        "offer_id": item["offer_id"],
                        "reason": f"Контрольная проверка не подтвердила бренд «Мегаполис»; сейчас «{actual}»",
                    })

        report["verified"] = len(report["changed"])
        report["not_changed"] = len(report["skipped"])
        report["ok"] = (report["not_changed"] == 0 and report["verified"] == report["matched"])
        write_report(report)

        print(json.dumps({
            "matched": report["matched"],
            "submitted": report["submitted"],
            "verified": report["verified"],
            "not_changed": report["not_changed"],
            "ok": report["ok"],
        }, ensure_ascii=False))

        if report["matched"] == 0:
            return 0
        return 0 if report["verified"] + report["not_changed"] >= report["matched"] else 1

    except Exception as exc:
        report["error"] = str(exc)
        report["ok"] = False
        write_report(report)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
