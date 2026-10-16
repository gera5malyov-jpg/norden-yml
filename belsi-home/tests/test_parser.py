import os,sys,xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
import parser

def test_original_image_with_nested_directory():
    x="/upload/resize_cache/iblock/6ed/mgq8yi11l54z8qaox1laxmsdb6iopmbx/800_800_12c9df8100840b354073c468866885c17/Seleton_k.jpg"
    assert parser.full_image(x)=="https://belsi-home.ru/upload/iblock/6ed/mgq8yi11l54z8qaox1laxmsdb6iopmbx/Seleton_k.jpg"

def test_watermark_resize_cache_replaced():
    x="/upload/resize_cache/iblock/249/800_800_12c9df8100840b354073c468866885c17/24998ce8dd75932f7a51d0dcb9fc8633.jpg"
    assert parser.full_image(x)=="https://belsi-home.ru/upload/iblock/249/24998ce8dd75932f7a51d0dcb9fc8633.jpg"

def test_no_supplier_contacts_or_sales_promises():
    p=parser.sanitize("Кресло на металлическом каркасе. Позвоните менеджеру по телефону +7 812 555 55 55. Цвет белый. Доставка по России. Размеры 75х60 см.")
    assert "металлическом" in p and "Размеры" in p
    assert "Позвоните" not in p and "Доставка" not in p and "+7" not in p

def test_four_colors_four_separate_cards():
    src="""<div class="breadcrumb"><span class="breadcrumb__title">Стулья</span></div>
    <div class="detail__text-content">Каркас выполнен из дерева. Доставка по всей России.</div>
    <div data-propname="COLOR_REF" id="bx_x_prop_31_cont">
      <li data-onevalue="80" data-color="Темное масло"></li>
      <li data-onevalue="91" data-color="Натуральный"></li>
      <li data-onevalue="62" data-color="Белый"></li>
      <li data-onevalue="63" data-color="Черный"></li>
    </div>
    <script>var obj=new JCCatalogElement({'PRODUCT':{'ID':'1276','NAME':'Стул Skeleton'},'OFFERS':[
    {'ID':'1275','NAME':'Skeleton темное масло','TREE':{'PROP_31':'80'},'PRICE':{'DISCOUNT_VALUE':'22770'},'CAN_BUY':true,'DISPLAY_PROPERTIES':'<dt>Артикул</dt><dd>1322</dd>','SLIDER':[{'SRC':'/upload/resize_cache/iblock/249/800_800_123abc/black.jpg'}]},
    {'ID':'1564','NAME':'Skeleton натуральный','TREE':{'PROP_31':'91'},'PRICE':{'DISCOUNT_VALUE':'22770'},'CAN_BUY':true,'DISPLAY_PROPERTIES':'<dt>Артикул</dt><dd>1327</dd>','SLIDER':[{'SRC':'/upload/iblock/250/natural.jpg'}]},
    {'ID':'1565','NAME':'Skeleton белый','TREE':{'PROP_31':'62'},'PRICE':{'DISCOUNT_VALUE':'32360'},'CAN_BUY':true,'DISPLAY_PROPERTIES':'<dt>Артикул</dt><dd>1328</dd>','SLIDER':[{'SRC':'/upload/iblock/251/white.jpg'}]},
    {'ID':'1566','NAME':'Skeleton черный','TREE':{'PROP_31':'63'},'PRICE':{'DISCOUNT_VALUE':'32360'},'CAN_BUY':true,'DISPLAY_PROPERTIES':'<dt>Артикул</dt><dd>1329</dd>','SLIDER':[{'SRC':'/upload/iblock/252/black.jpg'}]}
    ]});</script>"""
    offers,_=parser.parse_page(src,"https://belsi-home.ru/catalog/interernaya_mebel/stulya_i_kreslaa/skeleton_chair/")
    assert len(offers)==4
    assert len({o["id"] for o in offers})==4
    assert {o["params"]["Цвет"] for o in offers}=={"Темное масло","Натуральный","Белый","Черный"}
    assert offers[0]["sku"]=="1322"
    assert offers[2]["price"]==32360
    assert offers[0]["pictures"][0].endswith("/upload/iblock/249/black.jpg")
    assert offers[0]["group"]==offers[3]["group"]
    feed=ET.fromstring(parser.create_yml(offers))
    yml=feed.findall("./shop/offers/offer")
    assert len(yml)==4 and all("?model=" in x.findtext("url") for x in yml)
    assert all(x.findtext("vendorCode") for x in yml)

def test_both_color_and_opening_are_distinct_variants():
    # Bitrix's option TREE includes both color and door-opening mode.
    markup="""<div data-propname="COLOR_REF" id="bx_x_prop_31_cont">
    <li data-onevalue="18" data-color="Беленый дуб"></li></div>
    <div data-propname="DOOR_OPEN" id="bx_x_prop_56_cont">
    <li data-onevalue="23" title="Влево"></li><li data-onevalue="22" title="Вправо"></li></div>"""
    values=parser.option_map(BeautifulSoup(markup,"html.parser"))
    assert values[("PROP_31","18")]=="Беленый дуб"
    assert values[("PROP_56","22")]=="Вправо"
