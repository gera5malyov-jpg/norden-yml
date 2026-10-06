<?php
class shopOzonstocksyncPluginSync
{
    const STOCKS_URL = 'https://api-seller.ozon.ru/v2/products/stocks';
    const PRICES_URL = 'https://api-seller.ozon.ru/v1/product/import/prices';
    const STOCK_BATCH_SIZE = 100;
    const PRICE_BATCH_SIZE = 100;

    private $settings;
    private $log_file = 'ozonstocksync.log';
    private $account_log_file = null;
    private $commission_map = null;

    public function __construct($settings)
    {
        $this->settings = $settings;
    }

    public function run()
    {
        $dry_run = (int)$this->get('dry_run', 1) === 1;
        $update_stocks = (int)$this->get('update_stocks', 1) === 1;
        $update_prices = (int)$this->get('update_prices', 0) === 1;
        $accounts = $this->parseAccounts();

        if (!$accounts) {
            throw new waException('Не заполнены аккаунты Ozon или связки. Нужны Client-Id, Api-Key и хотя бы одна связка.');
        }
        if (!$update_stocks && !$update_prices) {
            throw new waException('Отключены и остатки, и цены. Включите хотя бы один режим.');
        }

        $total_mappings = 0;
        foreach ($accounts as $account) {
            $total_mappings += count($account['mappings']);
        }

        $this->log('START_ACCOUNTS: accounts=' . count($accounts) . ', mappings=' . $total_mappings . ', dry_run=' . ($dry_run ? '1' : '0') . ', update_stocks=' . ($update_stocks ? '1' : '0') . ', update_prices=' . ($update_prices ? '1' : '0'));

        $total_sent_stocks = 0;
        $total_sent_prices = 0;
        $total_errors = 0;

        foreach ($accounts as $account) {
            $this->setAccountLogFile($account['name']);
            $this->log('ACCOUNT_LOG_START: file=' . $this->account_log_file);
            $this->log('ACCOUNT_START: account=' . $account['name'] . ', mappings=' . count($account['mappings']));
            $this->log('ACCOUNT_MODE: account=' . $account['name'] . ', dry_run=' . ($dry_run ? '1' : '0') . ', update_stocks=' . ($update_stocks ? '1' : '0') . ', update_prices=' . ($update_prices ? '1' : '0'));

            $account_price_items = array();

            foreach ($account['mappings'] as $mapping) {
                $label = 'account=' . $account['name'] . ', set_id=' . $mapping['set_id'] . ', ozon_warehouse_id=' . $mapping['ozon_warehouse_id'] . ', webasyst_stock_ids=' . ($mapping['webasyst_stock_id'] === '' ? 'general' : $mapping['webasyst_stock_id']) . ', reserve=' . $mapping['reserve'];

                if ($update_stocks) {
                    $items = $this->collectStocks($mapping);
                    $this->log('MAPPING_STOCKS_START: ' . $label . ', collected=' . count($items));
                    $sent = 0; $errors = 0;
                    foreach (array_chunk($items, $this->getPositiveInt('stock_batch_size', self::STOCK_BATCH_SIZE)) as $batch) {
                        if ($dry_run) {
                            $this->log('DRY_RUN_STOCKS ' . $label . ' batch size=' . count($batch) . ' sample=' . json_encode(array_slice($batch, 0, 3), JSON_UNESCAPED_UNICODE));
                            $sent += count($batch);
                            continue;
                        }
                        $response = $this->sendStocksToOzonWithRetry($batch, $account['client_id'], $account['api_key'], $label);
                        if ($response['ok']) {
                            $sent += count($batch);
                            $this->log('OK_STOCKS ' . $label . ' batch size=' . count($batch) . ' http=' . $response['code']);
                        } else {
                            $errors += count($batch);
                            $this->log('ERROR_STOCKS ' . $label . ' batch size=' . count($batch) . ' http=' . $response['code'] . ' body=' . $response['body']);
                        }
                        $this->sleepIfPositive($this->getPositiveInt('stock_batch_pause_seconds', 2), 'STOCK_BATCH_WAIT ' . $label);
                    }
                    $total_sent_stocks += $sent;
                    $total_errors += $errors;
                    $this->log('MAPPING_STOCKS_FINISH: ' . $label . ', sent=' . $sent . ', errors=' . $errors);
                }

                if ($update_prices) {
                    $price_items = $this->collectPrices($mapping, $this->getForcePriceTouchDelta());
                    foreach ($price_items as $item) {
                        $account_price_items[$item['offer_id']] = $item;
                    }
                    $this->log('MAPPING_PRICES_COLLECTED: ' . $label . ', collected=' . count($price_items));
                }

                if ($update_prices) {
                    $this->sleepIfPositive($this->getPositiveInt('price_mapping_pause_seconds', 5), 'PRICE_MAPPING_WAIT ' . $label);
                }
            }

            if ($update_prices) {
                $price_items = array_values($account_price_items);
                $this->log('ACCOUNT_PRICES_START: account=' . $account['name'] . ', unique_prices=' . count($price_items));
                $sent = 0; $errors = 0;
                $this->sleepIfPositive($this->getPositiveInt('price_pre_wait_seconds', 60), 'PRE_PRICE_SEND_WAIT account=' . $account['name']);
                foreach (array_chunk($price_items, $this->getPositiveInt('price_batch_size', 50)) as $batch) {
                    if ($dry_run) {
                        $this->log('DRY_RUN_PRICES account=' . $account['name'] . ' batch size=' . count($batch) . ' sample=' . json_encode(array_slice($batch, 0, 3), JSON_UNESCAPED_UNICODE));
                        $sent += count($batch);
                        continue;
                    }
                    $response = $this->sendPricesToOzonWithRetry($batch, $account['client_id'], $account['api_key'], 'account=' . $account['name']);
                    if ($response['ok']) {
                        $sent += count($batch);
                        $this->log('OK_PRICES account=' . $account['name'] . ' batch size=' . count($batch) . ' http=' . $response['code']);
                    } else {
                        $errors += count($batch);
                        $this->log('ERROR_PRICES account=' . $account['name'] . ' batch size=' . count($batch) . ' http=' . $response['code'] . ' body=' . $response['body']);
                    }
                    $this->sleepIfPositive($this->getPositiveInt('price_batch_pause_seconds', 10), 'PRICE_BATCH_WAIT account=' . $account['name']);
                }
                $total_sent_prices += $sent;
                $total_errors += $errors;
                $this->log('ACCOUNT_PRICES_FINISH: account=' . $account['name'] . ', sent=' . $sent . ', errors=' . $errors);
            }

            $this->log('ACCOUNT_FINISH: account=' . $account['name']);
            $this->account_log_file = null;
        }

        $this->log('FINISH_ACCOUNTS: accounts=' . count($accounts) . ', sent_stocks=' . $total_sent_stocks . ', sent_prices=' . $total_sent_prices . ', errors=' . $total_errors);
        return array('accounts' => count($accounts), 'sent_stocks' => $total_sent_stocks, 'sent_prices' => $total_sent_prices, 'errors' => $total_errors, 'dry_run' => $dry_run);
    }

