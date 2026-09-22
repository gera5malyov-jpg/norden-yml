#!/usr/bin/env python3
import json
import sys
import time
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brand_sync_once as b

REPORT_JSON = Path("ozon/brand_sync_v2_report.json")
REPORT_MD = Path("ozon/brand_sync_v2_report.md")
SOURCE_NAMES = {"RIVA CHAIR", "RV DESIGN"}
TARGET_NAME = "Мегаполис"
BATCH_SIZE = 50

def search_values(category_id, type_id, attribute_id, value):
    data = b.post("/v1/description-category/attribute/values/search", {
        "attribute_id": int(attribute_id),
        "description_category_id": int(category_id),
        "type_id": int(type_id),
        "value": value,
        "limit": 100,
    })
    return data.get("result") or []

def find_attr(product, attr_id):
    for attr in product.get("attributes") or []:
        if int(attr.get("id") or attr.get("attribute_id") or 0) == int(attr_id):
            return attr
    return None

def attr_values(attr):
    out = []
    if not attr:
        return out
    for v in attr.get("values") or []:
        out.append({
            "value": str(v.get("value") or "").strip(),
            "dictionary_value_id": int(v.get("dictionary_value_id") or 0),
        })
    return out

def write_report(r):
    REPORT_JSON.write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Ozon — замена бренда на «Мегаполис» (v2)",
        "",
        f"- Результат: **{'УСПЕШНО' if r.get('ok') else 'ЗАВЕРШЕНО С ОШИБКАМИ'}**",
        f"- Товаров загружено: **{r.get('products_loaded', 0)}**",
        f"- Категорий/типов с найденным полем бренда: **{r.get('brand_groups', 0)}**",
        f"- Найдено RIVA Chair / Riva Chair / RV DESIGN: **{r.get('matched', 0)}**",
        f"- Отправлено на изменение: **{r.get('submitted', 0)}**",
        f"- Подтверждено как «Мегаполис»: **{r.get('verified', 0)}**",
        f"- Не изменено: **{r.get('not_changed', 0)}**",
    ]
    if r.get("daily_limit") is not None:
        lines.append(
            f"- Лимит обновлений Ozon до запуска: **{r.get('daily_usage')}/{r.get('daily_limit')}**, "
            f"остаток **{r.get('daily_remaining')}**"
        )
    if r.get("changed"):
        lines += ["", "## Изменено", "", "| Артикул | Было | Стало | Поле |", "|---|---|---|---|"]
        for x in r["changed"][:1000]:
            lines.append(f"| `{x['offer_id']}` | {x['old_brand']} | {x['new_brand']} | {x['attribute_name']} |")
    if r.get("skipped"):
        lines += ["", "## Не изменено", "", "| Артикул | Причина |", "|---|---|"]
        for x in r["skipped"][:1000]:
            lines.append(f"| `{x.get('offer_id','')}` | {x.get('reason','')} |")
    if r.get("diagnostics"):
        lines += ["", "## Диагностика справочников", "", "| Категория | Тип | Атрибут | Поле | RIVA IDs | RV DESIGN IDs | Мегаполис IDs |", "|---:|---:|---:|---|---|---|---|"]
        for d in r["diagnostics"][:300]:
            lines.append(
                f"| {d['description_category_id']} | {d['type_id']} | {d['attribute_id']} | "
                f"{d['attribute_name']} | {','.join(map(str,d['riva_ids']))} | "
                f"{','.join(map(str,d['rv_design_ids']))} | {','.join(map(str,d['target_ids']))} |"
            )
    if r.get("error"):
        lines += ["", "## Ошибка", "", r["error"]]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main():
    r = {
        "ok": False,
        "products_loaded": 0,
        "brand_groups": 0,
        "matched": 0,
        "submitted": 0,
        "verified": 0,
        "not_changed": 0,
        "changed": [],
        "skipped": [],
        "diagnostics": [],
        "task_ids": [],
    }
    try:
        catalog = b.list_products()
        ids = [int(x.get("product_id") or 0) for x in catalog if int(x.get("product_id") or 0)]
        products = b.get_product_attributes(ids)
        r["products_loaded"] = len(products)

        groups = sorted({
            (int(p.get("description_category_id") or 0), int(p.get("type_id") or 0))
            for p in products
            if int(p.get("description_category_id") or 0) and int(p.get("type_id") or 0)
        })

        brand_attrs = {}
        for cat, typ in groups:
            attrs = b.get_category_attributes(cat, typ)
            candidates = []
            for a in attrs:
                name = str(a.get("name") or "")
                n = b.norm(name)
                if "БРЕНД" in n or "BRAND" in n:
                    candidates.append((int(a.get("id") or 0), name))
            if candidates:
                brand_attrs[(cat, typ)] = candidates

        r["brand_groups"] = len(brand_attrs)

        products_by_group = defaultdict(list)
        for p in products:
            group = (int(p.get("description_category_id") or 0), int(p.get("type_id") or 0))
            if group in brand_attrs:
                products_by_group[group].append(p)

        config = {}
        for group, attrs in brand_attrs.items():
            cat, typ = group
            for aid, aname in attrs:
                # Only spend dictionary lookups if at least one product actually has this attribute.
                present = False
                for p in products_by_group.get(group, []):
                    if attr_values(find_attr(p, aid)):
                        present = True
                        break
                if not present:
                    continue

                riva = [x for x in search_values(cat, typ, aid, "RIVA Chair") if b.norm(x.get("value")) == "RIVA CHAIR"]
                rv = [x for x in search_values(cat, typ, aid, "RV DESIGN") if b.norm(x.get("value")) == "RV DESIGN"]
                target = [x for x in search_values(cat, typ, aid, TARGET_NAME) if b.norm(x.get("value")) == b.norm(TARGET_NAME)]

                riva_ids = [int(x.get("id") or 0) for x in riva if int(x.get("id") or 0)]
                rv_ids = [int(x.get("id") or 0) for x in rv if int(x.get("id") or 0)]
                target_ids = [int(x.get("id") or 0) for x in target if int(x.get("id") or 0)]

                r["diagnostics"].append({
                    "description_category_id": cat,
                    "type_id": typ,
                    "attribute_id": aid,
                    "attribute_name": aname,
                    "riva_ids": riva_ids,
                    "rv_design_ids": rv_ids,
                    "target_ids": target_ids,
                })

                source_id_to_name = {int(x.get("id") or 0): str(x.get("value") or "") for x in (riva + rv) if int(x.get("id") or 0)}
                config[(group, aid)] = {
                    "attribute_name": aname,
                    "source_ids": set(source_id_to_name),
                    "source_id_to_name": source_id_to_name,
                    "target": target,
                }

        candidates = []
        for group, plist in products_by_group.items():
            for aid, aname in brand_attrs[group]:
                cfg = config.get((group, aid))
                if not cfg:
                    continue
                for p in plist:
                    vals = attr_values(find_attr(p, aid))
                    for val in vals:
                        text_norm = b.norm(val["value"])
                        did = val["dictionary_value_id"]
                        if text_norm in SOURCE_NAMES or did in cfg["source_ids"]:
                            old = val["value"] or cfg["source_id_to_name"].get(did) or str(did)
                            candidates.append({
                                "product_id": int(p.get("id") or p.get("product_id") or 0),
                                "offer_id": str(p.get("offer_id") or ""),
                                "name": str(p.get("name") or ""),
                                "group": group,
                                "attribute_id": aid,
                                "attribute_name": aname,
                                "old_brand": old,
                            })
                            break

        # Deduplicate offer IDs in case more than one brand-like attribute exists.
        dedup = {}
        for x in candidates:
            dedup[x["offer_id"]] = x
        candidates = list(dedup.values())
        r["matched"] = len(candidates)

        ready = []
        for x in candidates:
            cfg = config.get((x["group"], x["attribute_id"])) or {}
            targets = cfg.get("target") or []
            target_ids = [int(v.get("id") or 0) for v in targets if int(v.get("id") or 0)]
            if len(target_ids) != 1:
                r["skipped"].append({
                    "offer_id": x["offer_id"],
                    "reason": "В справочнике брендов Ozon не найдено единственное точное значение «Мегаполис»",
                })
                continue
            x["target_id"] = target_ids[0]
            x["target_value"] = str(targets[0].get("value") or TARGET_NAME)
            ready.append(x)

        remaining, limit, usage, reset_at = b.get_daily_remaining()
        r["daily_remaining"] = remaining
        r["daily_limit"] = limit
        r["daily_usage"] = usage
        r["daily_reset_at"] = reset_at

        selected = ready
        if remaining is not None and len(selected) > remaining:
            for x in selected[remaining:]:
                r["skipped"].append({
                    "offer_id": x["offer_id"],
                    "reason": "Не хватило суточного лимита обновлений Ozon",
                })
            selected = selected[:remaining]

        submitted = []
        for batch in b.chunks(selected, BATCH_SIZE):
            payload = {
                "items": [
                    {
                        "offer_id": x["offer_id"],
                        "attributes": [{
                            "complex_id": 0,
                            "id": x["attribute_id"],
                            "values": [{
                                "dictionary_value_id": x["target_id"],
                                "value": x["target_value"],
                            }],
                        }],
                    }
                    for x in batch
                ]
            }
            try:
                resp = b.post("/v1/product/attributes/update", payload)
                if resp.get("task_id") is not None:
                    r["task_ids"].append(resp.get("task_id"))
                submitted.extend(batch)
            except Exception as exc:
                for x in batch:
                    r["skipped"].append({
                        "offer_id": x["offer_id"],
                        "reason": "Ошибка Ozon при обновлении: " + str(exc)[:500],
                    })
            time.sleep(0.4)

        r["submitted"] = len(submitted)

        if submitted:
            time.sleep(10)
            verify = b.get_product_attributes([x["product_id"] for x in submitted if x["product_id"]])
            by_offer = {str(p.get("offer_id") or ""): p for p in verify}
            for x in submitted:
                p = by_offer.get(x["offer_id"])
                if not p:
                    r["skipped"].append({
                        "offer_id": x["offer_id"],
                        "reason": "Товар не вернулся в контрольной выборке Ozon",
                    })
                    continue
                vals = attr_values(find_attr(p, x["attribute_id"]))
                ok = any(
                    v["dictionary_value_id"] == x["target_id"] or b.norm(v["value"]) == b.norm(TARGET_NAME)
                    for v in vals
                )
                if ok:
                    r["changed"].append({
                        "offer_id": x["offer_id"],
                        "name": x["name"],
                        "old_brand": x["old_brand"],
                        "new_brand": TARGET_NAME,
                        "attribute_name": x["attribute_name"],
                    })
                else:
                    actual = ", ".join(v["value"] or str(v["dictionary_value_id"]) for v in vals)
                    r["skipped"].append({
                        "offer_id": x["offer_id"],
                        "reason": f"Контрольная проверка не подтвердила «Мегаполис»; сейчас: {actual}",
                    })

        r["verified"] = len(r["changed"])
        r["not_changed"] = len(r["skipped"])
        r["ok"] = (r["verified"] == r["matched"] and r["not_changed"] == 0)
        write_report(r)
        print(json.dumps({
            "matched": r["matched"],
            "submitted": r["submitted"],
            "verified": r["verified"],
            "not_changed": r["not_changed"],
            "ok": r["ok"],
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        r["error"] = str(exc)
        r["not_changed"] = len(r.get("skipped") or [])
        write_report(r)
        print("ERROR:", exc, file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
