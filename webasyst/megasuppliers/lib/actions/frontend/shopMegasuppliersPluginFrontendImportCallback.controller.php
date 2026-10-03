<?php
class shopMegasuppliersPluginFrontendImportCallbackController extends waJsonController
{
    public function execute()
    {
        $this->getResponse()->setHeader('Content-Type', 'application/json; charset=utf-8');
        if (waRequest::method() !== 'post') {
            $this->errors = array('method_not_allowed');
            $this->getResponse()->setStatus(405);
            return;
        }
        $body = file_get_contents('php://input');
        if (strlen($body) > 2097152) {
            $this->errors = array('payload_too_large');
            $this->getResponse()->setStatus(413);
            return;
        }
        $plugin = wa('shop')->getPlugin('megasuppliers');
        $secret = trim((string)$plugin->getSettings('callback_secret'));
        $provided = trim((string)waRequest::server('HTTP_X_MEGASUPPLIERS_SIGNATURE', ''));
        $expected = $secret === '' ? '' : 'sha256='.hash_hmac('sha256', $body, $secret);
        if ($secret === '' || $provided === '' || !hash_equals($expected, $provided)) {
            $this->errors = array('unauthorized');
            $this->getResponse()->setStatus(401);
            return;
        }
        $payload = json_decode($body, true);
        if (!is_array($payload)) {
            $this->errors = array('invalid_json');
            $this->getResponse()->setStatus(400);
            return;
        }
        $supplier_id = (int)ifset($payload['supplier_id']);
        $request_id = trim((string)ifset($payload['request_id']));
        $report = ifset($payload['report'], null);
        if (!$supplier_id || $request_id === '' || !is_array($report)) {
            $this->errors = array('invalid_report');
            $this->getResponse()->setStatus(422);
            return;
        }

        $dir = wa()->getDataPath('plugins/megasuppliers/import-status', false, 'shop', true);
        waFiles::create($dir);
        $path = $dir.'/'.$supplier_id.'.json';
        $pending = file_exists($path) ? json_decode(file_get_contents($path), true) : null;
        if (!is_array($pending) || empty($pending['request_id']) || !hash_equals((string)$pending['request_id'], $request_id)) {
            $this->errors = array('stale_or_unknown_request');
            $this->getResponse()->setStatus(409);
            return;
        }

        $report_mode = (string)ifset($report['mode'], '');
        if ($report_mode !== '' && $report_mode !== (string)ifset($pending['mode'])) {
            $this->errors = array('mode_mismatch');
            $this->getResponse()->setStatus(409);
            return;
        }
        $report_config_sha = trim((string)ifset($report['config_sha256']));
        $pending_config_sha = trim((string)ifset($pending['config_sha256']));
        if ($report_config_sha !== '' && $pending_config_sha !== '' && !hash_equals($pending_config_sha, $report_config_sha)) {
            $this->errors = array('config_mismatch');
            $this->getResponse()->setStatus(409);
            return;
        }

        $report_status = (string)ifset($report['status'], '');
        if ($report_status === 'ok' && empty($report['blocked'])) {
            $pending['status'] = 'completed';
        } elseif ($report_status === 'failed') {
            $pending['status'] = 'failed';
        } else {
            $pending['status'] = 'blocked';
        }
        $pending['finished_at'] = date('c');
        $pending['report'] = $report;

        if ((string)ifset($pending['mode']) === 'dry-run' && $pending['status'] === 'completed') {
            $pending['last_dry_run'] = array(
                'config_sha256' => (string)ifset($pending['config_sha256']),
                'finished_at' => $pending['finished_at'],
                'report' => $report,
            );
        }

        if (waFiles::write($path, json_encode($pending, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT)) === false) {
            $this->errors = array('status_write_failed');
            $this->getResponse()->setStatus(500);
            return;
        }
        $this->response = array('status' => 'ok');
    }
}
