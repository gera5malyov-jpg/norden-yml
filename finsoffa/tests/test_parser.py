import json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import parser as f

class FinsoffaTests(unittest.TestCase):
    def test_sitemap_and_listings(self):
        xml="""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://finsoffa.ru/product/test-red/</loc></url>
          <url><loc>https://finsoffa.ru/catalog/</loc></url>
          <url><loc>https://foreign.ru/product/evil/</loc></url></urlset>"""
        with patch.object(f,"get",return_value=xml),patch.object(f,"MIN_OFFERS",1):
            self.assertEqual(f.sitemap_products(),{f.ROOT+"/product/test-red/"})
        markup='<a href="/product/test-red/"></a><a href="/product/test-blue/"></a><a href="https://bad.example/product/x"></a>'
        self.assertEqual(len(f.listing_products(markup)),2)

    def test_true_false_stock(self):
        for label,schema,expected in [
            ("в наличии","InStock",("true","В наличии")),
            ("под заказ","BackOrder",("false","Под заказ")),
            ("нет в наличии","OutOfStock",("false","Нет в наличии")),
        ]:
            soup=f.BeautifulSoup('<span class="fs-pd__stock">'+label+'</span>',"html.parser")
            self.assertEqual(f.presence(soup,{"offers":{"availability":"https://schema.org/"+schema}}),expected)
        soup=f.BeautifulSoup('<span class="fs-pd__stock">в наличии</span>',"html.parser")
        with self.assertRaises(RuntimeError):
            f.presence(soup,{"offers":{"availability":"https://schema.org/BackOrder"}})

    def test_product_extract(self):
        data={
          "@type":"Product","name":"Энн диван Велюр Formula 668 (зелёный) арт. 4673739700143",
          "sku":"4673739700143","image":[
            "https://finsoffa.ru/photos/upload/iblock/a/1.webp",
            "https://finsoffa.ru/photos/upload/iblock/b/2.webp"
          ],
          "offers":{"availability":"https://schema.org/InStock"}
        }
        html=('<html><head><script type="application/ld+json">'+json.dumps(data,ensure_ascii=False)
        +'</script></head><body><nav><ul>'
        +'<li class="breadcrumb__item">Главная</li><li class="breadcrumb__item">Каталог</li>'
        +'<li class="breadcrumb__item">Прямые диваны</li><li class="breadcrumb__item">Энн диван</li>'
        +'</ul></nav><h1 class="fs-pd__title">'+data["name"]+'</h1>'
        +'<div class="fs-pd__art">Артикул: 4673739700143</div>'
        +'<div class="fs-pd__stock is-in">в наличии</div>'
        +'<div class="fs-tabs__pane" data-pane="chars"><div class="fs-pd__char">'
        +'<span>Длина, см</span><b>206</b></div><div class="fs-pd__char">'
        +'<span>Производитель</span><b>Finsoffa</b></div></div></body></html>')
        with patch.object(f,"get",return_value=html):
            item=f.parse(f.ROOT+"/product/test-green/")
        self.assertEqual(item["sku"],"4673739700143")
        self.assertEqual(item["available"],"true")
        self.assertEqual(item["params"],{"Длина, см":"206"})
        self.assertEqual(len(item["photos"]),2)
        self.assertEqual(item["category"],["Прямые диваны"])
        self.assertNotIn("finsoffa",item["description"].lower())
        with patch.object(f,"MIN_OFFERS",1):
            xml=f.to_xml([item])
            num,pics,yes,no=f.verify(xml)
            self.assertEqual((num,pics,yes,no),(1,2,1,0))
        self.assertNotIn(b"<price>",xml)
        self.assertNotIn(b"<quantity>",xml)
        self.assertIn(b'available="true"',xml)

if __name__=="__main__":
    unittest.main()
