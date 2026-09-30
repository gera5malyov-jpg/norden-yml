#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys

import requests

BASEROW_URL = os.environ.get("BASEROW_URL", "http://147.78.67.6").rstrip("/")
BASEROW_TOKEN = os.environ.get("BASEROW_DATABASE_TOKEN", "").strip()
ENDPOINT = "/api/notifications/external-alert/"


def send_notification(
    severity: str,
    code: str,
    title: str,
    message: str = "",
    dedupe_key: str = "",
    target_url: str = "",
    *,
    quiet: bool = False,
) -> bool:
    if not BASEROW_TOKEN:
        if not quiet:
            print("BASEROW_DATABASE_TOKEN is missing", file=sys.stderr)
        return False

    payload = {
        "severity": severity,
        "code": code,
        "title": title,
        "message": message,
        "dedupe_key": dedupe_key or f"{code}:{title}",
        "target_url": target_url,
    }
    try:
        r = requests.post(
            BASEROW_URL + ENDPOINT,
            headers={
                "Authorization": f"Token {BASEROW_TOKEN}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        if r.status_code not in (200, 201):
            if not quiet:
                print(
                    f"Notification API HTTP {r.status_code}: {r.text[:500]}",
                    file=sys.stderr,
                )
            return False
        if not quiet:
            print(r.text)
        return True
    except requests.RequestException as exc:
        if not quiet:
            print(f"Notification API failed: {exc}", file=sys.stderr)
        return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--severity", required=True, choices=["red", "yellow", "green", "info"])
    p.add_argument("--code", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--message", default="")
    p.add_argument("--dedupe-key", default="")
    p.add_argument("--target-url", default="")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()
    ok = send_notification(
        a.severity,
        a.code,
        a.title,
        a.message,
        a.dedupe_key,
        a.target_url,
        quiet=a.quiet,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
