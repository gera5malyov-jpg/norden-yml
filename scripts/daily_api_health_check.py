#!/usr/bin/env python3
"""Daily read-only API checks. Never writes products, orders or payments."""
import json
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import requests

TIMEOUT = 45
DESTINATION = "shop@office-mag.com"
RUN_URL = os.getenv("RUN_URL", "")
RESULTS = []
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Megapolis-API-Monitor/1.0"


class APIProblem(Exception):
    pass


def require(*names):
    absent = [n for n in names if not os.getenv(n, "").strip()]
    if absent:
        raise APIProblem("Не настроены секреты GitHub: " + ", ".join(absent))


def data(method, url, **kwargs):
    """Do not expose tokens, request URLs or response bodies in logs."""
    timeout = kwargs.pop("timeout", TIMEOUT)
    try:
        response = SESSION.request(method, url, timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        raise APIProblem("Ошибка сети: " + type(exc).__name__) from None
    if not 200 <= response.status_code < 300:
        raise APIProblem("HTTP " + str(response.status_code))
    try:
        payload = response.json()
    except ValueError:
        raise APIProblem("API вернул ответ не в JSON") from None
    if isinstance(payload, dict) and payload.get("error"):
        raise APIProblem("Ответ API содержит ошибку")
    return payload


def verify(name, required, callback):
    try:
        require(*required)
        callback()
        RESULTS.append({"service": name, "status": "OK", "reason": ""})
    except APIProblem as exc:
        RESULTS.append({"service": name, "status": "FAIL", "reason": str(exc)[:160]})
    except Exception as exc:
        # Only Dalli errors have a known, sanitizable user-facing description.
        reason = "Сбой проверки: " + type(exc).__name__
        if type(exc).__name__ == "DalliApiError":
            reason = str(exc)
            for key in ("DALLI_TOKEN", "DALLI_TOKEN_MSK"):
                token = os.getenv(key, "")
                if token:
                    reason = reason.replace(token, "[секрет скрыт]")
        RESULTS.append({"service": name, "status": "FAIL",
                        "reason": reason[:160]})


def webasyst():
    url = "https://profikompany.ru/api.php/shop.type.getList"
    token = os.environ["WEBASYST_API_TOKEN"].strip()
    # POST fallback matches the existing official Webasyst diagnostic.
    try:
        result = data("GET", url, params={"format": "json"},
                      headers={"Authorization": "Bearer " + token})
    except APIProblem:
        # Legacy Webasyst installs can require access_token in the query.
        result = data("GET", url,
                      params={"access_token": token, "format": "json"})
    if not isinstance(result, (dict, list)):
        raise APIProblem("Webasyst вернул неожиданный ответ")


def kit():
    result = data("GET", "https://api.kit.yandex.net/v1/warehouses",
                  params={"page": 1, "per_page": 100, "status": "ACTIVE"},
                  headers={"Authorization": "Bearer " + os.environ["YANDEX_KIT_TOKEN"],
                           "Accept": "application/json"})
    if not isinstance(result, (dict, list)):
        raise APIProblem("Некорректный формат ответа KIT")


def norden():
    result = data("GET", "https://norden.group/api-products/",
                  params={"page": 1}, timeout=125,
                  headers={"secret": os.environ["NORDEN_SECRET"],
                           "Accept": "application/json"})
    if not isinstance(result, dict) or not isinstance(result.get("products"), list):
        raise APIProblem("Norden не вернул список товаров")


def samson():
    result = data("GET", "https://api.samsonopt.ru/v1/category/",
                  params={"api_key": os.environ["SAMSON_API_KEY"],
                          "response_format": "json", "pagination_page": 1})
    obj = result[0] if isinstance(result, list) and len(result) == 1 else result
    if not isinstance(obj, dict) or not isinstance(obj.get("data"), list):
        raise APIProblem("Самсон: неверный формат ответа")


def komus():
    name = next((n for n in ("KOMUS_API", "KOMUS_API_KEY", "KOMUS_TOKEN", "KOMUS_API_TOKEN")
                 if os.getenv(n, "").strip()), None)
    if not name:
        raise APIProblem("Не настроен ключ KOMUS_API / KOMUS_API_KEY / KOMUS_TOKEN")
    payload = data("GET", "https://komus-opt.ru/api2/shipdata/",
                   params={"format": "json"},
                   headers={"token": os.environ[name], "Accept": "application/json"})
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise APIProblem("Комус: неверный формат ответа")


def cdek():
    payload = data("POST", "https://api.cdek.ru/v2/oauth/token",
                   data={"grant_type": "client_credentials",
                         "client_id": os.environ["CDEK_CLIENT_ID"],
                         "client_secret": os.environ["CDEK_CLIENT_SECRET"]})
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise APIProblem("СДЭК не выдал токен доступа")


def pek():
    today = datetime.now(timezone.utc).date()
    result = data("POST", "https://kabinet.pecom.ru/api/v1/cargos/listallorderbylogin/",
                  auth=(os.environ["PEK_LOGIN"], os.environ["PEK_API_KEY"]),
                  json={"selectBy": 1, "dateBegin": str(today - timedelta(days=7)),
                        "dateEnd": str(today)})
    if not isinstance(result, (dict, list)):
        raise APIProblem("ПЭК: неверный формат ответа")


def dalli(account):
    # Calculation only. No shipping orders are created or modified.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dalli.dalli_api import DalliClient
    token = os.environ["DALLI_TOKEN" if account == "spb" else "DALLI_TOKEN_MSK"]
    client = DalliClient(token=token, account=account)
    address = ("Санкт-Петербург, Невский проспект, 1" if account == "spb"
               else "Москва, Тверская улица, 1")
    result = client.delivery_cost(
        address,
        [{"weight_kg": 1, "length_cm": 10, "width_cm": 10, "height_cm": 10}],
        output_x2=True)
    if not isinstance(result, dict):
        raise APIProblem("Dalli не вернул расчет доставки")


def netangels():
    payload = data("POST", "https://panel.netangels.ru/api/gateway/token/",
                   data={"api_key": os.environ["NETANGELS_API_KEY"]})
    token = payload.get("token") if isinstance(payload, dict) else None
    if not token:
        raise APIProblem("NetAngels не выдал токен")
    info = data("GET", "https://api-ms.netangels.ru/api/v1/account/info/",
                headers={"Authorization": "Bearer " + token})
    if not isinstance(info, dict):
        raise APIProblem("NetAngels: неверный формат ответа")


def send_alert(item):
    user = os.getenv("GMAIL_SMTP_USER", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not user or not password:
        print("::error::Cannot email: missing GMAIL_SMTP_USER / GMAIL_APP_PASSWORD", flush=True)
        return False
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = DESTINATION
    msg["Subject"] = "ВАЖНО !!! ВНИМАНИЕ !! Не работает API: " + item["service"]
    msg.set_content(
        "При ежедневной проверке API обнаружена проблема.\n\n"
        "Сервис: " + item["service"] + "\n"
        "Причина: " + item["reason"] + "\n"
        "Время (UTC): " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + "\n"
        "Журнал GitHub Actions: " + RUN_URL + "\n\n"
        "Проверка не вносила изменений в сервис. "
        "Необходимо проверить доступность API, авторизацию и права ключа."
    )
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        print("::error::Alert email failed: " + type(exc).__name__, flush=True)
        return False


def main():
    verify("Webasyst", ("WEBASYST_API_TOKEN",), webasyst)
    verify("Yandex KIT", ("YANDEX_KIT_TOKEN",), kit)
    verify("Norden", ("NORDEN_SECRET",), norden)
    verify("Самсон", ("SAMSON_API_KEY",), samson)
    verify("Комус", (), komus)
    verify("СДЭК", ("CDEK_CLIENT_ID", "CDEK_CLIENT_SECRET"), cdek)
    verify("ПЭК", ("PEK_LOGIN", "PEK_API_KEY"), pek)
    verify("Dalli СПб", ("DALLI_TOKEN",), lambda: dalli("spb"))
    verify("Dalli Москва", ("DALLI_TOKEN_MSK",), lambda: dalli("msk"))
    verify("NetAngels", ("NETANGELS_API_KEY",), netangels)

    folder = Path("reports")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "daily-api-health.json").write_text(
        json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(),
                    "services": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in RESULTS:
        # Never print secret values or response bodies.
        print(row["service"], row["status"], row["reason"], flush=True)

    failures = [row for row in RESULTS if row["status"] != "OK"]
    email_ok = True
    for item in failures:
        if not send_alert(item):
            email_ok = False
    if failures:
        print("::error::Failed API checks: " + str(len(failures)), flush=True)
    return 1 if failures or not email_ok else 0


if __name__ == "__main__":
    sys.exit(main())
