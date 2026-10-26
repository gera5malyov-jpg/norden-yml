<?php
class shopOzonstocksyncPluginBackendCommissionStatusController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->getRights('shop', 'settings')) {
            throw new waRightsException('Access denied');
        }
        $path = wa()->getDataPath('plugins/ozonstocksync/rfbs_refresh_status.json', false, 'shop', true);
        $data = is_readable($path) ? json_decode((string)file_get_contents($path), true) : null;
        if (!is_array($data)) $data = array('status' => 'idle');
        $this->response = array(
            'status' => isset($data['status']) ? (string)$data['status'] : 'idle',
            'updated_at' => isset($data['finished_at']) ? (int)$data['finished_at'] : 0,
            'result' => isset($data['result']) ? $data['result'] : null,
            'error' => isset($data['error']) ? (string)$data['error'] : ''
        );
    }
}
