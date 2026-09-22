#!/usr/bin/env python3
import json
import os
import sys
import time
from typing import Any, Dict, Iterable, List, Tuple

import requests

OZON_BASE = "https://api-seller.ozon.ru"
YANDEX_BASE = "https://api.partner.market.yandex.ru"
PREFERRED_BUSINESS_ID = str(os.getenv("YANDEX_MARKET_BUSINESS_ID") or "").strip()

OZON_CLIENT_ID = (os.getenv("OZON_CLIENT_ID") or "").strip()
OZON_API_KEY = (os.getenv("OZON_API_KEY") or "").strip()
YANDEX_API_KEY = (os.getenv("YANDEX_MARKET_API_KEY") or "").strip()

if not OZON_CLIENT_ID or not OZON_API_KEY or not YANDEX_API_KEY:
    missing = [
        name for name, value in (
            ("OZON_CLIENT_ID", OZON_CLIENT_ID),
            ("OZON_API_KEY", OZON_API_KEY),
            ("YANDEX_MARKET_API_KEY", YANDEX_API_KEY),
        ) if not value
    ]
    raise SystemExit("Отсутствуют GitHub Secrets: " + ", ".join(missing))

ozon = requests.Session()
ozon.headers.update({
    "Client-Id": OZON_CLIENT_ID,
    "Api-Key": OZON_API_KEY,
    "Content-Type": "application/json",
})

yandex = requests.Session()
yandex.headers.update({
    "Api-Key": YANDEX_API_KEY,
    "Content-Type": "application/json",
})


