"""Pure, fail-closed patches against the read-only stage53 theme audit.

No IO, network, deployment, data writes, or alteration of purchase handlers.
Hashes are for text read with universal-newline normalization (Path.read_text).
"""
from __future__ import annotations
import hashlib

AUDITED_SHA256 = {
    "product.html": "1e008f505a693bab2b592c4141d1c47a268d21cac326ce890d01838a08f086bb",
    "product.cart.html": "11ea8bc61bafb71d89c8716900e3310294b8119a017fafbae0225ff57e29e2d0",
    "category.filters.html": "5566362c10539bc7964607b95870fba943b9c49d360bcf031523b455b93cc79c",
}
SCOPE = "$wa->domainUrl() == 'https://profikompany.ru' && !empty($product.category_id)"
FEATURES_ORIGINAL = '          {if $product.features && !empty($theme_settings.product__features)}\n            <div class="features-list">\n              {foreach $product.features as $f_code => $f_value}\n                {if $f_value@iteration < 10 && $features[$f_code].type != \'divider\'}\n                  <div class="features-list__item">\n                    <div class="features-list__name">{$features[$f_code].name|escape}</div>\n                    <div class="features-list__line"></div>\n                    <div class="features-list__value">\n                      {if is_array($f_value)}\n                          {if $features[$f_code].type == \'color\'}\n                              {implode(\'<br /> \', $f_value)}\n                          {else}\n                              {implode(\', \', $f_value)}\n                          {/if}\n                      {else}\n                          {$f_value}\n                      {/if}\n                    </div>\n                  </div>\n                {/if}\n              {/foreach}\n              {if $theme_settings.product__info == "list"}<a href="#product-features" class="features-list__link smooth-scroll">[`All features`]</a>{/if}\n            </div>\n          {/if}'
FEATURES_REPLACEMENT = '          {* megapolis-product-filter-v1: buyer facts, original values and units only. *}\n          {if $wa->domainUrl() == \'https://profikompany.ru\' && !empty($product.category_id)}\n            {$_megapolis_priority = [\'color\',\'material\',\'material_karkasa\',\'material_sidenya\',\'material_stoleshnitsy\',\'razmer\',\'shirina\',\'glubina\',\'vysota\',\'razreshennaya_nagruzka_do\',\'podlokotniki\',\'kolichestvo_yashchikov\',\'kolichestvo_dverey\',\'garantiya\',\'garantiynyy_srok\',\'vendor\',\'strana\',\'obivka\',\'material_obivki\',\'razmery_stula_vkhshkhg\',\'razmery_kresla\',\'gabaritnyy_razmer_dshv\',\'regulirovka_vysoty\',\'mekhanizm_kachaniya\',\'krestovina\',\'vysota_sidenya\',\'kolichestvo_mest\',\'kollektsiya\',\'ms_s1_372e9683f239fd53\',\'ms_s1_5af1dea9d5916a8e\',\'ms_s1_f55983d699df1974\',\'ms_s1_f2c22d29eeca8e55\',\'ms_s1_18188210a236dc01\',\'ms_s1_9eb235a32fb2e2e5\',\'ms_s1_28a7cfd863de2a3f\',\'ms_s1_1b66eb4ba2adc943\',\'ms_s1_7129a076a1939833\',\'ms_s1_85f95885541f3c22\',\'ms_s1_d9b2d50e5906628a\',\'ms_s1_a24cbdca8d3d95d2\']}\n            {if $product.features && !empty($theme_settings.product__features) && count(array_intersect(array_keys($product.features), $_megapolis_priority))}\n              <div class="features-list megapolis-key-features">\n                {$_megapolis_shown = 0}\n                {foreach $_megapolis_priority as $f_code}\n                  {if isset($product.features[$f_code]) && $features[$f_code].type != \'divider\' && $_megapolis_shown < 6}\n                    {$f_value = $product.features[$f_code]}\n                  <div class="features-list__item">\n                    <div class="features-list__name">{if $features[$f_code].name == \'Высотамм\'}Высота, мм{elseif $features[$f_code].name == \'Длинамм\'}Длина, мм{elseif $features[$f_code].name == \'Ширинамм\'}Ширина, мм{elseif $features[$f_code].name == \'Цветкорпуса\'}Цвет корпуса{elseif $features[$f_code].name == \'Цветфасадов\'}Цвет фасадов{elseif $features[$f_code].name == \'Материалкаркаса\'}Материал каркаса{elseif $features[$f_code].name == \'Материалстолешницы\'}Материал столешницы{elseif $features[$f_code].name == \'Сиденьематериал\'}Материал сиденья{else}{$features[$f_code].name|escape}{/if}</div>\n                    <div class="features-list__line"></div>\n                    <div class="features-list__value">\n                      {if is_array($f_value)}\n                          {if $features[$f_code].type == \'color\'}\n                              {implode(\'<br /> \', $f_value)}\n                          {else}\n                              {implode(\', \', $f_value)}\n                          {/if}\n                      {else}\n                          {$f_value}\n                      {/if}\n                    </div>\n                  </div>\n                    {$_megapolis_shown = $_megapolis_shown + 1}\n                  {/if}\n                {/foreach}\n                {if !empty($theme_settings.product__features_tab)}\n                  {if $theme_settings.product__info == "list"}<a href="#product-features" class="features-list__link smooth-scroll">Все характеристики</a>\n                  {else}<a href="#product-features" class="features-list__link" data-toggle="tab" role="tab">Все характеристики</a>{/if}\n                {else}<a href="#megapolis-full-features" class="features-list__link smooth-scroll">Все характеристики</a>{/if}\n              </div>\n            {/if}\n          {else}\n          {if $product.features && !empty($theme_settings.product__features)}\n            <div class="features-list">\n              {foreach $product.features as $f_code => $f_value}\n                {if $f_value@iteration < 10 && $features[$f_code].type != \'divider\'}\n                  <div class="features-list__item">\n                    <div class="features-list__name">{$features[$f_code].name|escape}</div>\n                    <div class="features-list__line"></div>\n                    <div class="features-list__value">\n                      {if is_array($f_value)}\n                          {if $features[$f_code].type == \'color\'}\n                              {implode(\'<br /> \', $f_value)}\n                          {else}\n                              {implode(\', \', $f_value)}\n                          {/if}\n                      {else}\n                          {$f_value}\n                      {/if}\n                    </div>\n                  </div>\n                {/if}\n              {/foreach}\n              {if $theme_settings.product__info == "list"}<a href="#product-features" class="features-list__link smooth-scroll">[`All features`]</a>{/if}\n            </div>\n          {/if}\n          {/if}'
FULL_FEATURES_FALLBACK = '    {* Keep every original feature reachable even when the theme full-feature tab is disabled. *}\n    {if $wa->domainUrl() == \'https://profikompany.ru\' && !empty($product.category_id) && $product.features && empty($theme_settings.product__features_tab)}\n      <details id="megapolis-full-features" class="megapolis-full-features">\n        <summary>Все характеристики</summary>\n        <div class="features-list">\n          {foreach $product.features as $f_code => $f_value}\n            {if $features[$f_code].type == \'divider\'}\n              <h3 class="features-list__divider">{$features[$f_code].name|escape}</h3>\n            {else}\n                  <div class="features-list__item">\n                    <div class="features-list__name">{$features[$f_code].name|escape}</div>\n                    <div class="features-list__line"></div>\n                    <div class="features-list__value">\n                      {if is_array($f_value)}\n                          {if $features[$f_code].type == \'color\'}\n                              {implode(\'<br /> \', $f_value)}\n                          {else}\n                              {implode(\', \', $f_value)}\n                          {/if}\n                      {else}\n                          {$f_value}\n                      {/if}\n                    </div>\n                  </div>\n            {/if}\n          {/foreach}\n        </div>\n      </details>\n    {/if}\n'


