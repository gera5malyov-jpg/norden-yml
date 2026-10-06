import json

import pytest

from supplier_engine.adapters import load_pdf, load_xml_yml
from supplier_engine.formulas import evaluate_formula
from supplier_engine.models import Product
from supplier_engine.runner import load_config, normalize, config_sha256, fetch_source, _characteristic_names_for_plan, _unique_image_aliases
from supplier_engine import norden
from supplier_engine.kit_sync import sync_manifest, _price_pair, _webasyst_category_paths, _variant_indexes, _select_variant, _kit_characteristics, _identity_preflight, SUPPLIER_ARTICLE_CHARACTERISTIC, LEGACY_CODE_SITE_CHARACTERISTIC
from supplier_engine.bridge import MegasuppliersBridge
from supplier_engine.validators import validate_run
from supplier_engine.webasyst_sync import apply_plan, build_plan, index_by_sku, index_by_supplier_sku_name, validate_apply_plan, webasyst_sku_mode


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


def test_normalize_accepts_php_empty_array_mappings():
    c = {
        "identity": {"supplier_sku_field": "Артикул", "sku_prefix": "", "brand": "Norden"},
        "mapping": {
            "name": "Наименование",
            "purchase_price": "Цена_Опт",
            "price": "Цена_РРЦ",
            "compare_price": "",
            "stock": "",
            "category": "",
            "images": [],
            "characteristics": [],
        },
        "rules": {"price_formulas": []},
    }
    rows = [{"Артикул": "H-051", "Наименование": "Кресло", "Цена_Опт": "11960", "Цена_РРЦ": "18600"}]
    p = normalize(c, rows)[0]
    assert p.sku == "H-051"
    assert p.purchase_price == 11960
    assert p.price == 18600
    assert p.characteristics == {}



def test_norden_dynamic_characteristics_are_normalized():
    c = {
        "identity": {"supplier_sku_field": "product_code", "sku_prefix": "", "brand": "Norden"},
        "mapping": {
            "name": "name",
            "purchase_price": "price",
            "price": "",
            "compare_price": "",
            "stock": "qty",
            "category": "category",
            "images": ["images"],
            "characteristics": {},
            "dynamic_characteristics": "features",
        },
        "rules": {"price_formulas": {}},
    }
    rows = [{
        "product_code": "H-051",
        "name": "Кресло",
        "price": "11960",
        "qty": "7",
        "category": "Norden > Кресла",
        "images": ["https://x/1.jpg"],
        "features": {"Цвет": "Черный", "Материал": "Сетка"},
    }]
    p = normalize(c, rows)[0]
    assert p.sku == "H-051"
    assert p.purchase_price == 11960
    assert p.stock == 7
    assert p.images == ["https://x/1.jpg"]
    assert p.characteristics == {"Цвет": "Черный", "Материал": "Сетка"}



def test_norden_missing_purchase_price_skips_price_formulas():
    c = {
        "source": {"format": "norden"},
        "identity": {"supplier_sku_field": "product_code", "sku_prefix": "", "brand": "Norden"},
        "mapping": {
            "name": "name",
            "purchase_price": "price",
            "price": "",
            "compare_price": "",
            "stock": "qty",
            "category": "category",
            "images": [],
            "characteristics": {},
        },
        "rules": {
            "price_formulas": {
                "price": "purchase_price * 1.25",
                "compare_price": "purchase_price * 1.60",
            }
        },
    }
    p = normalize(c, [{
        "product_code": "NO-PRICE",
        "name": "Без закупки",
        "price": "",
        "qty": "5",
        "category": "Norden",
    }])[0]
    assert p.purchase_price is None
    assert p.price is None
    assert p.compare_price is None
    assert p.stock == 5


def test_norden_positive_purchase_price_applies_price_formulas():
    c = {
        "source": {"format": "norden"},
        "identity": {"supplier_sku_field": "product_code", "sku_prefix": "", "brand": "Norden"},
        "mapping": {
            "name": "name",
            "purchase_price": "price",
            "price": "",
            "compare_price": "",
            "stock": "qty",
            "category": "category",
            "images": [],
            "characteristics": {},
        },
        "rules": {
            "price_formulas": {
                "price": "purchase_price * 1.25",
                "compare_price": "purchase_price * 1.60",
            }
        },
    }
    p = normalize(c, [{
        "product_code": "H-051",
        "name": "Кресло",
        "price": "10000",
        "qty": "5",
        "category": "Norden",
    }])[0]
    assert p.purchase_price == 10000
    assert p.price == 12500
    assert p.compare_price == 16000


def test_norden_source_falls_back_to_xml(monkeypatch):
    monkeypatch.setenv("NORDEN_SECRET", "configured")
    monkeypatch.setattr(norden, "_api_rows", lambda secret: (_ for _ in ()).throw(RuntimeError("api down")))
    fallback_rows = [{
        "product_code": "H-051",
        "name": "Кресло",
        "price": "11960",
        "qty": 3,
        "category": "Norden",
        "images": [],
        "features": {},
    }]
    monkeypatch.setattr(norden, "_fallback_rows", lambda: fallback_rows)
    rows, source_bytes, meta = norden.load_norden_source()
    assert rows == fallback_rows
    assert source_bytes == norden.canonical_bytes(fallback_rows)
    assert meta["origin"] == "xml_fallback"
    assert meta["fallback_used"] is True
    assert "api down" in meta["api_error"]


def test_norden_source_prefers_api(monkeypatch):
    monkeypatch.setenv("NORDEN_SECRET", "configured")
    api_rows = [{
        "product_code": "H-051",
        "name": "Кресло",
        "price": "11960",
        "qty": 5,
        "category": "Norden",
        "images": [],
        "features": {},
    }]
    monkeypatch.setattr(norden, "_api_rows", lambda secret: api_rows)
    monkeypatch.setattr(norden, "_fallback_rows", lambda: (_ for _ in ()).throw(AssertionError("fallback must not run")))
    rows, _, meta = norden.load_norden_source()
    assert rows == api_rows
    assert meta["origin"] == "api"
    assert meta["fallback_used"] is False

def test_formula_rejects_code_execution():
    with pytest.raises(ValueError):
        evaluate_formula("__import__('os').system('id')", {"supplier_price":100})


