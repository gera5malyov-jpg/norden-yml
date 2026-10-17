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
    assert parser.product_url("https://www.avalonmebel.ru/catalog/stoly-dlya-kafe/stol1") == (
        "https://www.avalonmebel.ru/catalog/stoly-dlya-kafe/stol1")
    assert parser.product_url("https://www.avalonmebel.ru/catalog/chairs/stol1") is None
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


def test_specification_diagram_is_last_and_not_duplicated():
    # Avalon stores the dimensions drawing OUTSIDE the ordinary gallery.
    diagram="/upload/iblock/537/5373dfeb23e2819adb7c57eb95007a34.jpg"
    html=PRODUCT.replace("</body>", """
    <div class="sku__chars">
      <div class="magnific-gallery"><div class="magnific-gallery__item1">
        <a href="%s"><img class="magnific-gallery__img_c" src="%s"></a>
      </div></div>
    </div></body>""" % (diagram,diagram))
    parsed=parser.parse_product(html,"https://www.avalonmebel.ru/catalog/podstole-dlya-stola/-1282em")
    expected="https://www.avalonmebel.ru"+diagram
    assert parsed["pictures"]==[
        "https://www.avalonmebel.ru/upload/iblock/c7c/full-1.jpg",
        expected
    ]
    offer=ET.fromstring(parser.build_yml([parsed])).find("./shop/offers/offer")
    assert [x.text for x in offer.findall("picture")][-1]==expected


def test_dimensions_reserved_at_end_even_with_many_gallery_photos():
    soup=BeautifulSoup(PRODUCT,"html.parser")
    main=soup.select_one(".magnific-gallery")
    for n in range(40):
        div=soup.new_tag("div")
        div["class"]="magnific-gallery__item"
        a=soup.new_tag("a",href="/upload/iblock/abc/photo-%02d.jpg" % n)
        div.append(a)
        main.append(div)
    specs=soup.select_one(".sku__chars")
    div=soup.new_tag("div")
    div["class"]="magnific-gallery__item1"
    diagram=soup.new_tag("a",href="/upload/iblock/xyz/dimensions.jpg")
    div.append(diagram)
    specs.append(div)
    images=parser.pictures(soup)
    assert len(images)==35
    assert images[0].endswith("/full-1.jpg")
    assert images[-1].endswith("/dimensions.jpg")
    assert images.count(images[-1])==1


TABLE_CATALOG = """<html><div id="js-catalog-wrapper-id"
 data-page="1" data-pages="15" data-total="179" data-count="12">
 <a class="product" href="/catalog/stoly-dlya-kafe/-2109em">table one</a>
 <a class="product" href="/catalog/podstole-dlya-stola/-2109em">not a table</a>
 <a class="product" href="/catalog/stoly-dlya-kafe/0501d">table two</a>
 </div></html>"""


def test_tables_category_discovery_filters_subframes():
    urls,total,pages=parser.listing(TABLE_CATALOG,parser.CATEGORIES[1])
    assert (total,pages)==(179,15)
    assert urls==[
        "https://www.avalonmebel.ru/catalog/stoly-dlya-kafe/-2109em",
        "https://www.avalonmebel.ru/catalog/stoly-dlya-kafe/0501d",
    ]


def test_tables_products_keep_separate_category_and_stable_offer_ids():
    # Keep the existing subframes offer IDs unchanged; tables have their own namespace.
    table_url="https://www.avalonmebel.ru/catalog/stoly-dlya-kafe/-2109em"
    original_url="https://www.avalonmebel.ru/catalog/podstole-dlya-stola/-2109em"
    table_html=PRODUCT.replace("Подстолье 1282EM","Стол 2109EM").replace("1282EM","2109EM")
    table=parser.parse_product(table_html,table_url)
    subframe=parser.parse_product(table_html,original_url)
    assert parser.offer_id(table)=="avalon-table--2109em"
    assert parser.offer_id(subframe)=="avalon--2109em"
    catalog=ET.fromstring(parser.build_yml([subframe,table]))
    categories={x.get("id"):x.text for x in catalog.findall("./shop/categories/category")}
    assert categories=={"1":"Подстолья для столов", "2":"Столы для кафе"}
    offers={x.get("id"):x for x in catalog.findall("./shop/offers/offer")}
    assert offers["avalon-table--2109em"].findtext("categoryId")=="2"
    assert offers["avalon--2109em"].findtext("categoryId")=="1"
    assert offers["avalon-table--2109em"].findtext("vendorCode")=="2109EM"
    assert offers["avalon-table--2109em"].findtext("picture").endswith("/full-1.jpg")
