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
        $updated = 0;
        foreach ($items as $item) {
            if (!is_array($item)) {
                continue;
            }
            $supplier_sku = trim((string)ifset($item['supplier_sku']));
            $product_id = (int)ifset($item['product_id']);
            $sku_id = (int)ifset($item['sku_id']);
            if ($supplier_sku === '' || !$product_id || !$sku_id) {
                continue;
            }
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
