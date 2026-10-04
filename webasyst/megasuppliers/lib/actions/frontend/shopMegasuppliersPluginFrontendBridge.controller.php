<?php
class shopMegasuppliersPluginFrontendBridgeController extends waJsonController
{
    public function execute()
    {
        $this->getResponse()->setHeader('Content-Type', 'application/json; charset=utf-8');
        if (waRequest::method() !== 'post') {
            $this->fail('method_not_allowed', 405);
            return;
        }

        $body = file_get_contents('php://input');
        if (strlen($body) > 2097152) {
            $this->fail('payload_too_large', 413);
            return;
        }
        $plugin = wa('shop')->getPlugin('megasuppliers');
        $secret = trim((string)$plugin->getSettings('callback_secret'));
        $provided = trim((string)waRequest::server('HTTP_X_MEGASUPPLIERS_SIGNATURE', ''));
        $expected = $secret === '' ? '' : 'sha256='.hash_hmac('sha256', $body, $secret);
        if ($secret === '' || $provided === '' || !hash_equals($expected, $provided)) {
            $this->fail('unauthorized', 401);
            return;
        }

        $payload = json_decode($body, true);
        if (!is_array($payload)) {
            $this->fail('invalid_json', 400);
            return;
        }

        $supplier_id = (int)ifset($payload['supplier_id']);
        $request_id = trim((string)ifset($payload['request_id']));
        $action = trim((string)ifset($payload['action']));
        if (!$supplier_id || $request_id === '' || $action === '') {
            $this->fail('invalid_request', 422);
            return;
        }

        $supplier = (new shopMegasuppliersSupplierModel())->getById($supplier_id);
        if (!$supplier || empty($supplier['active'])) {
            $this->fail('supplier_not_found', 422);
            return;
        }

        $status_path = $this->statusPath($supplier_id);
        $pending = file_exists($status_path) ? json_decode(file_get_contents($status_path), true) : null;
        if (!is_array($pending) || empty($pending['request_id']) || !hash_equals((string)$pending['request_id'], $request_id)) {
            $this->fail('stale_or_unknown_request', 409);
            return;
        }

        if ($action === 'links') {
            $this->links($supplier_id, $payload);
            return;
        }
        if ($action === 'ensure_features') {
            if ((string)ifset($pending['mode']) !== 'apply') {
                $this->fail('apply_request_required', 409);
                return;
            }
            if (!$plugin->getSettings('enable_writes')) {
                $this->fail('writes_disabled', 403);
                return;
            }
            $this->ensureFeatures($supplier_id, $payload);
            return;
        }
        if ($action === 'sync_links') {
            if ((string)ifset($pending['mode']) !== 'apply') {
                $this->fail('apply_request_required', 409);
                return;
            }
            if (!$plugin->getSettings('enable_writes')) {
                $this->fail('writes_disabled', 403);
                return;
            }
            $this->syncLinks($supplier_id, ifset($payload['items'], array()));
            return;
        }

        $this->fail('unknown_action', 422);
    }

    private function links($supplier_id, array $payload)
    {
        $limit = min(2000, max(1, (int)ifset($payload['limit'], 1000)));
        $offset = max(0, (int)ifset($payload['offset'], 0));
        $model = new shopMegasuppliersProductModel();
        $sql = 'SELECT supplier_sku,product_id,sku_id,purchase_price,stock '
            .'FROM shop_megasuppliers_product WHERE supplier_id='.(int)$supplier_id
            .' ORDER BY id LIMIT '.$limit.' OFFSET '.$offset;
        $rows = $model->query($sql)->fetchAll();
        $this->response = array(
            'status' => 'ok',
            'items' => array_values($rows),
            'offset' => $offset,
            'limit' => $limit,
        );
    }


