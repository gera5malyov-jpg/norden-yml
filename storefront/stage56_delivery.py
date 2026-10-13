"""Pure presentation patches; no network, IO, payload, totals or tariff changes.

product.html baseline includes the authorized stage54 product/features patch.
The CMS baseline is page 12 content verbatim, including CRLF line endings.
"""
from __future__ import annotations
import hashlib

AUDITED_SHA256 = {
    'product.html': 'd8b84f04d936891d2a8595a979c2e93f169199096acba896909d79b44f861abf',
    'order.html': 'd6a1427790068795c5ee133de3519a4cec9fe780464bfb4dd3221309771b0be9',
    'page12.content': '01d4dbcdb3194ce04c524eeaa31dacac13538dbeb887e5773d7eaf2cd4c7d4cc',
}
PRODUCT_OLD = '<p>Доставляем мебель по Санкт-Петербургу и России. Стоимость и условия доставки уточняются при оформлении заказа.</p>'
PRODUCT_NEW = '<p>Товары отправляются из разных городов. Доставка рассчитывается и согласуется отдельно: стоимость зависит от города отправки, адреса получения, веса и объёма заказа.</p>'
ORDER_ANCHOR = '              <div class="order-page__body">\n                {$wa->shop->checkout()->form(['
ORDER_NOTICE = '''              {if $wa->domainUrl() == 'https://profikompany.ru'}
                <p id="megapolis-delivery-notice" class="order-page__body" style="margin:0;border-bottom:1px solid #e5e3e0;font-size:14px;line-height:1.5">
                  <strong>Доставка - отдельный расчёт.</strong> Товары отправляются из разных городов. Доставку и перемещение в пункт самовывоза менеджер рассчитает и согласует отдельно - с учётом города отправки, адреса получения, веса и объёма. Стоимость товаров не является окончательной стоимостью заказа с доставкой.
                </p>
              {/if}
'''
CMS_PRICE_OLD = '<li>Стоимость доставки по Санкт-Петербургу: <strong>от 600 ₽</strong></li>'
CMS_PRICE_NEW = '<li>Стоимость доставки рассчитывается <strong>индивидуально</strong>: товары отправляются из разных городов, поэтому расчёт зависит от <strong>города отправки, адреса получения, веса и объёма заказа</strong>.</li>'
CMS_FACTORS_OLD = '<li>Итоговая стоимость зависит от <strong>габаритов, веса, района доставки</strong>, а также дополнительных услуг (подъём/сборка).</li>'
CMS_FACTORS_NEW = '<li>Дополнительные услуги (подъём/сборка) также рассчитываются и согласуются отдельно.</li>'
CMS_WIDGET_BLOCK = '<div id="transportnye-kompanii"></div>\r\n\r\n<script src="https://transportnye-kompanii.com/transport/generate-calculator/"></script>\r\n\r\n<script>calculatorBuilder({\r\n    companies: [\r\n        [\'baikalService\', \'Байкал Сервис\'],\r\n        [\'pec\', \'ПЭК\'],\r\n        [\'cdek\', \'СДЭК\'],\r\n        [\'dellin\', \'Деловые линии\'],\r\n        [\'dpd\', \'DPD\'],\r\n    ],\r\n    title: \'Калькулятор стоимости доставки\',\r\n    cityFrom: \'Москва\',\r\n    cargoPickup: true,\r\n    cargoDelivery: true,\r\n    insurance: true,\r\n    rigidPackaging: true,\r\n    colorScheme: \'#62488E\',\r\n    buttonTextColor: \'white\',\r\n});\r\n</script>'
CMS_WIDGET_NOTE = '<p id="megapolis-delivery-calculator-note">Для расчёта нужны выбранные товары и адрес получения. Менеджер уточнит город отправления и согласует стоимость доставки отдельно.</p>'

def _verify(source: str, name: str, expected_sha256: str | None) -> None:
    if hashlib.sha256(source.encode('utf-8')).hexdigest() != (expected_sha256 or AUDITED_SHA256[name]):
        raise ValueError('Audited source changed: ' + name)

def _replace(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError('Audited delivery anchor is not unique')
    return source.replace(old, new, 1)

def patch_product(source: str, *, expected_sha256: str | None = None) -> str:
    _verify(source, 'product.html', expected_sha256)
    if source.count('<section class="megapolis-purchase-trust"') != 1:
        raise ValueError('Existing purchase trust section changed')
    return _replace(source, PRODUCT_OLD, PRODUCT_NEW)

def patch_order(source: str, *, expected_sha256: str | None = None) -> str:
    _verify(source, 'order.html', expected_sha256)
    if source.count('id="js-order-form"') != 1:
        raise ValueError('Native checkout form wrapper changed')
    if 'id="megapolis-delivery-notice"' in source:
        raise ValueError('Delivery notice already present')
    return _replace(source, ORDER_ANCHOR, ORDER_NOTICE + ORDER_ANCHOR)

def patch_delivery_content(source: str, *, expected_sha256: str | None = None) -> str:
    _verify(source, 'page12.content', expected_sha256)
    if 'id="megapolis-delivery-calculator-note"' in source:
        raise ValueError('Calculator notice already present')
    updated = _replace(source, CMS_PRICE_OLD, CMS_PRICE_NEW)
    updated = _replace(updated, CMS_FACTORS_OLD, CMS_FACTORS_NEW)
    return _replace(updated, CMS_WIDGET_BLOCK, CMS_WIDGET_NOTE)
