<?php
class shopMegasuppliersPluginFrontendApiController extends waJsonController
{
    public function execute()
    {
        $this->getResponse()->setHeader('Content-Type', 'application/json; charset=utf-8');
        if (waRequest::method() !== 'post') {
            $this->errors = array('method_not_allowed');
            $this->getResponse()->setStatus(405);
            return;
        }
        $meta = (new shopMegasuppliersMetaModel())->getById('api_key');
        $expected = $meta ? (string)$meta['value'] : '';
        $provided = trim((string)waRequest::server('HTTP_X_MEGASUPPLIERS_KEY', ''));
        if ($expected === '' || $provided === '' || !hash_equals($expected, $provided)) {
            $this->errors = array('unauthorized');
            $this->getResponse()->setStatus(401);
            return;
        }
        $body = file_get_contents('php://input');
        if (strlen($body) > 5242880) {
            $this->errors = array('payload_too_large');
            $this->getResponse()->setStatus(413);
            return;
        }
        $payload = json_decode($body, true);
        if (!is_array($payload)) {
            $this->errors = array('invalid_json');
            $this->getResponse()->setStatus(400);
            return;
        }
        $supplier_code = strtoupper(trim((string)ifset($payload['supplier_code'])));
        if ($supplier_code === '') {
            $this->errors = array('SUPPLIER_REQUIRED');
            $this->getResponse()->setStatus(422);
            return;
        }
        $supplier = (new shopMegasuppliersSupplierModel())->getByField('code', $supplier_code);
        if (!$supplier || empty($supplier['active'])) {
            $this->errors = array('SUPPLIER_NOT_FOUND');
            $this->getResponse()->setStatus(422);
            return;
        }
        if (!empty($payload['create_missing'])) {
            $this->errors = array('CREATE_MISSING_REQUIRES_PROFILE');
            $this->getResponse()->setStatus(422);
            return;
        }
        $items = ifset($payload['items'], array());
        if (!is_array($items) || !$items) {
            $this->errors = array('ITEMS_REQUIRED');
            $this->getResponse()->setStatus(422);
            return;
        }
        try {
            $service = new shopMegasuppliersImportService();
            $stats = $service->importRows($supplier['id'], $items, array(
                'source' => 'api',
                'filename' => '',
                'create_missing' => false,
                'update_catalog' => !array_key_exists('update_catalog', $payload) || !empty($payload['update_catalog']),
                'field_map' => ifset($payload['field_map'], array()),
            ));
            $this->response = array(
                'status' => 'ok',
                'supplier' => array('id' => (int)$supplier['id'], 'code' => $supplier['code'], 'name' => $supplier['name']),
                'import_id' => (int)$stats['import_id'],
                'created' => $stats['created'],
                'updated' => $stats['updated'],
                'linked' => $stats['linked'],
                'skipped' => $stats['skipped'],
                'errors' => $stats['errors'],
            );
        } catch (Exception $e) {
            $this->errors = array($e->getMessage());
            $this->getResponse()->setStatus(422);
        }
    }
}
