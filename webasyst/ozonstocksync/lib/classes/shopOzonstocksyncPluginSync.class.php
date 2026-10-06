<?php
class shopOzonstocksyncPluginSync
{
    const STOCKS_URL = 'https://api-seller.ozon.ru/v2/products/stocks';
    const PRICES_URL = 'https://api-seller.ozon.ru/v1/product/import/prices';
    const PRODUCT_LIST_URL = 'https://api-seller.ozon.ru/v3/product/list';
    const PRODUCT_INFO_LIST_URL = 'https://api-seller.ozon.ru/v3/product/info/list';
    const PRODUCT_IMPORT_URL = 'https://api-seller.ozon.ru/v3/product/import';
    const PRODUCT_IMPORT_INFO_URL = 'https://api-seller.ozon.ru/v1/product/import/info';
    const CREATE_SET_ID = 'ozon_upload';
    const CREATE_SET_NAME = 'Грузить в Ozon';
    const CREATE_PRIMARY_ACCOUNT_INDEX = 1;
    const CREATE_FALLBACK_DEPTH_CM = 50;
    const CREATE_FALLBACK_HEIGHT_CM = 100;
    const CREATE_FALLBACK_WIDTH_CM = 50;
    const CREATE_FALLBACK_WEIGHT_KG = 31;
    const STOCK_BATCH_SIZE = 100;
    const PRICE_BATCH_SIZE = 100;
    const CREATE_BATCH_SIZE = 1;
    const LOW_COST_THRESHOLD_RUB = 2000;
    const LOW_COST_TARGET_MARKUP_PERCENT = 50;

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
        $create_candidates = $this->collectCreateCandidates();

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

        $this->log('START_ACCOUNTS: accounts=' . count($accounts) . ', mappings=' . $total_mappings . ', dry_run=' . ($dry_run ? '1' : '0') . ', update_stocks=' . ($update_stocks ? '1' : '0') . ', update_prices=' . ($update_prices ? '1' : '0') . ', create_allowlist=' . count($create_candidates));

        $total_sent_stocks = 0;
        $total_sent_prices = 0;
        $total_create_submitted = 0;
        $total_create_would_create = 0;
        $total_create_skipped_existing = 0;
        $total_create_errors = 0;
        $total_errors = 0;

