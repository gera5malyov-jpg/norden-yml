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


class RobotsHeadTests(unittest.TestCase):
    def test_main_head_noindex_is_detected(self):
        from storefront.daily_seo_monitor import head_indexing_directives, directives_block_indexing
        head = b'<html><head><meta name="robots" content="noindex, follow"></head><body></body></html>'
        self.assertTrue(directives_block_indexing(head_indexing_directives(head)))

    def test_embedded_vk_widget_noindex_must_not_block_category(self):
        from storefront.daily_seo_monitor import head_indexing_directives, directives_block_indexing
        source = (b'<html><head><title>Cat</title></head><body>Catalog</body></html>'
                  b'<html><head><title>VK | Community Widget</title>'
                  b'<meta name="robots" content="noindex, nofollow"></head></html>')
        self.assertEqual(head_indexing_directives(source), [])
        self.assertFalse(directives_block_indexing(head_indexing_directives(source)))

    def test_nofollow_and_none_block(self):
        from storefront.daily_seo_monitor import directives_block_indexing
        self.assertTrue(directives_block_indexing(["nofollow"]))
        self.assertTrue(directives_block_indexing(["none"]))
        self.assertFalse(directives_block_indexing(["index, follow"]))


if __name__ == "__main__":
    unittest.main()