    private function ensureFeatures($supplier_id, array $payload)
    {
        $type_id = (int)ifset($payload['type_id']);
        $names = ifset($payload['names'], array());
        if (!$type_id || !is_array($names)) {
            $this->fail('invalid_feature_request', 422);
            return;
        }
        if (count($names) > 500) {
            $this->fail('too_many_features', 413);
            return;
        }

        $type = (new shopTypeModel())->getById($type_id);
        if (!$type) {
            $this->fail('product_type_not_found', 422);
            return;
        }

        $feature_model = new shopFeatureModel();
        $type_features_model = new shopTypeFeaturesModel();
        $mapping = array();
        $created = 0;
        $linked = 0;
        $seen = array();

        foreach ($names as $raw_name) {
            $name = trim((string)$raw_name);
            if ($name === '' || mb_strlen($name, 'UTF-8') > 255) {
                continue;
            }
            $key = mb_strtolower($name, 'UTF-8');
            if (isset($seen[$key])) {
                $mapping[$name] = $seen[$key];
                continue;
            }

            $code = 'ms_s'.(int)$supplier_id.'_'.substr(hash('sha256', $key), 0, 16);
            $feature = $feature_model->getByField('code', $code);
            if (!$feature) {
                $data = array(
                    'code' => $code,
                    'name' => $name,
                    'type' => shopFeatureModel::TYPE_VARCHAR,
                    'selectable' => 0,
                    'multiple' => 0,
                    'status' => 'private',
                    'available_for_sku' => 0,
                );
                $feature_id = $feature_model->save($data);
                if (!$feature_id) {
                    $this->fail('feature_create_failed', 500);
                    return;
                }
                $feature = $feature_model->getById($feature_id);
                $created++;
            }

            $feature_id = (int)ifset($feature['id']);
            if (!$feature_id) {
                $this->fail('feature_resolve_failed', 500);
                return;
            }
            $type_features_model->updateByFeature($feature_id, array($type_id), false);
            $linked++;
            $resolved_code = (string)ifset($feature['code'], $code);
            $mapping[$name] = $resolved_code;
            $seen[$key] = $resolved_code;
        }

        $this->response = array(
            'status' => 'ok',
            'mapping' => $mapping,
            'created' => $created,
            'linked' => $linked,
            'type_id' => $type_id,
        );
    }

    private function syncLinks($supplier_id, $items)
    {
        if (!is_array($items)) {
            $this->fail('items_required', 422);
            return;
        }
        if (count($items) > 500) {
            $this->fail('too_many_items', 413);
            return;
        }
        $model = new shopMegasuppliersProductModel();
        $sku_model = new shopProductSkusModel();
        $validated = array();
        foreach ($items as $item) {
            if (!is_array($item)) {
                $this->fail('invalid_mapping', 422);
                return;
            }
            $supplier_sku = trim((string)ifset($item['supplier_sku']));
            $product_id = (int)ifset($item['product_id']);
            $sku_id = (int)ifset($item['sku_id']);
            if ($supplier_sku === '' || mb_strlen($supplier_sku, 'UTF-8') > 255 || !$product_id || !$sku_id) {
                $this->fail('invalid_mapping', 422);
                return;
            }
            $sku = $sku_model->getById($sku_id);
            if (!$sku || (int)ifset($sku['product_id']) !== $product_id) {
                $this->fail('invalid_product_sku_mapping', 422);
                return;
            }
            $validated[] = array($item, $supplier_sku, $product_id, $sku_id);
        }

        $updated = 0;
        foreach ($validated as $row) {
            $item = $row[0];
            $supplier_sku = $row[1];
            $product_id = $row[2];
            $sku_id = $row[3];
            $data = array(
                'supplier_id' => (int)$supplier_id,
                'product_id' => $product_id,
                'sku_id' => $sku_id,
                'supplier_sku' => $supplier_sku,
                'external_id' => '',
                'purchase_price' => $this->nullableNumber(ifset($item['purchase_price'], null)),
                'stock' => $this->nullableNumber(ifset($item['stock'], null)),
                'raw_json' => null,
                'updated_at' => date('Y-m-d H:i:s'),
            );
            $existing = $model->getByField(array(
                'supplier_id' => (int)$supplier_id,
                'supplier_sku' => $supplier_sku,
            ));
            if ($existing) {
                $model->updateById($existing['id'], $data);
            } else {
                $model->insert($data);
            }
            $updated++;
        }
        $this->response = array('status' => 'ok', 'updated' => $updated);
    }

    private function nullableNumber($value)
    {
        if ($value === null || $value === '') {
            return null;
        }
        $value = str_replace(array("\xc2\xa0", ' ', ','), array('', '', '.'), trim((string)$value));
        return is_numeric($value) ? (float)$value : null;
    }

    private function statusPath($supplier_id)
    {
        $dir = wa()->getDataPath('plugins/megasuppliers/import-status', false, 'shop', true);
        waFiles::create($dir);
        return $dir.'/'.(int)$supplier_id.'.json';
    }

    private function fail($code, $status)
    {
        $this->errors = array($code);
        $this->getResponse()->setStatus((int)$status);
    }
}
