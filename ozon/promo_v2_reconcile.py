#!/usr/bin/env python3
"""Reconcile Ozon promotions using November 2026 v2 read APIs and unified price update.

Only the seller's established min_price is used as the hard floor. Neither
base price nor min_price is modified here. Default is read-only.
"""
import json
import os
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://api-seller.ozon.ru"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() not in ("false", "0", "no")
HEADERS = {
    "Client-Id": os.environ["OZON_CLIENT_ID"],
    "Api-Key": os.environ["OZON_API_KEY"],
    "Content-Type": "application/json",
}
OUT = Path("ozon/promo_v2_reconcile_report.json")
HUNDRED = Decimal("100")


def num(value):
    if isinstance(value, dict):
        value = value.get("amount")
    try:
        n = Decimal(str(value or "0"))
        return n if n.is_finite() else Decimal("0")
    except (InvalidOperation, TypeError):
        return Decimal("0")


def money(value):
    return {"amount": str(value.quantize(Decimal("0.01"))), "currency": "RUB"}


def api(path, data=None, method="POST"):
    payload = None if method == "GET" else json.dumps(data or {}).encode("utf-8")
    for attempt in range(12):
        req = Request(BASE + path, data=payload, headers=HEADERS, method=method)
        try:
            with urlopen(req, timeout=120) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")[:2500]
            if exc.code in (429, 500, 502, 503, 504) and attempt < 11:
                time.sleep(min(15, 1 + attempt * 2))
                continue
            raise RuntimeError(f"{path}: HTTP {exc.code}: {raw}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt < 11:
                time.sleep(min(15, 1 + attempt * 2))
                continue
            raise RuntimeError(f"{path}: {exc}") from exc
    raise RuntimeError(f"Retries exhausted: {path}")


def pages(path, action_id):
    result = []
    cursor = ""
    seen = set()
    for page_num in range(400):
        data = api(path, {"action_id": action_id, "limit": 100, "last_id": cursor})
        page = data.get("products") or []
        result.extend(page)
        nxt = str(data.get("last_id") or "")
        if not page or len(result) >= int(data.get("total") or 0) > 0:
            break
        if not nxt or nxt == cursor or nxt in seen:
            if len(page) == 100:
                raise RuntimeError(f"Incomplete pagination for {path} action {action_id}")
            break
        cursor = nxt
        seen.add(nxt)
    else:
        raise RuntimeError(f"Too many pages: {path} action {action_id}")
    return result


def load_prices():
    items = {}
    cursor = ""
    seen = set()
    for _ in range(100):
        data = api("/v5/product/info/prices", {"cursor": cursor, "filter": {"visibility": "ALL"}, "limit": 1000})
        page = data.get("items") or []
        for product in page:
            pid = int(product.get("product_id") or 0)
            if pid:
                items[pid] = product
        nxt = str(data.get("cursor") or "")
        if not page or not nxt:
            break
        if nxt == cursor or nxt in seen:
            raise RuntimeError("Price pagination cursor repeated")
        seen.add(nxt)
        cursor = nxt
    else:
        raise RuntimeError("Price pagination exceeded safe limit")
    return items


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def active_prices_and_bad(actions, price_by_pid):
    active_prices = defaultdict(list)
    bad = []
    participants = {}
    for action in actions:
        aid = int(action["id"])
        rows = pages("/v2/actions/products", aid)
        participants[aid] = rows
        for row in rows:
            pid = int(row.get("id") or 0)
            master = price_by_pid.get(pid)
            if master is None:
                continue
            floor = num((master.get("price") or {}).get("min_price"))
            floor = max(floor, num(row.get("min_seller_price")))
            actual = num(row.get("action_price"))
            if actual > 0 and floor > 0 and actual < floor:
                bad.append((aid, pid, floor, row))
            if actual > 0:
                active_prices[pid].append(actual)
    return participants, active_prices, bad


def main():
    report = {
        "mode": "DRY_RUN" if DRY_RUN else "WRITE",
        "prices_loaded": 0,
        "actions": [],
        "below_min_before": 0,
        "below_min_after": None,
        "remove_confirmed": 0,
        "remove_rejected": [],
        "eligible_action_rows": 0,
        "skipped_below_floor": 0,
        "skipped_quarantine": 0,
        "skipped_no_floor": 0,
        "planned_additions": 0,
        "confirmed_additions": 0,
        "rejected_additions": [],
        "final_action_counts": [],
    }
    try:
        price_by_pid = load_prices()
        report["prices_loaded"] = len(price_by_pid)
        actions = api("/v1/actions", method="GET").get("result") or []
        participant_rows, current_ap, violations = active_prices_and_bad(actions, price_by_pid)
        report["below_min_before"] = len(violations)
        for action in actions:
            aid = int(action["id"])
            report["actions"].append({"id": aid, "title": action.get("title"), "type": action.get("action_type"), "participants_before": len(participant_rows[aid]), "candidates": 0, "safe_candidates": 0, "confirmed_additions": 0})

        # Remove out-of-policy participants by setting a price above the action's
        # entry ceiling (new unified Ozon price logic). Only for actual violations.
        if not DRY_RUN:
            for aid, pid, floor, row in violations:
                limit = num(row.get("max_action_price"))
                if limit <= 0:
                    report["remove_rejected"].append({"action_id": aid, "product_id": pid, "reason": "missing max_action_price"})
                    continue
                leave_price = max(floor, limit + Decimal("1"))
                data = api("/v1/actions/products/update", {"action_id": aid, "products": [
                    {"product_id": pid, "action_price": money(leave_price)}
                ]})
                removed = data.get("deactivated_product_ids") or data.get("deactivated_ids") or []
                report["remove_confirmed"] += len(removed)
                if int(pid) not in [int(x) for x in removed]:
                    report["remove_rejected"].append({"action_id": aid, "product_id": pid, "response": data})

        # Refresh participant prices if removal changed unified prices.
        if violations and not DRY_RUN:
            participant_rows, current_ap, _ = active_prices_and_bad(actions, price_by_pid)

        candidates = {}
        plans_by_product = defaultdict(list)
        for summary, action in zip(report["actions"], actions):
            aid = int(action["id"])
            rows = pages("/v2/actions/candidates", aid)
            summary["candidates"] = len(rows)
            candidates[aid] = []
            existing_ids = {int(p.get("id") or 0) for p in participant_rows[aid]}
            for row in rows:
                pid = int(row.get("id") or 0)
                master = price_by_pid.get(pid)
                if not master or pid in existing_ids:
                    continue
                floor = max(num((master.get("price") or {}).get("min_price")), num(row.get("min_seller_price")))
                ceiling = num(row.get("max_action_price"))
                if floor <= 0:
                    report["skipped_no_floor"] += 1
                    continue
                if row.get("is_quarantined") is True:
                    report["skipped_quarantine"] += 1
                    continue
                if ceiling <= 0 or ceiling < floor:
                    report["skipped_below_floor"] += 1
                    continue
                # No inferred price below Ozon's current minimum.
                candidates[aid].append((pid, floor, ceiling))
                plans_by_product[pid].append((aid, floor, ceiling))
                summary["safe_candidates"] += 1

        report["eligible_action_rows"] = sum(len(x) for x in candidates.values())

        # Unified per-SKU price: lowest qualifying ceiling across all target
        # actions, never higher than an existing valid action price.
        selected_price = {}
        for pid, options in plans_by_product.items():
            floor = max(x[1] for x in options)
            ceiling = min(x[2] for x in options)
            if current_ap.get(pid):
                valid_existing = [p for p in current_ap[pid] if p >= floor]
                if valid_existing:
                    ceiling = min(ceiling, min(valid_existing))
            if ceiling >= floor:
                selected_price[pid] = ceiling

        plan = []
        for action in actions:
            aid = int(action["id"])
            for pid, floor, ceiling in candidates[aid]:
                p = selected_price.get(pid)
                if p is not None and floor <= p <= ceiling:
                    row = {"product_id": pid, "action_price": money(p)}
                    # Since 19 Oct 2026, stock is not accepted for boosting actions.
                    # Quantity, if applicable, must be managed via Ozon-specific controls.
                    plan.append((aid, row))
        report["planned_additions"] = len(plan)

        if not DRY_RUN:
            # Sending batch sizes small enough to avoid mass rejections during
            # unified-price validation; read back authoritative state afterwards.
            for summary in report["actions"]:
                aid = summary["id"]
                products = [row for action_id, row in plan if action_id == aid]
                for batch in chunks(products, 100):
                    data = api("/v1/actions/products/update", {"action_id": aid, "products": batch})
                    confirmed = data.get("active_product_ids") or data.get("product_ids") or []
                    rejected = data.get("rejected") or []
                    summary["confirmed_additions"] += len(confirmed)
                    report["confirmed_additions"] += len(confirmed)
                    if rejected:
                        report["rejected_additions"].extend([{"action_id": aid, "entry": r} for r in rejected][:250])

            # An independent v2 readback is the final source of truth.
            time.sleep(4)
            refreshed = load_prices()
            final_rows, _, final_bad = active_prices_and_bad(actions, refreshed)
            report["below_min_after"] = len(final_bad)
            report["remaining_below_min_sample"] = [
                {"action_id": aid, "product_id": pid, "promo": str(num(row.get("action_price"))), "minimum": str(floor)}
                for aid, pid, floor, row in final_bad[:40]
            ]
            report["final_action_counts"] = [
                {"id": int(a["id"]), "title": a.get("title"), "participants": len(final_rows[int(a["id"])])}
                for a in actions
            ]
            if final_bad:
                raise RuntimeError(f"{len(final_bad)} promotional prices remain below minimum")
        print("PROMO_V2_REPORT", json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    finally:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