        foreach ($accounts as $account) {
            $this->setAccountLogFile($account['name']);
            $this->log('ACCOUNT_LOG_START: file=' . $this->account_log_file);
            $this->log('ACCOUNT_START: account=' . $account['name'] . ', mappings=' . count($account['mappings']));
            $this->log('ACCOUNT_MODE: account=' . $account['name'] . ', dry_run=' . ($dry_run ? '1' : '0') . ', update_stocks=' . ($update_stocks ? '1' : '0') . ', update_prices=' . ($update_prices ? '1' : '0'));

            // Create only explicitly allowed, genuinely missing cards first.
            // This keeps the existing stock/price synchronizer independent, while allowing
            // the newly created offer to receive its normal stock/price update in this run.
            $create_result = $this->createMissingProductsForAccount($account, $create_candidates, $dry_run);
            $total_create_submitted += $create_result['submitted'];
            $total_create_would_create += $create_result['would_create'];
            $total_create_skipped_existing += $create_result['skipped_existing'];
            $total_create_errors += $create_result['errors'];
            $total_errors += $create_result['errors'];

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

        $this->log('FINISH_ACCOUNTS: accounts=' . count($accounts) . ', sent_stocks=' . $total_sent_stocks . ', sent_prices=' . $total_sent_prices . ', create_submitted=' . $total_create_submitted . ', create_would_create=' . $total_create_would_create . ', create_skipped_existing=' . $total_create_skipped_existing . ', create_errors=' . $total_create_errors . ', errors=' . $total_errors);
        return array(
            'accounts' => count($accounts),
            'sent_stocks' => $total_sent_stocks,
            'sent_prices' => $total_sent_prices,
            'create_submitted' => $total_create_submitted,
            'create_would_create' => $total_create_would_create,
            'create_skipped_existing' => $total_create_skipped_existing,
            'create_errors' => $total_create_errors,
            'errors' => $total_errors,
            'dry_run' => $dry_run
        );
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

    /**
     * Creation allow-list. Permission to create comes exclusively from this one list.
     * New cards are created only in the primary Ozon account. Existing stock/price
     * mappings remain independent and are not used as additional creation permissions.
     */
    private function collectCreateCandidates()
    {
        $model = new waModel();

        try {
            $set = $model->query(
                "SELECT id, name, type, count FROM shop_set WHERE id = s:id LIMIT 1",
                array('id' => self::CREATE_SET_ID)
            )->fetchAssoc();
        } catch (Exception $e) {
            $this->log('CREATE_ALLOWLIST_ERROR: cannot read set: ' . $e->getMessage());
            return array();
        }

        if (!$set) {
            $this->log('CREATE_ALLOWLIST_MISSING: id=' . self::CREATE_SET_ID . ', expected_name=' . self::CREATE_SET_NAME);
            return array();
        }

        if ((string)$set['name'] !== self::CREATE_SET_NAME || (int)$set['type'] !== 0) {
            $this->log(
                'CREATE_ALLOWLIST_INVALID: id=' . self::CREATE_SET_ID
                . ', actual_name=' . (string)$set['name']
                . ', type=' . (int)$set['type']
                . ', expected_name=' . self::CREATE_SET_NAME
                . ', expected_type=0'
            );
            return array();
        }

        $sku_columns = $this->getTableColumns('shop_product_skus');
        $cost_column = $this->detectCostColumn($sku_columns);
        $cost_select = $cost_column
            ? 'COALESCE(s.' . $cost_column . ', 0)'
            : '0';

        try {
            $rows = $model->query(
                "SELECT DISTINCT
                    p.id AS product_id,
                    p.name AS product_name,
                    p.status AS product_status,
                    s.id AS sku_id,
                    s.sku,
                    s.name AS sku_name,
                    s.available,
                    s.status AS sku_status,
                    COALESCE(s.count, 0) AS stock_count,
                    " . $cost_select . " AS purchase_price
                 FROM shop_set_products sp
                 INNER JOIN shop_product p ON p.id = sp.product_id
                 INNER JOIN shop_product_skus s ON s.product_id = p.id
                 WHERE sp.set_id = s:set_id
                   AND s.sku IS NOT NULL
                   AND s.sku <> ''
                   AND p.status = 1
                   AND s.status = 1
                   AND s.available = 1
                   AND COALESCE(s.count, 0) > 0
                 ORDER BY p.id, s.id",
                array('set_id' => self::CREATE_SET_ID)
            )->fetchAll();
        } catch (Exception $e) {
            $this->log('CREATE_ALLOWLIST_ERROR: cannot read products: ' . $e->getMessage());
            return array();
        }

        $this->log(
            'CREATE_ALLOWLIST: id=' . self::CREATE_SET_ID
            . ', name=' . self::CREATE_SET_NAME
            . ', products=' . (int)$set['count']
            . ', in_stock_active_sku_rows=' . count($rows)
        );

        return $rows;
    }

    private function routeCreateCandidatesForAccount($account, $candidates)
    {
        if (!$candidates) {
            return array();
        }

        // There is one global allow-list, therefore product creation must have one
        // deterministic target account. Otherwise the same SKU can be imported into
        // several seller accounts just because it belongs to several old sync sets.
        if ((int)$account['index'] !== self::CREATE_PRIMARY_ACCOUNT_INDEX) {
            $this->log(
                'CREATE_ROUTE_SKIP_NON_PRIMARY: account=' . $account['name']
                . ', account_index=' . (int)$account['index']
                . ', primary_index=' . self::CREATE_PRIMARY_ACCOUNT_INDEX
            );
            return array();
        }

        $result = array();
        $seen_skus = array();
        foreach ($candidates as $candidate) {
            $sku_id = (int)$candidate['sku_id'];
            if ($sku_id <= 0 || isset($seen_skus[$sku_id])) {
                continue;
            }
            $seen_skus[$sku_id] = true;
            $result[] = $candidate;
        }

        $this->log(
            'CREATE_ROUTE_PRIMARY: account=' . $account['name']
            . ', account_index=' . (int)$account['index']
            . ', allowlist_skus=' . count($candidates)
            . ', routed_skus=' . count($result)
        );

        return $result;
    }

    private function getSavedOzonEvidence($account, $sku_id)
    {
        // The installed Webasyst Ozon application has one global exported-product table.
        // It belongs to the primary account only. Secondary accounts are checked remotely.
        if ((int)$account['index'] !== 1 || (int)$sku_id <= 0) {
            return null;
        }

        $columns = $this->getTableColumns('ozon_exported_product');
        if (!in_array('shop_sku_id', $columns, true)) {
            return null;
        }

        try {
            $model = new waModel();
            $rows = $model->query(
                "SELECT id, ozon_product_id, ozon_offer_id, shop_sku_id
                 FROM ozon_exported_product
                 WHERE shop_sku_id = i:sku_id
                 ORDER BY id DESC
                 LIMIT 10",
                array('sku_id' => (int)$sku_id)
            )->fetchAll();

            foreach ($rows as $row) {
                $product_id = isset($row['ozon_product_id']) ? (int)$row['ozon_product_id'] : 0;
                $offer_id = isset($row['ozon_offer_id']) ? trim((string)$row['ozon_offer_id']) : '';
                if ($product_id > 0 || ($offer_id !== '' && $offer_id !== '0')) {
                    return array(
                        'ozon_product_id' => $product_id,
                        'ozon_offer_id' => $offer_id,
                        'row_id' => isset($row['id']) ? (int)$row['id'] : 0
                    );
                }
            }
        } catch (Exception $e) {
            $this->log('CREATE_SAVED_ID_CHECK_ERROR: sku_id=' . (int)$sku_id . ', error=' . $e->getMessage());
        }

        return null;
    }

    private function prepareCreateOffer($candidate)
    {
        $product_id = (int)$candidate['product_id'];
        $sku_id = (int)$candidate['sku_id'];
        $webasyst_sku = trim((string)$candidate['sku']);

        if ($product_id <= 0 || $sku_id <= 0 || $webasyst_sku === '') {
            return array('ok' => false, 'error' => 'bad_candidate');
        }

        $generator_errors = array();
        $offers = array();

        try {
            wa('ozon');
            if (!class_exists('ozonProduct')) {
                throw new waException('Класс ozonProduct не найден');
            }
            $ozon_product = new ozonProduct($product_id);
            $this->applyCreatePackageFallback($ozon_product, $product_id, $sku_id, $webasyst_sku);
            $offers = $ozon_product->getOzonOffers($sku_id, false, $generator_errors);
        } catch (Throwable $e) {
            try { wa('shop'); } catch (Throwable $ignore) {}
            return array('ok' => false, 'error' => 'generator_exception: ' . $e->getMessage());
        }

        try { wa('shop'); } catch (Throwable $ignore) {}

        if (!$offers) {
            return array(
                'ok' => false,
                'error' => 'generator_rejected: ' . ($generator_errors ? implode(' || ', $generator_errors) : 'no_offer')
            );
        }

        $offer = null;
        foreach ($offers as $generated) {
            if (isset($generated['offer_id']) && trim((string)$generated['offer_id']) === $webasyst_sku) {
                $offer = $generated;
                break;
            }
        }
        if ($offer === null && count($offers) === 1) {
            $offer = reset($offers);
        }

        if (!$offer || empty($offer['offer_id'])) {
            return array('ok' => false, 'error' => 'generator_no_offer_id');
        }

        $generated_offer_id = trim((string)$offer['offer_id']);
        if ($generated_offer_id !== $webasyst_sku) {
            $this->log(
                'CREATE_OFFER_ID_DIFFERS_FROM_SKU: product_id=' . $product_id
                . ', sku_id=' . $sku_id
                . ', sku=' . $webasyst_sku
                . ', offer_id=' . $generated_offer_id
            );
        }

        $cost = isset($candidate['purchase_price']) ? (float)$candidate['purchase_price'] : 0;
        if ($cost <= 0) {
            return array('ok' => false, 'error' => 'no_purchase_price');
        }

        $tips = $this->loadTipOzonValues(array($product_id));
        if (!isset($tips[$product_id]) || trim((string)$tips[$product_id]) === '') {
            return array('ok' => false, 'error' => 'no_tip_ozon');
        }
        $tip = trim((string)$tips[$product_id]);
        $commissions = $this->getCommissionForTip($tip);
        if (!$commissions) {
            return array('ok' => false, 'error' => 'no_commission_for_tip: ' . $tip);
        }
        $price_data = $this->calculatePrices($cost, $commissions, $tip);
        if (!$price_data) {
            return array('ok' => false, 'error' => 'price_formula_failed');
        }

        // Creation uses the same current price policy as the separate price synchronizer.
        $offer['price'] = (string)$price_data['price'];
        $offer['old_price'] = (string)$price_data['old_price'];

        // Supplier imports can keep remote image URLs in the Webasyst short
        // description inside [extimg]...[/extimg], while shop_product_images is
        // intentionally empty. The installed Ozon generator only sees native
        // product images, so merge these external URLs into the create payload.
        $external_images = $this->getCreateExternalImageUrls($product_id);
        if ($external_images) {
            $native_images = isset($offer['images']) && is_array($offer['images'])
                ? $offer['images']
                : array();

            $merged_images = array();
            foreach (array_merge($native_images, $external_images) as $image_url) {
                $image_url = trim((string)$image_url);
                if ($image_url === '' || isset($merged_images[$image_url])) {
                    continue;
                }
                $merged_images[$image_url] = $image_url;
            }

            $offer['images'] = array_values($merged_images);
            $this->log(
                'CREATE_EXTIMG_IMAGES: product_id=' . $product_id
                . ', sku_id=' . $sku_id
                . ', sku=' . $webasyst_sku
                . ', native=' . count($native_images)
                . ', extimg=' . count($external_images)
                . ', total=' . count($offer['images'])
            );
        }

        return array(
            'ok' => true,
            'offer' => $offer,
            'product_id' => $product_id,
            'sku_id' => $sku_id,
            'webasyst_sku' => $webasyst_sku,
            'offer_id' => $generated_offer_id
        );
    }

    private function getCreateExternalImageUrls($product_id)
    {
        $product_id = (int)$product_id;
        if ($product_id <= 0) {
            return array();
        }

        try {
            $model = new waModel();
            $summary = (string)$model->query(
                'SELECT summary FROM shop_product WHERE id = i:id LIMIT 1',
                array('id' => $product_id)
            )->fetchField();
        } catch (Throwable $e) {
            $this->log(
                'CREATE_EXTIMG_READ_ERROR: product_id=' . $product_id
                . ', error=' . $e->getMessage()
            );
            return array();
        }

        if ($summary === '' || stripos($summary, '[extimg]') === false) {
            return array();
        }

        $urls = array();
        if (preg_match_all('~\\[extimg\\](.*?)\\[/extimg\\]~is', $summary, $blocks)) {
            foreach ($blocks[1] as $block) {
                $block = html_entity_decode((string)$block, ENT_QUOTES, 'UTF-8');
                if (!preg_match_all("~https?://[^\\s<>\"'\\]\\[]+~iu", $block, $matches)) {
                    continue;
                }
                foreach ($matches[0] as $url) {
                    $url = trim((string)$url);
                    $url = rtrim($url, ".,;:)");
                    if ($url === '' || !filter_var($url, FILTER_VALIDATE_URL)) {
                        continue;
                    }
                    $scheme = strtolower((string)parse_url($url, PHP_URL_SCHEME));
                    if ($scheme !== 'http' && $scheme !== 'https') {
                        continue;
                    }
                    $urls[$url] = $url;
                }
            }
        }

        return array_values($urls);
    }

    private function applyCreatePackageFallback($ozon_product, $product_id, $sku_id, $webasyst_sku)
    {
        // The installed Ozon app reads these four service feature codes when it
        // validates dimensions. Some supplier imports have package data under
        // supplier-specific feature names instead, so the Ozon fields are empty.
        //
        // Write only to the shopProduct in-memory cache. No save() call is made,
        // therefore Webasyst catalog data are not changed by this fallback.
        $features = isset($ozon_product['features']) && is_array($ozon_product['features'])
            ? $ozon_product['features']
            : array();

        $fallbacks = array(
            'glubina_upakovki1' => self::CREATE_FALLBACK_DEPTH_CM,
            'vysota_upakovki1' => self::CREATE_FALLBACK_HEIGHT_CM,
            'shirina_upakovki1' => self::CREATE_FALLBACK_WIDTH_CM,
            'ves_upakovki' => self::CREATE_FALLBACK_WEIGHT_KG
        );

        $applied = array();
        foreach ($fallbacks as $code => $value) {
            $current = isset($features[$code]) ? $features[$code] : null;
            $numeric = null;

            if (is_object($current) && isset($current->value)) {
                $numeric = (float)$current->value;
            } elseif (is_array($current) && isset($current['value'])) {
                $numeric = (float)$current['value'];
            } elseif (is_scalar($current)) {
                $numeric = (float)str_replace(',', '.', (string)$current);
            }

            if ($numeric === null || $numeric <= 0) {
                $features[$code] = (string)$value;
                $applied[$code] = $value;
            }
        }

        if ($applied) {
            $ozon_product['features'] = $features;
            $this->log(
                'CREATE_PACKAGE_FALLBACK: product_id=' . (int)$product_id
                . ', sku_id=' . (int)$sku_id
                . ', sku=' . (string)$webasyst_sku
                . ', values=' . json_encode($applied, JSON_UNESCAPED_UNICODE)
                . ', persisted=0'
            );
        }
    }

    private function getRemoteExistingOfferIds($offer_ids, $client_id, $api_key, $product_ids = array(), $sku_ids = array())
    {
        $existing = array();

        $normalize = function ($values, $numeric_only = false) {
            $result = array();
            foreach ((array)$values as $value) {
                $value = trim((string)$value);
                if ($value === '') continue;
                if ($numeric_only && !ctype_digit($value)) continue;
                $result[$value] = $value;
            }
            return array_values($result);
        };

        $offer_ids = $normalize($offer_ids);
        $product_ids = $normalize($product_ids, true);
        $sku_ids = $normalize($sku_ids, true);

        // Fast exact check by seller offer_id.
        foreach (array_chunk($offer_ids, 1000) as $chunk) {
            $response = $this->sendToOzon(
                self::PRODUCT_LIST_URL,
                array(
                    'filter' => array(
                        'offer_id' => $chunk,
                        'visibility' => 'ALL'
                    ),
                    'limit' => 1000
                ),
                $client_id,
                $api_key
            );
            if (!$response['ok']) {
                return array('ok' => false, 'existing' => $existing, 'code' => $response['code'], 'body' => $response['body']);
            }
            $decoded = json_decode((string)$response['body'], true);
            if (!is_array($decoded)) {
                return array('ok' => false, 'existing' => $existing, 'code' => $response['code'], 'body' => 'invalid_json: ' . $response['body']);
            }
            $items = isset($decoded['result']['items']) && is_array($decoded['result']['items'])
                ? $decoded['result']['items']
                : (isset($decoded['items']) && is_array($decoded['items']) ? $decoded['items'] : array());
            foreach ($items as $item) {
                $offer_id = isset($item['offer_id']) ? trim((string)$item['offer_id']) : '';
                if ($offer_id !== '') {
                    $existing[$offer_id] = $item;
                }
                $sku = isset($item['sku']) ? trim((string)$item['sku']) : '';
                if ($sku !== '') {
                    $existing['sku:' . $sku] = $item;
                }
                $pid = isset($item['product_id']) ? trim((string)$item['product_id']) : (isset($item['id']) ? trim((string)$item['id']) : '');
                if ($pid !== '') {
                    $existing['product_id:' . $pid] = $item;
                }
            }
        }

        // Strong saved-ID checks. /v3/product/info/list accepts product_id and Ozon SKU.
        foreach (array('product_id' => $product_ids, 'sku' => $sku_ids) as $field => $values) {
            foreach (array_chunk($values, 1000) as $chunk) {
                if (!$chunk) continue;
                $response = $this->sendToOzon(
                    self::PRODUCT_INFO_LIST_URL,
                    array($field => $chunk),
                    $client_id,
                    $api_key
                );
                if (!$response['ok']) {
                    return array('ok' => false, 'existing' => $existing, 'code' => $response['code'], 'body' => $response['body']);
                }
                $decoded = json_decode((string)$response['body'], true);
                if (!is_array($decoded)) {
                    return array('ok' => false, 'existing' => $existing, 'code' => $response['code'], 'body' => 'invalid_json: ' . $response['body']);
                }
                $items = isset($decoded['items']) && is_array($decoded['items'])
                    ? $decoded['items']
                    : (isset($decoded['result']['items']) && is_array($decoded['result']['items']) ? $decoded['result']['items'] : array());
                foreach ($items as $item) {
                    $offer_id = isset($item['offer_id']) ? trim((string)$item['offer_id']) : '';
                    if ($offer_id !== '') {
                        $existing[$offer_id] = $item;
                    }
                    $sku = isset($item['sku']) ? trim((string)$item['sku']) : '';
                    if ($sku !== '') {
                        $existing['sku:' . $sku] = $item;
                    }
                    $pid = isset($item['id']) ? trim((string)$item['id']) : (isset($item['product_id']) ? trim((string)$item['product_id']) : '');
                    if ($pid !== '') {
                        $existing['product_id:' . $pid] = $item;
                    }
                }
            }
        }

        return array('ok' => true, 'existing' => $existing);
    }

    private function createMissingProductsForAccount($account, $candidates, $dry_run)
    {
        $result = array(
            'submitted' => 0,
            'would_create' => 0,
            'skipped_existing' => 0,
            'errors' => 0
        );

        $routed = $this->routeCreateCandidatesForAccount($account, $candidates);
        if (!$routed) {
            $this->log('CREATE_ACCOUNT_FINISH: account=' . $account['name'] . ', routed=0');
            return $result;
        }

        $prepared = array();
        foreach ($routed as $candidate) {
            $sku_id = (int)$candidate['sku_id'];
            $webasyst_sku = trim((string)$candidate['sku']);

            // A saved ID is evidence that must be verified remotely, not a reason
            // to skip blindly: the old Ozon card may have been deleted or archived.
            $saved = $this->getSavedOzonEvidence($account, $sku_id);

            $item = $this->prepareCreateOffer($candidate);
            if (!$item['ok']) {
                $result['errors']++;
                $this->log(
                    'CREATE_SKIP_INVALID: account=' . $account['name']
                    . ', product_id=' . (int)$candidate['product_id']
                    . ', sku_id=' . $sku_id
                    . ', sku=' . $webasyst_sku
                    . ', reason=' . $item['error']
                );
                continue;
            }

            $item['saved_ozon_product_id'] = $saved && isset($saved['ozon_product_id']) ? (int)$saved['ozon_product_id'] : 0;
            $item['saved_ozon_offer_id'] = $saved && isset($saved['ozon_offer_id']) ? trim((string)$saved['ozon_offer_id']) : '';
            $prepared[] = $item;
        }

        if (!$prepared) {
            $this->log(
                'CREATE_ACCOUNT_FINISH: account=' . $account['name']
                . ', routed=' . count($routed)
                . ', prepared=0'
                . ', skipped_existing=' . $result['skipped_existing']
                . ', errors=' . $result['errors']
            );
            return $result;
        }

        // First fail-closed remote guard: generated offer_id + raw Webasyst SKU.
        $lookup_ids = array();
        $lookup_product_ids = array();
        $lookup_sku_ids = array();
        foreach ($prepared as $item) {
            $lookup_ids[] = $item['offer_id'];
            $lookup_ids[] = $item['webasyst_sku'];
            if (!empty($item['saved_ozon_offer_id']) && $item['saved_ozon_offer_id'] !== '0') {
                $lookup_ids[] = $item['saved_ozon_offer_id'];
            }
            if (!empty($item['saved_ozon_product_id'])) {
                $lookup_product_ids[] = (string)$item['saved_ozon_product_id'];
            }
            // Webasyst SKU / saved offer_id are seller offer identifiers, not Ozon's
            // internal SKU. They are already checked through PRODUCT_LIST_URL by offer_id.
        }
        $remote = $this->getRemoteExistingOfferIds(
            $lookup_ids,
            $account['client_id'],
            $account['api_key'],
            $lookup_product_ids,
            $lookup_sku_ids
        );
        if (!$remote['ok']) {
            $result['errors'] += count($prepared);
            $this->log(
                'CREATE_ABORT_EXISTENCE_CHECK: account=' . $account['name']
                . ', prepared=' . count($prepared)
                . ', http=' . (isset($remote['code']) ? (int)$remote['code'] : 0)
                . ', body=' . (isset($remote['body']) ? $remote['body'] : '')
            );
            return $result;
        }

        $pending = array();
        foreach ($prepared as $item) {
            $exists_by_offer = isset($remote['existing'][$item['offer_id']])
                || isset($remote['existing'][$item['webasyst_sku']])
                || (!empty($item['saved_ozon_offer_id']) && isset($remote['existing'][$item['saved_ozon_offer_id']]));
            $exists_by_product_id = !empty($item['saved_ozon_product_id'])
                && isset($remote['existing']['product_id:' . $item['saved_ozon_product_id']]);
            if ($exists_by_offer || $exists_by_product_id) {
                $result['skipped_existing']++;
                $matched_by = $exists_by_product_id ? 'saved_product_id' : 'offer_id';
                $this->log(
                    'CREATE_SKIP_REMOTE_EXISTS: account=' . $account['name']
                    . ', sku_id=' . $item['sku_id']
                    . ', sku=' . $item['webasyst_sku']
                    . ', offer_id=' . $item['offer_id']
                    . ', saved_product_id=' . (int)$item['saved_ozon_product_id']
                    . ', matched_by=' . $matched_by
                );
                continue;
            }
            $pending[] = $item;
        }

        if ($dry_run) {
            $result['would_create'] = count($pending);
            foreach (array_slice($pending, 0, 20) as $item) {
                $offer = $item['offer'];
                $this->log(
                    'DRY_RUN_CREATE: account=' . $account['name']
                    . ', product_id=' . $item['product_id']
                    . ', sku_id=' . $item['sku_id']
                    . ', sku=' . $item['webasyst_sku']
                    . ', offer_id=' . $item['offer_id']
                    . ', description_category_id=' . (isset($offer['description_category_id']) ? $offer['description_category_id'] : '')
                    . ', type_id=' . (isset($offer['type_id']) ? $offer['type_id'] : '')
                    . ', attributes=' . (isset($offer['attributes']) && is_array($offer['attributes']) ? count($offer['attributes']) : 0)
                    . ', images=' . (isset($offer['images']) && is_array($offer['images']) ? count($offer['images']) : 0)
                    . ', price=' . (isset($offer['price']) ? $offer['price'] : '')
                );
            }
            $this->log(
                'CREATE_ACCOUNT_FINISH: account=' . $account['name']
                . ', routed=' . count($routed)
                . ', prepared=' . count($prepared)
                . ', would_create=' . $result['would_create']
                . ', skipped_existing=' . $result['skipped_existing']
                . ', errors=' . $result['errors']
                . ', dry_run=1'
            );
            return $result;
        }

        foreach (array_chunk($pending, self::CREATE_BATCH_SIZE) as $batch) {
            // product/import is an upsert endpoint. Re-check immediately before calling it.
            $batch_lookup = array();
            $batch_product_ids = array();
            $batch_sku_ids = array();
            foreach ($batch as $item) {
                $batch_lookup[] = $item['offer_id'];
                $batch_lookup[] = $item['webasyst_sku'];
                if (!empty($item['saved_ozon_offer_id']) && $item['saved_ozon_offer_id'] !== '0') {
                    $batch_lookup[] = $item['saved_ozon_offer_id'];
                }
                if (!empty($item['saved_ozon_product_id'])) {
                    $batch_product_ids[] = (string)$item['saved_ozon_product_id'];
                }
                // Seller offer identifiers must not be sent as Ozon internal SKU ids.
            }

            $second = $this->getRemoteExistingOfferIds(
                $batch_lookup,
                $account['client_id'],
                $account['api_key'],
                $batch_product_ids,
                $batch_sku_ids
            );
            if (!$second['ok']) {
                $result['errors'] += count($batch);
                $this->log(
                    'CREATE_ABORT_SECOND_EXISTENCE_CHECK: account=' . $account['name']
                    . ', batch=' . count($batch)
                    . ', offers=' . implode(',', array_column($batch, 'offer_id'))
                    . ', http=' . (isset($second['code']) ? (int)$second['code'] : 0)
                    . ', body=' . (isset($second['body']) ? $second['body'] : '')
                );
                continue;
            }

            $final = array();
            foreach ($batch as $item) {
                $second_exists = isset($second['existing'][$item['offer_id']])
                    || isset($second['existing'][$item['webasyst_sku']])
                    || (!empty($item['saved_ozon_offer_id']) && isset($second['existing'][$item['saved_ozon_offer_id']]))
                    || (!empty($item['saved_ozon_product_id']) && isset($second['existing']['product_id:' . $item['saved_ozon_product_id']]));
                if ($second_exists) {
                    $result['skipped_existing']++;
                    $this->log(
                        'CREATE_SKIP_SECOND_CHECK_EXISTS: account=' . $account['name']
                        . ', sku=' . $item['webasyst_sku']
                        . ', offer_id=' . $item['offer_id']
                    );
                    continue;
                }
                $final[] = $item;
            }

            if (!$final) {
                continue;
            }

            $offers = array();
            foreach ($final as $item) {
                $offers[] = $item['offer'];
            }

            $response = $this->sendToOzon(
                self::PRODUCT_IMPORT_URL,
                array('items' => $offers),
                $account['client_id'],
                $account['api_key']
            );

            if (!$response['ok']) {
                $result['errors'] += count($final);
                $this->log(
                    'CREATE_IMPORT_ERROR: account=' . $account['name']
                    . ', batch=' . count($final)
                    . ', offers=' . implode(',', array_column($final, 'offer_id'))
                    . ', http=' . $response['code']
                    . ', body=' . $response['body']
                );
                continue;
            }

            $decoded = json_decode((string)$response['body'], true);
            $task_id = 0;
            if (is_array($decoded)) {
                if (isset($decoded['result']['task_id'])) {
                    $task_id = (int)$decoded['result']['task_id'];
                } elseif (isset($decoded['task_id'])) {
                    $task_id = (int)$decoded['task_id'];
                }
            }

            if ($task_id <= 0) {
                $result['errors'] += count($final);
                $this->log(
                    'CREATE_IMPORT_NO_TASK_ID: account=' . $account['name']
                    . ', batch=' . count($final)
                    . ', offers=' . implode(',', array_column($final, 'offer_id'))
                    . ', body=' . $response['body']
                );
                continue;
            }

            $result['submitted'] += count($final);
            $this->log(
                'CREATE_SUBMITTED: account=' . $account['name']
                . ', batch=' . count($final)
                . ', task_id=' . $task_id
                . ', offers=' . implode(',', array_column($final, 'offer_id'))
            );

            $result['errors'] += $this->checkCreateImportTask(
                $task_id,
                array_column($final, 'offer_id'),
                $account['client_id'],
                $account['api_key'],
                $account['name']
            );
        }

        $this->log(
            'CREATE_ACCOUNT_FINISH: account=' . $account['name']
            . ', routed=' . count($routed)
            . ', prepared=' . count($prepared)
            . ', submitted=' . $result['submitted']
            . ', skipped_existing=' . $result['skipped_existing']
            . ', errors=' . $result['errors']
        );

        return $result;
    }

    private function checkCreateImportTask($task_id, $offer_ids, $client_id, $api_key, $account_name)
    {
        $offer_ids = array_values(array_unique(array_map('strval', (array)$offer_ids)));
        if (!$offer_ids || (int)$task_id <= 0) {
            return 0;
        }

        $attempts = 5;
        $last_body = '';

        for ($attempt = 1; $attempt <= $attempts; $attempt++) {
            if ($attempt > 1) {
                $this->sleepIfPositive(2, 'CREATE_TASK_WAIT account=' . $account_name . ', task_id=' . (int)$task_id);
            }

            $response = $this->sendToOzon(
                self::PRODUCT_IMPORT_INFO_URL,
                array('task_id' => (int)$task_id),
                $client_id,
                $api_key
            );
            $last_body = isset($response['body']) ? (string)$response['body'] : '';

            if (!$response['ok']) {
                if ($attempt >= $attempts) {
                    $this->log(
                        'CREATE_TASK_STATUS_ERROR: account=' . $account_name
                        . ', task_id=' . (int)$task_id
                        . ', http=' . (int)$response['code']
                        . ', body=' . $last_body
                    );
                    return count($offer_ids);
                }
                continue;
            }

            $decoded = json_decode($last_body, true);
            $items = array();
            if (is_array($decoded)) {
                if (isset($decoded['result']['items']) && is_array($decoded['result']['items'])) {
                    $items = $decoded['result']['items'];
                } elseif (isset($decoded['items']) && is_array($decoded['items'])) {
                    $items = $decoded['items'];
                }
            }

            if (!$items) {
                continue;
            }

            $seen = array();
            $errors = 0;
            $pending = 0;

            foreach ($items as $item) {
                $offer_id = isset($item['offer_id']) ? trim((string)$item['offer_id']) : '';
                if ($offer_id === '' || !in_array($offer_id, $offer_ids, true)) {
                    continue;
                }
                $seen[$offer_id] = true;

                $item_errors = isset($item['errors']) && is_array($item['errors']) ? $item['errors'] : array();
                if ($item_errors) {
                    $errors++;
                    $this->log(
                        'CREATE_TASK_ITEM_ERROR: account=' . $account_name
                        . ', task_id=' . (int)$task_id
                        . ', offer_id=' . $offer_id
                        . ', errors=' . json_encode($item_errors, JSON_UNESCAPED_UNICODE)
                    );
                    continue;
                }

                $status = '';
                if (isset($item['status'])) {
                    $status = trim((string)$item['status']);
                } elseif (isset($item['state'])) {
                    $status = trim((string)$item['state']);
                }

                if ($status !== '' && preg_match('/error|fail|reject/i', $status)) {
                    $errors++;
                    $this->log(
                        'CREATE_TASK_ITEM_FAILED: account=' . $account_name
                        . ', task_id=' . (int)$task_id
                        . ', offer_id=' . $offer_id
                        . ', status=' . $status
                    );
                } elseif (isset($item['product_id']) && (int)$item['product_id'] > 0) {
                    $this->log(
                        'CREATE_TASK_ITEM_OK: account=' . $account_name
                        . ', task_id=' . (int)$task_id
                        . ', offer_id=' . $offer_id
                        . ', product_id=' . (int)$item['product_id']
                        . ', status=' . $status
                    );
                } else {
                    $pending++;
                }
            }

            if ($errors > 0) {
                return $errors;
            }

            if (count($seen) >= count($offer_ids) && $pending === 0) {
                return 0;
            }
        }

        $this->log(
            'CREATE_TASK_PENDING: account=' . $account_name
            . ', task_id=' . (int)$task_id
            . ', offers=' . implode(',', $offer_ids)
            . ', last_body=' . $last_body
        );
        return 0;
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

        $target_markup_percent = $this->getTargetMarkupPercentForTip($tip);
        if ((float)$cost < self::LOW_COST_THRESHOLD_RUB) {
            $target_markup_percent = max(
                $target_markup_percent,
                self::LOW_COST_TARGET_MARKUP_PERCENT
            );
        }

        $target_markup = $target_markup_percent / 100;
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