def test_yml_parser_preserves_vendor_code_pictures_and_available():
    data=b'''<?xml version="1.0" encoding="UTF-8"?><yml_catalog><shop><offers><offer id="7" available="true"><name>Sofa</name><vendorCode>109775</vendorCode><price>999</price><picture>https://x/1.jpg</picture><picture>https://x/2.jpg</picture></offer></offers></shop></yml_catalog>'''
    row=load_xml_yml(data)[0]
    assert row["vendorCode"]=="109775"
    assert row["available"]=="true"
    assert row["pictures"]==["https://x/1.jpg","https://x/2.jpg"]


def test_norden_xml_parser_extracts_prices_and_sums_free_stock():
    data = """<?xml version="1.0" encoding="UTF-8"?>
<Файл ТипФайла="Номенклатура">
  <Номенклатура>
    <Наименование>Кресло офисное</Наименование>
    <Артикул>H-051 black frame</Артикул>
    <Цена ВидЦен="РРЦ">18 600</Цена>
    <Цена ВидЦен="Опт">11 960</Цена>
    <СвободныйОстаток Склад="Основной склад">64</СвободныйОстаток>
    <СвободныйОстаток Склад="Питер Основной склад">3</СвободныйОстаток>
  </Номенклатура>
</Файл>""".encode("utf-8")
    row = load_xml_yml(data)[0]
    assert row["Артикул"] == "H-051 black frame"
    assert row["Наименование"] == "Кресло офисное"
    assert row["Цена_РРЦ"] == "18 600"
    assert row["Цена_Опт"] == "11 960"
    assert row["СвободныйОстаток_Основной склад"] == "64"
    assert row["СвободныйОстаток_Питер Основной склад"] == "3"
    assert row["СвободныйОстаток_Итого"] == 67.0


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



def test_index_by_sku_uses_full_sku_fields_and_type_filter():
    class Fake:
        def __init__(self):
            self.params = None
        def call(self, method, *, params=None, **kwargs):
            self.params = params
            return {"products": [{
                "id": 11,
                "name": "Chair",
                "skus": [{"id": 21, "sku": "H-051"}],
            }]}
    wa = Fake()
    indexed = index_by_sku(wa, 142)
    assert "H-051" in indexed
    assert wa.params["hash"] == "type/142"
    assert wa.params["fields"] == "*,skus,stock_counts"



def test_norden_index_matches_webasyst_sku_name_not_internal_af_sku():
    class Fake:
        def call(self, method, *, params=None, **kwargs):
            assert method == "shop.product.search"
            assert params["hash"] == "type/142"
            return {"products": [{
                "id": 11,
                "name": "Кресло",
                "skus": [{
                    "id": 21,
                    "sku": "AF-31662421",
                    "name": "CK-2518A-P",
                    "purchase_price": "9200",
                    "price": "11500",
                    "compare_price": "14720",
                }],
            }]}
    indexed = index_by_supplier_sku_name(Fake(), 142, ["CK-2518A-P", "OTHER"])
    assert list(indexed) == ["CK-2518A-P"]
    product, sku = indexed["CK-2518A-P"][0]
    assert product["id"] == 11
    assert sku["sku"] == "AF-31662421"


def test_norden_index_normalizes_supplier_article_without_guessing_ambiguity():
    class Fake:
        def call(self, method, *, params=None, **kwargs):
            return {"products": [{
                "id": 11,
                "name": "Chair",
                "skus": [{"id": 21, "sku": "AF-1", "name": "RT-2031"}],
            }]}
    indexed = index_by_supplier_sku_name(Fake(), 142, ["RT.2031"])
    assert "RT.2031" in indexed

    ambiguous = index_by_supplier_sku_name(Fake(), 142, ["RT.2031", "RT_2031"])
    assert ambiguous == {}


def test_norden_index_exact_match_wins_over_normalized_historical_collision():
    class Fake:
        def call(self, method, *, params=None, **kwargs):
            return {"products": [
                {
                    "id": 11,
                    "name": "Canonical",
                    "skus": [{"id": 21, "sku": "AF-1", "name": "HY-815C"}],
                },
                {
                    "id": 12,
                    "name": "Historical variant",
                    "skus": [{"id": 22, "sku": "AF-2", "name": "HY815C"}],
                },
            ]}
    indexed = index_by_supplier_sku_name(Fake(), 142, ["HY-815C"])
    assert len(indexed["HY-815C"]) == 1
    assert indexed["HY-815C"][0][0]["id"] == 11


def test_build_plan_accepts_old_af_links_when_cards_match_by_supplier_article():
    products = [
        Product("CK-2518A-P", "CK-2518A-P", "Chair", stock=39),
        Product("B1816", "B1816", "Chair 2", stock=4),
    ]
    existing = {
        "CK-2518A-P": [({"id": 11}, {"id": 21, "sku": "AF-1", "name": "CK-2518A-P"})],
        "B1816": [({"id": 12}, {"id": 22, "sku": "AF-2", "name": "B1816"})],
    }
    links = [
        {"supplier_sku": "AF-1", "product_id": 11, "sku_id": 21},
        {"supplier_sku": "AF-2", "product_id": 12, "sku_id": 22},
    ]
    plan = build_plan(products, existing, links, {
        "create_new": False,
        "update_stock": True,
        "zero_if_missing": True,
    })
    assert len(plan["update"]) == 2
    assert plan["zero"] == []
    assert plan["blocked"] == []


def test_build_plan_uses_supplier_link_when_webasyst_sku_differs():
    products = [Product("H-051", "H-051", "Chair", stock=4)]
    existing = {
        "AF-123": [({"id": 11, "name": "Chair"}, {"id": 21, "sku": "AF-123"})],
    }
    links = [{"supplier_sku": "H-051", "product_id": 11, "sku_id": 21}]
    plan = build_plan(products, existing, links, {
        "create_new": False,
        "update_stock": True,
        "zero_if_missing": True,
    })
    assert len(plan["update"]) == 1
    assert plan["update"][0]["product_id"] == 11
    assert plan["zero"] == []
    assert plan["blocked"] == []


