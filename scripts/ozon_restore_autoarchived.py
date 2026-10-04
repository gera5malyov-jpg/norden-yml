#!/usr/bin/env python3
"""Restore only Ozon products archived automatically.

Safety:
- query only visibility=ARCHIVED;
- restore only when is_autoarchived=true;
- never restore products where is_autoarchived is false;
- restore no more than 100 products per run;
- report contains counts only (no credentials or product identifiers).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://api-seller.ozon.ru"
CLIENT_ID = os.environ.get("OZON_CLIENT_ID", "").strip()
API_KEY = os.environ.get("OZON_API_KEY", "").strip()
MAX_RESTORE = max(1, min(int(os.environ.get("OZON_MAX_RESTORE", "100")), 100))
REPORT_PATH = Path(os.environ.get("OZON_REPORT_PATH", "results/ozon-auto-unarchive-latest.json"))
TIMEOUT = 60


class OzonAPIError(RuntimeError):
    def __init__(self, path: str, status: int | None, body: str):
        super().__init__(f"Ozon API error on {path}: HTTP {status}: {body[:500]}")
        self.path = path
        self.status = status
        self.body = body


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def api_post(path: str, payload: dict[str, Any], retries: int = 3) -> dict[str, Any]:
    raw_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        BASE_URL + path,
        data=raw_payload,
        method="POST",
        headers={
            "Client-Id": CLIENT_ID,
            "Api-Key": API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "norden-yml/ozon-auto-unarchive",
        },
    )
    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise OzonAPIError(path, exc.code, body) from exc
        except URLError as exc:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise OzonAPIError(path, None, str(exc)) from exc
    raise AssertionError("unreachable")


def list_archived_page(last_id: str = "") -> tuple[list[dict[str, Any]], str, int | None]:
    payload: dict[str, Any] = {"filter": {"visibility": "ARCHIVED"}, "limit": 1000}
    if last_id:
        payload["last_id"] = last_id
    data = api_post("/v3/product/list", payload)
    result = data.get("result") or data
    items = result.get("items") or []
    next_last_id = str(result.get("last_id") or "")
    total = result.get("total")
    return items, next_last_id, int(total) if isinstance(total, int) else None


def get_info(product_ids: list[int]) -> list[dict[str, Any]]:
    data = api_post("/v3/product/info/list", {"product_id": product_ids})
    if isinstance(data.get("items"), list):
        return data["items"]
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"]
    return []


def unarchive(product_id: int) -> tuple[bool, str | None]:
    try:
        data = api_post("/v1/product/unarchive", {"product_id": [product_id]}, retries=2)
        return bool(data.get("result", False)), None
    except OzonAPIError as exc:
        body = exc.body.lower()
        if "restore limit exceeded" in body:
            return False, "restore_limit_exceeded"
        if "total limit exceeded" in body:
            return False, "total_limit_exceeded"
        return False, f"http_{exc.status}"


def write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def bump_error(report: dict[str, Any], reason: str) -> None:
    report["failed_count"] += 1
    failures = report["failures_by_reason"]
    failures[reason] = failures.get(reason, 0) + 1


def main() -> int:
    report: dict[str, Any] = {
        "started_at_utc": utc_now(),
        "mode": "is_autoarchived_true_only",
        "max_restore_per_run": MAX_RESTORE,
        "archived_total_reported": None,
        "archived_items_inspected": 0,
        "autoarchive_candidates_seen": 0,
        "manual_or_other_archive_skipped": 0,
        "missing_info_skipped": 0,
        "restored_count": 0,
        "failed_count": 0,
        "failures_by_reason": {},
        "limit_hit": None,
        "complete_for_this_run": False,
    }

    try:
        if not CLIENT_ID or not API_KEY:
            raise RuntimeError("Missing OZON_CLIENT_ID or OZON_API_KEY")

        last_id = ""
        seen_tokens: set[str] = set()

        while report["restored_count"] < MAX_RESTORE:
            items, next_last_id, total = list_archived_page(last_id)
            if report["archived_total_reported"] is None and total is not None:
                report["archived_total_reported"] = total
            if not items:
                report["complete_for_this_run"] = True
                break

            ids: list[int] = []
            for item in items:
                try:
                    ids.append(int(item.get("product_id")))
                except (TypeError, ValueError):
                    continue

            for start in range(0, len(ids), 1000):
                batch = ids[start:start + 1000]
                info_items = get_info(batch)
                info_by_id: dict[int, dict[str, Any]] = {}
                for info in info_items:
                    raw_id = info.get("id", info.get("product_id"))
                    try:
                        info_by_id[int(raw_id)] = info
                    except (TypeError, ValueError):
                        continue

                for pid in batch:
                    report["archived_items_inspected"] += 1
                    info = info_by_id.get(pid)
                    if not info:
                        report["missing_info_skipped"] += 1
                        continue

                    if info.get("is_autoarchived") is not True:
                        report["manual_or_other_archive_skipped"] += 1
                        continue

                    report["autoarchive_candidates_seen"] += 1
                    ok, error = unarchive(pid)
                    if ok:
                        report["restored_count"] += 1
                        print(f"Restored autoarchived product #{report['restored_count']}", flush=True)
                    else:
                        reason = error or "unknown"
                        bump_error(report, reason)
                        print(f"Skipped autoarchived product: {reason}", flush=True)
                        if reason in {"restore_limit_exceeded", "total_limit_exceeded"}:
                            report["limit_hit"] = reason
                            report["finished_at_utc"] = utc_now()
                            write_report(report)
                            return 0 if reason == "restore_limit_exceeded" else 2

                    if report["restored_count"] >= MAX_RESTORE:
                        report["limit_hit"] = "local_daily_safety_cap"
                        report["finished_at_utc"] = utc_now()
                        write_report(report)
                        return 0

                    time.sleep(0.25)

            if not next_last_id or next_last_id == last_id or next_last_id in seen_tokens:
                report["complete_for_this_run"] = True
                break
            seen_tokens.add(next_last_id)
            last_id = next_last_id

        report["finished_at_utc"] = utc_now()
        write_report(report)
        return 0

    except Exception as exc:
        report["fatal_error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        report["finished_at_utc"] = utc_now()
        write_report(report)
        print(report["fatal_error"]["message"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
