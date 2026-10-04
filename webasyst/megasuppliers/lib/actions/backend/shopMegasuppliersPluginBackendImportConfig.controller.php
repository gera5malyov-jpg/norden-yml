<?php
class shopMegasuppliersPluginBackendImportConfigController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->getRights('shop', 'settings')) {
            throw new waRightsException('Access denied');
        }
        $supplier_id = waRequest::request('supplier_id', 0, waRequest::TYPE_INT);
        if (!$supplier_id) {
            $this->errors[] = 'SUPPLIER_REQUIRED';
            return;
        }
        $supplier = (new shopMegasuppliersSupplierModel())->getById($supplier_id);
        if (!$supplier) {
            $this->errors[] = 'SUPPLIER_NOT_FOUND';
            return;
        }
        $dir = wa()->getDataPath('plugins/megasuppliers/import-config', false, 'shop', true);
        waFiles::create($dir);
        $path = $dir.'/'.$supplier_id.'.json';

        if (waRequest::method() === 'get') {
            $config = file_exists($path) ? json_decode(file_get_contents($path), true) : null;
            $this->response = array(
                'status' => 'ok',
                'supplier_id' => $supplier_id,
                'config' => $config,
                'stocks' => $this->stockOptions(),
                'types' => $this->typeOptions(),
            );
            return;
        }

        $raw = waRequest::post('config', '', waRequest::TYPE_STRING_TRIM);
        if ($raw !== '') {
            $config = json_decode($raw, true);
        } else {
            $config = $this->fromForm($supplier);
        }
        if (!$this->validConfig($config)) {
            $this->errors[] = 'INVALID_CONFIG';
            return;
        }
        $config['code'] = (string)$supplier['code'];
        $config['name'] = (string)$supplier['name'];
        $json = json_encode($config, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
        if ($json === false || waFiles::write($path, $json) === false) {
            $this->errors[] = 'CONFIG_WRITE_FAILED';
            return;
        }
        $this->response = array('status' => 'ok', 'supplier_id' => $supplier_id, 'config' => $config);
    }

    private function typeOptions()
    {
        $rows = (new shopTypeModel())
            ->select('id,name')
            ->order('name')
            ->fetchAll();
        return array_values($rows);
    }

    private function stockOptions()
    {
        $rows = (new shopStockModel())
            ->select('id,name')
            ->order('name')
            ->fetchAll();
        return array_values($rows);
    }

    private function fromForm($supplier)
    {
        $images = preg_split('/\\s*,\\s*/', (string)waRequest::post('images', '', waRequest::TYPE_STRING_TRIM), -1, PREG_SPLIT_NO_EMPTY);
        $format = strtolower((string)waRequest::post('format', 'yml', waRequest::TYPE_STRING_TRIM));
        $sku_mode = strtolower((string)waRequest::post('sku_mode', '', waRequest::TYPE_STRING_TRIM));
        if (!in_array($sku_mode, array('numeric', 'supplier'), true)) {
            $sku_mode = $format === 'norden' ? 'numeric' : 'supplier';
        }
        $config = array(
            'code' => (string)$supplier['code'],
            'name' => (string)$supplier['name'],
            'enabled' => (bool)waRequest::post('enabled', 0, waRequest::TYPE_INT),
            'source' => array(
                'kind' => $format === 'norden' ? 'norden' : 'url',
                'format' => $format,
                'location' => (string)waRequest::post('source_location', '', waRequest::TYPE_STRING_TRIM),
                'location_secret' => (string)waRequest::post('location_secret', '', waRequest::TYPE_STRING_TRIM),
                'sheet' => (string)waRequest::post('sheet', '', waRequest::TYPE_STRING_TRIM),
            ),
            'identity' => array(
                'supplier_sku_field' => (string)waRequest::post('supplier_sku_field', '', waRequest::TYPE_STRING_TRIM),
                'sku_prefix' => (string)waRequest::post('sku_prefix', '', waRequest::TYPE_STRING_TRIM),
                'brand' => (string)waRequest::post('brand', '', waRequest::TYPE_STRING_TRIM),
            ),
            'mapping' => array(
                'name' => (string)waRequest::post('name_field', '', waRequest::TYPE_STRING_TRIM),
                'purchase_price' => (string)waRequest::post('purchase_price_field', '', waRequest::TYPE_STRING_TRIM),
                'price' => (string)waRequest::post('price_field', '', waRequest::TYPE_STRING_TRIM),
                'compare_price' => (string)waRequest::post('compare_price_field', '', waRequest::TYPE_STRING_TRIM),
                'stock' => (string)waRequest::post('stock_field', '', waRequest::TYPE_STRING_TRIM),
                'category' => (string)waRequest::post('category_field', '', waRequest::TYPE_STRING_TRIM),
                'images' => $images ? array_values($images) : array(),
                'characteristics' => array(),
            ),
            'rules' => array(
                'create_new' => (bool)waRequest::post('create_new', 0, waRequest::TYPE_INT),
                'update_prices' => true,
                'update_stock' => true,
                'update_images' => (bool)waRequest::post('update_images', 0, waRequest::TYPE_INT),
                'update_characteristics' => (bool)waRequest::post('update_characteristics', 0, waRequest::TYPE_INT),
                'zero_if_missing' => (bool)waRequest::post('zero_if_missing', 0, waRequest::TYPE_INT),
                'only_create_in_stock' => (bool)waRequest::post('only_create_in_stock', 0, waRequest::TYPE_INT),
                'price_formulas' => array_filter(array(
                    'purchase_price' => (string)waRequest::post('purchase_price_formula', '', waRequest::TYPE_STRING_TRIM),
                    'price' => (string)waRequest::post('price_formula', '', waRequest::TYPE_STRING_TRIM),
                    'compare_price' => (string)waRequest::post('compare_price_formula', '', waRequest::TYPE_STRING_TRIM),
                ), 'strlen'),
                'warehouses' => array(),
            ),
            'webasyst' => array(
                'sku_mode' => $sku_mode,
                'type_id' => waRequest::post('type_id', 0, waRequest::TYPE_INT) ?: null,
                'stock_id' => waRequest::post('stock_id', 0, waRequest::TYPE_INT) ?: null,
                'category_id' => waRequest::post('category_id', 0, waRequest::TYPE_INT) ?: null,
                'feature_codes' => array(),
            ),
            'safety' => array(
                'dry_run_required' => true,
                'min_source_count_ratio' => 0.60,
                'max_price_change_pct' => 100,
                'block_duplicate_sku' => true,
                'pdf_requires_review' => true,
            ),
        );

        if ($format === 'norden') {
            $config['source']['location'] = '';
            $config['source']['location_secret'] = '';
            $config['identity']['supplier_sku_field'] = 'product_code';
            $config['identity']['sku_prefix'] = '';
            $config['identity']['brand'] = 'Norden';
            $config['mapping']['name'] = 'name';
            $config['mapping']['purchase_price'] = 'price';
            $config['mapping']['price'] = '';
            $config['mapping']['compare_price'] = '';
            $config['mapping']['stock'] = 'qty';
            $config['mapping']['category'] = 'category';
            $config['mapping']['images'] = array('images');
            $config['mapping']['dynamic_characteristics'] = 'features';
        }

        return $config;
    }

    private function validConfig($config)
    {
        if (!is_array($config) || empty($config['source']) || empty($config['identity']) || empty($config['mapping']) || empty($config['rules']) || empty($config['safety'])) {
            return false;
        }
        $format = strtolower((string)ifset($config['source']['format']));
        if (!in_array($format, array('yml', 'xml', 'csv', 'xlsx', 'pdf', 'norden'), true)) {
            return false;
        }
        if (trim((string)ifset($config['identity']['supplier_sku_field'])) === '' || trim((string)ifset($config['mapping']['name'])) === '') {
            return false;
        }
        $sku_mode = strtolower((string)ifset($config['webasyst']['sku_mode']));
        if ($sku_mode !== '' && !in_array($sku_mode, array('numeric', 'supplier'), true)) {
            return false;
        }
        $source_secret = trim((string)ifset($config['source']['location_secret']));
        if ($format !== 'norden' && trim((string)ifset($config['source']['location'])) === '' && $source_secret === '') {
            return false;
        }
        if ($source_secret !== '' && !preg_match('/^MEGASUPPLIERS_SOURCE_[A-Z0-9_]+$/', $source_secret)) {
            return false;
        }
        return true;
    }
}
