#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
RUNTIME = HERE / "runtime"
RUNTIME.mkdir(parents=True, exist_ok=True)
REPORT_FILE = RUNTIME / "last_report.json"

SOURCE_PATH = ROOT / "orders-sheet" / "sync_orders_sheet.py"
spec = importlib.util.spec_from_file_location("orders_sheet_source", SOURCE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load {SOURCE_PATH}")
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)

BASEROW_URL = os.getenv("BASEROW_URL", "http://147.78.67.6").rstrip("/")
BASEROW_TOKEN = os.getenv("BASEROW_DATABASE_TOKEN", "").strip()
REFERENCE_TABLE_ID = int(os.getenv("BASEROW_REFERENCE_TABLE_ID", "156"))
ORDERS_TABLE_NAME = os.getenv("BASEROW_ORDERS_TABLE_NAME", "Заказы").strip() or "Заказы"


def s(v: Any) -> str:
    return str(v or "").strip()


class Baserow:
    def __init__(self):
        if not BASEROW_TOKEN:
            raise RuntimeError("BASEROW_DATABASE_TOKEN is missing")
        self.row_headers = {
            "Authorization": f"Token {BASEROW_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "orders-baserow-sync/1.0",
        }
        self.session = requests.Session()
        self._schema_headers = None

    def request(self, method: str, path: str, *, headers: dict | None = None, body: dict | None = None,
                expected: tuple[int, ...] = (200, 201, 204), tries: int = 5):
        url = BASEROW_URL + path
        last = None
        for attempt in range(tries):
            try:
                r = self.session.request(
                    method,
                    url,
                    headers=headers or self.row_headers,
                    json=body,
                    timeout=60,
                )
            except requests.RequestException as exc:
                last = exc
                time.sleep(min(2 ** attempt, 15))
                continue
            if r.status_code in expected:
                if r.status_code == 204 or not r.text:
                    return None
                return r.json()
            if r.status_code >= 500:
                last = RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
                time.sleep(min(2 ** attempt, 15))
                continue
            raise RuntimeError(f"Baserow {method} {path} -> HTTP {r.status_code}: {r.text[:1000]}")
        raise RuntimeError(f"Baserow request failed: {last}")

    def _login_headers(self) -> list[dict]:
        out: list[dict] = []
        jwt = s(os.getenv("BASEROW_JWT"))
        access = s(os.getenv("BASEROW_ACCESS_TOKEN"))
        api_token = s(os.getenv("BASEROW_API_TOKEN"))
        if jwt:
            out += [
                {"Authorization": f"JWT {jwt}", "Accept": "application/json", "Content-Type": "application/json"},
                {"Authorization": f"Bearer {jwt}", "Accept": "application/json", "Content-Type": "application/json"},
            ]
        if access:
            out += [
                {"Authorization": f"JWT {access}", "Accept": "application/json", "Content-Type": "application/json"},
                {"Authorization": f"Bearer {access}", "Accept": "application/json", "Content-Type": "application/json"},
            ]
        if api_token:
            out.append({"Authorization": f"Token {api_token}", "Accept": "application/json", "Content-Type": "application/json"})

        email = s(os.getenv("BASEROW_EMAIL") or os.getenv("BASEROW_USER_EMAIL"))
        password = s(os.getenv("BASEROW_PASSWORD") or os.getenv("BASEROW_USER_PASSWORD"))
        if email and password:
            try:
                r = self.session.post(
                    BASEROW_URL + "/api/user/token-auth/",
                    json={"email": email, "password": password},
                    timeout=60,
                )
                if r.ok:
                    data = r.json() if r.text else {}
                    token = s(data.get("access_token") or data.get("token"))
                    if token:
                        out += [
                            {"Authorization": f"JWT {token}", "Accept": "application/json", "Content-Type": "application/json"},
                            {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"},
                        ]
            except requests.RequestException:
                pass

        out.append(self.row_headers)
        return out

    def schema_request(self, method: str, path: str, *, body: dict | None = None,
                       expected: tuple[int, ...] = (200, 201, 204)):
        candidates = [self._schema_headers] if self._schema_headers else self._login_headers()
        errors = []
        for headers in candidates:
            if not headers:
                continue
            try:
                result = self.request(method, path, headers=headers, body=body, expected=expected, tries=2)
                self._schema_headers = headers
                return result
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError("No Baserow schema credential worked: " + " | ".join(errors[-4:]))

    def reference_database_id(self) -> int:
        meta = self.schema_request("GET", f"/api/database/tables/{REFERENCE_TABLE_ID}/")
        if not isinstance(meta, dict):
            raise RuntimeError("Baserow table metadata is not an object")
        raw = meta.get("database_id")
        if raw in (None, ""):
            db = meta.get("database")
            if isinstance(db, dict):
                raw = db.get("id")
            elif db not in (None, ""):
                raw = db
        if raw in (None, ""):
            raise RuntimeError(f"Could not determine database id from table {REFERENCE_TABLE_ID}")
        return int(raw)

    def list_tables(self, database_id: int) -> list[dict]:
        data = self.schema_request("GET", f"/api/database/tables/database/{database_id}/")
        return data if isinstance(data, list) else (data.get("results") or []) if isinstance(data, dict) else []

    def ensure_orders_table(self) -> tuple[int, int]:
        database_id = self.reference_database_id()
        tables = self.list_tables(database_id)
        for table in tables:
            if s(table.get("name")).casefold() == ORDERS_TABLE_NAME.casefold():
                return int(table["id"]), database_id

        created = self.schema_request(
            "POST",
            f"/api/database/tables/database/{database_id}/",
            body={"name": ORDERS_TABLE_NAME},
        )
        if not isinstance(created, dict) or not created.get("id"):
            raise RuntimeError("Baserow did not return the created orders table id")
        return int(created["id"]), database_id

    def fields(self, table_id: int) -> list[dict]:
        data = self.schema_request("GET", f"/api/database/fields/table/{table_id}/")
        return data if isinstance(data, list) else []

    def patch_field(self, field_id: int, body: dict):
        return self.schema_request("PATCH", f"/api/database/fields/{field_id}/", body=body)

    def ensure_field(self, table_id: int, name: str, field_type: str):
        for field in self.fields(table_id):
            if s(field.get("name")) == name:
                return field
        body = {"name": name, "type": field_type}
        if field_type == "number":
            body.update({"number_decimal_places": 0, "number_negative": False})
        return self.schema_request("POST", f"/api/database/fields/table/{table_id}/", body=body)

    def ensure_schema(self, table_id: int) -> str:
        fields = self.fields(table_id)
        primary = next((f for f in fields if f.get("primary")), fields[0] if fields else None)
        primary_name = s(primary.get("name")) if primary else ""

        if not any(s(f.get("name")) == "Заказ" for f in fields):
            if primary and primary.get("id"):
                try:
                    self.patch_field(int(primary["id"]), {"name": "Заказ"})
                    primary_name = "Заказ"
                except Exception:
                    self.ensure_field(table_id, "Заказ", "text")
            else:
                self.ensure_field(table_id, "Заказ", "text")

        wanted = [
            ("Ключ", "text"),
            ("Маркетплейс", "text"),
            ("Номер заказа", "text"),
            ("Крайняя дата доставки", "text"),
            ("Что в заказе", "long_text"),
            ("Количество", "number"),
            ("Телефон", "text"),
            ("ФИО", "text"),
            ("Адрес", "long_text"),
            ("Подъём", "text"),
            ("Комментарий", "long_text"),
        ]
        for name, typ in wanted:
            self.ensure_field(table_id, name, typ)

        fields = self.fields(table_id)
        names = {s(f.get("name")) for f in fields}
        if "Заказ" in names:
            return "Заказ"
        return primary_name or next(iter(names))

    def all_rows(self, table_id: int) -> list[dict]:
        out = []
        page = 1
        while True:
            data = self.request(
                "GET",
                f"/api/database/rows/table/{table_id}/?user_field_names=true&size=200&page={page}",
            )
            batch = data.get("results", []) if isinstance(data, dict) else []
            out.extend(batch)
            if not isinstance(data, dict) or not data.get("next"):
                break
            page += 1
        return out

    def create_row(self, table_id: int, body: dict):
        return self.request(
            "POST",
            f"/api/database/rows/table/{table_id}/?user_field_names=true",
            body=body,
        )

    def update_row(self, table_id: int, row_id: int, body: dict):
        return self.request(
            "PATCH",
            f"/api/database/rows/table/{table_id}/{row_id}/?user_field_names=true",
            body=body,
        )

    def delete_row(self, table_id: int, row_id: int):
        return self.request(
            "DELETE",
            f"/api/database/rows/table/{table_id}/{row_id}/",
            expected=(204,),
        )


def gather_active_orders() -> tuple[list[dict], list[str]]:
    wa = source.WebasystClient(min_request_interval=0.20)
    sku_names = source.build_sku_names(wa)
    base = source.webasyst_active_rows(wa, sku_names)
    fresh, terminal, warnings = source.direct_marketplace_rows(sku_names)
    rows = source.merge_rows(base, fresh, terminal)
    return rows, warnings


def make_body(row: dict, primary_name: str) -> dict:
    code = s(row.get("code"))
    order_no = s(row.get("order_no"))
    src = s(row.get("source"))
    display = f"{code} {order_no}".strip()
    body = {
        "Заказ": display,
        "Ключ": source.order_key(src, order_no),
        "Маркетплейс": code,
        "Номер заказа": order_no,
        "Крайняя дата доставки": s(row.get("deadline")),
        "Что в заказе": s(row.get("items")),
        "Количество": int(row.get("quantity") or 0),
        "Телефон": s(row.get("phone")),
        "ФИО": s(row.get("fio")),
        "Адрес": s(row.get("address")),
        "Подъём": s(row.get("lift")),
        "Комментарий": s(row.get("comment")),
    }
    if primary_name and primary_name != "Заказ":
        body[primary_name] = display
    return body


def main():
    started = datetime.now(timezone.utc).isoformat()
    br = Baserow()
    table_id, database_id = br.ensure_orders_table()
    primary_name = br.ensure_schema(table_id)

    active, warnings = gather_active_orders()
    active_bodies = [make_body(row, primary_name) for row in active]
    active_by_key = {s(body.get("Ключ")): body for body in active_bodies if s(body.get("Ключ"))}

    existing = br.all_rows(table_id)
    existing_by_key = {}
    for row in existing:
        key = s(row.get("Ключ"))
        if key:
            existing_by_key[key] = row

    created = 0
    updated = 0
    deleted = 0

    for key, body in active_by_key.items():
        old = existing_by_key.get(key)
        if old:
            br.update_row(table_id, int(old["id"]), body)
            updated += 1
        else:
            br.create_row(table_id, body)
            created += 1

    for key, old in existing_by_key.items():
        if key not in active_by_key:
            br.delete_row(table_id, int(old["id"]))
            deleted += 1

    counts = {}
    for row in active:
        code = s(row.get("code"))
        counts[code] = counts.get(code, 0) + 1

    report = {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "database_id": database_id,
        "table_id": table_id,
        "table_name": ORDERS_TABLE_NAME,
        "orders_total": len(active),
        "counts_by_source": counts,
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "warnings": warnings,
    }
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
