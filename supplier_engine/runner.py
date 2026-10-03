import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path

from .adapters import load_csv, load_xlsx, load_xml_yml, load_pdf
from .formulas import apply_price_formulas
from .models import Product
from .validators import validate_run


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
    if s.get("kind") == "url":
        req = urllib.request.Request(loc, headers={"User-Agent": "Megasuppliers-Supplier-Engine/1.1"})
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.read()
    return Path(loc).read_bytes()


def parse_source(c, data):
    fmt = c["source"]["format"].lower()
    if fmt == "csv":
        return load_csv(data)
    if fmt == "xlsx":
        return load_xlsx(data, c["source"].get("sheet"))
    if fmt in ("xml", "yml"):
        return load_xml_yml(data)
    if fmt == "pdf":
        return load_pdf(data)
    raise ValueError("Unsupported format: %s" % fmt)


def normalize(c, rows):
    m = c["mapping"]
    ident = c["identity"]
    prefix = ident.get("sku_prefix", "")
    formulas = c.get("rules", {}).get("price_formulas", {})
    out = []
    for row in rows:
        ss = str(_get(row, ident.get("supplier_sku_field"), "")).strip()
        sku = "%s%s" % (prefix, ss) if ss else ""
        imgs = []
        for f in m.get("images", []):
            v = _get(row, f, [])
            if isinstance(v, list):
                imgs.extend(str(x).strip() for x in v if x)
            elif v:
                imgs.append(str(v).strip())
        chars = {dst: _get(row, src) for dst, src in m.get("characteristics", {}).items() if src}
        p = Product(
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
        out.append(apply_price_formulas(p, formulas))
    return out


def build_payload(c, rows, products, report, mode="dry-run"):
    return {
        "status": "blocked" if report.blocked else "ok",
        "mode": mode,
        "supplier": c["name"],
        "code": c["code"],
        "config_sha256": config_sha256(c),
        "source_rows": len(rows),
        "products": len(products),
        "blocked": report.blocked,
        "errors": report.errors,
        "warnings": report.warnings,
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--previous-count", type=int)
    ap.add_argument("--output", default="supplier-dry-run.json")
    a = ap.parse_args()
    c = load_config(a.config)
    rows = parse_source(c, fetch_source(c))
    products = normalize(c, rows)
    s = c["safety"]
    r = validate_run(
        products,
        a.previous_count,
        s.get("max_price_change_pct", 100),
        s.get("min_source_count_ratio", 0.6),
    )
    payload = build_payload(c, rows, products, r)
    Path(a.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(2 if r.blocked else 0)


if __name__ == "__main__":
    main()
