import importlib.util
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from webasyst.client import WebasystClient


class KitSyncError(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report or {}


def _s(value):
    return str(value or "").strip()


def _norm(value):
    return re.sub(r"[^0-9a-zа-яё]+", "", unicodedata.normalize("NFKC", _s(value)).casefold())


def _dec(value):
    try:
        return Decimal(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _money(value):
    d = _dec(value)
    if d is None:
        return None
    return format(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _quantity(value):
    d = _dec(value)
    if d is None or d <= 0:
        return 0
    return int(d)


def _price_pair(purchase):
    p = _dec(purchase)
    if p is None or p <= 0:
        return None
    return {
        "price": _money(p * Decimal("1.60")),
        "manual_discount_price": _money(p * Decimal("1.25")),
    }


def _feature_kit_id(features):
    for feature in features or []:
        if _s(feature.get("code")).casefold() != "kit_id":
            continue
        for value in feature.get("values") or []:
            value = _s(value)
            if value.isdigit():
                return value
    return ""


def _feature_values(features):
    out = []
    for feature in features or []:
        code = _s(feature.get("code"))
        title = _s(feature.get("name"))
        if not title or code.casefold() == "kit_id":
            continue
        values = []
        for value in feature.get("values") or []:
            value = _s(value)
            if value and value not in values:
                values.append(value)
        if values:
            out.append((title, values))
    return out


def _load_repository_kit_client():
    """Reuse the established KIT transport from norden-kit; do not fork the API client."""
    path = Path(__file__).resolve().parents[1] / "norden-kit" / "webasyst_n100_to_kit_once.py"
    spec = importlib.util.spec_from_file_location("megasuppliers_repository_kit_client", path)
    if spec is None or spec.loader is None:
        raise KitSyncError("Unable to load repository KIT client")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.KitClient


KitClient = _load_repository_kit_client()


def _kit_variants(kit):
    if hasattr(kit, "scan_all_variants_parallel"):
        return list(kit.scan_all_variants_parallel(workers=10))
    if hasattr(kit, "variants"):
        return list(kit.variants())
    return list(kit.iter_collection("/v1/variants"))


def _kit_get_variant(kit, variant_id):
    if hasattr(kit, "get_variant"):
        return kit.get_variant(variant_id)
    return kit.request("GET", "/v1/variants/%s" % variant_id)


def _kit_create_product(kit, category_ids):
    category_ids = list(category_ids or [])
    if hasattr(kit, "request"):
        return kit.request("POST", "/v1/products", body={"category_ids": category_ids})
    return kit.create_product(category_ids)


def _kit_patch_product(kit, product_id, category_ids):
    category_ids = list(category_ids or [])
    if hasattr(kit, "patch_product"):
        return kit.patch_product(product_id, category_ids)
    return kit.request(
        "PATCH",
        "/v1/products/%s" % product_id,
        body={"category_ids": category_ids},
        merge_patch=True,
    )


def _kit_file_url(kit, file_id):
    if hasattr(kit, "file_url"):
        return _s(kit.file_url(file_id))
    payload = kit.request("GET", "/v1/files/%s" % file_id)
    return _s(payload.get("url")) if isinstance(payload, dict) else ""


def _warehouse_ids(kit):
    by_name = {}
    for row in kit.warehouses():
        name = _norm(row.get("title") or row.get("name"))
        if name:
            by_name[name] = _s(row.get("id"))
    required = {}
    for name in ("МСК", "СПБ привозной"):
        wid = by_name.get(_norm(name))
        if not wid:
            raise KitSyncError("KIT warehouse not found: %s" % name)
        required[name] = wid
    return required


def _category_index(rows):
    index = defaultdict(list)
    for row in rows:
        title = _s(row.get("title") or row.get("name"))
        if not title:
            continue
        index[(_s(row.get("parent_id")), _norm(title))].append(row)
    return index


def _ensure_category_path(kit, categories, index, path):
    parent = ""
    for title in path:
        key = (parent, _norm(title))
        matches = index.get(key, [])
        if len(matches) > 1:
            raise KitSyncError("KIT category is ambiguous: %s" % " > ".join(path))
        if len(matches) == 1:
            row = matches[0]
        else:
            row = kit.create_category(title, parent or None)
            if not _s(row.get("id")):
                raise KitSyncError("KIT did not return category id for %s" % title)
            categories.append(row)
            index[key].append(row)
        parent = _s(row.get("id"))
    return parent


def _webasyst_category_paths(categories, category_ids):
    by_id = {str(row.get("id")): row for row in categories if row.get("id") is not None}
    out = []
    for raw_id in category_ids or []:
        cid = str(raw_id)
        row = by_id.get(cid)
        if not row:
            continue
        path = []
        seen = set()
        while row and str(row.get("id")) not in seen:
            seen.add(str(row.get("id")))
            title = _s(row.get("name") or row.get("title"))
            if title:
                path.append(title)
            parent = _s(row.get("parent_id"))
            row = by_id.get(parent) if parent and parent != "0" else None
        path.reverse()
        if path and path not in out:
            out.append(path)
    return out


def _characteristic_index(rows):
    index = defaultdict(list)
    for row in rows:
        title = _s(row.get("title") or row.get("name"))
        if title:
            index[_norm(title)].append(row)
    return index


def _characteristic_sort_key(row):
    value = _s(row.get("id"))
    return (0, int(value)) if value.isdigit() else (1, value)


def _pick_characteristic(matches, title):
    exact = [
        row for row in matches
        if _s(row.get("title") or row.get("name")) == title
    ]
    pool = exact or list(matches)
    if not pool:
        return None
    # Historical KIT data contains duplicate characteristic definitions.
    # Reuse one stable existing definition instead of creating yet another
    # duplicate or blocking the whole supplier.
    return sorted(pool, key=_characteristic_sort_key)[0]


def _single_characteristic_id(index, title):
    row = _pick_characteristic(index.get(_norm(title), []), title)
    return _s(row.get("id")) if row else ""


def _ensure_characteristic(kit, rows, index, title):
    key = _norm(title)
    row = _pick_characteristic(index.get(key, []), title)
    if row:
        return _s(row.get("id"))
    row = kit.create_characteristic(title)
    cid = _s(row.get("id"))
    if not cid:
        raise KitSyncError("KIT did not return characteristic id: %s" % title)
    rows.append(row)
    index[key].append(row)
    return cid


SUPPLIER_ARTICLE_CHARACTERISTIC = "Артикул поставщика"
LEGACY_CODE_SITE_CHARACTERISTIC = "Код для сайта"


def _kit_characteristics(
    kit,
    features,
    rows,
    index,
    supplier_article_id=None,
    legacy_code_site_id=None,
    supplier_sku="",
):
    by_id = {}
    for title, values in _feature_values(features):
        normalized_title = _norm(title)
        if normalized_title in {
            _norm(SUPPLIER_ARTICLE_CHARACTERISTIC),
            _norm(LEGACY_CODE_SITE_CHARACTERISTIC),
        }:
            continue
        cid = _ensure_characteristic(kit, rows, index, title)
        if not cid or cid in by_id:
            continue
        by_id[cid] = {
            "characteristic_id": cid,
            "value": values[0],
            "values": values,
        }
    supplier_sku = _s(supplier_sku)
    if supplier_article_id and supplier_sku:
        by_id[supplier_article_id] = {
            "characteristic_id": supplier_article_id,
            "value": supplier_sku,
            "values": [supplier_sku],
        }
    if legacy_code_site_id and supplier_sku and legacy_code_site_id != supplier_article_id:
        by_id[legacy_code_site_id] = {
            "characteristic_id": legacy_code_site_id,
            "value": supplier_sku,
            "values": [supplier_sku],
        }
    return list(by_id.values())


def _variant_characteristic_values(variant, characteristic_id):
    characteristic_id = _s(characteristic_id)
    if not characteristic_id:
        return []
    for item in variant.get("characteristics") or []:
        if not isinstance(item, dict):
            continue
        if _s(item.get("characteristic_id")) != characteristic_id:
            continue
        values = []
        raw_values = item.get("values") or []
        if not isinstance(raw_values, list):
            raw_values = [raw_values]
        for value in raw_values:
            value = _s(value)
            if value and value not in values:
                values.append(value)
        fallback = _s(item.get("value"))
        if fallback and fallback not in values:
            values.append(fallback)
        return values
    return []


def _variant_supplier_articles(variant, supplier_identity_ids):
    values = []
    for characteristic_id in supplier_identity_ids or []:
        for value in _variant_characteristic_values(variant, characteristic_id):
            if value and value not in values:
                values.append(value)
    return values


def _variant_supplier_article(variant, supplier_identity_ids):
    values = _variant_supplier_articles(variant, supplier_identity_ids)
    if len(values) > 1:
        raise KitSyncError(
            "KIT variant %s has conflicting supplier articles: %s"
            % (_s(variant.get("id")), ", ".join(values))
        )
    return values[0] if values else ""


def _variant_indexes(variants, supplier_identity_ids):
    by_sku = defaultdict(list)
    by_kit_id = defaultdict(list)
    by_supplier_brand = defaultdict(list)
    for row in variants:
        sku = _s(row.get("sku"))
        kit_id = _s(row.get("kit_id"))
        brand_key = _norm(row.get("brand"))
        if sku:
            by_sku[sku].append(row)
        if kit_id:
            by_kit_id[kit_id].append(row)
        if brand_key:
            # Index every historical identity value. A row with conflicting
            # values is only fatal when that specific row is selected for the
            # current Webasyst product; unrelated broken KIT rows must not
            # block the entire Norden export.
            for supplier_article in _variant_supplier_articles(row, supplier_identity_ids):
                by_supplier_brand[(supplier_article, brand_key)].append(row)
    return by_sku, by_kit_id, by_supplier_brand


def _select_variant(product, by_sku, by_kit_id, by_supplier_brand, supplier_identity_ids, brand):
    expected_kit_id = _feature_kit_id(product.get("features"))
    supplier_sku = _s(product.get("supplier_sku"))
    sku = _s(product.get("sku"))
    brand_key = _norm(brand)

    if expected_kit_id:
        matches = by_kit_id.get(expected_kit_id, [])
        if len(matches) > 1:
            raise KitSyncError("KIT ID %s matches %d variants" % (expected_kit_id, len(matches)))
        if not matches:
            raise KitSyncError(
                "KIT ID %s is stored in Webasyst but was not found in KIT; automatic creation is blocked"
                % expected_kit_id
            )
        variant = matches[0]
        existing_brand = _norm(variant.get("brand"))
        if existing_brand and brand_key and existing_brand != brand_key:
            raise KitSyncError(
                "KIT ID %s brand conflict: KIT=%s, Webasyst=%s"
                % (expected_kit_id, _s(variant.get("brand")), brand)
            )
        existing_supplier = _variant_supplier_article(variant, supplier_identity_ids)
        if existing_supplier and supplier_sku and existing_supplier != supplier_sku:
            raise KitSyncError(
                "KIT ID %s supplier article conflict: KIT=%s, Webasyst=%s"
                % (expected_kit_id, existing_supplier, supplier_sku)
            )
        return variant

    if not sku or not supplier_sku or not brand_key:
        raise KitSyncError(
            "First KIT match requires exact Webasyst SKU + supplier article + brand"
        )

    sku_matches = by_sku.get(sku, [])
    exact = []
    for variant in sku_matches:
        if _norm(variant.get("brand")) != brand_key:
            continue
        if _variant_supplier_article(variant, supplier_identity_ids) != supplier_sku:
            continue
        exact.append(variant)

    if len(exact) > 1:
        raise KitSyncError(
            "Exact KIT match is ambiguous for SKU=%s, supplier=%s, brand=%s"
            % (sku, supplier_sku, brand)
        )
    if len(exact) == 1:
        return exact[0]

    if sku_matches:
        raise KitSyncError(
            "KIT already contains SKU=%s but supplier article/brand do not match exactly; automatic linking is blocked"
            % sku
        )

    supplier_matches = by_supplier_brand.get((supplier_sku, brand_key), [])
    if supplier_matches:
        raise KitSyncError(
            "KIT already contains supplier article=%s and brand=%s under another SKU; automatic creation is blocked"
            % (supplier_sku, brand)
        )

    return None


def _identity_preflight(products, variants, characteristic_index, brand):
    supplier_article_id = _single_characteristic_id(
        characteristic_index,
        SUPPLIER_ARTICLE_CHARACTERISTIC,
    )
    legacy_code_site_id = _single_characteristic_id(
        characteristic_index,
        LEGACY_CODE_SITE_CHARACTERISTIC,
    )
    supplier_identity_ids = [
        value for value in (supplier_article_id, legacy_code_site_id) if value
    ]

    try:
        by_sku, by_kit_id, by_supplier_brand = _variant_indexes(
            variants,
            supplier_identity_ids,
        )
    except Exception as exc:
        return {
            "status": "blocked",
            "matched_by_kit_id": 0,
            "matched_exact_identity": 0,
            "create_new": 0,
            "conflicts": 1,
            "conflict_sample": [{"error": str(exc)[:1200]}],
        }, None

    report = {
        "status": "ok",
        "matched_by_kit_id": 0,
        "matched_exact_identity": 0,
        "create_new": 0,
        "conflicts": 0,
        "conflict_sample": [],
    }
    selected = {}
    for product in products:
        key = str(product.get("product_id") or product.get("sku_id") or product.get("supplier_sku") or "")
        try:
            variant = _select_variant(
                product,
                by_sku,
                by_kit_id,
                by_supplier_brand,
                supplier_identity_ids,
                brand,
            )
            selected[key] = variant
            if variant is None:
                report["create_new"] += 1
            elif _feature_kit_id(product.get("features")):
                report["matched_by_kit_id"] += 1
            else:
                report["matched_exact_identity"] += 1
        except Exception as exc:
            report["conflicts"] += 1
            if len(report["conflict_sample"]) < 100:
                report["conflict_sample"].append({
                    "product_id": product.get("product_id"),
                    "supplier_sku": product.get("supplier_sku"),
                    "sku": product.get("sku"),
                    "kit_id": _feature_kit_id(product.get("features")),
                    "error": str(exc)[:1200],
                })

    if report["conflicts"]:
        report["status"] = "blocked"
        return report, None

    return report, {
        "supplier_article_id": supplier_article_id,
        "legacy_code_site_id": legacy_code_site_id,
        "supplier_identity_ids": supplier_identity_ids,
        "by_sku": by_sku,
        "by_kit_id": by_kit_id,
        "by_supplier_brand": by_supplier_brand,
        "selected": selected,
    }


def _media_from_source(kit, urls):
    media = []
    for url in list(dict.fromkeys(_s(x) for x in (urls or []) if _s(x)))[:20]:
        upload = kit.upload_image_url(url)
        fid = _s(upload.get("id"))
        if not fid:
            raise KitSyncError("KIT image upload returned no file id for %s" % url)
        media.append({
            "type": "IMAGE",
            "display_sequence": len(media),
            "image_id": fid,
        })
    return media


def _public_media_urls(kit, variant):
    media = [
        row for row in (variant.get("media") or [])
        if isinstance(row, dict) and _s(row.get("type")).upper() == "IMAGE" and _s(row.get("image_id"))
    ]
    media.sort(key=lambda row: int(row.get("display_sequence") or 0))
    out = []
    for row in media:
        url = _kit_file_url(kit, _s(row.get("image_id")))
        if url and url not in out:
            out.append(url)
    return out


def _summary(urls):
    urls = [x for x in (_s(url) for url in urls or []) if x]
    return "\n".join("[extimg]\n%s\n[/extimg]" % url for url in urls)


_EXTIMG_BLOCK_RE = re.compile(r"\[extimg\].*?\[/extimg\]", re.IGNORECASE | re.DOTALL)


def _replace_extimg(summary, urls):
    new_blocks = _summary(urls)
    original = str(summary or "")
    if _EXTIMG_BLOCK_RE.search(original):
        remainder = _EXTIMG_BLOCK_RE.sub("", original).strip()
        if remainder and new_blocks:
            return remainder + "\n" + new_blocks
        return remainder or new_blocks
    return new_blocks or original


def _verify_unrelated_features(before, after):
    before_features = before.get("features") or {} if isinstance(before, dict) else {}
    after_features = after.get("features") or {} if isinstance(after, dict) else {}
    if not isinstance(before_features, dict) or not isinstance(after_features, dict):
        return
    lost = [
        key for key, value in before_features.items()
        if key != "kit_id"
        and value not in (None, "", [])
        and after_features.get(key) in (None, "", [])
    ]
    if lost:
        raise KitSyncError("Webasyst update lost unrelated features: %s" % ", ".join(lost[:20]))


def _set_webasyst_kit_data(wa, product, kit_id, image_urls):
    product_id = str(product.get("product_id") or "")
    before = wa.call("shop.product.getInfo", params={"id": product_id})
    data = {"features": {"kit_id": str(int(kit_id))}}
    if image_urls:
        source_summary = (before.get("summary") or product.get("summary")) if isinstance(before, dict) else product.get("summary")
        data["summary"] = _replace_extimg(source_summary, image_urls)
    wa.call(
        "shop.product.update",
        http_method="POST",
        params={"id": product_id},
        data=data,
    )
    after = wa.call("shop.product.getInfo", params={"id": product_id})
    if isinstance(after, dict) and isinstance(after.get("features"), dict):
        got = _s(after["features"].get("kit_id"))
        if got != str(int(kit_id)):
            raise KitSyncError("Webasyst KIT ID readback mismatch: expected %s, got %s" % (kit_id, got))
        _verify_unrelated_features(before, after)



def _plan_category_paths(categories, paths):
    index = _category_index(categories)
    missing = []
    required = []
    for path in paths:
        parent = ""
        normalized_path = []
        for title in path:
            title = _s(title)
            if not title:
                continue
            normalized_path.append(title)
            key = (parent, _norm(title))
            matches = index.get(key, [])
            if len(matches) > 1:
                raise KitSyncError("KIT category is ambiguous: %s" % " > ".join(normalized_path))
            if matches:
                row = matches[0]
            else:
                virtual_id = "__planned__:" + "/".join(_norm(x) for x in normalized_path)
                row = {"id": virtual_id, "title": title, "parent_id": parent}
                index[key].append(row)
                missing.append(" > ".join(normalized_path))
            parent = _s(row.get("id"))
        if normalized_path:
            label = " > ".join(normalized_path)
            if label not in required:
                required.append(label)
    return required, list(dict.fromkeys(missing))


def plan_manifest(manifest, config, *, kit=None):
    """Read-only KIT preflight. It never creates, patches, uploads or writes Webasyst."""
    rules = config.get("rules") or {}
    if not rules.get("export_to_kit", False):
        return {
            "status": "disabled",
            "readonly": True,
            "eligible": 0,
            "skipped_no_category": 0,
            "items": [],
        }

    kit = kit or KitClient()
    brand = _s((config.get("identity") or {}).get("brand"))
    products = [row for row in (manifest.get("items") or []) if isinstance(row, dict)]
    wa_categories = [row for row in (manifest.get("categories") or []) if isinstance(row, dict)]
    eligible = [row for row in products if row.get("category_ids")]

    report = {
        "status": "ok",
        "readonly": True,
        "eligible": len(eligible),
        "skipped_no_category": len(products) - len(eligible),
        "required_category_paths": [],
        "missing_category_paths": [],
        "missing_characteristics": [],
        "would_create": 0,
        "would_update": 0,
        "would_upload_images": 0,
        "errors": [],
        "items": [],
    }
    if not eligible:
        return report

    # All calls below are GET/read-only methods.
    _warehouse_ids(kit)
    kit_categories = kit.categories()
    kit_characteristics = kit.characteristics()
    characteristic_index = _characteristic_index(kit_characteristics)
    variants = _kit_variants(kit)

    preflight, identity = _identity_preflight(
        eligible,
        variants,
        characteristic_index,
        brand,
    )
    report["preflight"] = preflight
    if preflight.get("status") != "ok":
        report["status"] = "blocked"
        report["errors"] = list(preflight.get("conflict_sample") or [])

    all_paths = []
    for product in eligible:
        paths = _webasyst_category_paths(wa_categories, product.get("category_ids"))
        if not paths:
            report["errors"].append({
                "product_id": product.get("product_id"),
                "supplier_sku": product.get("supplier_sku"),
                "error": "Webasyst category path cannot be resolved",
            })
            continue
        all_paths.extend(paths)
    try:
        required, missing = _plan_category_paths(kit_categories, all_paths)
        report["required_category_paths"] = required
        report["missing_category_paths"] = missing
    except Exception as exc:
        report["status"] = "blocked"
        report["errors"].append({"error": str(exc)[:1200]})

    wanted_characteristics = {SUPPLIER_ARTICLE_CHARACTERISTIC}
    for product in eligible:
        for title, values in _feature_values(product.get("features")):
            if values and _norm(title) not in {
                _norm(SUPPLIER_ARTICLE_CHARACTERISTIC),
                _norm(LEGACY_CODE_SITE_CHARACTERISTIC),
            }:
                wanted_characteristics.add(title)
    for title in sorted(wanted_characteristics):
        try:
            cid = _single_characteristic_id(characteristic_index, title)
        except Exception as exc:
            report["status"] = "blocked"
            report["errors"].append({"characteristic": title, "error": str(exc)[:1200]})
            continue
        if not cid:
            report["missing_characteristics"].append(title)

    selected = identity.get("selected", {}) if identity else {}
    for product in eligible:
        key = str(product.get("product_id") or product.get("sku_id") or product.get("supplier_sku") or "")
        variant = selected.get(key) if identity else None
        action = "blocked" if preflight.get("status") != "ok" else ("create" if variant is None else "update")
        if action == "create":
            report["would_create"] += 1
        elif action == "update":
            report["would_update"] += 1

        source_images = list(dict.fromkeys(
            _s(url) for url in (product.get("image_urls") or []) if _s(url)
        ))[:20]
        current_media = []
        if isinstance(variant, dict):
            current_media = [
                row for row in (variant.get("media") or [])
                if isinstance(row, dict) and _s(row.get("type")).upper() == "IMAGE"
            ]
        image_uploads = len(source_images) if source_images and not current_media else 0
        report["would_upload_images"] += image_uploads
        paths = _webasyst_category_paths(wa_categories, product.get("category_ids"))
        report["items"].append({
            "product_id": product.get("product_id"),
            "supplier_sku": product.get("supplier_sku"),
            "webasyst_sku": product.get("sku"),
            "name": product.get("name"),
            "action": action,
            "kit_id": _s(variant.get("kit_id")) if isinstance(variant, dict) else "",
            "categories": [" > ".join(path) for path in paths],
            "source_images": len(source_images),
            "image_uploads": image_uploads,
        })

    if report["errors"] and report["status"] == "ok":
        report["status"] = "blocked"
    return report


def sync_manifest(manifest, config, *, kit=None, wa=None):
    rules = config.get("rules") or {}
    if not rules.get("export_to_kit", False):
        return {"status": "disabled", "eligible": 0, "created": 0, "updated": 0, "skipped_no_category": 0, "errors": []}

    kit = kit or KitClient()
    wa = wa or WebasystClient()
    brand = _s((config.get("identity") or {}).get("brand"))
    products = [row for row in (manifest.get("items") or []) if isinstance(row, dict)]
    wa_categories = [row for row in (manifest.get("categories") or []) if isinstance(row, dict)]

    eligible = [row for row in products if row.get("category_ids")]
    report = {
        "status": "ok",
        "eligible": len(eligible),
        "created": 0,
        "updated": 0,
        "skipped_no_category": len(products) - len(eligible),
        "categories_created": 0,
        "characteristics_created": 0,
        "images_uploaded": 0,
        "webasyst_updated": 0,
        "errors": [],
    }
    if not eligible:
        return report

    # Read-only safety phase. Nothing in KIT is created or patched until every
    # eligible Webasyst product has an unambiguous identity outcome.
    warehouses = _warehouse_ids(kit)
    kit_characteristics = kit.characteristics()
    characteristic_index = _characteristic_index(kit_characteristics)
    variants = _kit_variants(kit)
    preflight, identity = _identity_preflight(
        eligible,
        variants,
        characteristic_index,
        brand,
    )
    report["preflight"] = preflight
    if preflight.get("status") != "ok":
        report["status"] = "blocked"
        report["errors"] = list(preflight.get("conflict_sample") or [])
        return report

    supplier_article_id = identity["supplier_article_id"]
    legacy_code_site_id = identity["legacy_code_site_id"]
    if not supplier_article_id:
        supplier_article_id = _ensure_characteristic(
            kit,
            kit_characteristics,
            characteristic_index,
            SUPPLIER_ARTICLE_CHARACTERISTIC,
        )
    supplier_identity_ids = [
        value for value in (supplier_article_id, legacy_code_site_id) if value
    ]

    # Existing indexes were built before any writes. Adding the new empty
    # supplier characteristic does not change existing variant identities.
    by_sku = identity["by_sku"]
    by_kit_id = identity["by_kit_id"]
    by_supplier_brand = identity["by_supplier_brand"]
    selected_variants = identity["selected"]

    kit_categories = kit.categories()
    category_index = _category_index(kit_categories)
    category_count_before = len(kit_categories)
    characteristic_count_before = len(kit_characteristics)

    for product in eligible:
        try:
            paths = _webasyst_category_paths(wa_categories, product.get("category_ids"))
            if not paths:
                report["skipped_no_category"] += 1
                continue
            category_ids = []
            for path in paths:
                cid = _ensure_category_path(kit, kit_categories, category_index, path)
                if cid and cid not in category_ids:
                    category_ids.append(cid)
            if not category_ids:
                raise KitSyncError("No KIT category resolved")

            variant = _select_variant(
                product,
                by_sku,
                by_kit_id,
                by_supplier_brand,
                supplier_identity_ids,
                brand,
            )
            qty = _quantity(product.get("stock"))
            stocks = [
                {"warehouse_id": warehouses["МСК"], "quantity": qty, "reserved": 0},
                {"warehouse_id": warehouses["СПБ привозной"], "quantity": qty, "reserved": 0},
            ]
            pricing = _price_pair(product.get("purchase_price"))
            characteristics = _kit_characteristics(
                kit,
                product.get("features"),
                kit_characteristics,
                characteristic_index,
                supplier_article_id=supplier_article_id,
                legacy_code_site_id=legacy_code_site_id,
                supplier_sku=product.get("supplier_sku"),
            )
            variant_payload = {
                "sku": _s(product.get("sku")),
                "name": _s(product.get("name")),
                "description": _s(product.get("description")),
                "brand": brand,
                "status": "PUBLISHED" if int(product.get("status") or 0) else "HIDDEN",
                "stocks": stocks,
                "characteristics": characteristics,
            }
            if pricing:
                variant_payload["pricing"] = pricing

            if variant is None:
                created_product = _kit_create_product(kit, category_ids)
                product_id = _s(created_product.get("id"))
                if not product_id:
                    raise KitSyncError("KIT product create returned no id")
                variant_payload["product_id"] = product_id
                created_variant = kit.create_variant(variant_payload)
                variant_id = _s(created_variant.get("id"))
                if not variant_id:
                    raise KitSyncError("KIT variant create returned no id")
                report["created"] += 1
                variant = dict(created_variant)
                variant["id"] = variant_id
                by_sku[_s(product.get("sku"))].append(variant)
                by_supplier_brand[(_s(product.get("supplier_sku")), _norm(brand))].append(variant)
            else:
                variant_id = _s(variant.get("id"))
                product_id = _s(variant.get("product_id"))
                if not variant_id or not product_id:
                    full = _kit_get_variant(kit, variant_id)
                    product_id = _s(full.get("product_id"))
                if not product_id:
                    raise KitSyncError("Existing KIT variant has no product_id")
                _kit_patch_product(kit, product_id, category_ids)
                kit.patch_variant(variant_id, variant_payload)
                report["updated"] += 1

            full = _kit_get_variant(kit, variant_id)
            current_media = [
                row for row in (full.get("media") or [])
                if isinstance(row, dict) and _s(row.get("type")).upper() == "IMAGE"
            ]
            if not current_media and product.get("image_urls"):
                media = _media_from_source(kit, product.get("image_urls"))
                if media:
                    kit.patch_variant(variant_id, {"media": media})
                    report["images_uploaded"] += len(media)
                    full = _kit_get_variant(kit, variant_id)

            kit_id = _s(full.get("kit_id"))
            if not kit_id or not kit_id.isdigit():
                raise KitSyncError("KIT variant %s has no numeric kit_id" % variant_id)
            public_urls = _public_media_urls(kit, full)

            _set_webasyst_kit_data(wa, product, kit_id, public_urls)
            report["webasyst_updated"] += 1

            if _s(product.get("sku")):
                by_sku[_s(product.get("sku"))] = [full]
            by_kit_id[kit_id] = [full]
            supplier_key = (_s(product.get("supplier_sku")), _norm(brand))
            if supplier_key[0] and supplier_key[1]:
                by_supplier_brand[supplier_key] = [full]
        except Exception as exc:
            report["errors"].append({
                "product_id": product.get("product_id"),
                "supplier_sku": product.get("supplier_sku"),
                "sku": product.get("sku"),
                "error": str(exc)[:1200],
            })

    report["categories_created"] = max(0, len(kit_categories) - category_count_before)
    report["characteristics_created"] = max(0, len(kit_characteristics) - characteristic_count_before)
    if report["errors"]:
        report["status"] = "partial_failure"
    return report
