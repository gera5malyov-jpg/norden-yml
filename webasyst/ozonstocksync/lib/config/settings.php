<?php
$settings = array();

$account_names = array(
    1 => 'Мегаполис',
    2 => 'Профи',
    3 => 'Норден НДС',
);

for ($i = 1; $i <= 10; $i++) {
    $suffix = ($i === 1) ? '' : '_' . $i;
    $default_name = isset($account_names[$i]) ? $account_names[$i] : '';

    $settings['account_name' . $suffix] = array(
        'value' => $default_name,
        'title' => 'Аккаунт Ozon ' . $i . ' — название',
        'description' => 'Название используется в интерфейсе и отдельных логах.',
        'control_type' => waHtmlControl::INPUT,
    );
    $settings['client_id' . $suffix] = array(
        'value' => '',
        'title' => 'Аккаунт Ozon ' . $i . ' — Client-Id',
        'description' => 'Client-Id из кабинета Ozon Seller API.',
        'control_type' => waHtmlControl::INPUT,
    );
    $settings['api_key' . $suffix] = array(
        'value' => '',
        'title' => 'Аккаунт Ozon ' . $i . ' — Api-Key',
        'description' => 'Api-Key из кабинета Ozon Seller API.',
        'control_type' => waHtmlControl::PASSWORD,
    );
    $settings['mappings' . $suffix] = array(
        'value' => ($i === 1)
            ? "ozon_td_andrey;1020002090779000;;1\nozon;1020001825140000;;1"
            : '',
        'title' => 'Аккаунт Ozon ' . $i . ' — связки',
        'description' => 'Формат: ID_списка; Ozon_warehouse_id; ID_склада_Webasyst; резерв. Пустой ID склада Webasyst означает общий остаток SKU.',
        'control_type' => waHtmlControl::TEXTAREA,
    );
}

$settings['update_stocks'] = array(
    'value' => '1',
    'title' => 'Обновлять остатки',
    'description' => 'Отправлять остатки в Ozon.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['update_prices'] = array(
    'value' => '0',
    'title' => 'Обновлять цены',
    'description' => 'Отправлять рассчитанные цены в Ozon.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['tip_feature_code'] = array(
    'value' => 'tip_ozon',
    'title' => 'Код характеристики типа Ozon',
    'description' => 'Характеристика Webasyst, содержащая тип товара Ozon.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['acquiring_percent'] = array(
    'value' => '2',
    'title' => 'Расчётный эквайринг Ozon, %',
    'description' => 'Добавляется к комиссии Ozon при расчёте цены. Значение можно изменить.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['target_margin_percent'] = array(
    'value' => '20',
    'title' => 'Общая наценка после комиссий, % от закупки',
    'description' => 'Используется для типов, у которых не задана индивидуальная наценка.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['min_margin_percent'] = array(
    'value' => '18',
    'title' => 'Минимальная наценка после комиссий, % от закупки',
    'description' => 'Используется для min_price.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['old_price_multiplier'] = array(
    'value' => '1.5',
    'title' => 'Коэффициент зачёркнутой цены',
    'description' => 'old_price = цена продажи × коэффициент.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['round_step'] = array(
    'value' => '5',
    'title' => 'Округлять цену вверх до, ₽',
    'description' => 'Например, 5 — округлять вверх до 5 рублей.',
    'control_type' => waHtmlControl::INPUT,
);

/*
 * Associative array keyed by sha1(normalized tip_ozon).
 * No standard Webasyst control is rendered for it: the plugin builds a
 * dedicated product-type table in shopOzonstocksyncPlugin::getControls().
 */
$settings['type_margin_percent'] = array(
    'value' => array(),
);

$settings['stock_batch_size'] = array(
    'value' => '100',
    'title' => 'Ozon: размер батча остатков',
    'description' => 'Количество остатков в одном запросе.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['stock_batch_pause_seconds'] = array(
    'value' => '2',
    'title' => 'Пауза между батчами остатков, сек.',
    'description' => 'Защита от лимитов Ozon API.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['price_batch_size'] = array(
    'value' => '50',
    'title' => 'Ozon: размер батча цен',
    'description' => 'Рекомендуется 25–50 при 429/ResourceExhausted.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['price_pre_wait_seconds'] = array(
    'value' => '60',
    'title' => 'Пауза перед отправкой цен, сек.',
    'description' => 'Пауза перед первым батчем цен.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['price_batch_pause_seconds'] = array(
    'value' => '10',
    'title' => 'Пауза между батчами цен, сек.',
    'description' => 'Защита от лимитов Ozon API.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['price_mapping_pause_seconds'] = array(
    'value' => '5',
    'title' => 'Пауза между списками при сборе цен, сек.',
    'description' => 'Небольшая пауза между списками Webasyst.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['api_retry_count'] = array(
    'value' => '5',
    'title' => 'Повторов при 429/5xx',
    'description' => 'Количество повторов при временных ошибках Ozon API.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['api_retry_wait_seconds'] = array(
    'value' => '180',
    'title' => 'Пауза между повторами API, сек.',
    'description' => 'Ожидание перед повтором запроса.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['only_available'] = array(
    'value' => '1',
    'title' => 'Цены только для доступных SKU',
    'description' => 'Для остатков скрытые/недоступные SKU всё равно отправляются с остатком 0.',
    'control_type' => waHtmlControl::INPUT,
);
$settings['dry_run'] = array(
    'value' => '1',
    'title' => 'Тестовый режим',
    'description' => 'Не отправляет данные в Ozon, только пишет лог.',
    'control_type' => waHtmlControl::INPUT,
);

return $settings;
