import json

import pytest

from supplier_engine.adapters import load_pdf, load_xml_yml
from supplier_engine.formulas import evaluate_formula
from supplier_engine.models import Product
from supplier_engine.runner import load_config, normalize, config_sha256, fetch_source
from supplier_engine.validators import validate_run
from supplier_engine.webasyst_sync import apply_plan, build_plan, validate_apply_plan


def test_duplicate_blocks():
    p=[Product("1","X-1","A"),Product("2","X-1","B")]
    r=validate_run(p)
    assert r.blocked and any("Дубль" in x for x in r.errors)


def test_feed_collapse_blocks():
    p=[Product(str(i),f"X-{i}","A") for i in range(5)]
    r=validate_run(p, previous_count=100, min_count_ratio=.6)
    assert r.blocked


def test_negative_stock_blocks():
    r=validate_run([Product("1","X-1","A",stock=-1)])
    assert r.blocked


def test_price_jump_blocks():
    p=[Product("1","X-1","A",price=250)]
    r=validate_run(p,max_price_change_pct=100,previous_by_sku={"X-1":{"price":100}})
    assert r.blocked and any("Аномальное" in x for x in r.errors)


def test_safe_price_formulas_are_applied():
    c={
        "identity":{"supplier_sku_field":"vendorCode","sku_prefix":"Liga-","brand":"Лига Диванов"},
        "mapping":{"name":"name","purchase_price":"","price":"price","compare_price":"","stock":"available","category":"categoryId","images":["pictures"],"characteristics":{}},
        "rules":{"price_formulas":{"price":"supplier_price","compare_price":"supplier_price * 1.30"}},
    }
    rows=[{"vendorCode":"109775","name":"Диван","price":"10000","available":"true","pictures":["https://x/1.jpg","https://x/2.jpg"]}]
    p=normalize(c,rows)[0]
    assert p.sku=="Liga-109775"
    assert p.price==10000
    assert p.compare_price==13000
    assert p.stock==1
    assert p.images==["https://x/1.jpg","https://x/2.jpg"]


def test_formula_rejects_code_execution():
    with pytest.raises(ValueError):
        evaluate_formula("__import__('os').system('id')", {"supplier_price":100})


def test_yml_parser_preserves_vendor_code_pictures_and_available():
    data=b'''<?xml version="1.0" encoding="UTF-8"?><yml_catalog><shop><offers><offer id="7" available="true"><name>Sofa</name><vendorCode>109775</vendorCode><price>999</price><picture>https://x/1.jpg</picture><picture>https://x/2.jpg</picture></offer></offers></shop></yml_catalog>'''
    row=load_xml_yml(data)[0]
    assert row["vendorCode"]=="109775"
    assert row["available"]=="true"
    assert row["pictures"]==["https://x/1.jpg","https://x/2.jpg"]


def test_config_hash_is_stable():
    c={"b":2,"a":1}
    assert config_sha256(c)==config_sha256({"a":1,"b":2})


