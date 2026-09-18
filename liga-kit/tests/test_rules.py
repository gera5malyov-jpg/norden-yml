import unittest
from decimal import Decimal

from liga_kit.rules import to_kit_sku, normalize_price, desired_stock


class LigaRulesTests(unittest.TestCase):
    def test_liga_sku_prefix(self):
        self.assertEqual(to_kit_sku('109775'), 'liga-109775')

    def test_empty_vendor_code_is_rejected(self):
        with self.assertRaises(ValueError):
            to_kit_sku('   ')

    def test_price_is_feed_price_without_markup(self):
        self.assertEqual(normalize_price('87990'), Decimal('87990.00'))

    def test_invalid_or_nonpositive_price_is_none(self):
        self.assertIsNone(normalize_price('0'))
        self.assertIsNone(normalize_price('-1'))
        self.assertIsNone(normalize_price('bad'))

    def test_active_offer_stock_is_100(self):
        self.assertEqual(desired_stock(True), 100)

    def test_unavailable_offer_stock_is_zero(self):
        self.assertEqual(desired_stock(False), 0)


if __name__ == '__main__':
    unittest.main()
