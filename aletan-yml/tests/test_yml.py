import io
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_feed as feed


def row(code, slug, color="Белый"):
    return {
        "vendor": "Алетан",
        "id": code,
        "vendorCode": code,
        "name": "Тумба тест " + color,
        "price": "25000",
        "oldprice": "",
        "category": "Комоды и тумбы",
        "url": "https://aletan.ru/catalog/" + slug + "/",
        "available": "true",
        "stock": "3",
        "picture": "https://aletan.ru/upload/one.jpg;https://aletan.ru/upload/two.jpg",
        "description": "Описание",
        "color": color,
    }


class YmlTests(unittest.TestCase):
    def test_all_required_fields(self):
        csv_data = io.StringIO()
        import csv
        rows = [row("ART1", "a"), row("ART2", "b")]
        writer = csv.DictWriter(csv_data, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
        old_minimum = feed.MIN_ROWS
        feed.MIN_ROWS = 2
        try:
            parsed = feed.load_rows(csv_data.getvalue().encode("utf-8"))
        finally:
            feed.MIN_ROWS = old_minimum
        root = ET.fromstring(feed.build(parsed))
        offers = root.findall("./shop/offers/offer")
        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0].findtext("vendorCode"), "ART1")
        self.assertEqual(offers[0].findtext("count"), "3")
        self.assertEqual(len(offers[0].findall("picture")), 2)

    def test_duplicate_vendor_codes_are_unique_and_stable(self):
        rows = [row("В216", "test-a", "Розовый"), row("В216", "test-b", "Голубой")]
        for item in rows:
            item["_code"] = item["vendorCode"]
            item["_url"] = item["url"]
            item["_price"] = feed.price(item["price"])
            item["_pics"] = [item["picture"].split(";")[0]]
        first = ET.fromstring(feed.build(rows))
        second = ET.fromstring(feed.build(list(reversed(rows))))
        codes1 = {el.get("id") for el in first.findall("./shop/offers/offer")}
        codes2 = {el.get("id") for el in second.findall("./shop/offers/offer")}
        self.assertEqual(codes1, codes2)
        self.assertEqual(len(codes1), 2)
        self.assertTrue(all(code.startswith("В216-") for code in codes1))

    def test_unavailable_item_zero_stock(self):
        item = row("TEST", "test-c")
        item["_code"] = "TEST"
        item["_url"] = item["url"]
        item["_price"] = "25000"
        item["_pics"] = [item["picture"].split(";")[0]]
        item["available"] = "false"
        root = ET.fromstring(feed.build([item]))
        offer = root.find("./shop/offers/offer")
        self.assertEqual(offer.get("available"), "false")
        self.assertEqual(offer.findtext("count"), "0")


if __name__ == "__main__":
    unittest.main()
