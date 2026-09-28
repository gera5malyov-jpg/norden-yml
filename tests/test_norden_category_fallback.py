import unittest

from catalog.norden_category_fallback import enrich_missing_categories


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


if __name__ == "__main__":
    unittest.main()
