<?php
class shopOzonstocksyncPluginBackendRefreshCommissionsController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->getRights('shop', 'settings')) {
            throw new waRightsException('Access denied');
        }
        if (waRequest::getMethod() !== 'POST') {
            $this->errors[] = 'POST_REQUIRED';
            return;
        }
        $csrf = (string)waRequest::post('_csrf', '');
        $cookie = (string)waRequest::cookie('_csrf', '');
        if ($csrf === '' || $cookie === '' || !hash_equals($cookie, $csrf)) {
            $this->errors[] = 'CSRF_FAILED';
            return;
        }
        $path = wa()->getDataPath('plugins/ozonstocksync/rfbs_refresh_status.json', false, 'shop', true);
        $current = is_readable($path) ? json_decode((string)file_get_contents($path), true) : null;
        if (is_array($current) && isset($current['status']) &&
            in_array($current['status'], array('queued','running'), true)) {
            $start = !empty($current['started_at']) ? (int)$current['started_at'] : 0;
            if ($start && (time() - $start) < 3600) {
                $this->response = array('status' => 'already_running');
                return;
            }
        }
        $payload = json_encode(array('status' => 'queued', 'started_at' => time()), JSON_UNESCAPED_UNICODE);
        $tmp = $path . '.new.' . getmypid();
        if (file_put_contents($tmp, $payload, LOCK_EX) === false || !rename($tmp, $path)) {
            @unlink($tmp);
            $this->errors[] = 'QUEUE_WRITE_FAILED';
            return;
        }
        $this->response = array('status' => 'queued', 'message' => 'Запрос принят. Проверка очереди раз в минуту.');
    }
}