def chunks(seq: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def call(session: requests.Session, method: str, url: str, *, body=None, params=None,
         retries: int = 10, timeout: int = 60) -> Dict[str, Any]:
    last = None
    for attempt in range(retries):
        r = session.request(method, url, json=body, params=params, timeout=timeout)
        last = r
        if r.status_code == 429:
            delay = float(r.headers.get("Retry-After") or min(20, attempt + 1))
            time.sleep(delay)
            continue
        # Yandex Market uses HTTP 420 when the per-minute points limit is reached.
        # Wait for the quota window to reset and retry the SAME batch.
        if r.status_code == 420 and "rate limit" in r.text.lower():
            delay = float(r.headers.get("Retry-After") or 65)
            print(f"RATE_LIMIT_WAIT {delay}s {method} {url}", flush=True)
            time.sleep(delay)
            continue
        if r.status_code >= 500:
            time.sleep(min(20, attempt + 1))
            continue
        try:
            data = r.json() if r.content else {}
        except Exception:
            data = {"raw": r.text}
        if r.status_code >= 400:
            raise RuntimeError(
                f"HTTP {r.status_code} {method} {url}: "
                + json.dumps(data, ensure_ascii=False)[:3000]
            )
        return data
    raise RuntimeError(
        f"Не удалось выполнить {method} {url}; последний HTTP "
        + (str(last.status_code) if last is not None else "N/A")
    )


def normalize_url_values(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    out: List[str] = []
    seen = set()
    for x in values:
        if isinstance(x, dict):
            x = x.get("url") or x.get("file_name") or x.get("src")
        if not isinstance(x, str):
            continue
        x = x.strip()
        if not (x.startswith("https://") or x.startswith("http://")):
            continue
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def extract_ozon_images(item: Dict[str, Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for source in (
        item.get("primary_image"),
        item.get("images"),
    ):
        for url in normalize_url_values(source):
            if url not in seen:
                seen.add(url)
                result.append(url)
    return result[:30]


def load_ozon_offer_ids() -> List[str]:
    offer_ids: List[str] = []
    seen = set()
    last_id = ""
    while True:
        body: Dict[str, Any] = {
            "filter": {"visibility": "ALL"},
            "limit": 1000,
        }
        if last_id:
            body["last_id"] = last_id
        data = call(ozon, "POST", OZON_BASE + "/v3/product/list", body=body)
        result = data.get("result") or {}
        rows = result.get("items") or []
        for row in rows:
            offer_id = str(row.get("offer_id") or "").strip()
            if offer_id and offer_id not in seen:
                seen.add(offer_id)
                offer_ids.append(offer_id)
        new_last_id = str(result.get("last_id") or "")
        if not rows or not new_last_id or new_last_id == last_id:
            break
        last_id = new_last_id
    return offer_ids


def load_ozon_images() -> Dict[str, List[str]]:
    offer_ids = load_ozon_offer_ids()
    images: Dict[str, List[str]] = {}
    # Ozon допускает большой батч, но 500 оставляет запас по лимитам метода.
    for batch in chunks(offer_ids, 500):
        data = call(
            ozon, "POST", OZON_BASE + "/v3/product/info/list",
            body={"offer_id": batch},
        )
        for item in data.get("items") or []:
            offer_id = str(item.get("offer_id") or "").strip()
            if not offer_id:
                continue
            pics = extract_ozon_images(item)
            if pics:
                images[offer_id] = pics
    return images


def discover_yandex_business_id() -> str:
    data = call(
        yandex,
        "GET",
        YANDEX_BASE + "/v2/campaigns",
        params={"limit": 100},
    )
    campaigns = data.get("campaigns")
    if campaigns is None and isinstance(data.get("result"), dict):
        campaigns = data["result"].get("campaigns")
    campaigns = campaigns or []

    candidates = []
    for campaign in campaigns:
        business = campaign.get("business") or {}
        bid = str(business.get("id") or "").strip()
        if not bid:
            continue
        candidates.append({
            "business_id": bid,
            "business_name": str(business.get("name") or ""),
            "campaign_id": campaign.get("id"),
            "domain": campaign.get("domain"),
            "apiAvailability": campaign.get("apiAvailability"),
        })

    available = [x for x in candidates if x.get("apiAvailability") in (None, "AVAILABLE")]
    pool = available or candidates
    unique_ids = sorted({x["business_id"] for x in pool})

    if PREFERRED_BUSINESS_ID and PREFERRED_BUSINESS_ID in unique_ids:
        chosen = PREFERRED_BUSINESS_ID
    elif len(unique_ids) == 1:
        chosen = unique_ids[0]
    else:
        mega_ids = sorted({
            x["business_id"] for x in pool
            if "мегаполис" in (x.get("business_name") or "").lower()
            or "megapolis" in (x.get("business_name") or "").lower()
        })
        if len(mega_ids) == 1:
            chosen = mega_ids[0]
        else:
            raise RuntimeError(
                "Не удалось однозначно выбрать кабинет Яндекс Маркета. "
                + json.dumps(pool, ensure_ascii=False)[:4000]
            )

    print("YANDEX_BUSINESS_SELECTED", chosen)
    print("YANDEX_CAMPAIGNS", json.dumps(pool, ensure_ascii=False)[:4000])
    return chosen


def load_yandex_offers(business_id: str) -> Dict[str, Dict[str, Any]]:
    offers: Dict[str, Dict[str, Any]] = {}
    token = None
    while True:
        params: Dict[str, Any] = {"limit": 100}
        if token:
            params["pageToken"] = token
        data = call(
            yandex,
            "POST",
            f"{YANDEX_BASE}/v2/businesses/{business_id}/offer-mappings",
            body={},
            params=params,
        )
        result = data.get("result") or {}
        for row in result.get("offerMappings") or []:
            offer = row.get("offer") or {}
            offer_id = str(offer.get("offerId") or "").strip()
            if offer_id:
                offers[offer_id] = offer
        new_token = str(((result.get("paging") or {}).get("nextPageToken")) or "")
        if not new_token or new_token == token:
            break
        token = new_token
    return offers


def update_yandex(batch: List[Tuple[str, List[str]]], business_id: str) -> Dict[str, Any]:
    payload = {
        "offerMappings": [
            {
                "offer": {
                    "offerId": offer_id,
                    "pictures": pictures,
                }
            }
            for offer_id, pictures in batch
        ]
    }
    return call(
        yandex,
        "POST",
        f"{YANDEX_BASE}/v2/businesses/{business_id}/offer-mappings/update",
        body=payload,
    )


def response_errors(data: Dict[str, Any]) -> List[Any]:
    errors: List[Any] = []
    if data.get("status") not in (None, "OK"):
        errors.append({"status": data.get("status")})
    result = data.get("result")
    if isinstance(result, dict):
        e = result.get("errors")
        if e:
            if isinstance(e, list):
                errors.extend(e)
            else:
                errors.append(e)
    if data.get("errors"):
        e = data.get("errors")
        if isinstance(e, list):
            errors.extend(e)
        else:
            errors.append(e)
    return errors


def main() -> int:
    business_id = discover_yandex_business_id()
    report: Dict[str, Any] = {
        "business_id": business_id,
        "ozon_with_images": 0,
        "yandex_active_offers": 0,
        "matched_exact": 0,
        "without_ozon_match": 0,
        "ozon_without_images_for_yandex_offer": 0,
        "already_same": 0,
        "to_update": 0,
        "accepted_for_update": 0,
        "failed_batches": [],
        "unmatched_sample": [],
        "no_images_sample": [],
    }

    # Авторизацию Яндекс проверяем до любых изменений.
    auth = call(yandex, "POST", YANDEX_BASE + "/v2/auth/token", body={})
    if auth.get("status") not in (None, "OK"):
        raise RuntimeError("Яндекс Маркет API не подтвердил токен: " + json.dumps(auth, ensure_ascii=False)[:2000])

    ozon_images = load_ozon_images()
    yandex_offers = load_yandex_offers(business_id)

    report["ozon_with_images"] = len(ozon_images)
    report["yandex_active_offers"] = len(yandex_offers)

    if not yandex_offers:
        raise RuntimeError("Яндекс Маркет вернул 0 активных товаров — массовое обновление отменено.")
    if not ozon_images:
        raise RuntimeError("Ozon не вернул ни одного товара с изображениями — массовое обновление отменено.")

    to_update: List[Tuple[str, List[str]]] = []
    unmatched: List[str] = []
    no_images: List[str] = []

    for offer_id, yoffer in yandex_offers.items():
        if offer_id not in ozon_images:
            unmatched.append(offer_id)
            continue
        pictures = ozon_images.get(offer_id) or []
        if not pictures:
            no_images.append(offer_id)
            continue
        report["matched_exact"] += 1
        current = normalize_url_values(yoffer.get("pictures"))[:30]
        if current == pictures:
            report["already_same"] += 1
            continue
        to_update.append((offer_id, pictures))

    report["without_ozon_match"] = len(unmatched)
    report["ozon_without_images_for_yandex_offer"] = len(no_images)
    report["unmatched_sample"] = unmatched[:50]
    report["no_images_sample"] = no_images[:50]
    report["to_update"] = len(to_update)

    if not to_update:
        print("FINAL_REPORT_JSON", json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0

    # Безопасный пробный запрос на один товар. Меняем только pictures.
    probe = [to_update[0]]
    probe_data = update_yandex(probe, business_id)
    probe_errors = response_errors(probe_data)
    if probe_errors:
        raise RuntimeError(
            "Пробное обновление изображения Яндекс Маркета отклонено; массовый запуск остановлен: "
            + json.dumps(probe_errors, ensure_ascii=False)[:3000]
        )
    report["accepted_for_update"] += 1

    # Остальные товары — пакетами не более 100, как рекомендует Яндекс.
    for batch in chunks(to_update[1:], 100):
        try:
            data = update_yandex(batch, business_id)
            errs = response_errors(data)
            if errs:
                report["failed_batches"].append({
                    "offers": [x[0] for x in batch],
                    "errors": errs[:20],
                })
            else:
                report["accepted_for_update"] += len(batch)
        except Exception as exc:
            report["failed_batches"].append({
                "offers": [x[0] for x in batch],
                "errors": [str(exc)],
            })
        time.sleep(0.05)

    print("FINAL_REPORT_JSON", json.dumps(report, ensure_ascii=False, sort_keys=True))

    if report["failed_batches"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
