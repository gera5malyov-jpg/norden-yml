"""Offline checks for stable identification, source parsing, and stock semantics."""
import sys
import unittest
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
