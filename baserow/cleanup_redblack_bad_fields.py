#!/usr/bin/env python3
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

BASE = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
TABLE_ID = int(os.environ.get("BASEROW_CATALOG_TABLE_ID", "156"))
TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()
EMAIL = os.environ.get("BASEROW_EMAIL", "").strip()
PASSWORD = os.environ.get("BASEROW_PASSWORD", "").strip()

BAD_PREFIXES = (
    "Webasyst товар — ",
    "Webasyst SKU — ",
    "Red-Black — ",
)

def req(session, method, path, **kwargs):
    last = None
    for attempt in range(6):
        try:
            r = session.request(method, BASE + path, timeout=90, **kwargs)
        except requests.RequestException as e:
            last = e
            time.sleep(min(15, 2 ** attempt))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last = RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
            time.sleep(float(r.headers.get("Retry-After") or min(20, 2 ** attempt)))
            continue
        if not r.ok:
            raise RuntimeError(f"{method} {path}: HTTP {r.status_code}: {r.text[:1200]}")
        return r
    raise RuntimeError(str(last))

def auth_session():
    s = requests.Session()
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    if EMAIL and PASSWORD:
        for payload in (
            {"username": EMAIL, "password": PASSWORD},
            {"email": EMAIL, "password": PASSWORD},
        ):
            r = s.post(BASE + "/api/user/token-auth/", json=payload, timeout=60)
            if r.ok:
                s.headers["Authorization"] = "JWT " + r.json()["token"]
                print("Authentication: JWT")
                return s
        raise RuntimeError(f"Baserow JWT login failed: HTTP {r.status_code}: {r.text[:1000]}")
    if TOKEN:
        s.headers["Authorization"] = "Token " + TOKEN
        print("Authentication: database token")
        return s
    raise RuntimeError("No Baserow credentials available")

def main():
    s = auth_session()
    fields = req(s, "GET", f"/api/database/fields/table/{TABLE_ID}/").json()
    candidates = [
        {"id": int(f["id"]), "name": str(f.get("name") or "")}
        for f in fields
        if any(str(f.get("name") or "").startswith(p) for p in BAD_PREFIXES)
    ]
    print("Matched fields:", len(candidates))
    for f in candidates:
        print(f"DELETE field {f['id']}: {f['name']}")

    deleted = []
    errors = []
    for f in candidates:
        try:
            req(s, "DELETE", f"/api/database/fields/{f['id']}/")
            deleted.append(f)
        except Exception as e:
            errors.append({"field": f, "error": str(e)})
            print("ERROR:", f["name"], e, file=sys.stderr)

    remaining_fields = req(s, "GET", f"/api/database/fields/table/{TABLE_ID}/").json()
    remaining = [
        {"id": int(f["id"]), "name": str(f.get("name") or "")}
        for f in remaining_fields
        if any(str(f.get("name") or "").startswith(p) for p in BAD_PREFIXES)
    ]

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "table_id": TABLE_ID,
        "prefixes": BAD_PREFIXES,
        "matched": len(candidates),
        "deleted": len(deleted),
        "errors": errors,
        "remaining": remaining,
    }
    report_path = os.path.join(os.path.dirname(__file__), "cleanup_redblack_bad_fields_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("CLEANUP_REPORT=" + json.dumps(report, ensure_ascii=False))
    if errors or remaining:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
