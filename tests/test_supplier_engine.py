from supplier_engine.models import Product
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
