#!/usr/bin/env python3
"""Read-only smoke test: a public 4sis product must display HTTPS external photos."""
from __future__ import annotations

import html
import json
import re
import urllib.request
from urllib.parse import urlsplit

PAGE = (
    "https://profikompany.ru/"
    "barselona-kofeynyy-stolik-iz-iskusstvennogo-rotanga-tsvet-korichnevyy/"
)
PREFIX = "https://profikompany.ru/4sis-images/"
OLD_ORIGIN = "http://5.35.127.160/images/"
HEADERS = {"User-Agent": "Megapolis-4sis-gallery-healthcheck/1.0"}

def fetch(url: str, read_limit: int = 3_000_000):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=35) as response:
        return response.status, response.headers.get_content_type(), response.read(read_limit)

def main():
    code, content_type, body = fetch(PAGE + "?gallery_healthcheck=1")
    if code != 200 or not body or "text/html" not in content_type:
        raise RuntimeError(f"Product page unavailable: {code} {content_type}")
    markup = html.unescape(body.decode("utf-8", errors="replace"))
    srcs = re.findall(r'''(?i)\bsrc=["'](https?://[^"'\s>]+)''', markup)
    data_extimg = re.findall(r'''(?i)\bdata-extimg=["'](https?://[^"'\s>]+)''', markup)
    old_srcs = [url for url in srcs if url.startswith(OLD_ORIGIN)]
    if old_srcs:
        raise RuntimeError(f"Mixed-content image src present ({len(old_srcs)})")
    photos = list(dict.fromkeys(url for url in srcs if url.startswith(PREFIX)))
    if len(photos) < 3 or len(data_extimg) < 3:
        raise RuntimeError(f"Gallery missing: {len(photos)} unique pictures, {len(data_extimg)} thumbnails")
    verified = []
    for url in photos[:3]:
        parsed = urlsplit(url)
        if parsed.netloc != "profikompany.ru" or not parsed.path.startswith("/4sis-images/"):
            raise RuntimeError("Unexpected URL host or proxy path")
        status, mime, image_start = fetch(url, read_limit=32)
        if status != 200 or mime != "image/jpeg" or not image_start.startswith(b"\xff\xd8\xff"):
            raise RuntimeError(f"Broken proxied photo: {url} HTTP {status}, MIME {mime}")
        verified.append({"image": url.rsplit("/", 1)[-1], "http": status, "mime": mime})
    print("FOURSIS_GALLERY_HEALTH="+json.dumps({
        "status":"ok", "product": PAGE, "unique_images":len(photos),
        "thumbnails":len(data_extimg), "verified":verified
    }, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
