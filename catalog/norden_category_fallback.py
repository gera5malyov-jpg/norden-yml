from __future__ import annotations

import re
import unicodedata


def _norm_article(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-zа-яё]+", "", text)


def _generic_category_path(path: object) -> bool:
    if not isinstance(path, (list, tuple)):
        return True
    parts = [str(x or "").strip() for x in path if str(x or "").strip()]
    return not parts or (len(parts) == 1 and parts[0].casefold() == "norden")


def enrich_missing_categories(api_items: dict, xml_items: dict):
    xml_by_article = {
        _norm_article(article): item
        for article, item in (xml_items or {}).items()
        if _norm_article(article) and isinstance(item, dict)
    }
    merged = {}
    generic_before = 0
    enriched = 0

    for article, item in (api_items or {}).items():
        current = dict(item or {})
        if _generic_category_path(current.get("category_path")):
            generic_before += 1
            xml_item = xml_by_article.get(_norm_article(article))
            xml_path = (xml_item or {}).get("category_path")
            if not _generic_category_path(xml_path):
                current["category_path"] = list(xml_path)
                enriched += 1
        merged[article] = current

    generic_after = sum(
        1 for item in merged.values()
        if _generic_category_path((item or {}).get("category_path"))
    )
    return merged, {
        "generic_before": generic_before,
        "enriched_from_xml": enriched,
        "generic_after": generic_after,
    }
