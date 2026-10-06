<?php
class shopOzonstocksyncPlugin extends shopPlugin
{
    private $ui_commission_map = null;
    private $ui_feature_warning = '';

    public function getControls($params = array())
    {
        try {
            return array(
                'ozonstocksync_ui' => $this->renderSettingsUi($params),
            );
        } catch (Exception $e) {
            waLog::log(
                date('Y-m-d H:i:s') . ' SETTINGS_UI_FALLBACK: ' . $e->getMessage(),
                'ozonstocksync.log'
            );
            return parent::getControls($params);
        }
    }

    public function saveSettings($settings = array())
    {
        $type_margin_present = !empty($settings['_type_margin_present']);
        unset($settings['_type_margin_present']);

        if ($type_margin_present) {
            $settings['type_margin_percent'] = $this->sanitizeTypeMargins(
                isset($settings['type_margin_percent']) ? $settings['type_margin_percent'] : array()
            );
        } else {
            // If the dynamic UI could not be rendered, never wipe already saved per-type margins.
            $current = $this->getSettings('type_margin_percent');
            if ($current !== null) {
                $settings['type_margin_percent'] = $current;
            }
        }

        foreach (array('update_stocks', 'update_prices', 'only_available', 'dry_run') as $key) {
            if (isset($settings[$key])) {
                $settings[$key] = ((int)$settings[$key] === 1) ? '1' : '0';
            }
        }

        $numeric_rules = array(
            'acquiring_percent' => array(0, 50, 2),
            'target_margin_percent' => array(0, 300, 20),
            'min_margin_percent' => array(0, 300, 18),
            'old_price_multiplier' => array(1.01, 20, 1.5),
            'round_step' => array(1, 10000, 5),
            'stock_batch_size' => array(1, 100, 100),
            'stock_batch_pause_seconds' => array(0, 3600, 2),
            'price_batch_size' => array(1, 100, 50),
            'price_pre_wait_seconds' => array(0, 3600, 60),
            'price_batch_pause_seconds' => array(0, 3600, 10),
            'price_mapping_pause_seconds' => array(0, 3600, 5),
            'api_retry_count' => array(1, 20, 5),
            'api_retry_wait_seconds' => array(0, 3600, 180),
        );
        foreach ($numeric_rules as $key => $rule) {
            if (!isset($settings[$key]) || $settings[$key] === '') {
                continue;
            }
            $value = str_replace(',', '.', trim((string)$settings[$key]));
            if (!is_numeric($value)) {
                $settings[$key] = (string)$rule[2];
                continue;
            }
            $value = (float)$value;
            if ($value < $rule[0]) {
                $value = $rule[0];
            }
            if ($value > $rule[1]) {
                $value = $rule[1];
            }
            $settings[$key] = $this->formatNumber($value);
        }

        return parent::saveSettings($settings);
    }

