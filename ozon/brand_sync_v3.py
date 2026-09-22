#!/usr/bin/env python3
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brand_sync_once as b

REPORT_JSON = Path("ozon/brand_sync_v3_report.json")
REPORT_MD = Path("ozon/brand_sync_v3_report.md")
BRAND_ATTR_ID = 85
SOURCE_IDS = {
    115843344: "RIVA Chair",
    970976483: "RIVA Chair",
    971871159: "RV DESIGN",
}
TARGET_IDS = [115868128, 971438966, 972984135]
TARGET_NAME = "Мегаполис"

def list_visibility(visibility):
    out = []
    last_id = ""
    seen = set()
    while True:
        body = {"filter": {"visibility": visibility}, "limit": 1000}
        if last_id:
            body["last_id"] = last_id
        data = b.post("/v3/product/list", body)
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

def attr_values(product, attr_id=BRAND_ATTR_ID):
    for a in product.get("attributes") or []:
        if int(a.get("id") or a.get("attribute_id") or 0) != int(attr_id):
            continue
        return [
            {
                "dictionary_value_id": int(v.get("dictionary_value_id") or 0),
                "value": str(v.get("value") or "").strip(),
            }
            for v in (a.get("values") or [])
        ]
    return []

def write_report(r):
    REPORT_JSON.write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Ozon — замена RIVA/RV DESIGN на Мегаполис (v3)",
        "",
        f"- Результат: **{'УСПЕШНО' if r.get('ok') else 'ЗАВЕРШЕНО С ОШИБКАМИ'}**",
        f"- Неархивных товаров: **{r.get('active_count', 0)}**",
        f"- Архивных товаров: **{r.get('archived_count', 0)}**",
        f"- Всего уникальных товаров: **{r.get('total_count', 0)}**",
        f"- Найдено RIVA Chair / Riva Chair / RV DESIGN: **{r.get('matched', 0)}**",
        f"- В неархивных: **{r.get('matched_active', 0)}**",
        f"- В архиве: **{r.get('matched_archived', 0)}**",
        f"- Выбран ID бренда «Мегаполис»: **{r.get('target_id')}**",
        f"- Отправлено на изменение: **{r.get('submitted', 0)}**",
        f"- Подтверждено контрольным чтением: **{r.get('verified', 0)}**",
        f"- Ожидает отражения/не подтверждено: **{r.get('pending_or_failed', 0)}**",
    ]
    lines += ["", "## Использование ID «Мегаполис» до изменения", ""]
    for k,v in (r.get("target_id_usage") or {}).items():
        lines.append(f"- `{k}`: **{v}** товаров")
    if r.get("changed"):
        lines += ["", "## Найденные и отправленные товары", "", "| Артикул | Было | Архив | Контроль |", "|---|---|---|---|"]
        for x in r["changed"][:2000]:
            lines.append(f"| `{x['offer_id']}` | {x['old_brand']} | {'да' if x['archived'] else 'нет'} | {x['status']} |")
    if r.get("error"):
        lines += ["", "## Ошибка", "", str(r["error"])]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main():
    r = {
        "ok": False,
        "active_count": 0,
        "archived_count": 0,
        "total_count": 0,
        "matched": 0,
        "matched_active": 0,
        "matched_archived": 0,
        "target_id": None,
        "target_id_usage": {},
        "submitted": 0,
        "verified": 0,
        "pending_or_failed": 0,
        "task_ids": [],
        "changed": [],
    }
    try:
        active = list_visibility("ALL")
        archived = list_visibility("ARCHIVED")
        r["active_count"] = len(active)
        r["archived_count"] = len(archived)

        by_pid = {}
        archived_pids = set()
        for x in active:
            pid = int(x.get("product_id") or 0)
            if pid:
                by_pid[pid] = x
        for x in archived:
            pid = int(x.get("product_id") or 0)
            if pid:
                by_pid[pid] = x
                archived_pids.add(pid)

        ids = list(by_pid)
        r["total_count"] = len(ids)
        products = b.get_product_attributes(ids)

        target_usage = Counter()
        matches = []
        for p in products:
            pid = int(p.get("id") or p.get("product_id") or 0)
            offer = str(p.get("offer_id") or "")
            vals = attr_values(p)
            for v in vals:
                did = v["dictionary_value_id"]
                if did in TARGET_IDS:
                    target_usage[did] += 1
                if did in SOURCE_IDS:
                    matches.append({
                        "product_id": pid,
                        "offer_id": offer,
                        "name": str(p.get("name") or ""),
                        "old_brand": SOURCE_IDS[did],
                        "old_brand_id": did,
                        "archived": pid in archived_pids,
                    })
                    break

        r["target_id_usage"] = {str(x): int(target_usage.get(x, 0)) for x in TARGET_IDS}
        r["matched"] = len(matches)
        r["matched_archived"] = sum(1 for x in matches if x["archived"])
        r["matched_active"] = r["matched"] - r["matched_archived"]

        ranked = sorted(((target_usage.get(x, 0), x) for x in TARGET_IDS), reverse=True)
        if not ranked or ranked[0][0] <= 0:
            raise RuntimeError("Нельзя безопасно выбрать один из дублей бренда «Мегаполис»: ни один ID ещё не используется в каталоге")
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            raise RuntimeError("Нельзя безопасно выбрать ID бренда «Мегаполис»: два ID используются одинаково часто")
        target_id = ranked[0][1]
        r["target_id"] = target_id

        submitted = []
        for batch in b.chunks(matches, 50):
            payload = {
                "items": [
                    {
                        "offer_id": x["offer_id"],
                        "attributes": [{
                            "complex_id": 0,
                            "id": BRAND_ATTR_ID,
                            "values": [{
                                "dictionary_value_id": target_id,
                                "value": TARGET_NAME,
                            }],
                        }],
                    }
                    for x in batch
                ]
            }
            resp = b.post("/v1/product/attributes/update", payload)
            if resp.get("task_id") is not None:
                r["task_ids"].append(resp.get("task_id"))
            submitted.extend(batch)
            time.sleep(0.4)

        r["submitted"] = len(submitted)

        # Give Ozon a short window to apply the async attribute update, then read back.
        if submitted:
            time.sleep(15)
            verify = b.get_product_attributes([x["product_id"] for x in submitted])
            by_offer = {str(p.get("offer_id") or ""): p for p in verify}
            verified_offers = set()
            for x in submitted:
                p = by_offer.get(x["offer_id"])
                vals = attr_values(p or {})
                ok = any(v["dictionary_value_id"] == target_id for v in vals)
                status = "подтверждено" if ok else "запрос принят, ещё не отражён"
                if ok:
                    verified_offers.add(x["offer_id"])
                r["changed"].append({**x, "status": status})
            r["verified"] = len(verified_offers)
            r["pending_or_failed"] = len(submitted) - r["verified"]

        # Accepted Ozon task(s) count as successful submission; verification may lag asynchronously.
        r["ok"] = (r["submitted"] == r["matched"] and not r.get("error"))
        write_report(r)
        print(json.dumps({
            "active": r["active_count"],
            "archived": r["archived_count"],
            "matched": r["matched"],
            "matched_active": r["matched_active"],
            "matched_archived": r["matched_archived"],
            "target_id": r["target_id"],
            "submitted": r["submitted"],
            "verified": r["verified"],
            "pending_or_failed": r["pending_or_failed"],
            "task_ids": r["task_ids"],
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        r["error"] = str(exc)
        write_report(r)
        print("ERROR:", exc, file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
