#!/usr/bin/env python3
import json
import os
import sys
import urllib.parse
import urllib.request

API_KEY = os.environ.get("E_BULKY", "").strip()
if not API_KEY:
    print("E_BULKY is not set", file=sys.stderr)
    sys.exit(2)

def get_json(url, params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + qs, headers={"User-Agent": "norden-yml-e-bulky-check/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode("utf-8"))

def post_form_json(url, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "norden-yml-e-bulky-check/1.0",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode("utf-8"))

status, key_info = get_json("https://api.e-bulky.ru/apiv2/testkey", {"key": API_KEY})
print("testkey_http=", status)
print("testkey_ok=", status == 200)

status, services = get_json("https://api.e-bulky.ru/apiv2/list-services", {"key": API_KEY})
print("services_http=", status)

status, warehouses = get_json("https://api.e-bulky.ru/apiv2/warehouses", {"key": API_KEY, "page": 1})
print("warehouses_http=", status)

status, calc = post_form_json("https://api.e-bulky.ru/api/v1/public/calculate", {
    "apikey": API_KEY,
    "nds": "false",
    "address": "Москва, Тверская улица, дом 1",
    "weight": "20",
    "dimension_side1": "50",
    "dimension_side2": "50",
    "dimension_side3": "100",
    "floor": "1",
    "cargo_lift": "true",
})
print("calculate_http=", status)
response = calc.get("response") if isinstance(calc, dict) else None
if isinstance(response, dict):
    print("calculate_total_rub=", response.get("total"))
    print("calculate_detail=", json.dumps(response.get("detail"), ensure_ascii=False, separators=(",", ":")))
    print("calculate_info=", json.dumps(response.get("info"), ensure_ascii=False, separators=(",", ":")))
else:
    print("calculate_response=", json.dumps(calc, ensure_ascii=False, separators=(",", ":")))