    private function parseAccounts()
    {
        $accounts = array();
        for ($i = 1; $i <= 10; $i++) {
            $suffix = ($i === 1) ? '' : '_' . $i;
            $client_id = trim((string)$this->get('client_id' . $suffix));
            $api_key = trim((string)$this->get('api_key' . $suffix));
            $mappings = $this->parseMappings((string)$this->get('mappings' . $suffix, ''));

            if ($i === 1 && !$mappings) {
                $fallback = $this->parseFallbackMapping();
                if ($fallback) {
                    $mappings[] = $fallback;
                }
            }

            $account_name = trim((string)$this->get('account_name' . $suffix, ''));
            if ($account_name === '') {
                $account_name = 'ozon_' . $i;
            }

            if ($client_id && $api_key && $mappings) {
                $accounts[] = array(
                    'index' => $i,
                    'name' => $account_name,
                    'client_id' => $client_id,
                    'api_key' => $api_key,
                    'mappings' => $mappings
                );
            } elseif ($client_id || $api_key || $mappings) {
                $this->log('SKIP_ACCOUNT_' . $i . ': name=' . $account_name . ', должны быть заполнены Client-Id, Api-Key и хотя бы одна связка');
            }
        }
        return $accounts;
    }

    private function parseMappings($raw)
    {
        $raw = trim((string)$raw);
        $mappings = array();
        if ($raw === '') {
            return $mappings;
        }
        $lines = preg_split('/\r\n|\r|\n/', $raw);
        foreach ($lines as $line) {
            $line = trim($line);
            if ($line === '' || substr($line, 0, 1) === '#') {
                continue;
            }
            $parts = array_map('trim', explode(';', $line));
            $set_id = isset($parts[0]) ? $parts[0] : '';
            $ozon_warehouse_id = isset($parts[1]) ? (int)$parts[1] : 0;
            $webasyst_stock_id = isset($parts[2]) ? $this->normalizeStockIdsString($parts[2]) : '';
            $reserve = isset($parts[3]) && $parts[3] !== '' ? (int)$parts[3] : 0;
            if ($set_id === '' || !$ozon_warehouse_id) {
                $this->log('SKIP_BAD_MAPPING: ' . $line);
                continue;
            }
            $mappings[] = array('set_id' => $set_id, 'ozon_warehouse_id' => $ozon_warehouse_id, 'webasyst_stock_id' => $webasyst_stock_id, 'reserve' => max(0, $reserve));
        }
        return $mappings;
    }

