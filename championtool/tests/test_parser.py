import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import parser as c

class ChampionToolParserTests(unittest.TestCase):
    SAMPLE = """
    <div class="l-breadcrumb">
      <span itemprop="itemListElement"><a><span itemprop="name">Главная</span></a></span>
      <span itemprop="itemListElement"><a><span itemprop="name">Запчасти</span></a></span>
      <span itemprop="itemListElement"><a><span itemprop="name">Рукоятки</span></a></span>
      <span itemprop="itemListElement"><span itemprop="name">Рукоятка рабочая</span></span>
    </div>
    <div class="p-product" itemscope itemtype="http://schema.org/Product">
      <h1>Рукоятка рабочая LM4122 нижняя часть</h1>
      <div class="top-row__item" itemprop="sku">13011B01S010</div>
      <div class="p-product__image-section">
        <a class="fanbox-thumber" href="https://championtool.ru/media/cache/product_popup/images/product/zapchasti/13011B01S010.jpg"></a>
      </div>
      <div data-attributes-target><div class="p-product__attributes-group-value">
         <div class="p-product__attributes-group-value__title">Тип запчасти</div>
         <div class="p-product__attributes-group-value-data">Рукоятка</div>
      </div><div class="p-product__attributes-group-value">
         <div class="p-product__attributes-group-value__title">Производитель</div>
         <div class="p-product__attributes-group-value-data">CHAMPION</div>
      </div></div>
      <div class="p-product__description">
        <div class="description-target">Подходит для газонокосилок Champion: LM4122.
        Оригинальная запчасть. Обращайтесь к нашим менеджерам по телефону.</div>
      </div>
      <div class="p-product__form-wrap">
        <form id="buy-form">
          <meta itemprop="availability" content="InStock">
          <input type="hidden" name="product_id" value="20676" />
        </form>
      </div>
    </div>"""

    def test_url_isolation(self):
        self.assertEqual(c.normalize("https://bad.ru/item"),"")
        self.assertEqual(c.normalize("/test/?utm_source=a"),c.ROOT+"/test")

    def test_product_parse_and_content_cleanup(self):
        with patch.object(c,"get",return_value=self.SAMPLE):
            kind,p=c.parse(c.ROOT+"/rukoyatka-rabochaya-lm4122")
        self.assertEqual(kind,"product")
        self.assertEqual(p["article"],"13011B01S010")
        self.assertEqual(p["available"],"true")
        self.assertEqual(p["status"],"В наличии")
        self.assertEqual(p["props"]["Тип запчасти"],"Рукоятка")
        self.assertEqual(p["photos"],[c.ROOT+"/media/images/product/zapchasti/13011B01S010.jpg"])
        self.assertNotIn("телефон",p["description"].lower())
        self.assertEqual(p["categories"],["Запчасти","Рукоятки"])

    def test_preorder_is_unavailable(self):
        html=self.SAMPLE.replace('<form id="buy-form">','<div class="out-of-stock">Под заказ</div><form id="buy-form">')
        with patch.object(c,"get",return_value=html):
            kind,p=c.parse(c.ROOT+"/rukoyatka-rabochaya-lm4122")
        self.assertEqual(kind,"product")
        self.assertEqual(p["available"],"false")
        self.assertEqual(p["status"],"Под заказ")

    def test_no_product_is_not_product(self):
        with patch.object(c,"get",return_value="<div><h1>Новости компании</h1></div>"):
            self.assertEqual(c.parse(c.ROOT+"/news"),("not_product",None))

    def test_serialize_price_and_stock_never_invented(self):
        with patch.object(c,"get",return_value=self.SAMPLE):
            _,p=c.parse(c.ROOT+"/rukoyatka-rabochaya-lm4122")
        text=c.create_xml([p])
        xml=c.ET.fromstring(text)
        offer=xml.find("./shop/offers/offer")
        self.assertEqual(offer.get("available"),"true")
        self.assertIsNone(offer.find("price"))
        self.assertIsNone(offer.find("quantity"))
        self.assertIsNone(offer.find("url"))
        self.assertEqual(offer.find("./param[@name='Наличие']").text,"В наличии")

if __name__=="__main__":
    unittest.main()
