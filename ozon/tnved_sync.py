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
DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() in {"1", "true", "yes", "on"}
MAX_UPDATES_PER_RUN = max(1, int(os.environ.get("MAX_UPDATES_PER_RUN", "500")))
BATCH_SIZE = max(1, min(50, int(os.environ.get("UPDATE_BATCH_SIZE", "50"))))
OVERRIDES_PATH = Path(os.environ.get("TNVED_OVERRIDES", "ozon/tnved_overrides.json"))
SUMMARY_PATH = Path(os.environ.get("TNVED_SUMMARY", "ozon/tnved_summary.md"))

if not CLIENT_ID or not API_KEY:
    print("ERROR: OZON_CLIENT_ID and OZON_API_KEY must be set", file=sys.stderr)
    sys.exit(2)

HEADERS = {
    "Client-Id": CLIENT_ID,
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "megapolis-tnved-sync/1.0",
}


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
            raise RuntimeError(f"Ozon API {path}: HTTP {exc.code}: {body[:1000]}") from exc
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
    seen_last = set()
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
        if not items or len(out) >= total or not new_last or new_last == last_id or new_last in seen_last:
            break
        seen_last.add(last_id)
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


def get_category_attributes(description_category_id, type_id):
    return (post("/v1/description-category/attribute", {
        "description_category_id": int(description_category_id),
        "type_id": int(type_id),
        "language": "DEFAULT",
    }).get("result") or [])