    private function renderSettingsUi($params)
    {
        $namespace = !empty($params['namespace']) ? (string)$params['namespace'] : 'shop_ozonstocksync';
        $feature_code = trim((string)$this->getSettings('tip_feature_code'));
        if ($feature_code === '') {
            $feature_code = 'tip_ozon';
        }
        $acquiring = (float)$this->getSettings('acquiring_percent');
        $global_margin = (float)$this->getSettings('target_margin_percent');
        $type_margins = $this->getTypeMargins();
        $type_rows = $this->loadAssignedOzonTypes($feature_code);

        $configured_accounts = 0;
        for ($i = 1; $i <= 10; $i++) {
            $suffix = ($i === 1) ? '' : '_' . $i;
            if (
                trim((string)$this->getSettings('client_id' . $suffix)) !== ''
                || trim((string)$this->getSettings('api_key' . $suffix)) !== ''
                || trim((string)$this->getSettings('mappings' . $suffix)) !== ''
            ) {
                $configured_accounts++;
            }
        }

        $missing_commissions = 0;
        foreach ($type_rows as &$row) {
            $commission = $this->getUiCommissionForTip($row['tip']);
            $row['commission'] = $commission;
            if (!$commission) {
                $missing_commissions++;
            }
        }
        unset($row);

        $html = '';
        $html .= '<div class="ozs-settings">';
        $html .= '<div class="ozs-hero">';
        $html .= '<div><div class="ozs-title">Ozon Stock Sync</div><div class="ozs-subtitle">Остатки, цены и индивидуальная наценка по типам Ozon</div></div>';
        $html .= '<span class="ozs-version">v1.6.0</span>';
        $html .= '</div>';

        $html .= '<div class="ozs-summary-grid">';
        $html .= $this->summaryCard('Аккаунты Ozon', (string)$configured_accounts, 'из 10 настроено');
        $html .= $this->summaryCard('Типы товаров', (string)count($type_rows), 'используются в ' . $this->e($feature_code));
        $html .= $this->summaryCard('Эквайринг', $this->formatPercent($acquiring), 'добавляется к комиссии');
        $html .= $this->summaryCard('Общая наценка', $this->formatPercent($global_margin), 'если нет индивидуальной');
        $html .= '</div>';

        $html .= '<details class="ozs-card" open>';
        $html .= '<summary><span><b>Цены и комиссии</b><small>Основные параметры расчёта</small></span><span class="ozs-chevron">⌄</span></summary>';
        $html .= '<div class="ozs-card-body">';
        $html .= '<div class="ozs-switch-grid">';
        $html .= $this->checkboxField($namespace, 'update_stocks', 'Обновлять остатки', 'Скрытые и недоступные товары отправляются в Ozon с остатком 0.');
        $html .= $this->checkboxField($namespace, 'update_prices', 'Обновлять цены', 'Цена рассчитывается от закупки с учётом комиссии и наценки.');
        $html .= $this->checkboxField($namespace, 'dry_run', 'Тестовый режим', 'Включено — только лог, без записи в Ozon.');
        $html .= $this->checkboxField($namespace, 'only_available', 'Цены только для доступных SKU', 'На логику обнуления остатков это не влияет.');
        $html .= '</div>';

        $html .= '<div class="ozs-form-grid">';
        $html .= $this->inputField($namespace, 'tip_feature_code', 'Характеристика типа Ozon', 'text', 'Например: tip_ozon');
        $html .= $this->inputField($namespace, 'acquiring_percent', 'Расчётный эквайринг, %', 'number', 'Добавляется к комиссии Ozon', '0.01');
        $html .= $this->inputField($namespace, 'target_margin_percent', 'Общая наценка, %', 'number', 'После комиссии, от закупочной цены', '0.1');
        $html .= $this->inputField($namespace, 'min_margin_percent', 'Минимальная наценка, %', 'number', 'Используется для min_price', '0.1');
        $html .= $this->inputField($namespace, 'old_price_multiplier', 'Зачёркнутая цена ×', 'number', 'Например: 1.5', '0.01');
        $html .= $this->inputField($namespace, 'round_step', 'Округление вверх, ₽', 'number', 'Шаг округления цены', '1');
        $html .= '</div>';
        $html .= '<div class="ozs-formula">Цена = Закупка × (1 + наценка) ÷ (1 − комиссия Ozon − эквайринг). В Ozon себестоимость не отправляется.</div>';
        $html .= '</div></details>';

        $html .= '<details class="ozs-card" open>';
        $html .= '<summary><span><b>Типы товаров и наценка</b><small>Типы подтягиваются из Webasyst автоматически</small></span><span class="ozs-chevron">⌄</span></summary>';
        $html .= '<div class="ozs-card-body">';
        $html .= '<input type="hidden" name="' . $this->e($namespace) . '[_type_margin_present]" value="1">';

        if ($this->ui_feature_warning !== '') {
            $html .= '<div class="ozs-alert ozs-alert-warning">' . $this->e($this->ui_feature_warning) . '</div>';
        }

        if (!$type_rows) {
            $html .= '<div class="ozs-empty">В характеристике <b>' . $this->e($feature_code) . '</b> пока нет назначенных типов товаров. После заполнения характеристики строки появятся здесь автоматически.</div>';
        } else {
            $html .= '<div class="ozs-type-toolbar">';
            $html .= '<input type="search" id="ozs-type-search" class="ozs-search" placeholder="Найти тип товара…">';
            $html .= '<div class="ozs-legend">Комиссии: таблица Ozon с 28.08.2026 · эквайринг: ' . $this->formatPercent($acquiring) . '</div>';
            $html .= '</div>';

            if ($missing_commissions > 0) {
                $html .= '<div class="ozs-alert ozs-alert-warning">Для ' . (int)$missing_commissions . ' типов ставка не найдена в таблице комиссий. Для них цена не будет отправлена, пока тип не будет исправлен или ставка не появится в таблице.</div>';
            }

            $html .= '<div class="ozs-table-wrap"><table class="ozs-table">';
            $html .= '<thead><tr><th>Тип товара Ozon</th><th class="ozs-num">Товаров</th><th>Комиссия Ozon</th><th>Эквайринг</th><th>Итого комиссия</th><th>Наценка, %</th></tr></thead><tbody>';

            foreach ($type_rows as $row) {
                $tip = $row['tip'];
                $key = sha1($this->normalizeTip($tip));
                $margin_value = isset($type_margins[$key]) ? $type_margins[$key] : '';
                $commission = $row['commission'];
                $search = $this->normalizeTip($tip);

                if ($commission) {
                    $c_display = $this->commissionDisplay($commission, 0);
                    $total_display = $this->commissionDisplay($commission, $acquiring);
                    $commission_class = '';
                } else {
                    $c_display = '<span class="ozs-bad">не найдена</span>';
                    $total_display = '—';
                    $commission_class = ' ozs-row-warning';
                }

                $html .= '<tr class="ozs-type-row' . $commission_class . '" data-search="' . $this->e($search) . '">';
                $html .= '<td><b>' . $this->e($tip) . '</b></td>';
                $html .= '<td class="ozs-num">' . (int)$row['count'] . '</td>';
                $html .= '<td>' . $c_display . '</td>';
                $html .= '<td>' . $this->formatPercent($acquiring) . '</td>';
                $html .= '<td><b>' . $total_display . '</b></td>';
                $html .= '<td class="ozs-margin-cell"><input class="ozs-margin" type="number" min="0" max="300" step="0.1" '
                    . 'name="' . $this->e($namespace) . '[type_margin_percent][' . $this->e($key) . ']" '
                    . 'value="' . $this->e($margin_value) . '" placeholder="' . $this->e($this->formatNumber($global_margin)) . '" '
                    . 'title="Пусто — использовать общую наценку ' . $this->e($this->formatPercent($global_margin)) . '"></td>';
                $html .= '</tr>';
            }

            $html .= '</tbody></table></div>';
            $html .= '<div class="ozs-hint">Пустая наценка означает общую наценку ' . $this->formatPercent($global_margin) . '. Индивидуальное значение применяется только к этому типу товара.</div>';
        }
        $html .= '</div></details>';

        $html .= '<details class="ozs-card">';
        $html .= '<summary><span><b>Аккаунты и склады Ozon</b><small>Client-Id, Api-Key и связки складов</small></span><span class="ozs-chevron">⌄</span></summary>';
        $html .= '<div class="ozs-card-body ozs-accounts">';
        for ($i = 1; $i <= 10; $i++) {
            $suffix = ($i === 1) ? '' : '_' . $i;
            $account_name = trim((string)$this->getSettings('account_name' . $suffix));
            $client_id = trim((string)$this->getSettings('client_id' . $suffix));
            $api_key = trim((string)$this->getSettings('api_key' . $suffix));
            $mappings = (string)$this->getSettings('mappings' . $suffix);
            $is_configured = ($client_id !== '' || $api_key !== '' || trim($mappings) !== '');
            $display_name = ($account_name !== '') ? $account_name : ('Аккаунт ' . $i);

            $html .= '<details class="ozs-account">';
            $html .= '<summary><span><b>' . $i . '. ' . $this->e($display_name) . '</b></span><span class="ozs-account-state ' . ($is_configured ? 'is-ok' : '') . '">' . ($is_configured ? 'настроен' : 'пустой') . '</span></summary>';
            $html .= '<div class="ozs-account-body">';
            $html .= '<div class="ozs-form-grid">';
            $html .= $this->inputField($namespace, 'account_name' . $suffix, 'Название аккаунта', 'text', 'Для интерфейса и логов');
            $html .= $this->inputField($namespace, 'client_id' . $suffix, 'Client-Id', 'text', 'Ozon Seller API');
            $html .= $this->inputField($namespace, 'api_key' . $suffix, 'Api-Key', 'password', 'Ozon Seller API');
            $html .= '</div>';
            $html .= $this->textareaField($namespace, 'mappings' . $suffix, 'Связки складов', 'Одна строка: ID списка Webasyst; ID склада Ozon; ID склада Webasyst; резерв');
            $html .= '</div></details>';
        }
        $html .= '</div></details>';

        $html .= '<details class="ozs-card">';
        $html .= '<summary><span><b>Технические настройки</b><small>Батчи, паузы и повторы API</small></span><span class="ozs-chevron">⌄</span></summary>';
        $html .= '<div class="ozs-card-body"><div class="ozs-form-grid">';
        $html .= $this->inputField($namespace, 'stock_batch_size', 'Батч остатков', 'number', 'Максимум 100', '1');
        $html .= $this->inputField($namespace, 'stock_batch_pause_seconds', 'Пауза остатков, сек.', 'number', 'Между батчами', '1');
        $html .= $this->inputField($namespace, 'price_batch_size', 'Батч цен', 'number', 'Обычно 25–50, максимум 100', '1');
        $html .= $this->inputField($namespace, 'price_pre_wait_seconds', 'Пауза перед ценами, сек.', 'number', 'Перед первым батчем', '1');
        $html .= $this->inputField($namespace, 'price_batch_pause_seconds', 'Пауза цен, сек.', 'number', 'Между батчами', '1');
        $html .= $this->inputField($namespace, 'price_mapping_pause_seconds', 'Пауза между списками, сек.', 'number', 'Во время подготовки цен', '1');
        $html .= $this->inputField($namespace, 'api_retry_count', 'Повторов API', 'number', 'Для 429, 5xx и ResourceExhausted', '1');
        $html .= $this->inputField($namespace, 'api_retry_wait_seconds', 'Ожидание повтора, сек.', 'number', 'Перед повторной попыткой', '1');
        $html .= '</div>';
        $html .= '<div class="ozs-hint">Защитные правила прошлых версий сохранены: остатки батчами до 100, повтор при 429/5xx, обнуление скрытых товаров, принудительная обработка неизменившейся цены через безопасное изменение min_price.</div>';
        $html .= '</div></details>';

        $html .= '</div>';
        $html .= $this->settingsCss();
        $html .= $this->settingsJs();

        return $html;
    }