    private function normalizeStockIdsString($value)
    {
        $ids = $this->parseStockIds($value);
        return $ids ? implode(',', $ids) : '';
    }

    private function parseStockIds($value)
    {
        if (is_array($value)) {
            $parts = $value;
        } else {
            $parts = preg_split('/[\\s,|]+/', trim((string)$value));
        }
        $ids = array();
        foreach ((array)$parts as $part) {
            $id = (int)trim((string)$part);
            if ($id > 0) {
                $ids[$id] = $id;
            }
        }
        return array_values($ids);
    }

    private function parseFallbackMapping()
    {
        $set_id = trim((string)$this->get('set_id', ''));
        $ozon_warehouse_id = (int)$this->get('ozon_warehouse_id', 0);
        if ($set_id !== '' && $ozon_warehouse_id) {
            return array('set_id' => $set_id, 'ozon_warehouse_id' => $ozon_warehouse_id, 'webasyst_stock_id' => $this->normalizeStockIdsString((string)$this->get('webasyst_stock_id', '')), 'reserve' => max(0, (int)$this->get('reserve', 0)));
        }
        return null;
    }

    private function collectStocks($mapping)
    {
        $ozon_warehouse_id = (int)$mapping['ozon_warehouse_id'];
        $webasyst_stock_id = $this->normalizeStockIdsString(isset($mapping['webasyst_stock_id']) ? $mapping['webasyst_stock_id'] : '');
        $webasyst_stock_ids = $this->parseStockIds($webasyst_stock_id);
        $set_id = trim((string)$mapping['set_id']);
        $reserve = max(0, (int)$mapping['reserve']);
        $only_available = (int)$this->get('only_available', 1) === 1;
        $model = new waModel();
        $params = array('set_id' => $set_id);

        if ($webasyst_stock_ids) {
            $sql = "SELECT DISTINCT s.id, s.sku, COALESCE(ps.stock_count, 0) AS stock_count, s.available, s.status, p.status AS product_status
                FROM shop_product_skus s
                INNER JOIN shop_product p ON p.id = s.product_id
                INNER JOIN shop_set_products sp ON sp.product_id = s.product_id AND sp.set_id = s:set_id
                LEFT JOIN (
                    SELECT sku_id, SUM(COALESCE(count, 0)) AS stock_count
                    FROM shop_product_stocks
                    WHERE stock_id IN (i:stock_ids)
                    GROUP BY sku_id
                ) ps ON ps.sku_id = s.id
                WHERE s.sku IS NOT NULL AND s.sku <> ''";
            $params['stock_ids'] = $webasyst_stock_ids;
        } else {
            $sql = "SELECT DISTINCT s.id, s.sku, COALESCE(s.count, 0) AS stock_count, s.available, s.status, p.status AS product_status
                FROM shop_product_skus s
                INNER JOIN shop_product p ON p.id = s.product_id
                INNER JOIN shop_set_products sp ON sp.product_id = s.product_id AND sp.set_id = s:set_id
                WHERE s.sku IS NOT NULL AND s.sku <> ''";
        }
        $sql .= " ORDER BY s.id";
        $rows = $model->query($sql, $params)->fetchAll();
        $this->log('FILTER_STOCKS: set_id=' . $set_id . ', ozon_warehouse_id=' . $ozon_warehouse_id . ', webasyst_stock_ids=' . ($webasyst_stock_id === '' ? 'general' : $webasyst_stock_id) . ', rows=' . count($rows));
        $items = array();
        foreach ($rows as $row) {
            $offer_id = trim((string)$row['sku']);
            if ($offer_id === '') continue;
            $is_hidden_or_unavailable = ((int)$row['available'] !== 1 || (int)$row['status'] !== 1 || (isset($row['product_status']) && (int)$row['product_status'] !== 1));
            if ($is_hidden_or_unavailable) {
                // Important: do not skip hidden/unavailable products. Send stock=0 to Ozon
                // so the marketplace removes the offer from sale instead of keeping old stock.
                $stock = 0;
            } else {
                $stock = (int)floor((float)$row['stock_count']) - $reserve;
                if ($stock < 0) $stock = 0;
            }
            $items[] = array('offer_id' => $offer_id, 'warehouse_id' => $ozon_warehouse_id, 'stock' => $stock);
        }
        return $items;
    }

