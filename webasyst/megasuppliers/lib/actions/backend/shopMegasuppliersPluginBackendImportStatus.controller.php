<?php
class shopMegasuppliersPluginBackendImportStatusController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->isAdmin('shop')) throw new waRightsException('Access denied');
        $supplier_id=waRequest::get('supplier_id',0,waRequest::TYPE_INT);
        if (!$supplier_id) { $this->errors[]='SUPPLIER_REQUIRED'; return; }
        $dir=wa()->getDataPath('plugins/megasuppliers/import-status',false,'shop',true);
        $path=$dir.'/'.$supplier_id.'.json';
        $this->response=array('status'=>'ok','run'=>file_exists($path)?json_decode(file_get_contents($path),true):null);
    }
}