    private function summaryCard($label, $value, $hint)
    {
        return '<div class="ozs-summary"><span>' . $this->e($label) . '</span><b>' . $value . '</b><small>' . $hint . '</small></div>';
    }

    private function checkboxField($namespace, $key, $label, $hint)
    {
        $checked = ((int)$this->getSettings($key) === 1);
        $name = $this->e($namespace) . '[' . $this->e($key) . ']';
        return '<label class="ozs-switch-item">'
            . '<input type="hidden" name="' . $name . '" value="0">'
            . '<input type="checkbox" name="' . $name . '" value="1"' . ($checked ? ' checked' : '') . '>'
            . '<span class="ozs-switch-copy"><b>' . $this->e($label) . '</b><small>' . $this->e($hint) . '</small></span>'
            . '</label>';
    }

    private function inputField($namespace, $key, $label, $type, $hint, $step = null)
    {
        $value = (string)$this->getSettings($key);
        $name = $this->e($namespace) . '[' . $this->e($key) . ']';
        $attrs = '';
        if ($step !== null) {
            $attrs .= ' step="' . $this->e($step) . '"';
        }
        if ($type === 'number') {
            $attrs .= ' min="0"';
        }
        if ($type === 'password') {
            $attrs .= ' autocomplete="new-password"';
        }
        return '<label class="ozs-field"><span>' . $this->e($label) . '</span>'
            . '<input type="' . $this->e($type) . '" name="' . $name . '" value="' . $this->e($value) . '"' . $attrs . '>'
            . '<small>' . $this->e($hint) . '</small></label>';
    }