def test_build_plan_blocks_mass_zero_when_supplier_links_do_not_overlap():
    products = [Product("H-051", "H-051", "Chair", stock=4)]
    existing = {}
    links = [
        {"supplier_sku": "AF-%d" % i, "product_id": i + 1, "sku_id": i + 101}
        for i in range(10)
    ]
    plan = build_plan(products, existing, links, {
        "create_new": False,
        "update_stock": True,
        "zero_if_missing": True,
    })
    assert plan["zero"] == []
    assert any(x.get("reason") == "supplier_link_overlap_too_low" for x in plan["blocked"])
    errors = validate_apply_plan(plan, {"rules": {"update_stock": True}, "webasyst": {"stock_id": 66}})
    assert any("обнуление" in x for x in errors)

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
    assert add_call["data"]["status"] == 1
    assert "[extimg]" in add_call["data"]["summary"]
    assert "images" not in add_call["data"]
    assert not any("image" in call["method"].lower() for call in wa.calls)
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
        ["SKU", "Name", "Price"],
        ["109775", "Sofa Atlanta", "10000"],
        ["109776", "Armchair", "5000"],
    ])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
    ]))
    doc.build([table])

    rows = load_pdf(buf.getvalue(), required_headers=["SKU", "Name"])
    assert rows[0]["SKU"] == "109775"
    assert rows[0]["Name"] == "Sofa Atlanta"
    assert rows[0]["Price"] == "10000"
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


def test_norden_index_exact_sku_fallback_recovers_engine_created_product():
    class Fake:
        def call(self, method, *, params=None, **kwargs):
            return {"products": [{
                "id": 31,
                "name": "Engine-created chair",
                "status": 0,
                "skus": [{"id": 41, "sku": "H-051", "name": ""}],
            }]}
    indexed = index_by_supplier_sku_name(Fake(), 142, ["H-051"])
    assert len(indexed["H-051"]) == 1
    assert indexed["H-051"][0][0]["id"] == 31


def test_apply_plan_publishes_only_hidden_engine_created_match():
    wa = _FakeWebasyst()
    desired = {
        "supplier_sku": "H-051",
        "sku": "H-051",
        "name": "Chair",
        "purchase_price": 100,
        "price": 125,
        "compare_price": 160,
        "stock": 3,
        "brand": "",
        "category": "",
        "images": [],
        "characteristics": {},
    }
    plan = {
        "create": [],
        "update": [{
            "sku": "H-051",
            "supplier_sku": "H-051",
            "product_id": 31,
            "sku_id": 41,
            "current_product": {"id": 31, "status": 0},
            "current_sku": {"id": 41, "sku": "H-051", "name": ""},
            "desired": desired,
        }],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    apply_plan(
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
            "webasyst": {"stock_id": 1, "type_id": 142},
        },
    )
    product_call = next(x for x in wa.calls if x["method"] == "shop.product.update")
    assert product_call["data"]["status"] == 1