def get_dictionary_values(description_category_id, type_id, attribute_id):
    out = []
    last_value_id = 0
    while True:
        payload = {
            "attribute_id": int(attribute_id),
            "description_category_id": int(description_category_id),
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


def normalize_name(value):
    return " ".join(str(value or "").replace("Ё", "Е").replace("ё", "е").upper().split())


def is_tnved_name(name):
    s = normalize_name(name).replace("-", " ")
    return ("ТН" in s and "ВЭД" in s) or "TN VED" in s or "ТНВЭД" in s


def attr_value_tuple(attribute):
    values = attribute.get("values") or []
    for value in values:
        text = str(value.get("value") or "").strip()
        dictionary_id = int(value.get("dictionary_value_id") or 0)
        if text or dictionary_id:
            return (dictionary_id, text)
    return None


def find_product_attr(product, attribute_id):
    for attr in product.get("attributes") or []:
        if int(attr.get("id") or attr.get("attribute_id") or 0) == int(attribute_id):
            return attr
    return None


def load_overrides():
    if not OVERRIDES_PATH.exists():
        return {"by_offer_id": {}, "by_category_type": {}}
    with OVERRIDES_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("by_offer_id", {})
    data.setdefault("by_category_type", {})
    return data


def resolve_override(raw, category_id, type_id, attribute_id, dictionary_cache):
    if not raw:
        return None
    if isinstance(raw, str):
        raw = {"value": raw}
    value = str(raw.get("value") or "").strip()
    dictionary_value_id = int(raw.get("dictionary_value_id") or 0)
    if dictionary_value_id:
        return (dictionary_value_id, value)
    if not value:
        return None
    key = (category_id, type_id, attribute_id)
    if key not in dictionary_cache:
        dictionary_cache[key] = get_dictionary_values(category_id, type_id, attribute_id)
    target = normalize_name(value)
    matches = [x for x in dictionary_cache[key] if normalize_name(x.get("value")) == target]
    if len(matches) == 1:
        return (int(matches[0].get("id") or 0), str(matches[0].get("value") or value))
    return None


def get_daily_update_remaining():
    try:
        data = post("/v4/product/info/limit", {})
        daily = data.get("daily_update") or {}
        limit = int(daily.get("limit") or 0)
        usage = int(daily.get("usage") or 0)
        if limit > 0:
            return max(0, limit - usage), limit, usage, daily.get("reset_at")
    except Exception as exc:
        print(f"WARNING: could not read Ozon daily update limit: {exc}")
    return None, None, None, None


def main():
    catalog = list_products()
    ids = [int(x.get("product_id") or 0) for x in catalog if int(x.get("product_id") or 0)]
    products = get_product_attributes(ids)
    print(f"Products listed: {len(catalog)}; attributes loaded: {len(products)}")

    category_meta = {}
    tnved_attr_by_group = {}
    skipped_no_tnved_attr = set()

    groups = sorted({
        (int(p.get("description_category_id") or 0), int(p.get("type_id") or 0))
        for p in products
        if int(p.get("description_category_id") or 0) and int(p.get("type_id") or 0)
    })

    for category_id, type_id in groups:
        attrs = get_category_attributes(category_id, type_id)
        category_meta[(category_id, type_id)] = attrs
        matches = [a for a in attrs if is_tnved_name(a.get("name"))]
        if len(matches) == 1:
            tnved_attr_by_group[(category_id, type_id)] = matches[0]
        else:
            skipped_no_tnved_attr.add((category_id, type_id))

    known_by_model = defaultdict(set)
    missing = []
    already_filled = 0

    for product in products:
        category_id = int(product.get("description_category_id") or 0)
        type_id = int(product.get("type_id") or 0)
        group = (category_id, type_id)
        meta = tnved_attr_by_group.get(group)
        if not meta:
            continue
        attribute_id = int(meta.get("id") or 0)
        attr = find_product_attr(product, attribute_id)
        value = attr_value_tuple(attr or {})
        model_id = int((product.get("model_info") or {}).get("model_id") or 0)
        if value:
            already_filled += 1
            if model_id:
                known_by_model[(group, model_id)].add(value)
        else:
            missing.append({
                "product": product,
                "group": group,
                "model_id": model_id,
                "attribute_id": attribute_id,
            })

    overrides = load_overrides()
    dictionary_cache = {}
    proposals = []
    unresolved = []

    for item in missing:
        product = item["product"]
        group = item["group"]
        category_id, type_id = group
        offer_id = str(product.get("offer_id") or "")
        attribute_id = item["attribute_id"]
        source = None
        resolved = None

        raw_override = (overrides.get("by_offer_id") or {}).get(offer_id)
        if raw_override:
            resolved = resolve_override(raw_override, category_id, type_id, attribute_id, dictionary_cache)
            source = "verified offer override" if resolved else None

        if not resolved and item["model_id"]:
            candidates = known_by_model.get((group, item["model_id"]), set())
            if len(candidates) == 1:
                resolved = next(iter(candidates))
                source = f"same Ozon model_id={item['model_id']}"

        if not resolved:
            key = f"{category_id}:{type_id}"
            raw_group_override = (overrides.get("by_category_type") or {}).get(key)
            if raw_group_override:
                resolved = resolve_override(raw_group_override, category_id, type_id, attribute_id, dictionary_cache)
                source = "verified category/type override" if resolved else None

        if resolved:
            dictionary_value_id, value = resolved
            if not dictionary_value_id:
                unresolved.append((offer_id, "resolved value has no dictionary_value_id"))
                continue
            proposals.append({
                "offer_id": offer_id,
                "name": str(product.get("name") or ""),
                "attribute_id": attribute_id,
                "dictionary_value_id": dictionary_value_id,
                "value": value,
                "source": source,
            })
        else:
            unresolved.append((offer_id, "no unambiguous verified TN VED source"))

    remaining, limit, usage, reset_at = get_daily_update_remaining()
    cap = MAX_UPDATES_PER_RUN
    if remaining is not None:
        cap = min(cap, remaining)
    selected = proposals[:cap]
    deferred = proposals[cap:]

    updated = []
    tasks = []
    if not DRY_RUN:
        for batch in chunks(selected, BATCH_SIZE):
            payload = {
                "items": [
                    {
                        "offer_id": x["offer_id"],
                        "attributes": [{
                            "complex_id": 0,
                            "id": x["attribute_id"],
                            "values": [{
                                "dictionary_value_id": x["dictionary_value_id"],
                                "value": x["value"],
                            }],
                        }],
                    }
                    for x in batch
                ]
            }
            response = post("/v1/product/attributes/update", payload)
            tasks.append(response.get("task_id"))
            updated.extend(batch)
            time.sleep(0.25)
    else:
        updated = selected

    lines = []
    lines.append("# Ozon ТН ВЭД sync")
    lines.append("")
    lines.append(f"- Products loaded: **{len(products)}**")
    lines.append(f"- Already filled ТН ВЭД: **{already_filled}**")
    lines.append(f"- Missing ТН ВЭД: **{len(missing)}**")
    lines.append(f"- Safe proposals: **{len(proposals)}**")
    lines.append(f"- {'Would update' if DRY_RUN else 'Updated'} this run: **{len(updated)}**")
    lines.append(f"- Unresolved (left unchanged): **{len(unresolved)}**")
    lines.append(f"- Deferred by run/daily limit: **{len(deferred)}**")
    lines.append(f"- Category/type groups without exactly one ТН ВЭД attribute: **{len(skipped_no_tnved_attr)}**")
    if limit is not None:
        lines.append(f"- Ozon daily update quota before run: **{usage}/{limit}**, remaining **{remaining}**, reset \`{reset_at}\`")
    lines.append(f"- Mode: **{'DRY RUN' if DRY_RUN else 'WRITE'}**")

    if updated:
        lines += ["", "## Updated / proposed", "", "| Offer ID | ТН ВЭД | Source |", "|---|---|---|"]
        for x in updated[:200]:
            lines.append(f"| \`{x['offer_id']}\` | {x['value']} | {x['source']} |")
        if len(updated) > 200:
            lines.append(f"| … | … | {len(updated) - 200} more |")

    if unresolved:
        lines += ["", "## Not changed — needs verified mapping", "", "| Offer ID | Reason |", "|---|---|"]
        for offer_id, reason in unresolved[:300]:
            lines.append(f"| \`{offer_id}\` | {reason} |")
        if len(unresolved) > 300:
            lines.append(f"| … | {len(unresolved) - 300} more |")

    if skipped_no_tnved_attr:
        lines += ["", "## Category/type groups not handled", ""]
        for category_id, type_id in sorted(skipped_no_tnved_attr)[:100]:
            lines.append(f"- \`{category_id}:{type_id}\`")

    if tasks:
        lines += ["", "## Ozon task IDs", "", ", ".join(str(x) for x in tasks if x is not None)]

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:20]))


if __name__ == "__main__":
    main()