    private function collectPrices($mapping, $force_price_touch_delta = 0)
    {
        $set_id = trim((string)$mapping['set_id']);
        $only_available = (int)$this->get('only_available', 1) === 1;
        $model = new waModel();

        $sku_columns = $this->getTableColumns('shop_product_skus');
        $cost_column = $this->detectCostColumn($sku_columns);
        if (!$cost_column) {
            $this->log('PRICE_COST_COLUMN_NOT_FOUND: table=shop_product_skus, available_columns=' . implode(',', $sku_columns));
            return array();
        }

        $sql = "SELECT DISTINCT s.id AS sku_id, s.product_id, s.sku, COALESCE(s.`" . $cost_column . "`, 0) AS purchase_price, s.available, s.status
            FROM shop_product_skus s
            INNER JOIN shop_set_products sp ON sp.product_id = s.product_id AND sp.set_id = s:set_id
            WHERE s.sku IS NOT NULL AND s.sku <> ''";
        if ($only_available) {
            $sql .= " AND s.available = 1 AND s.status = 1";
        }
        $sql .= " ORDER BY s.id";
        $rows = $model->query($sql, array('set_id' => $set_id))->fetchAll();

        $product_ids = array();
        foreach ($rows as $row) {
            $product_ids[(int)$row['product_id']] = (int)$row['product_id'];
        }
        $tips = $this->loadTipOzonValues(array_values($product_ids));

        $items = array();
        $skipped_no_cost = 0; $skipped_no_tip = 0; $skipped_no_commission = 0; $skipped_bad_price = 0;
        if ($force_price_touch_delta !== 0) {
            $this->log('FORCE_PRICE_TOUCH: set_id=' . $set_id . ', min_price_delta=' . $force_price_touch_delta . ', visible_price_unchanged=1');
        }
        foreach ($rows as $row) {
            $offer_id = trim((string)$row['sku']);
            $cost = (float)$row['purchase_price'];
            $product_id = (int)$row['product_id'];
            if ($offer_id === '') continue;

            if ($cost <= 0) {
                $skipped_no_cost++;
                $this->log('PRICE_ERROR_NO_COST: offer_id=' . $offer_id . ', product_id=' . $product_id . ', purchase_price=' . $cost);
                continue;
            }
            if (!isset($tips[$product_id]) || trim((string)$tips[$product_id]) === '') {
                $skipped_no_tip++;
                $this->log('PRICE_ERROR_NO_TIP: offer_id=' . $offer_id . ', product_id=' . $product_id . ', feature_code=' . (string)$this->get('tip_feature_code', 'tip_ozon'));
                continue;
            }
            $tip = trim((string)$tips[$product_id]);
            $commissions = $this->getCommissionForTip($tip);
            if (!$commissions) {
                $skipped_no_commission++;
                $this->log('PRICE_ERROR_NO_COMMISSION: offer_id=' . $offer_id . ', product_id=' . $product_id . ', tip_ozon=' . $tip . ', normalized_tip=' . $this->normalizeTip($tip));
                continue;
            }
            $price_data = $this->calculatePrices($cost, $commissions, $tip);
            if (!$price_data) {
                $skipped_bad_price++;
                $this->log('PRICE_SKIP_BAD_FORMULA: offer_id=' . $offer_id . ', tip_ozon=' . $tip . ', cost=' . $cost);
                continue;
            }
            $min_price_for_ozon = (int)$price_data['min_price'];
            // Force Ozon to process the price row even when the visible sale price did not change.
            // Ozon ignores a completely identical price payload as a no-op. We keep the sale price
            // unchanged and only alternate min_price by 1 RUB when it is safe: min_price must remain
            // positive and strictly below price.
            if ($force_price_touch_delta !== 0 && $min_price_for_ozon > 1 && $min_price_for_ozon < (int)$price_data['price']) {
                $touched_min_price = $min_price_for_ozon + $force_price_touch_delta;
                if ($touched_min_price > 0 && $touched_min_price < (int)$price_data['price']) {
                    $min_price_for_ozon = $touched_min_price;
                }
            }
            $items[] = array('offer_id' => $offer_id, 'price' => (string)$price_data['price'], 'old_price' => (string)$price_data['old_price'], 'min_price' => (string)$min_price_for_ozon, 'currency_code' => 'RUB');
        }
        $this->log('FILTER_PRICES: set_id=' . $set_id . ', rows=' . count($rows) . ', prices=' . count($items) . ', skipped_no_cost=' . $skipped_no_cost . ', skipped_no_tip=' . $skipped_no_tip . ', skipped_no_commission=' . $skipped_no_commission . ', skipped_bad_price=' . $skipped_bad_price);
        return $items;
    }

