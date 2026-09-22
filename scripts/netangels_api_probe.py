#!/usr/bin/env python3
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

API_KEY = os.environ.get("NETANGELS_API_KEY", "").strip()
if not API_KEY:
    print("NETANGELS_API_KEY is not set", file=sys.stderr)
    sys.exit(2)

TOKEN_URL = "https://panel.netangels.ru/api/gateway/token/"
ACCOUNT_URL = "https://api-ms.netangels.ru/api/v1/account/info/"

def request_json(req):
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8")
            return r.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body[:500]}", file=sys.stderr)
        raise

data = urllib.parse.urlencode({"api_key": API_KEY}).encode("utf-8")
token_req = urllib.request.Request(
    TOKEN_URL,
    data=data,
    method="POST",
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "norden-yml-netangels-check/1.0",
    },
)
status, token_payload = request_json(token_req)
token = token_payload.get("token") if isinstance(token_payload, dict) else None
print("token_ok=", status == 200 and bool(token))
if not token:
    sys.exit(3)

account_req = urllib.request.Request(
    ACCOUNT_URL,
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "norden-yml-netangels-check/1.0",
    },
)
status, account = request_json(account_req)
print("account_ok=", status == 200 and isinstance(account, dict))
if isinstance(account, dict):
    print("login=", account.get("login", ""))
    print("name=", account.get("name", ""))