    private function textareaField($namespace, $key, $label, $hint)
    {
        $value = (string)$this->getSettings($key);
        $name = $this->e($namespace) . '[' . $this->e($key) . ']';
        return '<label class="ozs-field ozs-field-wide"><span>' . $this->e($label) . '</span>'
            . '<textarea name="' . $name . '" rows="4" spellcheck="false">' . $this->e($value) . '</textarea>'
            . '<small>' . $this->e($hint) . '</small></label>';
    }

    private function getTypeMargins()
    {
        $margins = $this->getSettings('type_margin_percent');
        if (is_string($margins) && $margins !== '') {
            $decoded = json_decode($margins, true);
            if (is_array($decoded)) {
                $margins = $decoded;
            }
        }
        return is_array($margins) ? $margins : array();
    }

    private function sanitizeTypeMargins($margins)
    {
        if (is_string($margins)) {
            $decoded = json_decode($margins, true);
            if (is_array($decoded)) {
                $margins = $decoded;
            }
        }
        if (!is_array($margins)) {
            return array();
        }

        $clean = array();
        foreach ($margins as $key => $value) {
            $key = strtolower(trim((string)$key));
            if (!preg_match('/^[a-f0-9]{40}$/', $key)) {
                continue;
            }
            $value = str_replace(',', '.', trim((string)$value));
            if ($value === '') {
                continue;
            }
            if (!is_numeric($value)) {
                continue;
            }
            $value = (float)$value;
            if ($value < 0) {
                $value = 0;
            }
            if ($value > 300) {
                $value = 300;
            }
            $clean[$key] = $this->formatNumber($value);
        }
        return $clean;
    }

