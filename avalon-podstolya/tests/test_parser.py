import os
import sys
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
import parser

CATALOG = """<html><div id="js-catalog-wrapper-id" data-page="1" data-pages="2"
data-total="200" data-count="12"><a class="product" href="/catalog/podstole-dlya-stola/-1282em">A</a>
<a class="product" href="/catalog/chairs/no">B</a>
<a class="product" href="/catalog/podstole-dlya-stola/-1281em">C</a></div></html>"""

PRODUCT = """<html><body><div class="sku"><h1 class="sku__heading">Подстолье 1282EM</h1>
<div class="magnific-gallery"><a class="magnific-gallery__item" href="#"></a>
<div class="magnific-gallery__item"><a href="/upload/iblock/c7c/full-1.jpg"><img src="/upload/resize_cache/iblock/c7c/100_100_1/full-1.jpg"></a></div></div>
<div class="sku-info"><div class="sku-info__top"><p class="sku__price">9 244 ₽</p></div></div>
<div class="sku__caption"><p>Чугунное подстолье. Основание из литого чугуна.</p>
<p>Позвоните в компанию Авалон по телефону +7 812 000 22 33.</p>
<p>Рекомендуемый размер столешницы 90×90 см.</p></div>
<div class="sku__chars"><ul class="sku__info-list">
<li>Артикул <span class="bold">1282EM</span></li>
<li>Наличие <span class="bold">в наличии</span></li>
<li>Пр-во <span class="bold">КИТАЙ</span></li>
<li>Высота, см <span class="bold">72</span></li>
<li>Вес, кг <span class="bold">15.95</span></li>
</ul></div></div></body></html>"""


def test_listing_category_only():
    urls,n,p=parser.listing(CATALOG)
    assert (n,p)==(200,2)
    assert urls==[
       "https://www.avalonmebel.ru/catalog/podstole-dlya-stola/-1282em",
       "https://www.avalonmebel.ru/catalog/podstole-dlya-stola/-1281em",
    ]


def test_parse_facts_price_source_images_and_no_ads():
    p=parser.parse_product(PRODUCT,"https://www.avalonmebel.ru/catalog/podstole-dlya-stola/-1282em")
    assert p["price"]==9244
    assert p["sku"]=="1282EM"
    assert p["available"]
    assert p["params"]["Высота, см"]=="72"
    assert p["params"]["Вес, кг"]=="15.95"
    assert p["pictures"]==["https://www.avalonmebel.ru/upload/iblock/c7c/full-1.jpg"]
    assert "Чугунное подстолье." in p["description"]
    assert "90×90" in p["description"]
    assert "Авалон" not in p["description"]
    assert "+7" not in p["description"]


def test_original_images_replace_resize_overlay():
    v="https://www.avalonmebel.ru/upload/resize_cache/iblock/ca3/585_530_1/ca311.jpg"
    assert parser.source_image(v)=="https://www.avalonmebel.ru/upload/iblock/ca3/ca311.jpg"


def test_no_other_category_or_generic_images():
    assert parser.product_url("https://www.avalonmebel.ru/catalog/stoly-dlya-kafe/stol1") is None
    assert parser.source_image("https://www.avalonmebel.ru/local/templates/main/logo.png") is None
    assert parser.source_image("https://unrelated.example/upload/iblock/a/file.jpg") is None


def test_yml_offer_and_sanitized_description():
    p=parser.parse_product(PRODUCT,"https://www.avalonmebel.ru/catalog/podstole-dlya-stola/-1282em")
    root=ET.fromstring(parser.build_yml([p]))
    x=root.find("./shop/offers/offer")
    assert x.attrib["id"]=="avalon--1282em"
    assert x.findtext("price")=="9244"
    assert x.findtext("vendorCode")=="1282EM"
    assert x.findtext("picture").endswith("/full-1.jpg")
    assert "Авалон" not in (x.findtext("description") or "")
    assert root.findtext("./shop/company")=="ООО Мегаполис"
