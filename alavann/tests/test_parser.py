import sys, unittest, xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import parser
from bs4 import BeautifulSoup

HTML = """<html><script type="application/ld+json">{"@type":"Product","name":"Mirror","sku":"ALV123","image":"https://alavann-shop.ru/upload/s.jpg","offers":{"price":9000,"availability":"https://schema.org/InStock"}}</script><script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[{"item":{"@id":"https://alavann-shop.ru/","name":"Главная"}},{"item":{"@id":"https://alavann-shop.ru/mebel/","name":"Мебель"}},{"item":{"@id":"https://alavann-shop.ru/mebel/mirror/","name":"Зеркала"}},{"item":{"@id":"https://alavann-shop.ru/mebel/mirror/a/","name":"Mirror"}}]}</script><section id="card-1"><div class="definitions-item"><span class="definitions-item__key">Размер</span><span class="definitions-item__value">60</span></div></section><form data-card-form><div class="card-slider"><a data-fancybox="gallery-slider" href="/upload/big.jpg">фото</a></div></form></html>"""

class ParserTests(unittest.TestCase):
    def test_parsing_blocks(self):
        soup=BeautifulSoup(HTML,"lxml")
        p=parser.product_object(soup)
        self.assertEqual(p["sku"],"ALV123")
        self.assertEqual(parser.specs(soup),{"Размер":"60"})
        self.assertEqual(parser.images(soup,p),["https://alavann-shop.ru/upload/big.jpg"])
        self.assertEqual(len(parser.breadcrumb_categories(soup)),2)
        self.assertEqual(parser.price("9 000"),"9000")
    def test_xml(self):
        data={"sku":"ALV123","name":"Mirror","url":"https://alavann-shop.ru/mebel/mirror/a/","price":"9000","available":True,"vendor":"Alavann","images":["https://alavann-shop.ru/upload/a.jpg"],"description":"A & B","params":{"Размер":"60"},"trail":[("https://alavann-shop.ru/mebel/","Мебель")]}
        root=ET.fromstring(parser.feed([data]))
        offer=root.find("./shop/offers/offer")
        self.assertEqual(offer.get("id"),"ALV123")
        self.assertEqual(offer.findtext("description"),"A & B")
    def test_reject_external_urls(self):
        self.assertEqual(parser.canonical("https://example.com/file.jpg"),"")

    def test_exact_six_category_scope(self):
        self.assertEqual(len(parser.CATALOG_ROOTS),6)
        self.assertEqual(set(parser.CATALOG_ROOTS),{
            "/mebel/cupboards/", "/mebel/komody/",
            "/mebel/zerkala-v-vannuyu/", "/mebel/stand/",
            "/mebel/set/", "/mebel/mirror-cupboard/"})
        for section in parser.CATALOG_ROOTS:
            self.assertTrue(parser.in_scope(parser.BASE+section+"example-product/"))
            self.assertFalse(parser.in_scope(parser.BASE+section))
        for path in ("/screens/ekran-pod-vannu/",
                     "/mebel/shelves/a/", "/mebel/fixtures/a/",
                     "/mebel/stand-other/a/", "/mebel/",
                     "/mebel/stand/filter/color-red/apply/"):
            self.assertFalse(parser.in_scope(parser.BASE+path), path)

    def test_listing_does_not_pull_sibling_categories(self):
        soup=BeautifulSoup("""<a class="product__name" href="/mebel/stand/allowed/">Allowed</a>
            <a class="product__name" href="/mebel/zerkala-v-vannuyu/also-allowed/">Allowed 2</a>
            <a class="product__name" href="/screens/excluded/">Excluded</a>
            <a class="product__name" href="/mebel/shelves/excluded/">Excluded</a>""","lxml")
        self.assertEqual(parser.listing_product_links(soup),{
            parser.BASE+"/mebel/stand/allowed/",
            parser.BASE+"/mebel/zerkala-v-vannuyu/also-allowed/"})

if __name__=="__main__": unittest.main()