    private function loadAssignedOzonTypes($feature_code)
    {
        $this->ui_feature_warning = '';
        $model = new waModel();
        $feature = $model->query(
            "SELECT id, type, name FROM shop_feature WHERE code = s:code LIMIT 1",
            array('code' => $feature_code)
        )->fetchAssoc();

        if (!$feature) {
            $this->ui_feature_warning = 'Характеристика ' . $feature_code . ' не найдена в Webasyst.';
            return array();
        }

        $feature_id = (int)$feature['id'];
        $columns = $this->getTableColumns('shop_product_features');
        if (!in_array('product_id', $columns, true) || !in_array('feature_id', $columns, true)) {
            $this->ui_feature_warning = 'Не удалось прочитать значения характеристики ' . $feature_code . '.';
            return array();
        }

        $value_id_column = null;
        foreach (array('feature_value_id', 'value_id') as $candidate) {
            if (in_array($candidate, $columns, true)) {
                $value_id_column = $candidate;
                break;
            }
        }

        $result = array();
        if ($value_id_column) {
            $rows = $model->query(
                "SELECT `" . $value_id_column . "` AS value_id, COUNT(DISTINCT product_id) AS product_count "
                . "FROM shop_product_features WHERE feature_id = i:feature_id "
                . "AND `" . $value_id_column . "` IS NOT NULL AND `" . $value_id_column . "` <> 0 "
                . "GROUP BY `" . $value_id_column . "`",
                array('feature_id' => $feature_id)
            )->fetchAll();

            $value_ids = array();
            foreach ($rows as $row) {
                $value_ids[(int)$row['value_id']] = (int)$row['value_id'];
            }
            $dict = $this->loadFeatureDictionaryValues($feature_id, array_values($value_ids));

            foreach ($rows as $row) {
                $id = (int)$row['value_id'];
                if (!isset($dict[$id])) {
                    continue;
                }
                $tip = trim((string)$dict[$id]);
                if ($tip === '') {
                    continue;
                }
                $normalized = $this->normalizeTip($tip);
                if (!isset($result[$normalized])) {
                    $result[$normalized] = array('tip' => $tip, 'count' => 0);
                }
                $result[$normalized]['count'] += (int)$row['product_count'];
            }
        }

        if (!$result) {
            foreach (array('value_varchar', 'value_text', 'value_decimal', 'value_double', 'value_int') as $direct_column) {
                if (!in_array($direct_column, $columns, true)) {
                    continue;
                }
                $rows = $model->query(
                    "SELECT `" . $direct_column . "` AS direct_value, COUNT(DISTINCT product_id) AS product_count "
                    . "FROM shop_product_features WHERE feature_id = i:feature_id "
                    . "AND `" . $direct_column . "` IS NOT NULL AND `" . $direct_column . "` <> '' "
                    . "GROUP BY `" . $direct_column . "`",
                    array('feature_id' => $feature_id)
                )->fetchAll();
                foreach ($rows as $row) {
                    $tip = trim((string)$row['direct_value']);
                    if ($tip === '') {
                        continue;
                    }
                    $normalized = $this->normalizeTip($tip);
                    if (!isset($result[$normalized])) {
                        $result[$normalized] = array('tip' => $tip, 'count' => 0);
                    }
                    $result[$normalized]['count'] += (int)$row['product_count'];
                }
                if ($result) {
                    break;
                }
            }
        }

        $result = array_values($result);
        usort($result, array($this, 'sortTypeRows'));
        return $result;
    }

