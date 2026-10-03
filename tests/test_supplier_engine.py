import json

import pytest

from supplier_engine.adapters import load_xml_yml
from supplier_engine.formulas import evaluate_formula
from supplier_engine.models import Product
from supplier_engine.runner import load_config, normalize, config_sha256
from supplier_engine.validators import validate_run


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
