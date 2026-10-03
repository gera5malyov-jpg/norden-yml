<?php
class shopMegasuppliersPluginBackendImportConfigController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->isAdmin('shop')) {
            throw new waRightsException('Access denied');
        }
        $supplier_id = waRequest::post('supplier_id', 0, waRequest::TYPE_INT);
        $config = waRequest::post('config', '', waRequest::TYPE_STRING_TRIM);
        if (!$supplier_id || !$config) {
            $this->errors[] = 'SUPPLIER_AND_CONFIG_REQUIRED';
            return;
        }
        $decoded = json_decode($config, true);
        if (!is_array($decoded) || empty($decoded['source']) || empty($decoded['identity']) || empty($decoded['mapping'])) {
            $this->errors[] = 'INVALID_CONFIG';
            return;
        }
        $dir = wa()->getDataPath('plugins/megasuppliers/import-config', false, 'shop', true);
        waFiles::create($dir);
        $path = $dir.'/'.$supplier_id.'.json';
        waFiles::write($path, json_encode($decoded, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
        $this->response = array('status'=>'ok','supplier_id'=>$supplier_id);
    }
}