    public function sortTypeRows($a, $b)
    {
        return strcmp($this->normalizeTip($a['tip']), $this->normalizeTip($b['tip']));
    }

    private function getTableColumns($table)
    {
        $columns = array();
        try {
            $model = new waModel();
            $rows = $model->query('SHOW COLUMNS FROM `' . $table . '`')->fetchAll();
            foreach ($rows as $row) {
                if (isset($row['Field'])) {
                    $columns[] = $row['Field'];
                }
            }
        } catch (Exception $e) {
        }
        return $columns;
    }

    private function loadFeatureDictionaryValues($feature_id, $value_ids)
    {
        $dict = array();
        if (!$value_ids) {
            return $dict;
        }
        $model = new waModel();
        foreach (array(
            'shop_feature_values_varchar',
            'shop_feature_values_text',
            'shop_feature_values_int',
            'shop_feature_values_double',
            'shop_feature_values_decimal',
            'shop_feature_values_color'
        ) as $table) {
            try {
                $columns = $this->getTableColumns($table);
                if (!$columns || !in_array('value', $columns, true)) {
                    continue;
                }
                $rows = $model->query(
                    "SELECT id, value FROM `" . $table . "` WHERE feature_id = i:feature_id AND id IN (i:value_ids)",
                    array('feature_id' => $feature_id, 'value_ids' => $value_ids)
                )->fetchAll();
                foreach ($rows as $row) {
                    $dict[(int)$row['id']] = (string)$row['value'];
                }
            } catch (Exception $e) {
            }
        }
        return $dict;
    }

    private function getUiCommissionForTip($tip)
    {
        if ($this->ui_commission_map === null) {
            $file = dirname(__FILE__) . '/config/rfbs_commissions.php';
            $this->ui_commission_map = file_exists($file) ? include($file) : array();
        }
        $key = $this->normalizeTip($tip);
        return isset($this->ui_commission_map[$key]) ? $this->ui_commission_map[$key] : null;
    }

