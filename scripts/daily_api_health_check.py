#!/usr/bin/env python3
"""Daily read-only API checks. Never writes products, orders or payments."""
import json
import os
import smtplib
import sys
import time
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


class APIInconclusive(Exception):
    """HTTP rate limit: the service cannot be declared down."""
    pass


def require(*names):
    absent = [n for n in names if not os.getenv(n, "").strip()]
    if absent:
        raise APIProblem("Не настроены секреты GitHub: " + ", ".join(absent))


def data(method, url, **kwargs):
    """Do not expose tokens, request URLs or response bodies in logs."""
    timeout = kwargs.pop("timeout", TIMEOUT)
    # A short 429 or 5xx from a shared API is not proof of a broken token.
    # Retries are read-only and never apply to write operations.
    retryable = method.upper() == "GET"
    response = None
    for attempt in range(4 if retryable else 1):
        try:
            response = SESSION.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            if retryable and attempt < 3:
                time.sleep((2, 6, 12)[attempt])
                continue
            raise APIProblem("Ошибка сети: " + type(exc).__name__) from None
        if retryable and response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            raw_wait = response.headers.get("Retry-After", "")
            try:
                wait = min(30, max(2, int(raw_wait)))
            except (ValueError, TypeError):
                wait = (5, 12, 25)[attempt]
            time.sleep(wait)
            continue
        break
    if response is None:
        raise APIProblem("API не вернул ответ")
    if not 200 <= response.status_code < 300:
        if response.status_code == 429:
            raise APIInconclusive("HTTP 429: временный лимит запросов, состояние API не подтверждено")
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
    except APIInconclusive as exc:
        RESULTS.append({"service": name, "status": "UNKNOWN", "reason": str(exc)[:160]})
    except APIProblem as exc:
        reason = str(exc)[:160]
        status = "NO_KEY" if reason.startswith(("Не настроены секреты GitHub:", "Не настроен ключ")) else "FAIL"
        RESULTS.append({"service": name, "status": status, "reason": reason})
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


def ozon():
    payload = data("POST", "https://api-seller.ozon.ru/v3/product/list",
                   headers={"Client-Id": os.environ["OZON_CLIENT_ID"],
                            "Api-Key": os.environ["OZON_API_KEY"],
                            "Content-Type": "application/json"},
                   json={"filter": {"visibility": "ALL"}, "limit": 1})
    if not isinstance(payload, dict) or "result" not in payload:
        raise APIProblem("Ozon: неожиданный ответ списка товаров")


def yandex_market():
    payload = data("GET", "https://api.partner.market.yandex.ru/v2/campaigns",
                   params={"limit": 1},
                   headers={"Api-Key": os.environ["YANDEX_MARKET_API_KEY"],
                            "Accept": "application/json"})
    if not isinstance(payload, dict) or "campaigns" not in payload:
        raise APIProblem("Яндекс Маркет: неожиданный ответ")


def wildberries():
    payload = data("GET", "https://common-api.wildberries.ru/ping",
                   headers={"Authorization": os.environ["WB_API_TOKEN"],
                            "Accept": "application/json"})
    if not isinstance(payload, dict):
        raise APIProblem("Wildberries: неожиданный ответ")


def baserow():
    payload = data("GET", "http://147.78.67.6/api/database/rows/table/156/",
                   params={"size": 1, "page": 1},
                   headers={"Authorization": "Token " + os.environ["BASEROW_DATABASE_TOKEN"],
                            "Accept": "application/json"})
    if not isinstance(payload, dict) or "results" not in payload:
        raise APIProblem("Baserow: неожиданный ответ")


def e_bulky():
    payload = data("GET", "https://api.e-bulky.ru/apiv2/testkey",
                   params={"key": os.environ["E_BULKY"]})
    if not isinstance(payload, (dict, list)):
        raise APIProblem("E-Bulky: неожиданный ответ API")


def moysklad():
    payload = data("GET", "https://api.moysklad.ru/api/remap/1.2/entity/product",
                   params={"limit": 1},
                   headers={"Authorization": "Bearer " + os.environ["MOYSKLAD_TOKEN"],
                            "Accept": "application/json;charset=utf-8"})
    if not isinstance(payload, dict) or "meta" not in payload:
        raise APIProblem("МойСклад: неожиданный ответ")


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


