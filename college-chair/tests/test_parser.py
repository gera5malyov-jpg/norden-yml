import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import parser as college

class CollegeParserTests(unittest.TestCase):
    def test_catalog_links_are_category_scoped(self):
        html = '''<a href="/catalog/7/614">A</a>
                  <a href="/catalog/7/608">B</a>
                  <a href="/catalog/8/545">wrong section</a>
                  <a href="/catalog/7/?PAGEN_1=2">page 2</a>
                  <a href="https://evil.example/catalog/7/100">external</a>'''
        products, pages = college.listing_links(html, "7", college.BASE + "/catalog/7/")
        self.assertEqual(len(products), 2)
        self.assertEqual(pages, {college.BASE + "/catalog/7/?PAGEN_1=2"})

    def test_reject_offsite_url(self):
        self.assertEqual(college.canonical("https://example.org/a"), "")

    def test_extract_product_and_strip_supplier_fields(self):
        html = """<div class="bx_item_detail"><div class="product">
            <li><p><a href="#">Артикул: HLC-2415L-1/Black</a></p></li>
            <a class="imagelightbox" href="/upload/iblock/a/a.jpg">PHOTO</a>
            </div></div>
            <div id="tb2"><table class="propsTable">
            <tr><td>Бренд</td><td>College</td></tr>
            <tr><td>С этим товаром рекомендуем</td><td>Другие кресла</td></tr>
            <tr><td>Обивка</td><td>Экокожа</td></tr>
            <tr><td>Цвет</td><td>Черный</td></tr>
            <tr><td>Контакты</td><td>+7 499 222-22-22</td></tr>
            </table></div>
            <script>new JCCatalogElement({'PRODUCT':{'SLIDER':[{'SRC':'/upload/iblock/a/a.jpg'},
                {'SRC':'/upload/iblock/b/b.jpg'}],'CAN_BUY':true}});</script>"""
        with patch.object(college, "get", return_value=html):
            data = college.parse_product(college.BASE + "/catalog/7/614", "7")
        self.assertEqual(data["sku"], "HLC-2415L-1/Black")
        self.assertEqual(len(data["images"]), 2)
        self.assertEqual(data["params"], {"Обивка": "Экокожа", "Цвет": "Черный"})
        self.assertNotIn("College", data["description"])
        xml = college.build_xml([data])
        self.assertNotIn(b"<price>", xml)
        self.assertNotIn(b"<available>", xml)
        self.assertNotIn(b"<vendor>", xml)

if __name__ == "__main__":
    unittest.main()
