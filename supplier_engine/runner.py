import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path

from .adapters import load_csv, load_xlsx, load_xml_yml, load_pdf
from .bridge import MegasuppliersBridge
from .formulas import apply_price_formulas
from .models import Product
from .norden import load_norden_source
from .validators import validate_run
from .webasyst_sync import (
    ApplyError,
    apply_plan,
    build_plan,
    index_by_sku,
    index_by_supplier_sku_name,
    previous_prices,
    validate_apply_plan,
    webasyst_sku_mode,
)
from webasyst.client import WebasystClient


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _stock(v):
    if v in (None, ""):
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    text = str(v).strip().lower()
    if text in {"true", "yes", "y", "да", "in_stock", "available"}:
        return 1.0
    if text in {"false", "no", "n", "нет", "out_of_stock", "unavailable"}:
        return 0.0
    return _num(v)


def _get(row, key, default=""):
    return row.get(key, default) if key else default


def load_config(path):
    c = json.loads(Path(path).read_text(encoding="utf-8"))
    for k in ("code", "name", "source", "identity", "mapping", "rules", "safety"):
        if k not in c:
            raise ValueError("Missing config section: %s" % k)
    if not c["identity"].get("supplier_sku_field"):
        raise ValueError("identity.supplier_sku_field is empty")
    if not c["mapping"].get("name"):
        raise ValueError("mapping.name is empty")
    return c