def _verify(source: str, filename: str, expected_sha256: str | None) -> None:
    expected = expected_sha256 or AUDITED_SHA256[filename]
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != expected:
        raise ValueError("Audited source changed: " + filename)


def _replace(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError("Audited anchor is not unique")
    return source.replace(old, new, 1)


def patch_product(source: str, *, expected_sha256: str | None = None) -> str:
    _verify(source, "product.html", expected_sha256)
    updated = _replace(source, FEATURES_ORIGINAL, FEATURES_REPLACEMENT)
    return _replace(updated, '    {include file="product.info.html" inline}', FULL_FEATURES_FALLBACK + '\n    {include file="product.info.html" inline}')


def patch_product_cart(source: str, *, expected_sha256: str | None = None) -> str:
    _verify(source, "product.cart.html", expected_sha256)
    old = '                              {if $n > $low or $n === null}'
    new = ('                              {if $megapolis_scoped}\n'
           '                                {if $n === null}\n'
           '                                  <span class="product-stocks__text">Наличие уточняйте</span>\n'
           '                                {elseif $n > 0}\n'
           '                                  <span class="product-stocks__text product-stocks__high">В наличии</span>\n'
           '                                {else}\n'
           '                                  <span class="product-stocks__text product-stocks__none">{if $wa->shop->settings(\'ignore_stock_count\')}[`Pre-order only`]{else}[`Out of stock`]{/if}</span>\n'
           '                                {/if}\n'
           '                              {else}\n' + old)
    updated = _replace(source, old, new)
    updated = _replace(updated, '                              {/if}\n                          {/function}', '                              {/if}\n                              {/if}\n                          {/function}')
    updated = _replace(updated, '{function name=in_stock n=0 low=5 critical=2}', '{function name=in_stock n=0 low=5 critical=2 megapolis_scoped=false}')
    updated = _replace(updated, '{in_stock n=$stock_count low=$stock.low_count critical=$stock.critical_count}', '{in_stock n=$stock_count low=$stock.low_count critical=$stock.critical_count megapolis_scoped=(' + SCOPE + ')}')
    updated = _replace(updated, '{in_stock n=$sku.count}', '{in_stock n=$sku.count megapolis_scoped=(' + SCOPE + ')}')
    return _replace(updated, '{if $product_available || count($product.skus) > 1}', '{if (' + SCOPE + ') || $product_available || count($product.skus) > 1}')


def patch_filters(source: str, *, expected_sha256: str | None = None) -> str:
    _verify(source, "category.filters.html", expected_sha256)
    assets = '''    {* Display only: keep original IDs, links, selection and AJAX semantics. *}
    {if $wa->domainUrl() == 'https://profikompany.ru'}
      <link rel="stylesheet" href="{$wa_real_theme_url}css/megapolis-product-filter-v1.css?v=20261012">
      <script src="{$wa_real_theme_url}js/megapolis-product-filter-v1.js?v=20261012" defer></script>
    {/if}
'''
    return _replace(source, '<section class="category-filter filters" id="s-category-filters">', assets + '\n<section class="category-filter filters" id="s-category-filters">')
