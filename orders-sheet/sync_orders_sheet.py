#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gspread

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
RUNTIME = HERE / "runtime"
RUNTIME.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT / "webasyst"))
import marketplace_order_sync as mp  # noqa: E402
from client import WebasystClient  # noqa: E402

# Google Sheet is owned by the user; GitHub service account only edits it.\nSHEET_TITLE = "заказы"
TAB_TITLE = "Заказы"
OWNER_EMAIL = os.getenv("GOOGLE_ORDERS_OWNER_EMAIL", "gera5malyov@gmail.com").strip()
ID_FILE = HERE / "google_sheet_id.txt"
RUNTIME_ID_FILE = RUNTIME / "google_sheet_id.txt"
REPORT_FILE = RUNTIME / "last_report.json"

SOURCE_CODE = {
    "yandex_market": "Я",
    "ozon": "OZ",
    "wildberries": "WB^",
    "yandex_kit": "KIT",
    "webasyst": "П",
}
TERMINAL_TARGETS = {"completed", "otmenen", "refunded"}
TERMINAL_STATE_IDS = {
    "completed", "complete", "refunded", "refund", "otmenen",
    "cancelled", "canceled", "deleted",
}


def s(v: Any) -> str:
    return str(v or "").strip()


def listify(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [x for x in value.values() if isinstance(x, dict)]
    return []


def first_scalar(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (str, int, float)):
        return s(value)
    if isinstance(value, list):
        for x in value:
            got = first_scalar(x)
            if got:
                return got
        return ""
    if isinstance(value, dict):
        for key in ("value", "phone", "name", "text"):
            got = first_scalar(value.get(key))
            if got:
                return got
        for x in value.values():
            got = first_scalar(x)
            if got:
                return got
    return ""


def service_account_info(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured")
    if raw.startswith("{"):
        return json.loads(raw)
    return json.loads(base64.b64decode(raw).decode("utf-8"))


def normalize_date(value: Any) -> str:
    text = s(value)
    if not text:
        return ""
    text = text[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d.%m.%Y")
        except ValueError:
            pass
    return text


def date_sort_key(value: str):
    try:
        return datetime.strptime(value, "%d.%m.%Y")
    except Exception:
        return datetime.max


def address_text(address: Any) -> str:
    if not isinstance(address, dict):
        return s(address)
    full = s(address.get("fullAddress") or address.get("full_address"))
    if full:
        return full
    parts = []
    for key in ("zip", "postcode", "country", "region", "city", "street"):
        val = s(address.get(key))
        if val and val not in parts:
            parts.append(val)
    return ", ".join(parts)


def person_name(data: Any) -> str:
    if not isinstance(data, dict):
        return s(data)
    full = s(data.get("name") or data.get("fullName") or data.get("full_name"))
    if full:
        return full
    return " ".join(x for x in (
        s(data.get("lastname") or data.get("lastName")),
        s(data.get("firstname") or data.get("firstName")),
        s(data.get("middlename") or data.get("middleName")),
    ) if x).strip()


def phone_text(data: Any) -> str:
    if not isinstance(data, dict):
        return s(data)
    phone = first_scalar(
        data.get("phone")
        or data.get("telephone")
        or data.get("mobile")
        or data.get("replacementPhone")
    )
    ext = s(data.get("phoneCode") or data.get("extension") or data.get("ext"))
    if phone and ext and ext not in phone:
        phone = f"{phone} доб. {ext}"
    return phone


def customer_comment(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    candidates = [
        raw.get("customer_comment"), raw.get("customerComment"),
        raw.get("buyer_comment"), raw.get("buyerComment"),
        raw.get("comment"), raw.get("notes"), raw.get("note"),
    ]
    buyer = raw.get("buyer") if isinstance(raw.get("buyer"), dict) else {}
    customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
    address = raw.get("shipping_address") if isinstance(raw.get("shipping_address"), dict) else {}
    candidates += [
        buyer.get("comment"), buyer.get("notes"),
        customer.get("comment"), customer.get("notes"),
        address.get("comment"), address.get("notes"),
    ]
    for value in candidates:
        text = s(value)
        if text and not text.startswith("Импортировано из "):
            return text
    return ""


def lift_text(lift_type: Any, lift_price: Any) -> str:
    lt = s(lift_type)
    lp = s(lift_price)
    try:
        price = float(str(lift_price).replace(" ", "").replace(",", ".")) if lift_price not in (None, "") else None
    except Exception:
        price = None
    if lt.upper() == "NOT_NEEDED" or (price is not None and price <= 0 and not lt):
        return "Нет"
    if price is not None and price > 0:
        return f"Да, {price:g} ₽"
    if lt:
        low = lt.lower()
        if low in {"не нужен", "нет", "false", "0"}:
            return "Нет"
        return "Да" if low in {"yes", "true", "needed", "required"} else lt
    return ""


def build_sku_names(wa: WebasystClient) -> dict[str, str]:
    out: dict[str, str] = {}
    offset = 0
    while True:
        payload = wa.call("shop.product.search", params={
            "offset": offset,
            "limit": 1000,
            "fields": "id,name,skus",
        })
        batch = []
        if isinstance(payload, dict):
            batch = listify(payload.get("products") or payload.get("items"))
        elif isinstance(payload, list):
            batch = listify(payload)
        for prod in batch:
            name = s(prod.get("name"))
            for sku in listify(prod.get("skus")):
                code = s(sku.get("sku"))
                if code:
                    out.setdefault(code, name or code)
        if len(batch) < 1000:
            break
        offset += len(batch)
    return out


def item_text(items: Any, sku_names: dict[str, str]) -> tuple[str, int]:
    rows = listify(items)
    parts = []
    total = 0
    for item in rows:
        qty_raw = item.get("quantity", 1)
        try:
            qty = max(1, int(float(str(qty_raw).replace(",", "."))))
        except Exception:
            qty = 1
        total += qty
        sku = s(item.get("sku") or item.get("sku_code") or item.get("offer_id"))
        name = s(item.get("name") or item.get("product_name")) or sku_names.get(sku, "") or sku
        if not name:
            name = "Товар"
        parts.append(f"{name} × {qty}")
    return "; ".join(parts), total


def order_key(source: str, external_id: str) -> str:
    return f"{source}|{external_id}"


def webasyst_active_rows(wa: WebasystClient, sku_names: dict[str, str]) -> dict[str, dict]:
    settings = wa.call("shop.settings.get")
    states = settings.get("order_states") if isinstance(settings, dict) else []
    states = listify(states)

    active_states = []
    for st in states:
        sid = s(st.get("id"))
        name = s(st.get("name")).lower()
        terminal_by_name = bool(re.search(r"выполн|доставлен|получен|отмен|возврат|refund|cancel|complete", name))
        if sid and sid.lower() not in TERMINAL_STATE_IDS and not terminal_by_name:
            active_states.append(sid)

    rows: dict[str, dict] = {}
    for state_id in active_states:
        offset = 0
        while True:
            payload = wa.call("shop.order.search", params={
                "hash": f"search/state_id={state_id}",
                "offset": offset,
                "limit": 100,
                "fields": "*,state",
            })
            batch = []
            if isinstance(payload, dict):
                batch = listify(payload.get("orders") or payload.get("items"))
            elif isinstance(payload, list):
                batch = listify(payload)
            for summary in batch:
                oid = s(summary.get("id"))
                if not oid:
                    continue
                try:
                    info = wa.call("shop.order.getInfo", params={"id": oid})
                except Exception:
                    info = summary
                if not isinstance(info, dict):
                    continue
                params = dict(info.get("params") or {}) if isinstance(info.get("params"), dict) else {}
                source = s(params.get("mp_source")) or "webasyst"
                ext = s(params.get("mp_external_id")) or s(info.get("id_str")) or oid

                products, qty = item_text(info.get("items"), sku_names)
                contact = info.get("contact") if isinstance(info.get("contact"), dict) else {}
                phone = phone_text(contact)
                fio = person_name(contact)
                addr = address_text(info.get("shipping_address"))

                deadline = (
                    s(params.get("mp_delivery_to"))
                    or s(params.get("mp_delivery_from"))
                    or s(params.get("shipping_params_desired_delivery.date"))
                    or s(params.get("shipping_end_datetime"))[:10]
                )
                lift = lift_text(params.get("mp_lift_type"), params.get("mp_lift_price"))

                comment = s(info.get("comment"))
                if source != "webasyst" and comment.startswith("Импортировано из "):
                    comment = ""

                rows[order_key(source, ext)] = {
                    "source": source,
                    "code": SOURCE_CODE.get(source, "П"),
                    "order_no": ext,
                    "deadline": normalize_date(deadline),
                    "items": products,
                    "quantity": qty,
                    "phone": phone,
                    "fio": fio,
                    "address": addr,
                    "lift": lift,
                    "comment": comment,
                    "created_at": s(params.get("mp_created_at") or info.get("create_datetime")),
                }
            if len(batch) < 100:
                break
            offset += len(batch)
    return rows


def direct_marketplace_rows(sku_names: dict[str, str]) -> tuple[dict[str, dict], set[str], list[str]]:
    rows: dict[str, dict] = {}
    terminal_keys: set[str] = set()
    warnings: list[str] = []
    loaders = [
        ("yandex_market", mp.load_yandex_market),
        ("wildberries", mp.load_wb),
        ("yandex_kit", mp.load_kit),
        ("ozon", mp.load_ozon),
    ]
    for source, loader in loaders:
        try:
            orders = loader()
        except Exception as exc:
            warnings.append(f"{source}: {type(exc).__name__}: {str(exc)[:300]}")
            continue
        for o in orders:
            if not isinstance(o, dict):
                continue
            ext = s(o.get("external_id"))
            if not ext:
                continue
            k = order_key(source, ext)
            target = s(o.get("target_state"))
            if target in TERMINAL_TARGETS:
                terminal_keys.add(k)
                continue
            products, qty = item_text(o.get("items"), sku_names)
            buyer = o.get("buyer") if isinstance(o.get("buyer"), dict) else {}
            fio = s(o.get("recipient_name")) or person_name(buyer)
            phone = phone_text(buyer)
            rows[k] = {
                "source": source,
                "code": SOURCE_CODE[source],
                "order_no": ext,
                "deadline": normalize_date(o.get("delivery_to") or o.get("delivery_from")),
                "items": products,
                "quantity": qty,
                "phone": phone,
                "fio": fio,
                "address": address_text(o.get("shipping_address")),
                "lift": lift_text(o.get("lift_type"), o.get("lift_price")),
                "comment": customer_comment(o),
                "created_at": s(o.get("created_at")),
            }
    return rows, terminal_keys, warnings


def merge_rows(base: dict[str, dict], fresh: dict[str, dict], terminal: set[str]) -> list[dict]:
    for k in terminal:
        base.pop(k, None)
    for k, new in fresh.items():
        old = base.get(k, {})
        merged = dict(old)
        for field, value in new.items():
            if value not in ("", None, 0) or field in {"quantity"}:
                merged[field] = value
        base[k] = merged
    out = list(base.values())
    out.sort(key=lambda x: (
        date_sort_key(s(x.get("deadline"))),
        s(x.get("code")),
        s(x.get("order_no")),
    ))
    return out


def open_or_create_sheet():
    info = service_account_info(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""))
    gc = gspread.service_account_from_dict(info)

    sheet_id = s(os.getenv("GOOGLE_ORDERS_SHEET_ID"))
    if not sheet_id and ID_FILE.exists():
        sheet_id = ID_FILE.read_text(encoding="utf-8").strip()

    created = False
    if sheet_id:
        sh = gc.open_by_key(sheet_id)
    else:
        sh = gc.create(SHEET_TITLE)
        sheet_id = sh.id
        created = True
        if OWNER_EMAIL:
            try:
                sh.share(OWNER_EMAIL, perm_type="user", role="writer", notify=False)
            except TypeError:
                sh.share(OWNER_EMAIL, perm_type="user", role="writer")

    RUNTIME_ID_FILE.write_text(sheet_id + "\n", encoding="utf-8")
    return sh, created


def write_sheet(sh, rows: list[dict]):
    try:
        ws = sh.worksheet(TAB_TITLE)
    except gspread.WorksheetNotFound:
        if len(sh.worksheets()) == 1 and sh.sheet1.title in {"Sheet1", "Лист1"}:
            ws = sh.sheet1
            ws.update_title(TAB_TITLE)
        else:
            ws = sh.add_worksheet(title=TAB_TITLE, rows=max(100, len(rows) + 20), cols=10)

    headers = [
        "Маркетплейс",
        "Номер заказа",
        "Крайняя дата доставки",
        "Что в заказе",
        "Количество",
        "Телефон",
        "ФИО",
        "Адрес",
        "Подъём",
        "Комментарий",
    ]
    values = [headers]
    for r in rows:
        values.append([
            r.get("code", ""),
            r.get("order_no", ""),
            r.get("deadline", ""),
            r.get("items", ""),
            r.get("quantity", 0),
            r.get("phone", ""),
            r.get("fio", ""),
            r.get("address", ""),
            r.get("lift", ""),
            r.get("comment", ""),
        ])

    ws.clear()
    ws.resize(rows=max(100, len(values) + 20), cols=10)
    ws.update(range_name="A1", values=values, value_input_option="RAW")

    sheet_id = ws.id
    nrows = max(1, len(values))
    requests = [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 10},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.12, "green": 0.35, "blue": 0.55},
                        "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": nrows, "startColumnIndex": 0, "endColumnIndex": 10},
                "cell": {
                    "userEnteredFormat": {
                        "verticalAlignment": "TOP",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy",
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": nrows, "startColumnIndex": 0, "endColumnIndex": 10}
                }
            }
        },
    ]
    widths = [90, 155, 150, 360, 95, 190, 210, 380, 130, 320]
    for idx, px in enumerate(widths):
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": idx, "endIndex": idx + 1},
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
            }
        })
    sh.batch_update({"requests": requests})


def main():
    started = datetime.now(timezone.utc).isoformat()
    wa = WebasystClient(min_request_interval=0.20)
    direct_refresh = s(os.getenv("DIRECT_MARKETPLACE_REFRESH")).lower() in {"1", "true", "yes", "on"}
    sku_names = build_sku_names(wa) if direct_refresh else {}

    base = webasyst_active_rows(wa, sku_names)
    if direct_refresh:
        fresh, terminal, warnings = direct_marketplace_rows(sku_names)
    else:
        fresh, terminal, warnings = {}, set(), []
    rows = merge_rows(base, fresh, terminal)

    sh, created = open_or_create_sheet()
    write_sheet(sh, rows)

    counts = {}
    for row in rows:
        counts[row["code"]] = counts.get(row["code"], 0) + 1

    report = {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "spreadsheet_id": sh.id,
        "spreadsheet_url": sh.url,
        "created": created,
        "orders_total": len(rows),
        "counts_by_source": counts,
        "warnings": warnings,
    }
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
