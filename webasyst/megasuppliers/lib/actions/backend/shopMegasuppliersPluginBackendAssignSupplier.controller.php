<?php
class shopMegasuppliersPluginBackendAssignSupplierController extends waJsonController
{
    public function execute()
    {
        $product_id = waRequest::post('product_id', 0, waRequest::TYPE_INT);
        $supplier_id = waRequest::post('supplier_id', 0, waRequest::TYPE_INT);

        if (!$product_id) {
            $this->errors[] = 'PRODUCT_REQUIRED';
            return;
        }

        $product_model = new shopProductModel();
        $product = $product_model->getById($product_id);
        if (!$product) {
            $this->errors[] = 'PRODUCT_NOT_FOUND';
            return;
        }
        if (!$product_model->checkRights($product_id)) {
            throw new waRightsException('Access denied');
        }

        $map_model = new shopMegasuppliersProductModel();

        if (!$supplier_id) {
            $map_model->deleteByField('product_id', $product_id);
            $this->response = array('status' => 'ok', 'supplier_id' => 0);
            return;
        }

        $supplier = (new shopMegasuppliersSupplierModel())->getById($supplier_id);
        if (!$supplier || empty($supplier['active'])) {
            $this->errors[] = 'SUPPLIER_NOT_FOUND';
            return;
        }

        $sku_model = new shopProductSkusModel();
        $sku = !empty($product['sku_id']) ? $sku_model->getById($product['sku_id']) : array();
        if (!$sku) {
            $rows = $sku_model->getByField('product_id', $product_id, true);
            $sku = $rows ? reset($rows) : array();
        }

        $sku_id = !empty($sku['id']) ? (int)$sku['id'] : 0;
        $supplier_sku = !empty($sku['sku']) ? (string)$sku['sku'] : ('product-'.$product_id);

        $map_model->deleteByField('product_id', $product_id);
        $map_model->insert(array(
            'supplier_id' => (int)$supplier_id,
            'product_id' => (int)$product_id,
            'sku_id' => $sku_id,
            'supplier_sku' => $supplier_sku,
            'external_id' => '',
            'purchase_price' => null,
            'stock' => null,
            'raw_json' => json_encode(array('source' => 'manual_product_card'), JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES),
            'updated_at' => date('Y-m-d H:i:s'),
        ));

        $this->response = array(
            'status' => 'ok',
            'supplier_id' => (int)$supplier_id,
            'supplier_name' => $supplier['name'],
        );
    }
}
