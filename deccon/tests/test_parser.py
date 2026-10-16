import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import parser as d

class ParserTests(unittest.TestCase):
    def test_catalog_pagination(self):
        html = '''<div class="module-pagination"><a href="/catalog/?PAGEN_6=2">2</a>
                   <a href="/catalog/?PAGEN_6=34">34</a></div>
                   <div class="new_catalog_list_item"><a href="/catalog/chasy_napolnye_mekhanicheskie/2680/">
                   <img src="/upload/resize_cache/b.jpg"></a></div>
                   <div class="new_catalog_list_item"><a href="/catalog/nastennye_chasy_s_kukushkoy/2580/"></a></div>
                   <div class="new_catalog_list_item"><a href="https://other.example/catalog/fake/1234/"></a></div>'''
        key,pages=d.listing_page_numbers(html)
        self.assertEqual(key,"PAGEN_6")
        self.assertEqual(len(pages),34)
        self.assertEqual(len(d.collect_listing(html)),2)

    def test_original_images_and_no_supplier_data(self):
        html = """<div class="new_product"><div class="new_product_img">
        <div class="slider-single">
        <a class="item" data-lightbox="image" href="/upload/iblock/aa/photo-one.jpg">
          <img src="/upload/webp/90/upload/resize-cache/iblock/aa/170-small.webp"></a>
        <a class="item" data-lightbox="image" href="/upload/iblock/bb/photo-two.jpg"></a>
        </div></div></div>
        <div class="new_product_info"><h1>Напольные часы Columbus CR2435-451z01</h1>
          <div class="new_product_param_2"><ul>
            <li>Вес - <strong>47 кг</strong></li>
            <li>Механизм - <strong>Hermle-451</strong></li>
            <li>Контакты - <strong>+7 499 111-11-11</strong></li>
          </ul></div></div>
        <div data-module="zverushki_microm"><meta itemprop="sku" content="CR-2435-451z01"></div>"""
        url=d.BASE+"/catalog/chasy_napolnye_mekhanicheskie/2680/"
        with patch.object(d,"fetch",return_value=html):
            product=d.parse(url)
        self.assertEqual(product["sku"],"CR-2435-451z01")
        self.assertEqual(product["id"],"deccon-2680")
        self.assertEqual(len(product["photos"]),2)
        self.assertEqual(product["props"],{"Вес":"47 кг","Механизм":"Hermle-451"})
        self.assertTrue(all("/upload/iblock/" in im for im in product["photos"]))
        data=d.serialize([product])
        for token in (b"<price>",b"<available>",b"<quantity>",b"<vendor>"):
            self.assertNotIn(token,data)
        self.assertNotIn(b"499 111",data)
        offer_xml=data.split(b"<offer id=",1)[1].split(b"</offer>",1)[0]
        self.assertNotIn(b"<url>",offer_xml)

    def test_block_external_image(self):
        soup=d.BeautifulSoup('<div class="new_product_img"><div class="slider-single">'
            '<a data-lightbox="image" href="https://evil.example/a.jpg"></a>'
            '<a data-lightbox="image" href="/upload/iblock/a/b.jpg"></a>'
            '</div></div>',"html.parser")
        self.assertEqual(d.images(soup),[d.BASE+"/upload/iblock/a/b.jpg"])

if __name__=="__main__":
    unittest.main()