def test_config_requires_identity_field(tmp_path):
    p=tmp_path/"c.json"
    p.write_text(json.dumps({"code":"X","name":"X","source":{},"identity":{},"mapping":{"name":"name"},"rules":{},"safety":{}}),encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(p)


class _FakeWebasyst:
    def __init__(self):
        self.calls = []

    def call(self, method, *, http_method="GET", params=None, data=None, files=None):
        self.calls.append({
            "method": method,
            "http_method": http_method,
            "params": params or {},
            "data": data or {},
        })
        if method == "shop.product.add":
            return {"id": "101"}
        if method == "shop.product.skus.getList":
            return {"skus": [{"id": "202"}]}
        return {}


def test_build_plan_zeroes_missing_link_and_skips_out_of_stock_create():
    products = [
        Product("A", "X-A", "Existing", stock=5),
        Product("B", "X-B", "No stock", stock=0),
    ]
    existing = {
        "X-A": [({"id": 11, "name": "Existing"}, {"id": 21, "sku": "X-A"})],
    }
    links = [
        {"supplier_sku": "OLD", "product_id": 12, "sku_id": 22},
    ]
    plan = build_plan(
        products,
        existing,
        links,
        {
            "create_new": True,
            "only_create_in_stock": True,
            "zero_if_missing": True,
            "update_stock": True,
        },
    )
    assert len(plan["update"]) == 1
    assert plan["skipped"] == [{"sku": "X-B", "reason": "not_in_stock"}]
    assert plan["zero"] == [{"supplier_sku": "OLD", "sku_id": 22, "product_id": 12}]


def test_validate_apply_plan_requires_explicit_type_and_stock():
    plan = {
        "create": [{"sku": "X-1", "desired": {"stock": 1, "characteristics": {}}}],
        "update": [],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    errors = validate_apply_plan(
        plan,
        {
            "rules": {"update_stock": True},
            "webasyst": {"type_id": None, "stock_id": None, "feature_codes": {}},
        },
    )
    assert any("type_id" in x for x in errors)
    assert any("stock_id" in x for x in errors)


def test_apply_plan_updates_existing_prices_and_stock():
    wa = _FakeWebasyst()
    plan = {
        "create": [],
        "update": [{
            "sku": "X-1",
            "supplier_sku": "1",
            "product_id": 11,
            "sku_id": 21,
            "current_product": {"id": 11},
            "current_sku": {"id": 21},
            "desired": {
                "supplier_sku": "1",
                "sku": "X-1",
                "name": "Chair",
                "purchase_price": 100,
                "price": 125,
                "compare_price": 160,
                "stock": 3,
                "brand": "",
                "category": "",
                "images": [],
                "characteristics": {},
            },
        }],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    result = apply_plan(
        wa,
        plan,
        {
            "rules": {
                "update_prices": True,
                "update_stock": True,
                "update_name": False,
                "update_images": False,
                "update_characteristics": False,
            },
            "webasyst": {"stock_id": 1, "type_id": 5},
        },
    )
    assert result["updated"] == 1
    sku_call = next(x for x in wa.calls if x["method"] == "shop.product.skus.update")
    assert sku_call["data"]["price"] == "125"
    assert sku_call["data"]["compare_price"] == "160"
    assert sku_call["data"]["purchase_price"] == "100"
    assert sku_call["data"]["stock"] == {"1": "3"}


def test_apply_plan_creates_product_then_sets_final_sku():
    wa = _FakeWebasyst()
    desired = {
        "supplier_sku": "1",
        "sku": "X-1",
        "name": "Chair",
        "purchase_price": 100,
        "price": 125,
        "compare_price": 160,
        "stock": 3,
        "brand": "",
        "category": "",
        "images": ["https://img/1.jpg"],
        "characteristics": {},
    }
    plan = {
        "create": [{"sku": "X-1", "supplier_sku": "1", "desired": desired}],
        "update": [],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    result = apply_plan(
        wa,
        plan,
        {
            "rules": {
                "update_prices": True,
                "update_stock": True,
                "update_images": True,
                "update_characteristics": False,
            },
            "webasyst": {
                "stock_id": 1,
                "type_id": 5,
                "category_id": 9,
                "currency": "RUB",
                "new_product_status": 0,
            },
        },
    )
    assert result["created"] == 1
    add_call = next(x for x in wa.calls if x["method"] == "shop.product.add")
    assert add_call["data"]["type_id"] == 5
    assert add_call["data"]["categories"] == [9]
    assert "[extimg]" in add_call["data"]["summary"]
    sku_calls = [x for x in wa.calls if x["method"] == "shop.product.skus.update"]
    assert sku_calls[-1]["data"]["sku"] == "X-1"
    assert result["mappings"][0]["product_id"] == 101
    assert result["mappings"][0]["sku_id"] == 202


def test_source_size_limit_blocks_oversized_local_file(tmp_path):
    p = tmp_path / "feed.csv"
    p.write_bytes(b"x" * 2048)
    with pytest.raises(ValueError, match="size limit"):
        fetch_source({"source": {"kind": "file", "location": str(p), "max_bytes": 1024}})


def test_source_download_error_does_not_echo_private_url(monkeypatch):
    private_url = "https://user:secret@example.invalid/feed.xlsx"

    def fail(*args, **kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr("supplier_engine.runner.urllib.request.urlopen", fail)
    with pytest.raises(RuntimeError) as exc:
        fetch_source({"source": {"kind": "url", "location": private_url}})
    assert private_url not in str(exc.value)
    assert "secret" not in str(exc.value)


def test_tabular_pdf_parser_extracts_configured_columns():
    import io
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    table = Table([
        ["Артикул", "Название", "Цена"],
        ["109775", "Диван Атланта", "10000"],
        ["109776", "Кресло", "5000"],
    ])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
    ]))
    doc.build([table])

    rows = load_pdf(buf.getvalue(), required_headers=["Артикул", "Название"])
    assert rows[0]["Артикул"] == "109775"
    assert rows[0]["Название"] == "Диван Атланта"
    assert rows[0]["Цена"] == "10000"
    assert len(rows) == 2


def test_pdf_parser_rejects_table_without_required_columns():
    import io
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    table = Table([["Код", "Описание"], ["1", "Test"]])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    doc.build([table])

    with pytest.raises(RuntimeError, match="required columns"):
        load_pdf(buf.getvalue(), required_headers=["Артикул", "Название"])
