import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import pytest
import parser

def test_contacts_removed_without_losing_furniture_facts():
    src = (
      "Стул на прочном металлическом каркасе. "
      "В нашей компании можно заказать любые партии. "
      "Позвоните +7 (812) 309-74-59 или пишите sale@artikul-mebel.ru. "
      "Высота сиденья 450 мм."
    )
    clean = parser.sanitize_description(src)
    assert "прочное" in clean or "прочном" in clean
    assert "450 мм" in clean
    for bad in ("нашей компании", "+7", "@", "Позвоните"):
        assert bad not in clean

def test_supplier_website_and_address_removed():
    src = ("Износостойкое покрытие. ООО «Артикул-Мебель», "
           "адрес офиса: Санкт-Петербург, улица Примерная, 25. "
           "Столешница из ЛДСП.")
    clean = parser.sanitize_description(src)
    assert "Износостойкое покрытие." in clean
    assert "Столешница из ЛДСП." in clean
    assert "Примерная" not in clean
    assert "ООО" not in clean

def test_clean_params_hides_contacts_but_preserves_dimensions():
    source={"Вес":"8 кг","Телефон":"8 800 302 44 32", "Контакт":"sale@artikul-mebel.ru"}
    result=parser.clean_params(source)
    assert result == {"Вес":"8 кг"}

def test_original_gallery_images_instead_of_bitrix_resize_cache():
    html = ('<div class="wrapper-big-picture"><img '
            'data-big-src="/upload/resize_cache/iblock/303/1000_1000_x/file.jpg"'
            '></div>')
    soup=BeautifulSoup(html,"html.parser")
    assert parser.extract_pictures(soup,soup)==[
        "https://artikul-mebel.ru/upload/iblock/303/file.jpg"
    ]

def test_missing_variant_fails_full_feed(monkeypatch):
    from test_parser import BASE_VARIANT_URL, VARIANT_BASE_HTML, VARIANT_1490_URL
    def fake_fetch(url,**kwargs):
        if url == BASE_VARIANT_URL: return VARIANT_BASE_HTML
        if url == VARIANT_1490_URL: raise RuntimeError("supplier temporarily unavailable")
        raise AssertionError("Unexpected request "+url)
    monkeypatch.setattr(parser, "fetch", fake_fetch)
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        parser.load_one(BASE_VARIANT_URL)

def test_yml_also_cleans_description_fields():
    item={
       "url":"https://artikul-mebel.ru/catalog/detail/test/",
       "name":"Тестовый стул","sku":"T-1","category":"Стулья","price":1000,
       "pictures":[],"params":{"Телефон":"8 800 302 44 32","Материал":"сталь"},
       "description":"Каркас из стали. Звоните +7 812 309 74 59."
    }
    root=ET.fromstring(parser.build_yml([item]))
    offer=root.find("./shop/offers/offer")
    assert "Звоните" not in (offer.findtext("description") or "")
    assert "Каркас из стали" in (offer.findtext("description") or "")
    assert not any(x.get("name")=="Телефон" for x in offer.findall("param"))
