"""Regression tests for sitemap URL normalization (no network calls)."""
import unittest

from storefront.daily_seo_monitor import encode_request_url


class SeoUrlEncodingTests(unittest.TestCase):
    def test_literal_plus_brands_and_other_paths(self):
        cases = [
            ("https://profikompany.ru/brand/Eva+Solo/", "https://profikompany.ru/brand/Eva+Solo/"),
            ("https://profikompany.ru/brand/Suck+UK/", "https://profikompany.ru/brand/Suck+UK/"),
            ("https://profikompany.ru/brand/%D0%A3%D1%8E%D1%82+%D0%9C%D0%B5%D0%B1%D0%B5%D0%BB%D1%8C/", "https://profikompany.ru/brand/%D0%A3%D1%8E%D1%82+%D0%9C%D0%B5%D0%B1%D0%B5%D0%BB%D1%8C/"),
            ("https://profikompany.ru/", "https://profikompany.ru/"),
            ("https://profikompany.ru/category/kompyuternye-kresla/", "https://profikompany.ru/category/kompyuternye-kresla/"),
            ("https://profikompany.ru/stol-stol-0108d/", "https://profikompany.ru/stol-stol-0108d/"),
            ("https://profikompany.ru/category/a/?page=2", "https://profikompany.ru/category/a/?page=2"),
            ("https://profikompany.ru/category/a/?page=2&sort=price", "https://profikompany.ru/category/a/?page=2&sort=price"),
            ("https://profikompany.ru/brand/Eva%2BSolo/", "https://profikompany.ru/brand/Eva%2BSolo/"),
            ("https://profikompany.ru/brand/Eva Solo/", "https://profikompany.ru/brand/Eva%20Solo/"),
            ("https://profikompany.ru/brand/a+b+c/", "https://profikompany.ru/brand/a+b+c/"),
            ("https://profikompany.ru/brand/%2B/", "https://profikompany.ru/brand/%2B/"),
        ]
        for original, expected in cases:
            with self.subTest(url=original):
                self.assertEqual(encode_request_url(original), expected)


if __name__ == "__main__":
    unittest.main()
