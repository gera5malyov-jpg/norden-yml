"""Meaningful pure patch regression tests, independent of private snapshot files."""
import hashlib
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stage54_product_filters as patch


def digest(s):
    return hashlib.sha256(s.encode()).hexdigest()


class ProductFiltersTest(unittest.TestCase):
    def product(self):
        return '<form>buy sentinel</form>\n' + patch.FEATURES_ORIGINAL + '\n    {include file="product.info.html" inline}\nphone sticky AJAX'

    def test_changed_source_rejected(self):
        s = self.product()
        with self.assertRaisesRegex(ValueError, 'source changed'):
            patch.patch_product(s + 'changed', expected_sha256=digest(s))
        with self.assertRaises(ValueError):
            patch.patch_filters('<section id="s-category-filters">')

    def test_uncategorized_branch_is_verbatim_and_original_values_retained(self):
        s = self.product()
        result = patch.patch_product(s, expected_sha256=digest(s))
        self.assertIn('{else}\n' + patch.FEATURES_ORIGINAL + '\n          {/if}', result)
        self.assertIn(patch.SCOPE, result)
        self.assertIn('$_megapolis_shown < 6', result)
        self.assertIn('{$f_value = $product.features[$f_code]}', result)
        self.assertNotIn('{$f_value} мм', result)
        self.assertNotIn('{$f_value} см', result)
        self.assertTrue(result.startswith('<form>buy sentinel</form>'))
        self.assertTrue(result.endswith('phone sticky AJAX'))

    def test_all_features_fallback_below_buy_and_conditional(self):
        result = patch.patch_product(self.product(), expected_sha256=digest(self.product()))
        self.assertIn('empty($theme_settings.product__features_tab)', result)
        self.assertIn('{foreach $product.features as $f_code => $f_value}', patch.FULL_FEATURES_FALLBACK)
        self.assertLess(result.index('buy sentinel'), result.index('id="megapolis-full-features"'))
        self.assertLess(result.index('id="megapolis-full-features"'), result.index('include file="product.info.html"'))

    def test_availability_preserves_stock_count_and_original_branch(self):
        s = '{function name=in_stock n=0 low=5 critical=2}{in_stock n=$stock_count low=$stock.low_count critical=$stock.critical_count}{in_stock n=$sku.count}{if $product_available || count($product.skus) > 1}sku ID count controls\n                              {if $n > $low or $n === null}\nold status\n                              {/if}\n                          {/function}\n<form>AJAX</form>'
        result = patch.patch_product_cart(s, expected_sha256=digest(s))
        self.assertIn('megapolis_scoped=false', result)
        self.assertIn('megapolis_scoped=(' + patch.SCOPE + ')', result)
        self.assertIn('{if $n === null}', result)
        self.assertIn('Наличие уточняйте', result)
        self.assertIn('{elseif $n > 0}', result)
        self.assertIn('{else}\n                              {if $n > $low or $n === null}\nold status', result)
        self.assertTrue(result.endswith('<form>AJAX</form>'))

    def test_filters_keep_submit_ajax_and_ids_verbatim(self):
        s = '<section class="category-filter filters" id="s-category-filters">\n<input name="strana[]" value="17" id="checkboxstrana17"><input name="strana[]" value="21" id="checkboxstrana21">\n<form method="get">submit</form><script>new ProductsFilter({});</script>'
        result = patch.patch_filters(s, expected_sha256=digest(s))
        self.assertTrue(result.endswith(s))
        self.assertIn('js/megapolis-product-filter-v1.js', result)

    def test_duplicate_anchor_rejected(self):
        s = self.product() + patch.FEATURES_ORIGINAL
        with self.assertRaisesRegex(ValueError, 'not unique'):
            patch.patch_product(s, expected_sha256=digest(s))

if __name__ == '__main__':
    unittest.main()