    private function detectCostColumn($columns)
    {
        $preferred = trim((string)$this->get('purchase_price_column', ''));
        if ($preferred !== '' && in_array($preferred, $columns, true)) return $preferred;
        foreach (array('purchase_price', 'buy_price', 'cost_price', 'cost', 'purchase') as $candidate) {
            if (in_array($candidate, $columns, true)) return $candidate;
        }
        return null;
    }

    private function getTableColumns($table)
    {
        $model = new waModel();
        $columns = array();
        try {
            $rows = $model->query('SHOW COLUMNS FROM `' . $table . '`')->fetchAll();
            foreach ($rows as $row) {
                if (isset($row['Field'])) $columns[] = $row['Field'];
            }
        } catch (Exception $e) {
            // Some Webasyst installations do not have all optional feature value tables.
            // Missing/unsupported tables are not fatal for Ozon sync. Keep logs clean.
        }
        return $columns;
    }

    private function loadTipOzonValues($product_ids)
    {
        $result = array();
        if (!$product_ids) return $result;
        $model = new waModel();
        $feature_code = (string)$this->get('tip_feature_code', 'tip_ozon');
        $feature = $model->query("SELECT id, type FROM shop_feature WHERE code = s:code LIMIT 1", array('code' => $feature_code))->fetchAssoc();
        if (!$feature) {
            $this->log('PRICE_FEATURE_NOT_FOUND: code=' . $feature_code);
            return $result;
        }
        $feature_id = (int)$feature['id'];
        $columns = array();
        try {
            $desc = $model->query("SHOW COLUMNS FROM shop_product_features")->fetchAll();
            foreach ($desc as $col) {
                if (isset($col['Field'])) $columns[$col['Field']] = true;
            }
        } catch (Exception $e) {
            $this->log('PRICE_SCHEMA_ERROR: ' . $e->getMessage());
        }
        if (!isset($columns['product_id']) || !isset($columns['feature_id'])) {
            $this->log('PRICE_SCHEMA_BAD: shop_product_features has no product_id/feature_id');
            return $result;
        }
        $select = array('product_id');
        foreach (array('feature_value_id', 'value_id', 'value_int', 'value_double', 'value_decimal', 'value_varchar', 'value_text') as $column) {
            if (isset($columns[$column])) $select[] = $column;
        }
        $rows = $model->query("SELECT " . implode(', ', $select) . " FROM shop_product_features WHERE feature_id = i:feature_id AND product_id IN (i:product_ids)", array('feature_id' => $feature_id, 'product_ids' => $product_ids))->fetchAll();
        $this->log('PRICE_TIP_ROWS: feature_code=' . $feature_code . ', feature_id=' . $feature_id . ', rows=' . count($rows) . ', selected_columns=' . implode(',', $select));
        $value_ids = array();
        foreach ($rows as $row) {
            if (isset($row['feature_value_id']) && $row['feature_value_id'] !== null && $row['feature_value_id'] !== '') $value_ids[(int)$row['feature_value_id']] = (int)$row['feature_value_id'];
            if (isset($row['value_id']) && $row['value_id'] !== null && $row['value_id'] !== '') $value_ids[(int)$row['value_id']] = (int)$row['value_id'];
        }
        $dict = $this->loadFeatureDictionaryValues($feature_id, array_values($value_ids));
        $this->log('PRICE_TIP_DICT: feature_id=' . $feature_id . ', value_ids=' . count($value_ids) . ', dict=' . count($dict));
        foreach ($rows as $row) {
            $product_id = (int)$row['product_id'];
            $value = '';
            if (isset($row['feature_value_id']) && $row['feature_value_id'] !== null && $row['feature_value_id'] !== '' && isset($dict[(int)$row['feature_value_id']])) $value = $dict[(int)$row['feature_value_id']];
            elseif (isset($row['value_id']) && $row['value_id'] !== null && $row['value_id'] !== '' && isset($dict[(int)$row['value_id']])) $value = $dict[(int)$row['value_id']];
            else {
                foreach (array('value_varchar', 'value_text', 'value_decimal', 'value_double', 'value_int') as $col) {
                    if (isset($row[$col]) && $row[$col] !== null && $row[$col] !== '') {
                        $value = $row[$col]; break;
                    }
                }
            }
            if ($value !== '' && !isset($result[$product_id])) $result[$product_id] = (string)$value;
        }
        return $result;
    }