def test_norden_create_uses_supplier_code_as_sku_name_and_numeric_sku():
    wa = _FakeWebasyst()
    desired = {
        "supplier_sku": "NS01025-03-01",
        "sku": "NS01025-03-01",
        "name": "Chair",
        "purchase_price": 100,
        "price": 125,
        "compare_price": 160,
        "stock": 3,
        "brand": "Norden",
        "category": "",
        "images": [],
        "characteristics": {},
    }
    plan = {
        "create": [{"sku": "NS01025-03-01", "supplier_sku": "NS01025-03-01", "desired": desired}],
        "update": [],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    apply_plan(
        wa,
        plan,
        {
            "source": {"format": "norden"},
            "rules": {
                "update_prices": True,
                "update_stock": True,
                "update_images": False,
                "update_characteristics": False,
            },
            "webasyst": {"stock_id": 1, "type_id": 142},
        },
    )
    sku_calls = [x for x in wa.calls if x["method"] == "shop.product.skus.update"]
    assert sku_calls[-1]["params"]["id"] == "202"
    assert sku_calls[-1]["data"]["sku"] == "202"
    assert sku_calls[-1]["data"]["name"] == "NS01025-03-01"


def test_norden_partial_old_apply_migrates_supplier_code_out_of_sku_field():
    wa = _FakeWebasyst()
    desired = {
        "supplier_sku": "NS01025-03-01",
        "sku": "NS01025-03-01",
        "name": "Chair",
        "purchase_price": 100,
        "price": 125,
        "compare_price": 160,
        "stock": 3,
        "brand": "Norden",
        "category": "",
        "images": [],
        "characteristics": {},
    }
    plan = {
        "create": [],
        "update": [{
            "sku": "NS01025-03-01",
            "supplier_sku": "NS01025-03-01",
            "product_id": 31,
            "sku_id": 41,
            "current_product": {"id": 31, "status": 0},
            "current_sku": {"id": 41, "sku": "NS01025-03-01", "name": ""},
            "desired": desired,
        }],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    apply_plan(
        wa,
        plan,
        {
            "source": {"format": "norden"},
            "rules": {
                "update_prices": True,
                "update_stock": True,
                "update_name": False,
                "update_images": False,
                "update_characteristics": False,
            },
            "webasyst": {"stock_id": 1, "type_id": 142},
        },
    )
    sku_call = next(x for x in wa.calls if x["method"] == "shop.product.skus.update")
    assert sku_call["data"]["sku"] == "41"
    assert sku_call["data"]["name"] == "NS01025-03-01"
    product_call = next(x for x in wa.calls if x["method"] == "shop.product.update")
    assert product_call["data"]["status"] == 1


def test_norden_existing_internal_article_is_preserved_but_supplier_name_is_enforced():
    wa = _FakeWebasyst()
    desired = {
        "supplier_sku": "NS01025-03-01",
        "sku": "NS01025-03-01",
        "name": "Chair",
        "purchase_price": 100,
        "price": 125,
        "compare_price": 160,
        "stock": 3,
        "brand": "Norden",
        "category": "",
        "images": [],
        "characteristics": {},
    }
    plan = {
        "create": [],
        "update": [{
            "sku": "NS01025-03-01",
            "supplier_sku": "NS01025-03-01",
            "product_id": 31,
            "sku_id": 41,
            "current_product": {"id": 31, "status": 1},
            "current_sku": {"id": 41, "sku": "AF-31662421", "name": ""},
            "desired": desired,
        }],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    apply_plan(
        wa,
        plan,
        {
            "source": {"format": "norden"},
            "rules": {
                "update_prices": True,
                "update_stock": True,
                "update_name": False,
                "update_images": False,
                "update_characteristics": False,
            },
            "webasyst": {"stock_id": 1, "type_id": 142},
        },
    )
    sku_call = next(x for x in wa.calls if x["method"] == "shop.product.skus.update")
    assert sku_call["data"]["name"] == "NS01025-03-01"
    assert "sku" not in sku_call["data"]



def test_per_supplier_sku_mode_defaults_are_backward_compatible():
    assert webasyst_sku_mode({"source": {"format": "norden"}, "webasyst": {}}) == "numeric"
    assert webasyst_sku_mode({"source": {"format": "yml"}, "webasyst": {}}) == "supplier"
    assert webasyst_sku_mode({"source": {"format": "norden"}, "webasyst": {"sku_mode": "supplier"}}) == "supplier"


def test_supplier_article_mode_uses_prefixed_article_and_supplier_name():
    wa = _FakeWebasyst()
    desired = {
        "supplier_sku": "109775",
        "sku": "Liga-109775",
        "name": "Sofa",
        "purchase_price": 100,
        "price": 125,
        "compare_price": 160,
        "stock": 2,
        "brand": "Liga",
        "category": "",
        "images": [],
        "characteristics": {},
    }
    plan = {
        "create": [{"sku": "Liga-109775", "supplier_sku": "109775", "desired": desired}],
        "update": [],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    apply_plan(
        wa,
        plan,
        {
            "source": {"format": "yml"},
            "rules": {"update_prices": True, "update_stock": True},
            "webasyst": {"stock_id": 1, "type_id": 5, "sku_mode": "supplier"},
        },
    )
    sku_calls = [x for x in wa.calls if x["method"] == "shop.product.skus.update"]
    assert sku_calls[-1]["data"]["sku"] == "Liga-109775"
    assert sku_calls[-1]["data"]["name"] == "109775"


def test_switch_numeric_managed_article_to_supplier_article():
    wa = _FakeWebasyst()
    desired = {
        "supplier_sku": "NS01025-03-01",
        "sku": "NS01025-03-01",
        "name": "Chair",
        "purchase_price": 100,
        "price": 125,
        "compare_price": 160,
        "stock": 3,
        "brand": "Norden",
        "category": "",
        "images": [],
        "characteristics": {},
    }
    plan = {
        "create": [],
        "update": [{
            "sku": "NS01025-03-01",
            "supplier_sku": "NS01025-03-01",
            "product_id": 31,
            "sku_id": 41,
            "current_product": {"id": 31, "status": 1},
            "current_sku": {"id": 41, "sku": "41", "name": "NS01025-03-01"},
            "desired": desired,
        }],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    apply_plan(
        wa,
        plan,
        {
            "source": {"format": "norden"},
            "rules": {"update_prices": True, "update_stock": True},
            "webasyst": {"stock_id": 1, "type_id": 142, "sku_mode": "supplier"},
        },
    )
    sku_call = next(x for x in wa.calls if x["method"] == "shop.product.skus.update")
    assert sku_call["data"]["sku"] == "NS01025-03-01"


def test_index_matches_prefixed_supplier_article_alias():
    class Fake:
        def call(self, method, *, params=None, **kwargs):
            return {"products": [{
                "id": 11,
                "name": "Sofa",
                "skus": [{"id": 21, "sku": "Liga-109775", "name": ""}],
            }]}
    indexed = index_by_supplier_sku_name(
        Fake(),
        5,
        ["109775"],
        {"Liga-109775": "109775"},
    )
    assert "109775" in indexed
    assert indexed["109775"][0][0]["id"] == 11



def test_zero_other_stocks_writes_explicit_zero_rows():
    wa = _FakeWebasyst()
    plan = {
        "create": [],
        "update": [{
            "sku": "X-1",
            "supplier_sku": "X-1",
            "product_id": 11,
            "sku_id": 21,
            "current_product": {"id": 11, "status": 1},
            "current_sku": {"id": 21, "sku": "21", "name": "X-1"},
            "desired": {
                "supplier_sku": "X-1",
                "sku": "X-1",
                "name": "Chair",
                "purchase_price": 100,
                "price": 125,
                "compare_price": 160,
                "stock": 2,
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
    apply_plan(
        wa,
        plan,
        {
            "source": {"format": "norden"},
            "rules": {
                "update_prices": True,
                "update_stock": True,
                "zero_other_stocks": True,
            },
            "webasyst": {
                "stock_id": 66,
                "stock_ids": [6, 14, 66, 72],
                "type_id": 142,
                "sku_mode": "numeric",
            },
        },
    )
    sku_call = next(x for x in wa.calls if x["method"] == "shop.product.skus.update")
    assert sku_call["data"]["stock"] == {"6": "0", "14": "0", "66": "2", "72": "0"}


def test_zero_other_stocks_requires_stock_list():
    plan = {
        "create": [],
        "update": [{
            "sku": "X-1",
            "desired": {"stock": 2, "characteristics": {}},
        }],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    errors = validate_apply_plan(
        plan,
        {
            "rules": {"update_stock": True, "zero_other_stocks": True},
            "webasyst": {"stock_id": 66, "stock_ids": []},
        },
    )
    assert any("stock_ids" in x for x in errors)



def test_norden_fallback_extracts_commercial_characteristics(monkeypatch):
    price_xml = """<?xml version="1.0" encoding="utf-8"?>
    <Данные>
      <Номенклатура>
        <Артикул>PR.243.WH.O59.BL</Артикул>
        <Цена ВидЦен="Опт">23 420</Цена>
        <СвободныйОстаток Склад="Основной склад">2</СвободныйОстаток>
        <Акция>Акция</Акция>
      </Номенклатура>
    </Данные>""".encode("utf-8")
    full_xml = """<?xml version="1.0" encoding="utf-8"?>
    <Данные>
      <Номенклатура>
        <Код>00-00019020</Код>
        <Артикул>PR.243.WH.O59.BL</Артикул>
        <Наименование>Рабочая станция</Наименование>
        <НаименованиеПолное>PR.243.WH.O59.BL / Рабочая станция</НаименованиеПолное>
        <Группа>Офисная мебель///Призма / Prizma</Группа>
        <ВидНоменклатуры>Корпусная мебель Наборы</ВидНоменклатуры>
        <ТипНоменклатуры>Набор</ТипНоменклатуры>
        <Серия>Prizma</Серия>
        <ВесЧислитель>59</ВесЧислитель>
        <ВесЗнаменатель>1</ВесЗнаменатель>
        <ОбъемЧислитель>0,237</ОбъемЧислитель>
        <ОбъемЗнаменатель>1</ОбъемЗнаменатель>
        <НаборУпаковок>
          <Упаковка/><Упаковка/><Упаковка/><Упаковка/><Упаковка/>
        </НаборУпаковок>
      </Номенклатура>
    </Данные>""".encode("utf-8")

    def fake_http(url, **kwargs):
        return price_xml if "K8%25" in url else full_xml

    monkeypatch.setattr(norden, "_http_bytes", fake_http)
    row = norden._fallback_rows()[0]
    f = row["features"]
    assert f["Вид номенклатуры"] == "Корпусная мебель Наборы"
    assert f["Тип номенклатуры"] == "Набор"
    assert f["Серия"] == "Prizma"
    assert f["Акция"] == "Да"
    assert f["Вес, кг"] == "59"
    assert f["Объем,м3"] == "0.237"
    assert f["Количество мест"] == "5"


def test_auto_features_does_not_require_manual_feature_codes():
    plan = {
        "create": [],
        "update": [{
            "sku": "X",
            "desired": {"stock": None, "characteristics": {"Серия": "Prizma"}},
        }],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    errors = validate_apply_plan(
        plan,
        {
            "rules": {
                "update_stock": False,
                "update_characteristics": True,
                "auto_features": True,
            },
            "webasyst": {"type_id": 142, "feature_codes": {}},
        },
    )
    assert not any("коды характеристик" in x for x in errors)


def test_characteristic_names_include_create_and_enabled_updates():
    plan = {
        "create": [{"desired": {"characteristics": {"Серия": "Prizma"}}}],
        "update": [{"desired": {"characteristics": {"Вес, кг": "59"}}}],
    }
    names = _characteristic_names_for_plan(plan, {"update_characteristics": True})
    assert names == ["Вес, кг", "Серия"]


def test_bridge_ensure_features_returns_mapping_without_duplicates():
    bridge = MegasuppliersBridge(url="https://example.invalid/bridge", secret="x")
    calls = []
    def fake_call(payload):
        calls.append(payload)
        return {"mapping": {"Серия": "ms_s1_a"}}
    bridge.call = fake_call
    mapping = bridge.ensure_features(1, "req", 142, ["Серия", "Серия"])
    assert mapping == {"Серия": "ms_s1_a"}
    assert calls[0]["action"] == "ensure_features"
    assert calls[0]["names"] == ["Серия"]



def test_interrupted_af_create_recovers_by_unique_summary_image():
    class Fake:
        def call(self, method, *, params=None, **kwargs):
            return {"products": [{
                "id": 1487358,
                "name": "Desk",
                "summary": "[extimg]\nhttps://norden.group/images/detailed/432/item.jpg\n[/extimg]",
                "skus": [{"id": 31666496, "sku": "AF-31666496", "name": ""}],
            }]}
    indexed = index_by_supplier_sku_name(
        Fake(),
        142,
        ["AG.193.WH.L.72.AN"],
        {"AG.193.WH.L.72.AN": "AG.193.WH.L.72.AN"},
        {"https://norden.group/images/detailed/432/item.jpg": "AG.193.WH.L.72.AN"},
    )
    assert list(indexed) == ["AG.193.WH.L.72.AN"]
    product, sku = indexed["AG.193.WH.L.72.AN"][0]
    assert product["id"] == 1487358
    assert sku["sku"] == "AF-31666496"


def test_unique_image_aliases_drop_shared_images():
    products = [
        Product("A", "A", "One", images=["https://x/1.jpg", "https://x/shared.jpg"]),
        Product("B", "B", "Two", images=["https://x/2.jpg", "https://x/shared.jpg"]),
    ]
    aliases = _unique_image_aliases(products)
    assert aliases["https://x/1.jpg"] == "A"
    assert aliases["https://x/2.jpg"] == "B"
    assert "https://x/shared.jpg" not in aliases


def test_create_starts_with_supplier_identity_before_numeric_finalize():
    wa = _FakeWebasyst()
    desired = {
        "supplier_sku": "NS01025-03-01",
        "sku": "NS01025-03-01",
        "name": "Chair",
        "purchase_price": 100,
        "price": 125,
        "compare_price": 160,
        "stock": 3,
        "brand": "Norden",
        "category": "",
        "images": ["https://x/1.jpg"],
        "characteristics": {},
    }
    plan = {
        "create": [{"sku": "NS01025-03-01", "supplier_sku": "NS01025-03-01", "desired": desired}],
        "update": [],
        "zero": [],
        "blocked": [],
        "skipped": [],
    }
    apply_plan(
        wa,
        plan,
        {
            "source": {"format": "norden"},
            "rules": {"update_prices": True, "update_stock": True},
            "webasyst": {"stock_id": 66, "type_id": 142, "sku_mode": "numeric"},
        },
    )
    add_call = next(x for x in wa.calls if x["method"] == "shop.product.add")
    initial_sku = add_call["data"]["skus"][0]
    assert initial_sku["sku"] == "NS01025-03-01"
    assert initial_sku["name"] == "NS01025-03-01"
    final_call = [x for x in wa.calls if x["method"] == "shop.product.skus.update"][-1]
    assert final_call["data"]["sku"] == "202"
    assert final_call["data"]["name"] == "NS01025-03-01"



class _FakeKitForSupplierExport:
    def __init__(self):
        self.created_categories = []
        self.created_products = []
        self.created_variants = []
        self.patched_products = []
        self.patched_variants = []
        self.files = {}
        self._cat_seq = 10
        self._char_seq = 20

    def warehouses(self):
        return [
            {"id": "w-msk", "title": "МСК"},
            {"id": "w-spb", "title": "СПБ привозной"},
        ]

    def categories(self):
        return []

    def characteristics(self):
        return []

    def variants(self):
        return []

    def create_category(self, title, parent_id=None):
        self._cat_seq += 1
        row = {"id": "c%d" % self._cat_seq, "title": title, "parent_id": parent_id}
        self.created_categories.append(row)
        return row

    def create_characteristic(self, title):
        self._char_seq += 1
        return {"id": "f%d" % self._char_seq, "title": title}

    def create_product(self, category_ids):
        row = {"id": "p-kit", "category_ids": list(category_ids)}
        self.created_products.append(row)
        return row

    def patch_product(self, product_id, category_ids):
        self.patched_products.append((product_id, list(category_ids)))
        return {}

    def create_variant(self, body):
        self.created_variants.append(dict(body))
        return {"id": "v-kit", "product_id": body["product_id"]}

    def patch_variant(self, variant_id, body):
        self.patched_variants.append((variant_id, dict(body)))
        return {}

    def get_variant(self, variant_id):
        media = []
        for fid in sorted(self.files):
            media.append({"type": "IMAGE", "display_sequence": len(media), "image_id": fid})
        return {
            "id": variant_id,
            "product_id": "p-kit",
            "sku": "12345",
            "kit_id": "987654",
            "media": media,
        }

    def upload_image_url(self, url):
        fid = "img%d" % (len(self.files) + 1)
        self.files[fid] = url
        return {"id": fid}

    def file_url(self, file_id):
        return "https://kit.example/%s.jpg" % file_id


class _FakeWaForKit:
    def __init__(self):
        self.calls = []

    def call(self, method, *, http_method="GET", params=None, data=None):
        self.calls.append({
            "method": method,
            "http_method": http_method,
            "params": params,
            "data": data,
        })
        return {}


def test_kit_export_uses_webasyst_categories_and_user_prices():
    kit = _FakeKitForSupplierExport()
    wa = _FakeWaForKit()
    manifest = {
        "categories": [
            {"id": 1, "name": "Мебель", "parent_id": 0},
            {"id": 2, "name": "Кресла", "parent_id": 1},
        ],
        "items": [
            {
                "supplier_sku": "NS-1",
                "product_id": 101,
                "sku_id": 201,
                "sku": "12345",
                "name": "Кресло",
                "description": "Описание",
                "status": 1,
                "purchase_price": 1000,
                "stock": 7,
                "category_ids": [2],
                "features": [
                    {"code": "series", "name": "Серия", "values": ["Prizma"]},
                    {"code": "kit_id", "name": "KIT ID", "values": []},
                ],
                "image_urls": ["https://supplier.example/1.jpg"],
            },
            {
                "supplier_sku": "NS-2",
                "product_id": 102,
                "sku_id": 202,
                "sku": "12346",
                "name": "Без категории",
                "purchase_price": 500,
                "stock": 2,
                "category_ids": [],
                "features": [],
                "image_urls": [],
            },
        ],
    }
    report = sync_manifest(
        manifest,
        {
            "identity": {"brand": "Norden"},
            "rules": {"export_to_kit": True},
        },
        kit=kit,
        wa=wa,
    )
    assert report["status"] == "ok"
    assert report["eligible"] == 1
    assert report["skipped_no_category"] == 1
    assert report["created"] == 1
    assert [x["title"] for x in kit.created_categories] == ["Мебель", "Кресла"]
    assert kit.created_products[0]["category_ids"] == ["c12"]
    body = kit.created_variants[0]
    assert body["pricing"] == {"price": "1600.00", "manual_discount_price": "1250.00"}
    assert body["stocks"] == [
        {"warehouse_id": "w-msk", "quantity": 7, "reserved": 0},
        {"warehouse_id": "w-spb", "quantity": 7, "reserved": 0},
    ]
    supplier_chars = [
        x for x in body["characteristics"]
        if x["values"] == ["NS-1"]
    ]
    assert len(supplier_chars) == 1
    updates = [x for x in wa.calls if x["method"] == "shop.product.update"]
    kit_id_update = next(x for x in updates if "features" in (x["data"] or {}))
    summary_update = next(x for x in updates if "summary" in (x["data"] or {}))
    assert kit_id_update["data"]["features"]["kit_id"] == "987654"
    assert "https://kit.example/img1.jpg" in summary_update["data"]["summary"]


def test_kit_export_preserves_multiple_webasyst_categories():
    paths = _webasyst_category_paths(
        [
            {"id": 1, "name": "Мебель", "parent_id": 0},
            {"id": 2, "name": "Офис", "parent_id": 1},
            {"id": 3, "name": "Распродажа", "parent_id": 0},
        ],
        [2, 3],
    )
    assert paths == [["Мебель", "Офис"], ["Распродажа"]]


def test_kit_prices_follow_current_25_60_rule():
    assert _price_pair(1000) == {
        "price": "1600.00",
        "manual_discount_price": "1250.00",
    }



def _kit_variant(variant_id, kit_id, sku, brand, supplier_char_id, supplier_article):
    characteristics = []
    if supplier_article is not None:
        characteristics.append({
            "characteristic_id": supplier_char_id,
            "value": supplier_article,
            "values": [supplier_article],
        })
    return {
        "id": variant_id,
        "kit_id": kit_id,
        "sku": sku,
        "brand": brand,
        "product_id": "p-" + variant_id,
        "characteristics": characteristics,
    }


def test_kit_first_match_requires_sku_supplier_article_and_brand():
    supplier_char_id = "f-supplier"
    variants = [
        _kit_variant("v1", "7001", "12345", "Norden", supplier_char_id, "NS-1"),
        _kit_variant("v2", "7002", "12346", "Norden", supplier_char_id, "NS-2"),
    ]
    by_sku, by_kit_id, by_supplier_brand = _variant_indexes(variants, [supplier_char_id])
    product = {
        "sku": "12345",
        "supplier_sku": "NS-1",
        "features": [],
    }
    found = _select_variant(
        product,
        by_sku,
        by_kit_id,
        by_supplier_brand,
        [supplier_char_id],
        "Norden",
    )
    assert found["id"] == "v1"


def test_kit_unique_same_sku_and_brand_accepts_stale_supplier_article():
    supplier_char_id = "f-supplier"
    variants = [
        _kit_variant("v1", "7001", "12345", "Norden", supplier_char_id, "OTHER"),
    ]
    by_sku, by_kit_id, by_supplier_brand = _variant_indexes(variants, [supplier_char_id])
    found = _select_variant(
        {"sku": "12345", "supplier_sku": "NS-1", "features": []},
        by_sku,
        by_kit_id,
        by_supplier_brand,
        [supplier_char_id],
        "Norden",
    )
    assert found["id"] == "v1"


def test_kit_duplicate_same_sku_same_brand_stays_blocked():
    variants = [
        {"id": "v1", "kit_id": "7001", "sku": "12345", "brand": "Norden", "product_id": "p1", "characteristics": []},
        {"id": "v2", "kit_id": "7002", "sku": "12345", "brand": "Norden", "product_id": "p2", "characteristics": []},
    ]
    by_sku, by_kit_id, by_supplier_brand = _variant_indexes(variants, [])
    with pytest.raises(Exception) as exc:
        _select_variant(
            {"sku": "12345", "supplier_sku": "NS-1", "features": []},
            by_sku,
            by_kit_id,
            by_supplier_brand,
            [],
            "Norden",
        )
    assert "ambiguous" in str(exc.value)


def test_kit_same_supplier_article_and_brand_under_other_sku_is_blocked():
    supplier_char_id = "f-supplier"
    variants = [
        _kit_variant("v1", "7001", "99999", "Norden", supplier_char_id, "NS-1"),
    ]
    by_sku, by_kit_id, by_supplier_brand = _variant_indexes(variants, [supplier_char_id])
    with pytest.raises(Exception) as exc:
        _select_variant(
            {"sku": "12345", "supplier_sku": "NS-1", "features": []},
            by_sku,
            by_kit_id,
            by_supplier_brand,
            [supplier_char_id],
            "Norden",
        )
    assert "under another SKU" in str(exc.value)


def test_kit_id_is_authoritative_and_missing_id_never_creates_duplicate():
    supplier_char_id = "f-supplier"
    by_sku, by_kit_id, by_supplier_brand = _variant_indexes([], [supplier_char_id])
    with pytest.raises(Exception) as exc:
        _select_variant(
            {
                "sku": "12345",
                "supplier_sku": "NS-1",
                "features": [{"code": "kit_id", "name": "KIT ID", "values": ["7001"]}],
            },
            by_sku,
            by_kit_id,
            by_supplier_brand,
            [supplier_char_id],
            "Norden",
        )
    assert "stored in Webasyst but was not found in KIT" in str(exc.value)


def test_kit_id_match_rejects_conflicting_supplier_article():
    supplier_char_id = "f-supplier"
    variants = [
        _kit_variant("v1", "7001", "12345", "Norden", supplier_char_id, "OTHER"),
    ]
    by_sku, by_kit_id, by_supplier_brand = _variant_indexes(variants, [supplier_char_id])
    with pytest.raises(Exception) as exc:
        _select_variant(
            {
                "sku": "12345",
                "supplier_sku": "NS-1",
                "features": [{"code": "kit_id", "name": "KIT ID", "values": ["7001"]}],
            },
            by_sku,
            by_kit_id,
            by_supplier_brand,
            [supplier_char_id],
            "Norden",
        )
    assert "supplier article conflict" in str(exc.value)


def test_supplier_article_characteristic_title_is_fixed():
    assert SUPPLIER_ARTICLE_CHARACTERISTIC == "Артикул поставщика"



def test_kit_legacy_code_for_site_matches_existing_norden_card():
    legacy_id = "f-code-site"
    variants = [{
        "id": "v-legacy",
        "kit_id": "1500515",
        "sku": "AF-31646769",
        "brand": "Norden",
        "product_id": "p-legacy",
        "characteristics": [{
            "characteristic_id": legacy_id,
            "value": "RT-2031",
            "values": ["RT-2031"],
        }],
    }]
    by_sku, by_kit_id, by_supplier_brand = _variant_indexes(
        variants,
        ["f-new-supplier", legacy_id],
    )
    found = _select_variant(
        {
            "sku": "AF-31646769",
            "supplier_sku": "RT-2031",
            "features": [],
        },
        by_sku,
        by_kit_id,
        by_supplier_brand,
        ["f-new-supplier", legacy_id],
        "Norden",
    )
    assert found["id"] == "v-legacy"


def test_kit_conflicting_identity_blocks_only_when_that_variant_is_selected():
    variant = {
        "id": "v-conflict",
        "kit_id": "7001",
        "sku": "12345",
        "brand": "Norden",
        "product_id": "p1",
        "characteristics": [
            {
                "characteristic_id": "f-new",
                "value": "NS-1",
                "values": ["NS-1"],
            },
            {
                "characteristic_id": "f-code-site",
                "value": "OTHER",
                "values": ["OTHER"],
            },
        ],
    }
    by_sku, by_kit_id, by_supplier_brand = _variant_indexes(
        [variant],
        ["f-new", "f-code-site"],
    )
    with pytest.raises(Exception) as exc:
        _select_variant(
            {"sku": "12345", "supplier_sku": "NS-1", "features": []},
            by_sku,
            by_kit_id,
            by_supplier_brand,
            ["f-new", "f-code-site"],
            "Norden",
        )
    assert "conflicting supplier articles" in str(exc.value)


def test_kit_characteristics_write_new_and_legacy_supplier_identity():
    class FakeKit:
        def __init__(self):
            self._seq = 0

        def create_characteristic(self, title):
            self._seq += 1
            return {"id": "auto-%d" % self._seq, "title": title}

    rows = []
    from collections import defaultdict
    index = defaultdict(list)
    result = _kit_characteristics(
        FakeKit(),
        [{"code": "series", "name": "Серия", "values": ["Prizma"]}],
        rows,
        index,
        supplier_article_id="f-new",
        legacy_code_site_id="f-code-site",
        supplier_sku="RT-2031",
    )
    identity_rows = [
        row for row in result
        if row["characteristic_id"] in {"f-new", "f-code-site"}
    ]
    assert {row["characteristic_id"] for row in identity_rows} == {
        "f-new",
        "f-code-site",
    }
    assert all(row["values"] == ["RT-2031"] for row in identity_rows)


def test_legacy_code_site_characteristic_title_is_fixed():
    assert LEGACY_CODE_SITE_CHARACTERISTIC == "Код для сайта"



def test_kit_preflight_blocks_all_writes_on_any_identity_conflict():
    class ConflictKit(_FakeKitForSupplierExport):
        def variants(self):
            return [
                {
                    "id": "v-existing-1",
                    "kit_id": "7001",
                    "sku": "12345",
                    "brand": "Norden",
                    "product_id": "p-existing-1",
                    "characteristics": [],
                },
                {
                    "id": "v-existing-2",
                    "kit_id": "7002",
                    "sku": "12345",
                    "brand": "Norden",
                    "product_id": "p-existing-2",
                    "characteristics": [],
                },
            ]

    kit = ConflictKit()
    wa = _FakeWaForKit()
    manifest = {
        "categories": [{"id": 2, "name": "Кресла", "parent_id": 0}],
        "items": [{
            "supplier_sku": "NS-1",
            "product_id": 101,
            "sku_id": 201,
            "sku": "12345",
            "name": "Кресло",
            "description": "",
            "status": 1,
            "purchase_price": 1000,
            "stock": 3,
            "category_ids": [2],
            "features": [],
            "image_urls": [],
        }],
    }
    report = sync_manifest(
        manifest,
        {
            "identity": {"brand": "Norden"},
            "rules": {"export_to_kit": True},
        },
        kit=kit,
        wa=wa,
    )
    assert report["status"] == "blocked"
    assert report["preflight"]["conflicts"] == 1
    assert not kit.created_categories
    assert not kit.created_products
    assert not kit.created_variants
    assert not kit.patched_products
    assert not kit.patched_variants
    assert wa.calls == []


def test_kit_preflight_accepts_authoritative_webasyst_kit_id_without_old_identity_fields():
    product = {
        "supplier_sku": "RT-2031",
        "product_id": 1467631,
        "sku_id": 31646769,
        "sku": "AF-31646769",
        "features": [{
            "code": "kit_id",
            "name": "KIT ID",
            "values": ["1597882"],
        }],
        "category_ids": [1],
    }
    variants = [{
        "id": "01a0ed0c-9f29-7797-ad47-586d09b6770e",
        "kit_id": "1597882",
        "sku": "AF-31646769",
        "brand": "Norden",
        "product_id": "p-current",
        "characteristics": [],
    }]
    report, identity = _identity_preflight(
        [product],
        variants,
        {},
        "Norden",
    )
    assert report["status"] == "ok"
    assert report["matched_by_kit_id"] == 1
    assert report["conflicts"] == 0
    assert identity["selected"]["1467631"]["kit_id"] == "1597882"

def test_webasyst_delta_skips_equal_price_stock_and_availability():
    from supplier_engine.webasyst_sync import _delta_sku_data

    desired = {
        "purchase_price": "100",
        "price": "125",
        "compare_price": "160",
        "stock": {"1": "3"},
        "available": 1,
    }
    current = {
        "purchase_price": "100.0000",
        "price": "125.00",
        "compare_price": "160",
        "stock_counts": {"1": "3.0"},
        "available": "1",
    }
    assert _delta_sku_data(desired, current, 1) == {}


def test_webasyst_delta_keeps_real_stock_change():
    from supplier_engine.webasyst_sync import _delta_sku_data

    desired = {"stock": {"1": "4"}, "available": 1}
    current = {"stock_counts": {"1": "3"}, "available": "1"}
    delta = _delta_sku_data(desired, current, 1)
    assert delta["stock"] == {"1": "4"}
    assert "available" not in delta


def test_kit_variant_delta_skips_equal_payload_and_keeps_price_change():
    from supplier_engine.kit_sync import _variant_delta

    current = {
        "sku": "AF-1",
        "name": "Chair",
        "description": "",
        "brand": "Norden",
        "status": "PUBLISHED",
        "stocks": [
            {"warehouse_id": "10", "quantity": 3, "reserved": 0},
            {"warehouse_id": "20", "quantity": 3, "reserved": 0},
        ],
        "characteristics": [
            {"characteristic_id": "7", "value": "ABC", "values": ["ABC"]},
        ],
        "pricing": {"price": "160.00", "manual_discount_price": "125"},
    }
    desired = {
        "sku": "AF-1",
        "name": "Chair",
        "description": "",
        "brand": "Norden",
        "status": "PUBLISHED",
        "stocks": [
            {"warehouse_id": "20", "quantity": 3, "reserved": 0},
            {"warehouse_id": "10", "quantity": 3, "reserved": 0},
        ],
        "characteristics": [
            {"characteristic_id": "7", "value": "ABC", "values": ["ABC"]},
        ],
        "pricing": {"price": "160", "manual_discount_price": "125.00"},
    }
    assert _variant_delta(current, desired) == {}

    desired["pricing"] = {"price": "161", "manual_discount_price": "125"}
    assert _variant_delta(current, desired) == {"pricing": desired["pricing"]}

def test_missing_webasyst_product_404_is_recoverable():
    from supplier_engine.webasyst_sync import _is_missing_webasyst_object_error

    exc = RuntimeError(
        'HTTP 404: {"error": "invalid_param", "error_description": "Товар не найден."}'
    )
    assert _is_missing_webasyst_object_error(exc)


def test_apply_plan_recreates_product_when_product_update_returns_404():
    class MissingProductWa:
        def __init__(self):
            self.calls = []
            self.product_update_failed = False

        def call(self, method, *, http_method="GET", params=None, data=None, files=None):
            self.calls.append({
                "method": method,
                "http_method": http_method,
                "params": params or {},
                "data": data or {},
            })
            if method == "shop.product.skus.update":
                return {}
            if method == "shop.product.update" and not self.product_update_failed:
                self.product_update_failed = True
                raise RuntimeError(
                    'HTTP 404: {"error": "invalid_param", "error_description": "Товар не найден."}'
                )
            if method == "shop.product.search":
                return {"products": []}
            if method == "shop.product.getInfo":
                raise RuntimeError(
                    'HTTP 404: {"error": "invalid_param", "error_description": "Товар не найден."}'
                )
            if method == "shop.product.add":
                return {"id": "101"}
            if method == "shop.product.skus.getList":
                return {"skus": [{"id": "202", "sku": "X-1", "name": "1"}]}
            return {}

    wa = MissingProductWa()
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
        "images": [],
        "characteristics": {},
    }
    plan = {
        "create": [],
        "update": [{
            "sku": "X-1",
            "supplier_sku": "1",
            "product_id": 11,
            "sku_id": 21,
            "current_product": {"id": 11, "name": "Old", "status": 1},
            "current_sku": {"id": 21, "sku": "X-1", "name": "1"},
            "desired": desired,
        }],
        "repair_sku": [],
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
                "update_name": True,
                "update_images": False,
                "update_characteristics": False,
            },
            "webasyst": {
                "stock_id": 1,
                "type_id": 5,
                "sku_mode": "supplier",
            },
        },
    )

    assert result["recreated_products"] == 1
    assert result["mappings"][-1]["product_id"] == 101
    assert result["mappings"][-1]["sku_id"] == 202

