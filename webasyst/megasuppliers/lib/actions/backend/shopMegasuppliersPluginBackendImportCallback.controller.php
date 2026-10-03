<?php
class shopMegasuppliersPluginBackendImportCallbackController extends waJsonController
{
    public function execute()
    {
        $secret=(string)wa()->getSetting('callback_secret','', 'shop.megasuppliers');
        $provided=(string)waRequest::request('secret','',waRequest::TYPE_STRING_TRIM);
        if (!$secret || !hash_equals($secret,$provided)) { throw new waRightsException('Access denied'); }
        $supplier_id=waRequest::post('supplier_id',0,waRequest::TYPE_INT);
        $report=waRequest::post('report','',waRequest::TYPE_STRING_TRIM);
        $decoded=json_decode($report,true);
        if (!$supplier_id || !is_array($decoded)) { $this->errors[]='INVALID_REPORT'; return; }
        $dir=wa()->getDataPath('plugins/megasuppliers/import-status',false,'shop',true); waFiles::create($dir);
        waFiles::write($dir.'/'.$supplier_id.'.json',json_encode($decoded,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
        $this->response=array('status'=>'ok');
    }
}
