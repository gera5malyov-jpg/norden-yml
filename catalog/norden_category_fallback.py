from __future__ import annotations

import re
import unicodedata


CONFUSABLES = str.maketrans({
    "а": "a", "в": "b", "с": "c", "е": "e", "н": "h", "к": "k",
    "м": "m", "о": "o", "р": "p", "т": "t", "х": "x", "у": "y",
    "А": "a", "В": "b", "С": "c", "Е": "e", "Н": "h", "К": "k",
    "М": "m", "О": "o", "Р": "p", "Т": "t", "Х": "x", "У": "y",
})


def _norm_article(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).translate(CONFUSABLES).casefold()
    return re.sub(r"[^0-9a-zа-яё]+", "", text)


def _generic_category_path(path: object) -> bool:
    if not isinstance(path, (list, tuple)):
        return True
    parts = [str(x or "").strip() for x in path if str(x or "").strip()]
    return not parts or (len(parts) == 1 and parts[0].casefold() == "norden")


def enrich_missing_categories(api_items: dict, xml_items: dict):
    """Replace API category paths with official XML paths during category fallback.

    This function is called only after the API-derived chair/stool scope is
    suspiciously small. In that mode the API category tree is not trusted,
    while all non-category product fields remain sourced from the API.
    """
    xml_by_article = {
        _norm_article(article): item
        for article, item in (xml_items or {}).items()
        if _norm_article(article) and isinstance(item, dict)
    }
    merged = {}
    generic_before = 0
    matched_xml = 0
    replaced = 0

    for article, item in (api_items or {}).items():
        current = dict(item or {})
        if _generic_category_path(current.get("category_path")):
            generic_before += 1
        xml_item = xml_by_article.get(_norm_article(article))
        xml_path = (xml_item or {}).get("category_path")
        if xml_item is not None:
            matched_xml += 1
        if not _generic_category_path(xml_path):
            if list(current.get("category_path") or []) != list(xml_path):
                replaced += 1
            current["category_path"] = list(xml_path)
        merged[article] = current

    generic_after = sum(
        1 for item in merged.values()
        if _generic_category_path((item or {}).get("category_path"))
    )
    return merged, {
        "generic_before": generic_before,
        "matched_xml_articles": matched_xml,
        "enriched_from_xml": replaced,
        "generic_after": generic_after,
    }


def target_article_keys(xml_items: dict, predicate):
    return {
        _norm_article(article)
        for article, item in (xml_items or {}).items()
        if _norm_article(article) and isinstance(item, dict) and predicate(item)
    }
