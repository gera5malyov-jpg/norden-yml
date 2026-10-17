"""Offline checks for stable identification, source parsing, and stock semantics."""
import sys
import unittest
from unittest.mock import patch
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import parser

class TestArimpex(unittest.TestCase):
    def test_server_state(self):
        page = r"""<script>window.__INITIAL_STATE__ = JSON.parse('{\"detail\":{\"product\":{\"id\":123,\"pid\":456}}}');</script>"""
        m=parser.STATE_RE.search(page)
        self.assertTrue(m)
        self.assertEqual(parser.id_of({"id":123}),123)
    def test_stock(self):
        self.assertEqual(parser.availability({"availability":{"state":"on-order"}}),("on-order",0))
        self.assertEqual(parser.availability({"availability":{"state":"buy","quantities":[{"type":"available","value":3}]}}),("buy",3))
    def test_price(self):
        self.assertEqual(parser.current_price({"prices":[{"type":"minimum","value":1},{"type":"current","value":4990}]}),"4990")
    def test_ids(self):
        self.assertEqual(parser.category_id(("Стулья","Барные стулья")),parser.category_id(("Стулья","Барные стулья")))
    def test_imgproxy(self):
        u=("https://imgproxy.arimpex.ru/X/dpr:2/q:90/w:1008/h:739/"
           "aHR0cHM6Ly9hcmlt/cGV4LnJ1L3VwbG9h/ZC9pYmxvY2svNzdl/"
           "LzJrcjFoaDVhdmYw/YWRzdjljZWRsN3B3/NXA3dnRldmpxLmpw/Zw.jpg")
        self.assertEqual(parser.picture(u),"https://arimpex.ru/upload/iblock/77e/2kr1hh5avf0adsv9cedl7pw5p7vtevjq.jpg")

    def test_name_contains_configuration(self):
        p = {"name":"TUKA CB1994 Стул", "configuration":[
            {"name":"Сиденье","items":[{"name":"экокожа Ekos","value":"белый"}]},
            {"name":"Каркас","items":[{"name":"бук","value":"выбеленный бук"}]}]}
        name=parser.variant_name(p)
        self.assertIn("экокожа Ekos белый",name)
        self.assertIn("бук выбеленный бук",name)

    def test_complete_supplier_offers_are_not_just_preview(self):
        base={"id":76160,"pid":64386,"eid":"model-uid#variant-uid",
              "article":"CB1994","link":"/product/CB1994/",
              "offers":{"preview":{"id":76160,"pid":64386}}}
        full=[]
        for i in range(57):
            full.append({"id":76160+i,"pid":64386,
                         "configuration":[{"name":"Сиденье","items":[
                             {"id":100+i,"name":"ткань","value":str(i)}]}]})
        st={"detail":{"product":base,
             "options":[{"name":"configurator","enabled":True}]}}
        with patch.object(parser,"state",return_value=st), patch.object(
                parser,"request",return_value=json.dumps({"offers":full})) as req:
            result=parser.load_model("https://arimpex.ru/product/CB1994/")
        self.assertEqual(len(result["offers"]),57)
        self.assertEqual(len({o["id"] for o in result["offers"]}),57)
        self.assertIn("/api/product/model-uid/offers/",req.call_args.args[0])

    def test_separate_product_cards_without_grouping(self):
        base={"id":1,"pid":42,"name":"Стул","article":"CB1994",
              "link":"/product/CB1994/","prices":[{"type":"current","value":42000}],
              "availability":{"state":"on-order"},
              "configuration":[{"name":"Каркас","items":[{"name":"бук","value":"орех"}]}]}
        parent={"url":"https://arimpex.ru/product/CB1994/",
                "base":base,"category":("Стулья",)}
        content,ids=parser.xml_feed([(base,parent,base)])
        import xml.etree.ElementTree as ET
        item=ET.fromstring(content).find("./shop/offers/offer")
        self.assertNotIn("group_id",item.attrib)
        self.assertIn("бук орех",item.findtext("name"))
        self.assertEqual(item.findtext("vendorCode"),"ARIMPEX-1")

    def test_configuration_swatches_only_selected_sku(self):
        white="https://arimpex.ru/upload/white.jpg"
        black="https://arimpex.ru/upload/black.jpg"
        walnut="https://arimpex.ru/upload/walnut.jpg"
        white_sku={"configuration":[
            {"name":"Сиденье","items":[{"name":"экокожа Ekos","value":"белый",
                 "image":{"src":white}}]},
            {"name":"Каркас","items":[{"name":"бук","value":"орех",
                 "image":{"src":walnut}}]}]}
        black_sku={"configuration":[
            {"name":"Сиденье","items":[{"name":"экокожа Ekos","value":"черный",
                 "image":{"src":black}}]},
            {"name":"Каркас","items":[{"name":"бук","value":"орех",
                 "image":{"src":walnut}}]}]}
        self.assertEqual(parser.configuration_swatches(white_sku),[white,walnut])
        self.assertEqual(parser.configuration_swatches(black_sku),[black,walnut])
        self.assertNotIn(black,parser.configuration_swatches(white_sku))

    def test_independent_offer_picture_gallery_and_color_square(self):
        base={"pid":42,"name":"Стул","article":"CB1994",
              "link":"/product/CB1994/",
              "prices":[{"type":"current","value":42000}],
              "availability":{"state":"on-order"},
              "images":[{"src":"https://arimpex.ru/upload/chair.jpg"}]}
        parent={"url":"https://arimpex.ru/product/CB1994/",
                "base":base,"category":("Стулья",)}
        rows=[]
        for sku, sample, color in (
                (101,"https://arimpex.ru/upload/white.jpg","белый"),
                (102,"https://arimpex.ru/upload/black.jpg","черный")):
            item=dict(base,id=sku,configuration=[{"name":"Сиденье",
                "items":[{"name":"экокожа","value":color,
                          "image":{"src":sample}}]}])
            rows.append((item,parent,item))
        xml,ids=parser.xml_feed(rows)
        import xml.etree.ElementTree as ET
        result={x.get("id"): x for x in ET.fromstring(xml).findall("./shop/offers/offer")}
        white=[x.text for x in result["arimpex-101"].findall("picture")]
        black=[x.text for x in result["arimpex-102"].findall("picture")]
        self.assertEqual(white,["https://arimpex.ru/upload/chair.jpg",
                                "https://arimpex.ru/upload/white.jpg"])
        self.assertEqual(black,["https://arimpex.ru/upload/chair.jpg",
                                "https://arimpex.ru/upload/black.jpg"])
        self.assertEqual(len(ids),2)
