import argparse, json, urllib.request
from pathlib import Path
from .adapters import load_csv, load_xlsx, load_xml_yml, load_pdf
from .models import Product
from .validators import validate_run

def _num(v):
    if v in (None, ""): return None
    try: return float(str(v).replace(" ","").replace(",","."))
    except ValueError: return None

def _get(row, key, default=""):
    return row.get(key, default) if key else default

def load_config(path):
    c=json.loads(Path(path).read_text(encoding="utf-8"))
    for k in ("code","name","source","identity","mapping","rules","safety"):
        if k not in c: raise ValueError(f"Missing config section: {k}")
    return c

def fetch_source(c):
    s=c["source"]; loc=s.get("location","")
    if not loc: raise ValueError("source.location is empty")
    if s.get("kind")=="url":
        with urllib.request.urlopen(loc, timeout=60) as r: return r.read()
    return Path(loc).read_bytes()

def parse_source(c,data):
    fmt=c["source"]["format"].lower()
    if fmt=="csv": return load_csv(data)
    if fmt=="xlsx": return load_xlsx(data,c["source"].get("sheet"))
    if fmt in ("xml","yml"): return load_xml_yml(data)
    if fmt=="pdf": return load_pdf(data)
    raise ValueError(f"Unsupported format: {fmt}")

def normalize(c, rows):
    m=c["mapping"]; ident=c["identity"]; prefix=ident.get("sku_prefix","")
    out=[]
    for row in rows:
        ss=str(_get(row,ident.get("supplier_sku_field"),"")).strip()
        sku=f"{prefix}{ss}" if ss else ""
        imgs=[]
        for f in m.get("images",[]):
            v=_get(row,f,[])
            if isinstance(v,list): imgs.extend(str(x).strip() for x in v if x)
            elif v: imgs.append(str(v).strip())
        chars={dst:_get(row,src) for dst,src in m.get("characteristics",{}).items() if src}
        out.append(Product(
            supplier_sku=ss, sku=sku, name=str(_get(row,m.get("name"),"")).strip(),
            purchase_price=_num(_get(row,m.get("purchase_price"),None)),
            price=_num(_get(row,m.get("price"),None)),
            compare_price=_num(_get(row,m.get("compare_price"),None)),
            stock=_num(_get(row,m.get("stock"),None)),
            brand=ident.get("brand",""), category=str(_get(row,m.get("category"),"")).strip(),
            images=imgs, characteristics=chars))
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("config"); ap.add_argument("--previous-count",type=int)
    ap.add_argument("--output",default="supplier-dry-run.json")
    a=ap.parse_args(); c=load_config(a.config)
    rows=parse_source(c,fetch_source(c)); products=normalize(c,rows)
    s=c["safety"]; r=validate_run(products,a.previous_count,s.get("max_price_change_pct",100),s.get("min_source_count_ratio",.6))
    payload={"supplier":c["name"],"code":c["code"],"source_rows":len(rows),"products":len(products),"blocked":r.blocked,"errors":r.errors,"warnings":r.warnings}
    Path(a.output).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    raise SystemExit(2 if r.blocked else 0)

if __name__=="__main__": main()
