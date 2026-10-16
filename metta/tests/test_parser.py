import os,sys,xml.etree.ElementTree as ET
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
import parser

BASE="https://www.metta.ru/catalog/samurai/kreslo_samurai_s_1_1017896/"
HTML="""<div class="ds-catalog-detail-page">
<div data-fl-item-id="1017915" data-price="35670" data-fl-item-available="true"></div>
<h1>Samurai S-1</h1>
<a href="https://www.metta.ru/catalog/samurai/kreslo_samurai_s_1_1017896/?offer=1017912"
 data-code="AA.12" data-color="Серый" data-left-image="https://3do.metta.ru/render/model/1.1.webp">Gray</a>
<a href="https://www.metta.ru/catalog/samurai/kreslo_samurai_s_1_1017896/?offer=1017915"
 data-code="BB.15" data-color="Черный" data-left-image="https://3do.metta.ru/render/model/4.2.1.49.webp">Black</a>
<a href="https://www.metta.ru/catalog/yogax2/kreslo_yogax2_karbon_1061376/?offer=1061688">Related</a>
<meta itemprop="image" content="https://3do.metta.ru/render/model/x2048/4.2.1.49.webp">
<div class="ds-detail-image-gallery">
<img data-ds-image src="https://3do.metta.ru/render/model/x2048/4.2.1.49.webp">
<img data-ds-image src="https://3do.metta.ru/render/model/x2048/back/4.2.1.49.webp">
</div>
<div class="ds-detail-props-table">
<div class="py-1x"><div class="text-gray-950">Материал обивки</div><div class="text-gray-800">Экокожа</div></div>
<div class="py-1x"><div class="text-gray-950">Цвет обивки</div><div class="text-gray-800">Черный</div></div>
<div class="py-1x"><div class="text-gray-950">Телефон</div><div class="text-gray-800">+7 812 000-00-00</div></div>
</div></div>"""

def test_only_base_product_pages():
    assert parser.product_base("https://www.metta.ru/spb/catalog/samurai/kreslo_samurai_s_1_1017896/?offer=1017912")==BASE
    assert parser.product_base("https://www.metta.ru/catalog/samurai/") is None
    assert parser.product_base("https://www.metta.ru/catalog/metta/filter/abc/apply/") is None

def test_each_color_own_card():
    variants=parser.variant_links(HTML,BASE)
    assert set(variants)=={"1017912","1017915"}
    assert variants["1017915"]["color"]=="Черный"
    actual=parser.parse_offer(HTML,BASE,"1017915",variants)
    assert actual["id"]=="metta-1017915"
    assert actual["sku"]=="BB.15"
    assert actual["price"]==35670
    assert actual["available"]
    assert actual["name"]=="Samurai S-1 - Черный"
    assert len(actual["photos"])==2
    assert "Телефон" not in actual["params"]
    assert "Черный" in actual["params"]["Цвет"]
    feed=ET.fromstring(parser.build([actual]))
    assert feed.find("./shop/offers/offer").get("id")=="metta-1017915"
    assert feed.find("./shop/offers/offer/vendorCode").text=="BB.15"

def test_supplier_ads_removed():
    s=parser.scrub("Металлический каркас. Позвоните менеджеру +7 (812) 333 55 66. Высота 120 см. Доставка по России. Цвет серый.")
    assert "Металлический" in s and "120 см" in s
    assert "+7" not in s and "Позвоните" not in s and "Доставка" not in s

def test_product_specific_gallery_not_related_chair():
    assert parser.picture("https://3do.metta.ru/render/model/x2048/4.2.1.49.webp")
    assert parser.picture("https://untrusted.ru/pic.jpg") is None
    assert parser.picture("https://www.metta.ru/upload/resize_cache/iblock/a1/800_800_1/p.jpg")=="https://www.metta.ru/upload/iblock/a1/p.jpg"
