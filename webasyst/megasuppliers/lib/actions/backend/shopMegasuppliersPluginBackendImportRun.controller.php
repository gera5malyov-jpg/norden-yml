<?php
class shopMegasuppliersPluginBackendImportRunController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->getRights('shop', 'settings')) {
            throw new waRightsException('Access denied');
        }
        $supplier_id = waRequest::post('supplier_id', 0, waRequest::TYPE_INT);
        $mode = waRequest::post('mode', 'dry-run', waRequest::TYPE_STRING_TRIM);
        if (!$supplier_id || !in_array($mode, array('dry-run', 'apply'), true)) {
            $this->errors[] = 'INVALID_RUN_MODE';
            return;
        }

        $config_path = wa()->getDataPath('plugins/megasuppliers/import-config', false, 'shop', true).'/'.$supplier_id.'.json';
        if (!file_exists($config_path)) {
            $this->errors[] = 'CONFIG_NOT_FOUND';
            return;
        }
        $config = json_decode(file_get_contents($config_path), true);
        if (!is_array($config)) {
            $this->errors[] = 'INVALID_CONFIG';
            return;
        }

        $plugin = wa('shop')->getPlugin('megasuppliers');
        $repo = trim((string)$plugin->getSettings('github_repo'));
        $ref = trim((string)$plugin->getSettings('github_ref'));
        $token = trim((string)$plugin->getSettings('github_token'));
        $callback_secret = trim((string)$plugin->getSettings('callback_secret'));
        if ($repo === '') $repo = 'gera5malyov-jpg/norden-yml';
        if ($ref === '') $ref = 'main';
        if ($token === '' || $callback_secret === '') {
            $this->errors[] = 'BRIDGE_NOT_CONFIGURED';
            return;
        }

        $config_json = json_encode($config, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
        $config_b64 = base64_encode($config_json);
        if (strlen($config_b64) > 50000) {
            $this->errors[] = 'CONFIG_TOO_LARGE';
            return;
        }
        $config_sha = hash('sha256', json_encode($this->sortRecursive($config), JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES));

        $status_dir = wa()->getDataPath('plugins/megasuppliers/import-status', false, 'shop', true);
        waFiles::create($status_dir);
        $status_path = $status_dir.'/'.$supplier_id.'.json';
        $previous = file_exists($status_path) ? json_decode(file_get_contents($status_path), true) : array();
        if (!is_array($previous)) $previous = array();
        $last_dry = !empty($previous['last_dry_run']) && is_array($previous['last_dry_run']) ? $previous['last_dry_run'] : null;

        $approved_source_sha = '';
        if ($mode === 'apply') {
            if (empty($config['enabled'])) {
                $this->errors[] = 'PROFILE_DISABLED';
                return;
            }
            if (!$plugin->getSettings('enable_writes')) {
                $this->errors[] = 'WRITES_DISABLED';
                return;
            }
            if (!$last_dry || empty($last_dry['config_sha256']) || !hash_equals((string)$last_dry['config_sha256'], $config_sha)) {
                $this->errors[] = 'FRESH_DRY_RUN_REQUIRED';
                return;
            }
            if (empty($last_dry['report']['source_sha256']) || !empty($last_dry['report']['blocked']) || (string)ifset($last_dry['report']['status']) !== 'ok') {
                $this->errors[] = 'SUCCESSFUL_DRY_RUN_REQUIRED';
                return;
            }
            $finished = strtotime((string)ifset($last_dry['finished_at']));
            $window = max(10, min(1440, (int)$plugin->getSettings('apply_window_minutes')));
            if (!$finished || (time() - $finished) > $window * 60) {
                $this->errors[] = 'DRY_RUN_EXPIRED';
                return;
            }
            $approved_source_sha = trim((string)$last_dry['report']['source_sha256']);
        }

        try {
            $request_id = date('YmdHis').'-'.bin2hex(random_bytes(8));
        } catch (Exception $e) {
            $request_id = date('YmdHis').'-'.sha1(uniqid('', true));
        }

        $previous_count = '';
        if ($last_dry && empty($last_dry['report']['blocked']) && !empty($last_dry['report']['products'])) {
            $previous_count = (string)(int)$last_dry['report']['products'];
        }

        $root = rtrim(wa()->getRootUrl(true), '/');
        $callback = $root.'/megasuppliers-callback/';
        $bridge = $root.'/megasuppliers-bridge/';
        $source_secret = trim((string)ifset($config['source']['location_secret']));
        if ($source_secret !== '' && !preg_match('/^MEGASUPPLIERS_SOURCE_[A-Z0-9_]+$/', $source_secret)) {
            $this->errors[] = 'INVALID_SOURCE_SECRET';
            return;
        }

        $pending = array(
            'status' => 'queued',
            'supplier_id' => $supplier_id,
            'request_id' => $request_id,
            'mode' => $mode,
            'config_sha256' => $config_sha,
            'requested_at' => date('c'),
            'report' => null,
            'last_dry_run' => $last_dry,
        );
        if (waFiles::write($status_path, json_encode($pending, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT)) === false) {
            $this->errors[] = 'STATUS_WRITE_FAILED';
            return;
        }

        $parts = explode('/', $repo, 2);
        if (count($parts) !== 2) {
            $this->markDispatchFailure($status_path, $pending, 'INVALID_GITHUB_REPO');
            $this->errors[] = 'INVALID_GITHUB_REPO';
            return;
        }
        $url = 'https://api.github.com/repos/'.rawurlencode($parts[0]).'/'.rawurlencode($parts[1]).'/actions/workflows/supplier-engine-dry-run.yml/dispatches';
        $payload = json_encode(array('ref' => $ref, 'inputs' => array(
            'mode' => $mode,
            'supplier_id' => (string)$supplier_id,
            'request_id' => $request_id,
            'config_b64' => $config_b64,
            'callback_url' => $callback,
            'bridge_url' => $bridge,
            'webasyst_base_url' => $root,
            'source_secret' => $source_secret,
            'previous_count' => $previous_count,
            'approved_source_sha256' => $approved_source_sha,
        )));
        $ch = curl_init($url);
        curl_setopt_array($ch, array(
            CURLOPT_POST => true,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => array(
                'Accept: application/vnd.github+json',
                'Authorization: Bearer '.$token,
                'X-GitHub-Api-Version: 2022-11-28',
                'User-Agent: Webasyst-Megasuppliers',
            ),
            CURLOPT_POSTFIELDS => $payload,
            CURLOPT_TIMEOUT => 30,
        ));
        $body = curl_exec($ch);
        $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        curl_close($ch);
        if ($code !== 204) {
            $message = 'GITHUB_DISPATCH_FAILED_'.$code.($error ? '_'.$error : '');
            $this->markDispatchFailure($status_path, $pending, $message);
            $this->errors[] = $message;
            return;
        }

        $this->response = array(
            'status' => 'ok',
            'mode' => $mode,
            'supplier_id' => $supplier_id,
            'request_id' => $request_id,
        );
    }

    private function markDispatchFailure($path, array $pending, $message)
    {
        $pending['status'] = 'dispatch_failed';
        $pending['finished_at'] = date('c');
        $pending['report'] = array('status' => 'failed', 'blocked' => true, 'errors' => array($message));
        waFiles::write($path, json_encode($pending, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
    }

    private function sortRecursive($value)
    {
        if (!is_array($value)) return $value;
        $keys = array_keys($value);
        $assoc = $keys !== array_keys($keys);
        if ($assoc) ksort($value);
        foreach ($value as $k => $v) $value[$k] = $this->sortRecursive($v);
        return $value;
    }
}