def include_finance_health():
    """Read sanitized finance statuses from isolated Baserow table 871."""
    expected = {"Моё дело", "Ozon DirectBank", "Точка Банк"}
    try:
        require("BASEROW_DATABASE_TOKEN")
        payload = data("GET", "http://147.78.67.6/api/database/rows/table/871/",
                       params={"size": 100, "user_field_names": "true"},
                       headers={"Authorization": "Token " + os.environ["BASEROW_DATABASE_TOKEN"],
                                "Accept": "application/json"})
        rows = payload.get("results", []) if isinstance(payload, dict) else []
        matches = [x for x in rows if x.get("Name") == "finance-api-health"]
        if len(matches) != 1:
            raise APIInconclusive("Служебная запись финансового мониторинга отсутствует")
        report = json.loads(matches[0]["Status JSON"])
        stamp = datetime.fromisoformat(report["checked_at"].replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - stamp
        if not timedelta(minutes=-5) <= age <= timedelta(hours=27):
            raise APIInconclusive("Данные финансового мониторинга устарели")
        services = report["services"]
        names = {x["service"] for x in services}
        if len(services) != 3 or names != expected:
            raise APIInconclusive("Неполный список финансовых сервисов")
        if any(x.get("status") not in ("OK", "FAIL", "UNKNOWN", "NO_KEY")
               for x in services):
            raise APIInconclusive("Недопустимый статус финансового API")
        for x in services:
            RESULTS.append({"service": x["service"],
                            "status": x["status"],
                            "reason": str(x.get("reason", ""))[:160]})
    except Exception as exc:
        RESULTS.append({"service": "Финансовые API (источник Baserow)",
                        "status": "UNKNOWN",
                        "reason": "Не удалось подтвердить результат: " + type(exc).__name__})


def send_failure_alert(failures):
    """Send an email ONLY when one or more API checks genuinely failed."""
    if not failures:
        print("No API outages; no email sent.", flush=True)
        return True

    user = os.getenv("GMAIL_SMTP_USER", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not user or not password:
        print("::error::Cannot send outage alert: Gmail SMTP credentials unavailable", flush=True)
        return False

    report_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = ("ВАЖНО !!! ВНИМАНИЕ !! Не работает API: " +
               ", ".join(row["service"] for row in failures))
    lines = [
        "Обнаружена проблема с доступностью API / авторизацией.",
        "Дата проверки: " + report_time,
        "",
    ]
    for row in failures:
        lines.append(row["service"] + ": " + row["reason"])
    lines.extend([
        "",
        "Проверки только читают данные, ничего не изменяют.",
        "GitHub Actions: " + RUN_URL,
        "",
        "Другие проверенные API работают; уведомления о них не отправляются.",
        "Отсутствующие секреты или лимит запросов (429) не считаются доказанным отказом API.",
    ])
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = DESTINATION
    msg["Subject"] = subject
    msg.set_content("\n".join(lines))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        print("Outage alert email sent for", len(failures), "API(s).", flush=True)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        print("::error::Outage alert delivery failed: " + type(exc).__name__, flush=True)
        return False


def main():
    verify("Ozon", ("OZON_CLIENT_ID", "OZON_API_KEY"), ozon)
    verify("Wildberries", ("WB_API_TOKEN",), wildberries)
    verify("Яндекс Маркет", ("YANDEX_MARKET_API_KEY",), yandex_market)
    verify("Baserow", ("BASEROW_DATABASE_TOKEN",), baserow)
    verify("E-Bulky", ("E_BULKY",), e_bulky)
    verify("МойСклад", ("MOYSKLAD_TOKEN",), moysklad)
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
    include_finance_health()

    folder = Path("reports")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "daily-api-health.json").write_text(
        json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(),
                    "services": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in RESULTS:
        # Never print secret values or response bodies.
        print(row["service"], row["status"], row["reason"], flush=True)

    failures = [row for row in RESULTS if row["status"] == "FAIL"]
    email_ok = (True if os.getenv("API_HEALTH_SKIP_EMAIL") == "1"
                else send_failure_alert(failures))
    unchecked = [row for row in RESULTS if row["status"] in ("NO_KEY", "UNKNOWN")]
    if unchecked:
        print("::warning::Not confirmed (no key or API rate limit):",
              ", ".join(row["service"] for row in unchecked), flush=True)
    if failures:
        print("::error::Failed API checks: " + str(len(failures)), flush=True)
    return 1 if failures or not email_ok else 0


if __name__ == "__main__":
    sys.exit(main())