    private function commissionDisplay($commission, $extra_percent)
    {
        if (!is_array($commission) || !isset($commission[0])) {
            return '—';
        }
        $rates = array();
        foreach (array(0, 1, 2, 3) as $idx) {
            if (isset($commission[$idx])) {
                $rates[] = round(((float)$commission[$idx] * 100) + (float)$extra_percent, 4);
            }
        }
        if (!$rates) {
            return '—';
        }

        $unique = array_values(array_unique(array_map(array($this, 'formatNumber'), $rates)));
        if (count($unique) === 1) {
            $display = $unique[0] . '%';
        } else {
            $min = min($rates);
            $max = max($rates);
            $display = '<span title="Ставка зависит от ценового диапазона">' . $this->e($this->formatNumber($min) . '–' . $this->formatNumber($max) . '%') . '</span>';
        }

        if (isset($commission['over_500000'])) {
            $over = ((float)$commission['over_500000'] * 100) + (float)$extra_percent;
            $display .= '<small class="ozs-rate-note">&gt;500 000 ₽: ' . $this->e($this->formatPercent($over)) . '</small>';
        }
        return $display;
    }

    private function normalizeTip($value)
    {
        $value = trim((string)$value);
        $value = str_replace(array('ё', 'Ё'), array('е', 'Е'), $value);
        if (function_exists('mb_strtolower')) {
            $value = mb_strtolower($value, 'UTF-8');
        } else {
            $value = strtolower($value);
        }
        return preg_replace('/\s+/u', ' ', $value);
    }

    private function formatPercent($value)
    {
        return $this->formatNumber((float)$value) . '%';
    }

    private function formatNumber($value)
    {
        $value = round((float)$value, 4);
        if (abs($value - round($value)) < 0.00001) {
            return (string)(int)round($value);
        }
        return rtrim(rtrim(number_format($value, 4, '.', ''), '0'), '.');
    }

    private function e($value)
    {
        return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
    }

