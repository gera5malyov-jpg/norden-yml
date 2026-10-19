#!/usr/bin/env python3
"""Monitor a rotating sample of the public Webasyst Sitemap without changing site data."""
import concurrent.futures
from collections import Counter
import datetime
import json
import os
import re
import sys
from html.parser import HTMLParser
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SITE = "https://profikompany.ru"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)"}
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
TODAY = datetime.datetime.now(datetime.timezone.utc).date()


def encode_request_url(url):
    """Preserve literal '+' in URL paths (brand slugs); quote Unicode safely."""
    parsed = urllib.parse.urlsplit(url)
    # Webasyst brand slugs may contain literal '+'. Re-encoding it to '%2B'
    # produces a different route and false 404s in the Sitemap monitor.
    return urllib.parse.urlunsplit((
        parsed.scheme,
        parsed.netloc,
        urllib.parse.quote(parsed.path, safe="/%:@-._~+"),
        urllib.parse.quote(parsed.query, safe="=&%:@-._~"),
        parsed.fragment,
    ))


def read_response(url, limit=5_000_000):
    # Sitemap XML may contain internationalized URLs; send RFC3986-compatible
    # percent-encoded UTF-8 paths without changing source URLs.
    encoded_url = encode_request_url(url)
    request = urllib.request.Request(encoded_url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=22) as response:
            code = response.status
            content = response.read(limit) if limit else b""
            return {"url": url, "status": code, "body": content, "final_url": response.url,
                    "x_robots_tags": response.headers.get_all("X-Robots-Tag", [])}
    except urllib.error.HTTPError as error:
        return {"url": url, "status": error.code, "body": b"", "final_url": error.geturl()}
    except Exception as error:
        return {"url": url, "status": None, "error": type(error).__name__ + ": " + str(error)[:120]}


def site_url(url):
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" and parsed.netloc == "profikompany.ru" and parsed.path.startswith("/")


class _MainHeadRobots(HTMLParser):
    """Inspect only the FIRST page head, never embedded widget/iframe documents."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.head_seen = False
        self.in_head = False
        self.directives = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "head" and not self.head_seen:
            self.head_seen = True
            self.in_head = True
        elif tag.lower() == "meta" and self.in_head:
            attrs = dict((str(k).lower(), v or "") for k, v in attrs)
            if attrs.get("name", "").lower() in ("robots", "yandex", "googlebot"):
                self.directives.append(attrs.get("content", ""))

    def handle_endtag(self, tag):
        if tag.lower() == "head":
            self.in_head = False


def head_indexing_directives(html):
    """Return robots directives from the page's primary <head> only."""
    parser = _MainHeadRobots()
    parser.feed(html.decode("utf-8", "replace") if isinstance(html, bytes) else html)
    return parser.directives if parser.head_seen else None


def directives_block_indexing(directives):
    """noindex, nofollow or none on the main document are SEO blockers."""
    if not directives:
        return False
    tokens = {x.lower() for value in directives for x in re.split(r"[\s,;]+", value) if x}
    return bool(tokens.intersection({"noindex", "nofollow", "none"}))



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

    # Check what YandexBot actually receives in the main document, not inside
    # third-party widgets (e.g. VK iframe, whose own document can say noindex).
    commercial_routes = [
        "/", "/category/kompyuternye-kresla/", "/category/mebel-dlya-ofisa/",
        "/category/krovati/", "/category/stul/",
        "/category/kompyuternye-kresla/?page=2",
        "/stul-kenner-313sd-chernyy-zhemchug-e10-334-322382/",
    ]
    protected_routes = [
        "/search/?query=test", "/order/", "/compare/",
    ]
    result["indexability"] = []
    for path, should_index in [(p, True) for p in commercial_routes] + [
        (p, False) for p in protected_routes
    ]:
        page = read_response(SITE + path)
        directives = head_indexing_directives(page.get("body", b"")) if page.get("status") == 200 else None
        header_directives = page.get("x_robots_tags", [])
        combined = (directives or []) + header_directives
        blocked = directives_block_indexing(combined)
        error = None
        if page.get("status") != 200:
            error = "unexpected_status"
        elif directives is None:
            error = "missing_primary_html_head"
        elif should_index and blocked:
            error = "commercial_page_blocked_from_indexing"
        elif not should_index and not any(
            "noindex" in str(x).lower() or "none" in str(x).lower() for x in combined
        ):
            error = "technical_page_missing_noindex"
        record = {"url": SITE + path, "status": page.get("status"),
                  "should_index": should_index, "meta_robots": directives,
                  "x_robots_tags": header_directives, "blocked": blocked,
                  "error": error}
        result["indexability"].append(record)
        if error:
            result["failures"].append({"url": record["url"], "error": error,
                                       "status": page.get("status"),
                                       "directives": combined})

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
        "indexability_checked": len(result["indexability"]),
        "indexability_failures": sum(bool(x["error"]) for x in result["indexability"]),
        "failures": len(result["failures"]),
    }, ensure_ascii=False))
    for item in result["failures"][:30]:
        print("SEO_MONITOR_FAILURE=" + json.dumps(item, ensure_ascii=False))
    if result["failures"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