    private function loadFeatureDictionaryValues($feature_id, $value_ids)
    {
        $dict = array();
        if (!$value_ids) return $dict;
        $model = new waModel();
        foreach (array('shop_feature_values_varchar', 'shop_feature_values_text', 'shop_feature_values_int', 'shop_feature_values_double', 'shop_feature_values_decimal', 'shop_feature_values_color') as $table) {
            try {
                $columns = $this->getTableColumns($table);
                if (!$columns || !in_array('value', $columns, true)) continue;
                $rows = $model->query("SELECT id, value FROM `" . $table . "` WHERE feature_id = i:feature_id AND id IN (i:value_ids)", array('feature_id' => $feature_id, 'value_ids' => $value_ids))->fetchAll();
                foreach ($rows as $row) $dict[(int)$row['id']] = (string)$row['value'];
            } catch (Exception $e) {}
        }
        return $dict;
    }

    private function getCommissionForTip($tip)
    {
        if ($this->commission_map === null) {
            $file = dirname(__FILE__) . '/../config/rfbs_commissions.php';
            $this->commission_map = file_exists($file) ? include($file) : array();
        }
        $key = $this->normalizeTip($tip);
        return isset($this->commission_map[$key]) ? $this->commission_map[$key] : null;
    }

    private function normalizeTip($value)
    {
        $value = trim((string)$value);
        $value = str_replace(array('ё','Ё'), array('е','Е'), $value);
        if (function_exists('mb_strtolower')) $value = mb_strtolower($value, 'UTF-8');
        else $value = strtolower($value);
        return preg_replace('/\s+/u', ' ', $value);
    }

    private function calculatePrices($cost, $commissions, $tip = '')
    {
        $acquiring = max(0, (float)$this->get('acquiring_percent', 2)) / 100;
        $target_markup = $this->getTargetMarkupPercentForTip($tip) / 100;
        $min_markup = max(0, (float)$this->get('min_margin_percent', 18)) / 100;
        $old_multiplier = max(1.01, (float)$this->get('old_price_multiplier', 1.5));
        $round_step = max(1, (int)$this->get('round_step', 5));
        $price = $this->calculatePriceForMarkup($cost, $commissions, $acquiring, $target_markup, $round_step);
        $min_price = $this->calculatePriceForMarkup($cost, $commissions, $acquiring, $min_markup, $round_step);
        if (!$price || !$min_price) return null;
        if ($min_price > $price) $min_price = $price;
        $old_price = $this->ceilToStep($price * $old_multiplier, $round_step);
        if ($old_price <= $price) $old_price = $price + $round_step;
        return array('price' => (int)$price, 'old_price' => (int)$old_price, 'min_price' => (int)$min_price);
    }

    private function getTargetMarkupPercentForTip($tip)
    {
        $default = max(0, (float)$this->get('target_margin_percent', 20));
        $margins = $this->get('type_margin_percent', array());

        if (is_string($margins) && $margins !== '') {
            $decoded = json_decode($margins, true);
            if (is_array($decoded)) {
                $margins = $decoded;
            }
        }
        if (!is_array($margins) || trim((string)$tip) === '') {
            return $default;
        }

        $key = sha1($this->normalizeTip($tip));
        if (!isset($margins[$key]) || $margins[$key] === '' || !is_numeric($margins[$key])) {
            return $default;
        }

        $value = (float)$margins[$key];
        if ($value < 0) {
            $value = 0;
        }
        if ($value > 300) {
            $value = 300;
        }
        return $value;
    }