    private function settingsCss()
    {
        return '<style>
        .ozs-settings{max-width:1180px;padding:4px 0 18px;color:var(--text-color,#222)}
        .ozs-hero{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:0 0 16px}
        .ozs-title{font-size:24px;font-weight:700;line-height:1.2}
        .ozs-subtitle{margin-top:4px;color:var(--text-color-hint,#777);font-size:13px}
        .ozs-version{border:1px solid var(--border-color-soft,#ddd);border-radius:999px;padding:5px 10px;font-size:12px;color:var(--text-color-hint,#777);white-space:nowrap}
        .ozs-summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:14px}
        .ozs-summary{border:1px solid var(--border-color-soft,#e5e5e5);border-radius:12px;padding:12px 14px;background:var(--background-color-blank,#fff)}
        .ozs-summary span,.ozs-summary small{display:block;color:var(--text-color-hint,#777);font-size:12px}
        .ozs-summary b{display:block;font-size:22px;line-height:1.25;margin:3px 0}
        .ozs-card{border:1px solid var(--border-color-soft,#dedede);border-radius:12px;background:var(--background-color-blank,#fff);margin:0 0 10px;overflow:hidden}
        .ozs-card>summary,.ozs-account>summary{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:14px}
        .ozs-card>summary::-webkit-details-marker,.ozs-account>summary::-webkit-details-marker{display:none}
        .ozs-card>summary{padding:15px 16px}
        .ozs-card>summary span:first-child{display:flex;flex-direction:column;gap:3px}
        .ozs-card>summary b{font-size:16px}
        .ozs-card>summary small{font-size:12px;color:var(--text-color-hint,#777);font-weight:400}
        .ozs-chevron{font-size:20px;transition:transform .16s ease;color:var(--text-color-hint,#777)}
        .ozs-card[open]>summary .ozs-chevron{transform:rotate(180deg)}
        .ozs-card-body{border-top:1px solid var(--border-color-soft,#eee);padding:16px}
        .ozs-switch-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:16px}
        .ozs-switch-item{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--border-color-soft,#eee);border-radius:10px;padding:11px 12px;cursor:pointer}
        .ozs-switch-item input[type=checkbox]{margin-top:3px}
        .ozs-switch-copy{display:flex;flex-direction:column;gap:3px}
        .ozs-switch-copy small{font-size:12px;color:var(--text-color-hint,#777);font-weight:400}
        .ozs-form-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
        .ozs-field{display:flex;flex-direction:column;gap:5px;min-width:0}
        .ozs-field>span{font-size:13px;font-weight:600}
        .ozs-field>small,.ozs-hint{font-size:12px;color:var(--text-color-hint,#777)}
        .ozs-field input,.ozs-field textarea,.ozs-search,.ozs-margin{box-sizing:border-box;width:100%;max-width:none!important}
        .ozs-field textarea{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
        .ozs-field-wide{margin-top:12px}
        .ozs-formula{margin-top:14px;padding:10px 12px;border-radius:8px;background:var(--background-color-input,#f7f7f7);font-size:12px;color:var(--text-color-hint,#666)}
        .ozs-type-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
        .ozs-search{max-width:360px!important}
        .ozs-legend{font-size:12px;color:var(--text-color-hint,#777);text-align:right}
        .ozs-table-wrap{overflow:auto;border:1px solid var(--border-color-soft,#e7e7e7);border-radius:10px}
        .ozs-table{width:100%;border-collapse:collapse;min-width:820px}
        .ozs-table th,.ozs-table td{padding:9px 10px;border-bottom:1px solid var(--border-color-soft,#eee);text-align:left;vertical-align:middle;font-size:13px}
        .ozs-table th{position:sticky;top:0;background:var(--background-color-blank,#fff);z-index:1;color:var(--text-color-hint,#666);font-size:12px}
        .ozs-table tr:last-child td{border-bottom:0}
        .ozs-num{text-align:right!important;white-space:nowrap}
        .ozs-margin-cell{width:125px}
        .ozs-margin{min-width:100px}
        .ozs-rate-note{display:block;margin-top:2px;color:var(--text-color-hint,#777);font-size:10px}
        .ozs-bad{color:var(--red,#d33);font-weight:600}
        .ozs-row-warning{background:rgba(255,180,0,.06)}
        .ozs-alert{border-radius:8px;padding:9px 11px;margin:0 0 10px;font-size:12px}
        .ozs-alert-warning{background:rgba(255,180,0,.11)}
        .ozs-empty{padding:22px;text-align:center;color:var(--text-color-hint,#777)}
        .ozs-hint{margin-top:9px}
        .ozs-accounts{display:flex;flex-direction:column;gap:8px}
        .ozs-account{border:1px solid var(--border-color-soft,#e8e8e8);border-radius:9px;overflow:hidden}
        .ozs-account>summary{padding:10px 12px}
        .ozs-account-state{font-size:11px;color:var(--text-color-hint,#888);border-radius:999px;padding:3px 8px;background:var(--background-color-input,#f3f3f3)}
        .ozs-account-state.is-ok{color:var(--green,#178b4e);background:rgba(25,160,90,.09)}
        .ozs-account-body{border-top:1px solid var(--border-color-soft,#eee);padding:12px}
        @media(max-width:900px){.ozs-summary-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.ozs-form-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
        @media(max-width:640px){.ozs-summary-grid,.ozs-form-grid,.ozs-switch-grid{grid-template-columns:1fr}.ozs-type-toolbar{align-items:stretch;flex-direction:column}.ozs-search{max-width:none!important}.ozs-legend{text-align:left}.ozs-title{font-size:21px}}
        </style>';
    }

    private function settingsJs()
    {
        return '<script>(function(){var q=document.getElementById("ozs-type-search");if(!q){return;}q.addEventListener("input",function(){var needle=(q.value||"").toLowerCase().trim();var rows=document.querySelectorAll(".ozs-type-row");for(var i=0;i<rows.length;i++){var hay=(rows[i].getAttribute("data-search")||"").toLowerCase();rows[i].style.display=(!needle||hay.indexOf(needle)!==-1)?"":"none";}});})();</script>';
    }
}
