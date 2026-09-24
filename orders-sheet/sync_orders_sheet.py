#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
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

    zip_code = s(address.get("zip") or address.get("postcode"))
    country = s(address.get("country"))
    region = s(address.get("region"))
    city = s(address.get("city"))
    street = s(address.get("street"))

    street_low = street.lower()
    markers = [x for x in (zip_code, country, region, city) if x]
    if street and sum(1 for x in markers if x.lower() in street_low) >= 2:
        return street

    parts = []
    for value in (zip_code, country, region, city, street):
        value = s(value)
        if value and not any(value.lower() == old.lower() for old in parts):
            parts.append(value)
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


def format_phone(value: Any) -> str:
    text = s(value)
    if not text:
        return ""
    ext = ""
    m = re.search(r"(?:доб\\.?|ext\\.?|x)\\s*(\\d+)", text, re.I)
    if m:
        ext = m.group(1)
        base = text[:m.start()]
    else:
        base = text
    digits = re.sub(r"\\D", "", base)
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        digits = "7" + digits[1:]
        text = f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    else:
        text = s(base)
    if ext:
        text += f" доб. {ext}"
    return text


def find_extension(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("phoneCode", "extension", "ext"):
            ext = s(value.get(key))
            if ext:
                return ext
        for item in value.values():
            ext = find_extension(item)
            if ext:
                return ext
    elif isinstance(value, list):
        for item in value:
            ext = find_extension(item)
            if ext:
                return ext
    return ""


def phone_text(data: Any) -> str:
    if not isinstance(data, dict):
        return format_phone(data)
    raw_phone = (
        data.get("phone")
        or data.get("telephone")
        or data.get("mobile")
        or data.get("replacementPhone")
    )
    phone = first_scalar(raw_phone)
    ext = s(data.get("phoneCode") or data.get("extension") or data.get("ext")) or find_extension(raw_phone)
    phone = format_phone(phone)
    if phone and ext and f"доб. {ext}" not in phone:
        phone += f" доб. {ext}"
    return phone


def address_from_params(params: Any) -> str:
    if not isinstance(params, dict):
        return ""
    zip_code = s(params.get("shipping_address.zip"))
    country = s(params.get("shipping_address.country"))
    region = s(params.get("shipping_address.region"))
    city = s(params.get("shipping_address.city"))
    street = s(params.get("shipping_address.street"))

    # Some marketplace integrations store a complete address in street.
    # Do not prepend the same index/country/city a second time.
    street_low = street.lower()
    markers = [x for x in (zip_code, country, region, city) if x]
    if street and sum(1 for x in markers if x.lower() in street_low) >= 2:
        return street

    if country.lower() in {"rus", "ru", "russia"}:
        country = ""
    if region.isdigit():
        region = ""

    parts = []
    for value in (zip_code, country, region, city, street):
        value = s(value)
        if not value:
            continue
        if any(value.lower() == old.lower() for old in parts):
            continue
        parts.append(value)
    return ", ".join(parts)


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
    buyer_address = buyer.get("address") if isinstance(buyer.get("address"), dict) else {}
    customer_address = customer.get("address") if isinstance(customer.get("address"), dict) else {}
    candidates += [
        buyer.get("comment"), buyer.get("notes"),
        customer.get("comment"), customer.get("notes"),
        address.get("comment"), address.get("notes"),
        buyer_address.get("comment"), buyer_address.get("notes"),
        customer_address.get("comment"), customer_address.get("notes"),
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
    low = lt.lower()
    if lt.upper() == "NOT_NEEDED" or low in {"none", "no", "not needed", "не нужен", "нет"} or (price is not None and price <= 0 and not lt):
        return "Нет"
    if low == "delivery_default":
        return "Да (включён в доставку)"
    if low == "lift":
        return f"Да, лифт{f' — {price:g} ₽' if price is not None and price > 0 else ''}"
    if low == "stairs":
        return f"Да, по лестнице{f' — {price:g} ₽' if price is not None and price > 0 else ''}"
    if price is not None and price > 0:
        return f"Да, {price:g} ₽"
    if lt:
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
                addr = address_text(info.get("shipping_address")) or address_from_params(params)

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


def load_active_ozon_for_sheet() -> list[dict]:
    cid = s(os.getenv("OZON_CLIENT_ID"))
    secret = s(os.getenv("OZON_API_KEY"))
    if not cid or not secret:
        return []

    headers = {
        "Client-Id": cid,
        "Api-Key": secret,
        "Content-Type": "application/json",
    }
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=30)
    out: list[dict] = []

    for kind, url in (
        ("FBS", "https://api-seller.ozon.ru/v3/posting/fbs/list"),
        ("FBO", "https://api-seller.ozon.ru/v2/posting/fbo/list"),
    ):
        offset = 0
        while True:
            body = {
                "dir": "ASC",
                "filter": {"since": mp.iso(since), "to": mp.iso(now)},
                "limit": 1000,
                "offset": offset,
            }
            if kind == "FBS":
                body["with"] = {
                    "analytics_data": False,
                    "barcodes": False,
                    "financial_data": False,
                    "translit": False,
                }
            else:
                body["translit"] = False
                body["with"] = {"analytics_data": False, "financial_data": False}

            r = mp.net.req("POST", url, headers=headers, body=body)
            if not r.ok:
                if kind == "FBO":
                    break
                raise RuntimeError(f"Ozon {kind} HTTP {r.status_code}: {r.text[:300]}")

            data = r.json()
            root = data.get("result")
            rows = (root.get("postings") or []) if isinstance(root, dict) else root if isinstance(root, list) else []

            for listed in rows:
                if not isinstance(listed, dict):
                    continue

                listed_status = s(listed.get("status"))
                if mp.ozon_state(listed_status) in TERMINAL_TARGETS:
                    continue

                posting_number = s(listed.get("posting_number"))
                detail = mp.ozon_posting_detail(headers, posting_number) if kind == "FBS" and posting_number else {}
                src = detail or listed

                status = s(src.get("status") or listed.get("status"))
                sub = s(src.get("substatus") or listed.get("substatus"))
                target_state = mp.ozon_state(status)
                if target_state in TERMINAL_TARGETS:
                    continue

                products = []
                for x in (src.get("products") or listed.get("products") or []):
                    if not isinstance(x, dict):
                        continue
                    products.append({
                        "sku": s(x.get("offer_id")),
                        "name": s(x.get("name")),
                        "quantity": max(1, int(mp.num(x.get("quantity"), 1))),
                        "price": mp.num(x.get("price")) if x.get("price") not in (None, "") else None,
                    })

                customer = dict(src.get("customer") or {}) if isinstance(src.get("customer"), dict) else {}
                addressee = src.get("addressee") if isinstance(src.get("addressee"), dict) else {}
                if not s(customer.get("name")) and s(addressee.get("name")):
                    customer["name"] = s(addressee.get("name"))
                if not s(customer.get("phone")) and s(addressee.get("phone")):
                    customer["phone"] = s(addressee.get("phone"))

                raw_address = customer.get("address") if isinstance(customer.get("address"), dict) else {}
                address = mp.normalize_ozon_address(raw_address)

                delivery_price = mp.num(src.get("delivery_price")) if src.get("delivery_price") not in (None, "") else None
                prr = src.get("prr_option") if isinstance(src.get("prr_option"), dict) else {}
                lift_price = mp.num(prr.get("price")) if prr.get("price") not in (None, "") else None
                lift_type = s(prr.get("code"))

                created = mp.dt(src.get("in_process_at") or src.get("created_at") or src.get("shipment_date"))
                out.append({
                    "source": "ozon",
                    "external_id": posting_number,
                    "ozon_kind": kind,
                    "created_at": mp.iso(created) if created else "",
                    "status_raw": status + ("/" + sub if sub else ""),
                    "target_state": target_state,
                    "items": products,
                    "buyer": customer,
                    "recipient_name": s(customer.get("name")),
                    "shipping_address": address,
                    "delivery_price": delivery_price,
                    "lift_price": lift_price,
                    "lift_type": lift_type,
                    "delivery_to": s(src.get("delivering_date")),
                })

            if len(rows) < 1000:
                break
            offset += len(rows)

    return out


def direct_ozon_rows(sku_names: dict[str, str]) -> tuple[dict[str, dict], set[str], list[str]]:
    rows: dict[str, dict] = {}
    terminal_keys: set[str] = set()
    warnings: list[str] = []
    try:
        orders = load_active_ozon_for_sheet()
    except Exception as exc:
        return rows, terminal_keys, [f"ozon: {type(exc).__name__}: {str(exc)[:300]}"]

    for o in orders:
        if not isinstance(o, dict):
            continue
        ext = s(o.get("external_id"))
        if not ext:
            continue
        k = order_key("ozon", ext)
        target = s(o.get("target_state"))
        if target in TERMINAL_TARGETS:
            terminal_keys.add(k)
            continue

        products, qty = item_text(o.get("items"), sku_names)
        buyer = o.get("buyer") if isinstance(o.get("buyer"), dict) else {}
        rows[k] = {
            "source": "ozon",
            "code": "OZ",
            "order_no": ext,
            "deadline": normalize_date(o.get("delivery_to") or o.get("delivery_from")),
            "items": products,
            "quantity": qty,
            "phone": phone_text(buyer),
            "fio": s(o.get("recipient_name")) or person_name(buyer),
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
    direct_ozon = s(os.getenv("DIRECT_OZON_REFRESH", "1")).lower() in {"1", "true", "yes", "on"}
    sku_names = build_sku_names(wa) if (direct_refresh or direct_ozon) else {}

    base = webasyst_active_rows(wa, sku_names)
    if direct_refresh:
        fresh, terminal, warnings = direct_marketplace_rows(sku_names)
    elif direct_ozon:
        fresh, terminal, warnings = direct_ozon_rows(sku_names)
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
