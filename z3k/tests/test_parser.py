import os,sys,xml.etree.ElementTree as ET
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
import parser

P="https://z3k.ru/shop/podem-i-peremeshhenie-gruza/tali-ruchnye-zitrek/tal-ruchnaya-shesterennaya-zitrek-hs-z-2t-3m-004-4477/"
PAGE="""<html><body><h2>Таль ручная шестеренная Zitrek HS-Z 2т 3м 004-4477</h2>
<div class="thumbnails"><div class="main-image">
<a href="https://z3k.ru/upload/shop_3/2/6/5/item_26572/shop_items_catalog_image26572.jpg"></a>
</div></div>
<div class="pr"><div class="item-price"><div class="diller-price-right">6 790<i class="fa fa-ruble"></i></div></div>
<div class="nalichiei">В наличии</div></div>
<div class="shop_item_properties">
<div class="shop_property">Артикул: <span>004-4477</span></div>
<div class="shop_property">Производитель: <span>Zitrek</span></div>
<button id="cart" data-item-id="26572">Купить</button>
<div class="item-description">Таль шестеренная применяется для подъема грузов. Позвоните менеджеру +7 812 555 34 34. Корпус стальной. Доставка по всей России.</div></div>
<div id="attributes"><div class="shop_property">Вес нетто, кг<span>: 12,00</span></div><div class="shop_property">Высота подъема, м<span>: 3,00</span></div></div>
</body></html>"""

def test_parse_product():
    x=parser.parse_product(PAGE,P)
    assert x["id"]=="z3k-26572"
    assert x["name"].startswith("Таль ручная")
    assert x["sku"]=="004-4477"
    assert x["price"]==6790.0
    assert x["available"]
    assert x["params"]["Вес нетто, кг"]=="12,00"
    assert x["params"]["Бренд"]=="Zitrek"
    assert "Позвоните" not in x["description"]
    assert "Доставка" not in x["description"]
    assert x["photos"][0].endswith("/shop_items_catalog_image26572.jpg")
    feed=ET.fromstring(parser.generate([x]))
    offer=feed.find("./shop/offers/offer")
    assert offer.get("id")=="z3k-26572"
    assert offer.findtext("vendorCode")=="004-4477"

def test_awaiting_stock():
    x=parser.parse_product(PAGE.replace('class="nalichiei">В наличии','class="whaiting">Ожидаем поступление'),P)
    assert not x["available"]

def test_unknown_price_not_available():
    x=parser.parse_product(PAGE.replace("6 790",""),P)
    assert x["price"]==0
    assert not x["available"]

def test_categories_not_exported():
    x=parser.parse_product("<html><h1>Тали ручные Zitrek</h1></html>","https://z3k.ru/shop/podem-i-peremeshhenie-gruza/tali-ruchnye-zitrek/")
    assert x is None

def test_image_is_original_and_external_logo_filtered():
    assert parser.image_url("https://z3k.ru/upload/shop_3/2/6/5/item_26572/shop_items_catalog_image26572.jpg")
    assert parser.image_url("https://z3k.ru/images/logo.png") is None
    assert parser.image_url("https://outside.example/upload/foo.jpg") is None
