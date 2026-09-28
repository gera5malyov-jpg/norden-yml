import unittest

from catalog.norden_category_fallback import enrich_missing_categories, target_article_keys


class CategoryFallbackTest(unittest.TestCase):
    def test_enriches_generic_api_categories_from_xml_by_normalized_article(self):
        api = {
            "CH-1 grey": {
                "article": "CH-1 grey",
                "name": "Кресло A",
                "category_path": ["Norden"],
                "source": "api",
            },
            "TABLE-1": {
                "article": "TABLE-1",
                "name": "Стол",
                "category_path": ["Norden", "Столы"],
                "source": "api",
            },
        }
        xml = {
            "ch 1-grey": {
                "article": "ch 1-grey",
                "category_path": ["Norden", "Кресло офисное"],
            },
            "TABLE-1": {
                "article": "TABLE-1",
                "category_path": ["Norden", "Столы"],
            },
        }

        merged, stats = enrich_missing_categories(api, xml)

        self.assertEqual(
            merged["CH-1 grey"]["category_path"],
            ["Norden", "Кресло офисное"],
        )
        self.assertEqual(merged["CH-1 grey"]["source"], "api")
        self.assertEqual(
            merged["TABLE-1"]["category_path"],
            ["Norden", "Столы"],
        )
        self.assertEqual(stats["generic_before"], 1)
        self.assertEqual(stats["enriched_from_xml"], 1)
        self.assertEqual(stats["generic_after"], 0)

    def test_matches_visually_confusable_cyrillic_and_latin_article(self):
        api = {
            "CH-1": {"article": "CH-1", "name": "Кресло", "category_path": ["Norden"]},
        }
        xml = {
            "СН-1": {"article": "СН-1", "category_path": ["Norden", "Кресло офисное"]},
        }

        merged, stats = enrich_missing_categories(api, xml)

        self.assertEqual(
            merged["CH-1"]["category_path"],
            ["Norden", "Кресло офисное"],
        )
        self.assertEqual(stats["enriched_from_xml"], 1)


    def test_replaces_non_generic_api_category_when_fallback_is_active(self):
        api = {
            "CH-2": {
                "article": "CH-2",
                "name": "Кресло B",
                "category_path": ["Norden", "API category 999"],
            },
        }
        xml = {
            "CH-2": {
                "article": "CH-2",
                "category_path": ["Norden", "Кресло офисное"],
            },
        }

        merged, stats = enrich_missing_categories(api, xml)

        self.assertEqual(
            merged["CH-2"]["category_path"],
            ["Norden", "Кресло офисное"],
        )
        self.assertEqual(stats["matched_xml_articles"], 1)
        self.assertEqual(stats["enriched_from_xml"], 1)

    def test_target_keys_can_be_driven_by_xml_name_and_category(self):
        xml = {
            "CH-3": {
                "article": "CH-3",
                "name": "Кресло офисное / Test",
                "category_path": ["Norden", "Кресло офисное"],
            },
            "TABLE-3": {
                "article": "TABLE-3",
                "name": "Стол",
                "category_path": ["Norden", "Столы"],
            },
        }

        keys = target_article_keys(
            xml,
            lambda item: "кресл" in " ".join(item.get("category_path") or []).casefold()
            and "кресл" in str(item.get("name") or "").casefold(),
        )

        self.assertIn("ch3", keys)
        self.assertNotIn("table3", keys)


if __name__ == "__main__":
    unittest.main()
