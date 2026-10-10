#!/usr/bin/env python3
"""Monitor a rotating sample of the public Webasyst Sitemap without changing site data."""
import concurrent.futures
from collections import Counter
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SITE = "https://profikompany.ru"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)"}
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
TODAY = datetime.datetime.now(datetime.timezone.utc).date()


def read_response(url, limit=5_000_000):
    parsed = urllib.parse.urlsplit(url)
    # Sitemap XML may contain internationalized URLs; send RFC3986-compatible
    # percent-encoded UTF-8 paths without changing source URLs.
    encoded_url = urllib.parse.urlunsplit((
        parsed.scheme,
        parsed.netloc,
        urllib.parse.quote(parsed.path, safe="/%:@-._~"),
        urllib.parse.quote(parsed.query, safe="=&%:@-._~"),
        parsed.fragment,
    ))
    request = urllib.request.Request(encoded_url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=22) as response:
            code = response.status
            content = response.read(limit) if limit else b""
            return {"url": url, "status": code, "body": content, "final_url": response.url}
    except urllib.error.HTTPError as error:
        return {"url": url, "status": error.code, "body": b"", "final_url": error.geturl()}
    except Exception as error:
        return {"url": url, "status": None, "error": type(error).__name__ + ": " + str(error)[:120]}


def site_url(url):
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" and parsed.netloc == "profikompany.ru" and parsed.path.startswith("/")


def main():
    result = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "site": SITE,
        "method": "sitemap-index plus 4 rotating URLs per child sitemap, YandexBot User-Agent",
        "checks": [],
        "child_sitemaps": [],
        "failures": [],
    }
    base_urls = [SITE + "/", SITE + "/robots.txt", SITE + "/sitemap.xml",
                 SITE + "/category/kompyuternye-kresla/",
                 SITE + "/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/"]
    parent = read_response(SITE + "/sitemap.xml")
    if parent.get("status") != 200:
        result["failures"].append({"url": parent["url"], "status": parent.get("status"), "error": parent.get("error")})
        children = []
    else:
        try:
            xml = ET.fromstring(parent["body"])
            children = [x.text for x in xml.findall("sm:sitemap/sm:loc", NS) if x.text and site_url(x.text)]
        except Exception as error:
            children = []
            result["failures"].append({"url": SITE + "/sitemap.xml", "error": "Invalid XML: " + str(error)[:100]})

    sampled = []
    all_locs = set()
    for i, sm in enumerate(children):
        response = read_response(sm)
        doc = {"url": sm, "status": response.get("status")}
        if response.get("status") == 200:
            try:
                xml = ET.fromstring(response["body"])
                urls = [x.text for x in xml.findall("sm:url/sm:loc", NS) if x.text and site_url(x.text)]
                doc["count"] = len(urls)
                counts = Counter(urls)
                doc["duplicates_inside"] = len(urls) - len(counts)
                doc["duplicate_examples"] = [
                    {"url": url, "entries": qty}
                    for url, qty in counts.items() if qty > 1
                ][:15]
                doc["duplicates_across"] = len(set(urls) & all_locs)
                doc["cross_sitemap_duplicate_examples"] = list(set(urls) & all_locs)[:10]
                all_locs.update(urls)
                if urls:
                    # Vary positions daily so that different pages get sampled each day.
                    shift = (TODAY.toordinal() * 43 + i * 103) % len(urls)
                    indices = [(shift + k * max(1, len(urls) // 4)) % len(urls) for k in range(4)]
                    sampled.extend(urls[index] for index in indices)
            except Exception as error:
                doc["error"] = "Invalid Sitemap XML: " + str(error)[:110]
                result["failures"].append({"url": sm, "error": doc["error"]})
        else:
            doc["error"] = response.get("error")
            result["failures"].append({"url": sm, "status": response.get("status"), "error": doc.get("error")})
        result["child_sitemaps"].append(doc)

    sample_urls = list(dict.fromkeys(base_urls + sampled))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for response in pool.map(lambda url: read_response(url, 0), sample_urls):
            item = {"url": response["url"], "status": response.get("status")}
            if response.get("error"):
                item["error"] = response["error"]
            if response.get("final_url") != response["url"]:
                item["redirected_to"] = response.get("final_url")
            result["checks"].append(item)
            if response.get("status") != 200:
                result["failures"].append(item)

    os.makedirs("seo-reports", exist_ok=True)
    with open("seo-reports/sitemap-daily.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("SEO_MONITOR_SUMMARY=" + json.dumps({
        "date": str(TODAY),
        "sitemaps": len(result["child_sitemaps"]),
        "total_sitemap_urls": sum(d.get("count", 0) for d in result["child_sitemaps"]),
        "internal_duplicate_entries": sum(d.get("duplicates_inside", 0) for d in result["child_sitemaps"]),
        "cross_sitemap_duplicates": sum(d.get("duplicates_across", 0) for d in result["child_sitemaps"]),
        "unique_urls_in_sitemaps": len(all_locs),
        "sample_checked": len(result["checks"]),
        "failures": len(result["failures"]),
    }, ensure_ascii=False))
    for item in result["failures"][:30]:
        print("SEO_MONITOR_FAILURE=" + json.dumps(item, ensure_ascii=False))
    if result["failures"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