    private function calculatePriceForMarkup($cost, $commissions, $acquiring, $markup, $round_step)
    {
        $has_over_500000_rate = isset($commissions['over_500000']);
        $tiers = array(
            array('min' => 0, 'max' => 1500, 'commission' => (float)$commissions[0]),
            array('min' => 1500.01, 'max' => 5000, 'commission' => (float)$commissions[1]),
            array('min' => 5000.01, 'max' => 10000, 'commission' => (float)$commissions[2]),
            array('min' => 10000.01, 'max' => $has_over_500000_rate ? 500000 : null, 'commission' => (float)$commissions[3]),
        );
        if ($has_over_500000_rate) {
            $tiers[] = array('min' => 500000.01, 'max' => null, 'commission' => (float)$commissions['over_500000']);
        }
        $candidates = array();
        $required_net = $cost * (1 + $markup);
        foreach ($tiers as $tier) {
            $denominator = 1 - $tier['commission'] - $acquiring;
            if ($denominator <= 0) continue;
            $candidate = $required_net / $denominator;
            if ($candidate < $tier['min']) $candidate = $tier['min'];
            $candidate = $this->ceilToStep($candidate, $round_step);
            $upper_ok = $tier['max'] === null || $candidate <= $tier['max'];
            if ($candidate >= $tier['min'] && $upper_ok) {
                $net_after_fees = $candidate * (1 - $tier['commission'] - $acquiring);
                if ($net_after_fees + 0.000001 >= $required_net) $candidates[] = $candidate;
            }
        }
        if ($candidates) return min($candidates);
        $last = $tiers[count($tiers) - 1];
        $denominator = 1 - $last['commission'] - $acquiring;
        if ($denominator <= 0) return null;
        return $this->ceilToStep($required_net / $denominator, $round_step);
    }

    private function ceilToStep($value, $step)
    {
        // Neutralize insignificant floating-point tails (for example 27500.000000000004)
        // before rounding up to the configured whole-ruble step.
        return (int)(ceil(round($value, 8) / $step) * $step);
    }


    private function getForcePriceTouchDelta()
    {
        // Always alternate min_price by +/-1 RUB between runs. This helps Ozon process the row
        // when the actual visible price is unchanged. The sale price itself is not changed.
        $file = wa()->getDataPath('plugins/ozonstocksync/force_price_touch_state.txt', false, 'shop', true);
        $prev = '0';
        if (is_readable($file)) {
            $prev = trim((string)file_get_contents($file));
        }
        $next = ($prev === '1') ? '-1' : '1';
        try {
            file_put_contents($file, $next, LOCK_EX);
        } catch (Exception $e) {
            $next = ((int)date('i') % 2 === 0) ? '1' : '-1';
        }
        return (int)$next;
    }


    private function sendStocksToOzonWithRetry($stocks, $client_id, $api_key, $label)
    {
        return $this->sendToOzonWithRetry('stocks', $stocks, $client_id, $api_key, $label);
    }

    private function sendPricesToOzonWithRetry($prices, $client_id, $api_key, $label)
    {
        return $this->sendToOzonWithRetry('prices', $prices, $client_id, $api_key, $label);
    }

    private function sendToOzonWithRetry($type, $items, $client_id, $api_key, $label)
    {
        $retries = max(1, $this->getPositiveInt('api_retry_count', 5));
        $retry_wait = $this->getPositiveInt('api_retry_wait_seconds', 180);
        $response = array('ok' => false, 'code' => 0, 'body' => 'not_sent');

        for ($attempt = 1; $attempt <= $retries; $attempt++) {
            $response = ($type === 'prices')
                ? $this->sendPricesToOzon($items, $client_id, $api_key)
                : $this->sendStocksToOzon($items, $client_id, $api_key);

            if ($response['ok']) {
                if ($attempt > 1) {
                    $this->log('RETRY_OK_' . strtoupper($type) . ': ' . $label . ', batch=' . count($items) . ', attempt=' . $attempt . '/' . $retries . ', http=' . $response['code']);
                }
                return $response;
            }

            if (!$this->shouldRetryOzonResponse($response) || $attempt >= $retries) {
                return $response;
            }

            $this->log('RETRY_WAIT_' . strtoupper($type) . ': ' . $label . ', batch=' . count($items) . ', attempt=' . $attempt . '/' . $retries . ', http=' . $response['code'] . ', sleep=' . $retry_wait . 's, body=' . $response['body']);
            $this->sleepIfPositive($retry_wait, 'RETRY_SLEEP_' . strtoupper($type) . ' ' . $label);
        }

        return $response;
    }