def config_sha256(c):
    raw = json.dumps(c, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fetch_source(c):
    s = c["source"]
    loc = s.get("location", "") or os.getenv("SUPPLIER_SOURCE_URL", "")
    if not loc:
        raise ValueError("source.location is empty and SUPPLIER_SOURCE_URL is not set")
    max_bytes = int(s.get("max_bytes") or 100 * 1024 * 1024)
    if max_bytes < 1024 or max_bytes > 250 * 1024 * 1024:
        raise ValueError("source.max_bytes must be between 1 KB and 250 MB")
    if s.get("kind") == "url":
        req = urllib.request.Request(loc, headers={"User-Agent": "Megasuppliers-Supplier-Engine/1.1"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                length = r.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise ValueError("Supplier source exceeds configured size limit")
                data = r.read(max_bytes + 1)
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError("Unable to download supplier source (%s)" % type(exc).__name__) from exc
        if len(data) > max_bytes:
            raise ValueError("Supplier source exceeds configured size limit")
        return data
    path = Path(loc)
    if path.stat().st_size > max_bytes:
        raise ValueError("Supplier source exceeds configured size limit")
    return path.read_bytes()


def parse_source(c, data):
    fmt = c["source"]["format"].lower()
    if fmt == "csv":
        return load_csv(data)
    if fmt == "xlsx":
        return load_xlsx(data, c["source"].get("sheet"))
    if fmt in ("xml", "yml"):
        return load_xml_yml(data)
    if fmt == "pdf":
        required = [
            c.get("identity", {}).get("supplier_sku_field"),
            c.get("mapping", {}).get("name"),
        ]
        return load_pdf(data, required_headers=[x for x in required if x])
    raise ValueError("Unsupported format: %s" % fmt)


def normalize(c, rows):
    m = c["mapping"]
    ident = c["identity"]
    prefix = ident.get("sku_prefix", "")
    formulas = c.get("rules", {}).get("price_formulas") or {}
    if not isinstance(formulas, dict):
        formulas = {}
    characteristic_map = m.get("characteristics") or {}
    if not isinstance(characteristic_map, dict):
        characteristic_map = {}
    out = []
    for row in rows:
        ss = str(_get(row, ident.get("supplier_sku_field"), "")).strip()
        sku = "%s%s" % (prefix, ss) if ss else ""
        imgs = []
        for field in m.get("images", []):
            value = _get(row, field, [])
            if isinstance(value, list):
                imgs.extend(str(x).strip() for x in value if x)
            elif value:
                imgs.append(str(value).strip())
        chars = {dst: _get(row, src) for dst, src in characteristic_map.items() if src}
        dynamic_characteristics = m.get("dynamic_characteristics")
        if dynamic_characteristics:
            raw_chars = _get(row, dynamic_characteristics, {})
            if isinstance(raw_chars, dict):
                for key, value in raw_chars.items():
                    if key and value not in (None, ""):
                        chars[str(key)] = value
        product = Product(
            supplier_sku=ss,
            sku=sku,
            name=str(_get(row, m.get("name"), "")).strip(),
            purchase_price=_num(_get(row, m.get("purchase_price"), None)),
            price=_num(_get(row, m.get("price"), None)),
            compare_price=_num(_get(row, m.get("compare_price"), None)),
            stock=_stock(_get(row, m.get("stock"), None)),
            brand=ident.get("brand", ""),
            category=str(_get(row, m.get("category"), "")).strip(),
            images=imgs,
            characteristics=chars,
        )
        is_norden = c.get("source", {}).get("format", "").lower() == "norden"
        if is_norden and (product.purchase_price is None or product.purchase_price <= 0):
            out.append(product)
        else:
            out.append(apply_price_formulas(product, formulas))
    return out


def _unique_image_aliases(products):
    by_url = {}
    ambiguous = set()
    for product in products:
        code = str(product.supplier_sku or "").strip()
        if not code:
            continue
        for url in product.images or []:
            url = str(url or "").strip()
            if not url:
                continue
            previous = by_url.get(url)
            if previous and previous != code:
                ambiguous.add(url)
            else:
                by_url[url] = code
    for url in ambiguous:
        by_url.pop(url, None)
    return by_url


def _characteristic_names_for_plan(plan, rules):
    rows = list(plan.get("create") or []) + list(plan.get("repair_sku") or [])
    if rules.get("update_characteristics", False):
        rows.extend(plan.get("update") or [])
    names = set()
    for row in rows:
        for key, value in (row.get("desired", {}).get("characteristics") or {}).items():
            if str(key).strip() and value not in (None, ""):
                names.add(str(key).strip())
    return sorted(names)


def _plan_summary(plan):
    return {
        "create": len(plan.get("create") or []),
        "repair_sku": len(plan.get("repair_sku") or []),
        "update": len(plan.get("update") or []),
        "zero_stock": len(plan.get("zero") or []),
        "skipped": len(plan.get("skipped") or []),
        "blocked": len(plan.get("blocked") or []),
        "blocked_sample": (plan.get("blocked") or [])[:20],
        "skipped_sample": (plan.get("skipped") or [])[:20],
    }


def build_payload(c, rows, products, report, source_sha, mode, plan=None, source_meta=None):
    payload = {
        "status": "blocked" if report.blocked else "ok",
        "mode": mode,
        "supplier": c["name"],
        "code": c["code"],
        "config_sha256": config_sha256(c),
        "source_sha256": source_sha,
        "source_rows": len(rows),
        "products": len(products),
        "blocked": report.blocked,
        "errors": list(report.errors),
        "warnings": list(report.warnings),
        "sample": [
            {
                "supplier_sku": p.supplier_sku,
                "sku": p.sku,
                "name": p.name,
                "purchase_price": p.purchase_price,
                "price": p.price,
                "compare_price": p.compare_price,
                "stock": p.stock,
                "images": p.images[:3],
            }
            for p in products[:20]
        ],
    }
    if source_meta:
        payload["source"] = source_meta
    if plan is not None:
        payload["plan"] = _plan_summary(plan)
    return payload


def _write_payload(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--mode", choices=("dry-run", "apply"), default="dry-run")
    ap.add_argument("--supplier-id", type=int)
    ap.add_argument("--request-id", default="")
    ap.add_argument("--previous-count", type=int)
    ap.add_argument("--approved-source-sha256", default="")
    ap.add_argument("--output", default="supplier-run.json")
    args = ap.parse_args()

    try:
        config = load_config(args.config)
        source_meta = None
        if config.get("source", {}).get("format", "").lower() == "norden":
            rows, source_data, source_meta = load_norden_source()
        else:
            source_data = fetch_source(config)
            rows = parse_source(config, source_data)
        source_sha = hashlib.sha256(source_data).hexdigest()
        if args.mode == "apply":
            approved = args.approved_source_sha256.strip().lower()
            if not approved or source_sha.lower() != approved:
                raise ValueError("Источник изменился после dry-run; применение заблокировано.")

        products = normalize(config, rows)
        if source_meta is not None and config.get("source", {}).get("format", "").lower() == "norden":
            source_meta["missing_purchase_price"] = sum(
                1 for product in products
                if product.purchase_price is None or product.purchase_price <= 0
            )
        safety = config["safety"]

        wa = None
        existing = {}
        links = []
        bridge = None
        catalog_warning = None
        if os.getenv("WEBASYST_API_TOKEN", "").strip():
            wa = WebasystClient()
            type_id = (config.get("webasyst") or {}).get("type_id")
            if type_id:
                by_supplier = index_by_supplier_sku_name(
                    wa,
                    type_id,
                    [product.supplier_sku for product in products],
                    {product.sku: product.supplier_sku for product in products},
                    _unique_image_aliases(products),
                )
                existing = {
                    product.sku: by_supplier[product.supplier_sku]
                    for product in products
                    if product.supplier_sku in by_supplier
                }
                if source_meta is not None:
                    source_meta["existing_match"] = "supplier_article"
                    source_meta["existing_matched"] = len(existing)
                    source_meta["sku_mode"] = webasyst_sku_mode(config)
            else:
                existing = index_by_sku(wa, type_id)
        elif args.mode == "apply":
            raise ValueError("WEBASYST_API_TOKEN is required for apply")
        else:
            catalog_warning = "Webasyst API token недоступен: dry-run выполнен без сверки с текущим каталогом."

        if args.supplier_id and args.request_id and os.getenv("MEGASUPPLIERS_BRIDGE_URL", "").strip() and os.getenv("MEGASUPPLIERS_CALLBACK_SECRET", "").strip():
            bridge = MegasuppliersBridge()
            links = bridge.get_links(args.supplier_id, args.request_id)
        elif args.mode == "apply":
            raise ValueError("Signed Megasuppliers bridge is required for apply")

        report = validate_run(
            products,
            args.previous_count,
            safety.get("max_price_change_pct", 100),
            safety.get("min_source_count_ratio", 0.6),
            previous_by_sku=previous_prices(existing),
        )
        if catalog_warning:
            report.warnings.append(catalog_warning)
        if source_meta and source_meta.get("fallback_used"):
            report.warnings.append("Norden API недоступен; использован резервный XML-источник.")
        if source_meta and source_meta.get("missing_purchase_price"):
            report.warnings.append(
                "У %d позиций Norden нет положительной закупочной цены; их цены не будут изменяться."
                % source_meta["missing_purchase_price"]
            )

        plan = build_plan(products, existing, links, config.get("rules") or {})
        characteristic_names = _characteristic_names_for_plan(plan, config.get("rules") or {})
        if source_meta is not None:
            source_meta["characteristics_detected"] = len(characteristic_names)
            source_meta["characteristics_sample"] = characteristic_names[:20]
        for error in validate_apply_plan(plan, config):
            if error not in report.errors:
                report.errors.append(error)
        report.blocked = bool(report.errors)

        payload = build_payload(config, rows, products, report, source_sha, args.mode, plan, source_meta)

        if args.mode == "apply" and not report.blocked:
            try:
                features_resolved = 0
                rules = config.get("rules") or {}
                if characteristic_names and rules.get("auto_features", False):
                    type_id = (config.get("webasyst") or {}).get("type_id")
                    if not type_id:
                        raise ValueError("Для автоматических характеристик не задан webasyst.type_id.")
                    resolved = bridge.ensure_features(
                        args.supplier_id,
                        args.request_id,
                        type_id,
                        characteristic_names,
                    )
                    web_cfg = config.setdefault("webasyst", {})
                    feature_codes = web_cfg.get("feature_codes")
                    if not isinstance(feature_codes, dict):
                        feature_codes = {}
                    feature_codes.update(resolved)
                    web_cfg["feature_codes"] = feature_codes
                    features_resolved = len(resolved)
                result = apply_plan(wa, plan, config)
                synced = bridge.sync_links(args.supplier_id, args.request_id, result.get("mappings") or [])
                payload["apply"] = {
                    "status": "ok",
                    "created": result["created"],
                    "updated": result["updated"],
                    "zeroed": result["zeroed"],
                    "links_synced": synced,
                    "features_resolved": features_resolved,
                    "kit_export": "separate_job" if rules.get("export_to_kit", False) else "disabled",
                }
            except ApplyError as exc:
                payload["status"] = "failed"
                payload["blocked"] = True
                payload["errors"].append(str(exc))
                payload["apply"] = dict(exc.result, status="partial_failure")
            except Exception as exc:
                payload["status"] = "failed"
                payload["blocked"] = True
                payload["errors"].append(str(exc))
                payload["apply"] = {"status": "failed"}

        _write_payload(args.output, payload)
        raise SystemExit(0 if payload["status"] == "ok" else 2)

    except SystemExit:
        raise
    except Exception as exc:
        payload = {
            "status": "failed",
            "mode": args.mode,
            "blocked": True,
            "errors": [str(exc)],
        }
        _write_payload(args.output, payload)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
