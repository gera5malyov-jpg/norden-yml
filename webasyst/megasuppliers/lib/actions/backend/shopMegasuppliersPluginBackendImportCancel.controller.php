<?php
class shopMegasuppliersPluginBackendImportCancelController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->getRights('shop', 'settings')) {
            throw new waRightsException('Access denied');
        }
        $supplier_id = waRequest::post('supplier_id', 0, waRequest::TYPE_INT);
        $request_id = waRequest::post('request_id', '', waRequest::TYPE_STRING_TRIM);
        if (!$supplier_id || $request_id === '') {
            $this->errors[] = 'INVALID_CANCEL_REQUEST';
            return;
        }

        $dir = wa()->getDataPath('plugins/megasuppliers/import-status', false, 'shop', true);
        $path = $dir.'/'.$supplier_id.'.json';
        if (!file_exists($path)) {
            $this->errors[] = 'STATUS_NOT_FOUND';
            return;
        }
        $run = json_decode(file_get_contents($path), true);
        if (!is_array($run) || (string)ifset($run['request_id']) !== $request_id) {
            $this->errors[] = 'REQUEST_ID_MISMATCH';
            return;
        }
        if (!in_array((string)ifset($run['status']), array('queued', 'running'), true)) {
            $this->errors[] = 'RUN_NOT_ACTIVE';
            return;
        }

        $run['status'] = 'cancelled';
        $run['finished_at'] = date('c');
        $run['report'] = array(
            'status' => 'cancelled',
            'blocked' => true,
            'errors' => array('RUN_CANCELLED'),
        );
        if (waFiles::write($path, json_encode($run, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT)) === false) {
            $this->errors[] = 'STATUS_WRITE_FAILED';
            return;
        }

        $this->response = array(
            'status' => 'ok',
            'supplier_id' => $supplier_id,
            'request_id' => $request_id,
        );
    }
}