    private function shouldRetryOzonResponse($response)
    {
        $code = isset($response['code']) ? (int)$response['code'] : 0;
        if ($code === 0 || $code === 429 || $code >= 500) return true;
        $body = isset($response['body']) ? (string)$response['body'] : '';
        if (stripos($body, 'ResourceExhausted') !== false) return true;
        if (stripos($body, 'limit exceeded') !== false) return true;
        if (stripos($body, 'Too Many Requests') !== false) return true;
        return false;
    }

    private function sendStocksToOzon($stocks, $client_id, $api_key)
    {
        return $this->sendToOzon(self::STOCKS_URL, array('stocks' => $stocks), $client_id, $api_key);
    }

    private function sendPricesToOzon($prices, $client_id, $api_key)
    {
        return $this->sendToOzon(self::PRICES_URL, array('prices' => $prices), $client_id, $api_key);
    }

    private function sendToOzon($url, $payload_array, $client_id, $api_key)
    {
        $payload = json_encode($payload_array, JSON_UNESCAPED_UNICODE);
        $ch = curl_init($url);
        curl_setopt_array($ch, array(
            CURLOPT_POST => true,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => array('Content-Type: application/json', 'Client-Id: ' . $client_id, 'Api-Key: ' . $api_key),
            CURLOPT_POSTFIELDS => $payload,
            CURLOPT_CONNECTTIMEOUT => 20,
            CURLOPT_TIMEOUT => 90,
        ));
        $body = curl_exec($ch);
        $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $err = curl_error($ch);
        curl_close($ch);
        if ($body === false) return array('ok' => false, 'code' => 0, 'body' => $err);
        return array('ok' => $code >= 200 && $code < 300, 'code' => $code, 'body' => $body);
    }

    private function setAccountLogFile($account_name)
    {
        $this->account_log_file = 'ozonstocksync_' . $this->safeLogFileName($account_name) . '.log';
    }

    private function safeLogFileName($value)
    {
        $value = trim((string)$value);
        if (function_exists('mb_strtolower')) $value = mb_strtolower($value, 'UTF-8');
        else $value = strtolower($value);
        $map = array('а'=>'a','б'=>'b','в'=>'v','г'=>'g','д'=>'d','е'=>'e','ё'=>'e','ж'=>'zh','з'=>'z','и'=>'i','й'=>'y','к'=>'k','л'=>'l','м'=>'m','н'=>'n','о'=>'o','п'=>'p','р'=>'r','с'=>'s','т'=>'t','у'=>'u','ф'=>'f','х'=>'h','ц'=>'c','ч'=>'ch','ш'=>'sh','щ'=>'sch','ъ'=>'','ы'=>'y','ь'=>'','э'=>'e','ю'=>'yu','я'=>'ya');
        $value = strtr($value, $map);
        $value = preg_replace('/[^a-z0-9]+/i', '_', $value);
        $value = trim($value, '_');
        return $value === '' ? 'account' : $value;
    }


    private function getPositiveInt($key, $default)
    {
        $value = (int)$this->get($key, $default);
        return $value > 0 ? $value : (int)$default;
    }

    private function sleepIfPositive($seconds, $label = '')
    {
        $seconds = (int)$seconds;
        if ($seconds <= 0) return;
        if ($label !== '') {
            $this->log($label . ': sleep=' . $seconds . 's');
        }
        sleep($seconds);
    }

    private function get($key, $default = null)
    {
        return isset($this->settings[$key]) && $this->settings[$key] !== '' ? $this->settings[$key] : $default;
    }

    private function log($message)
    {
        $line = date('Y-m-d H:i:s') . ' ' . $message;
        waLog::log($line, $this->log_file);
        if ($this->account_log_file) {
            waLog::log($line, $this->account_log_file);
        }
    }
}
