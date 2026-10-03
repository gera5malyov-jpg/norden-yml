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
        if (!$supplier_id || $mode !== 'dry-run') {
            $this->errors[] = 'DRY_RUN_ONLY';
            return;
        }
        $dir = wa()->getDataPath('plugins/megasuppliers/import-config', false, 'shop', true);
        $path = $dir.'/'.$supplier_id.'.json';
        if (!file_exists($path)) {
            $this->errors[] = 'CONFIG_NOT_FOUND';
            return;
        }
        $config = json_decode(file_get_contents($path), true);
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
        try {
            $request_id = date('YmdHis').'-'.bin2hex(random_bytes(8));
        } catch (Exception $e) {
            $request_id = date('YmdHis').'-'.sha1(uniqid('', true));
        }
        $config_sha = hash('sha256', json_encode($this->sortRecursive($config), JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES));
        $status_dir = wa()->getDataPath('plugins/megasuppliers/import-status', false, 'shop', true);
        waFiles::create($status_dir);
        $status_path = $status_dir.'/'.$supplier_id.'.json';
        $previous = file_exists($status_path) ? json_decode(file_get_contents($status_path), true) : array();
        $previous_count = (!empty($previous['report']['blocked']) || empty($previous['report']['products'])) ? '' : (string)(int)$previous['report']['products'];

        $root = rtrim(wa()->getRootUrl(true), '/');
        $callback = $root.'/megasuppliers-callback/';
        $source_secret = trim((string)ifset($config['source']['location_secret']));
        $parts = explode('/', $repo, 2);
        if (count($parts) !== 2) {
            $this->errors[] = 'INVALID_GITHUB_REPO';
            return;
        }
        $url = 'https://api.github.com/repos/'.rawurlencode($parts[0]).'/'.rawurlencode($parts[1]).'/actions/workflows/supplier-engine-dry-run.yml/dispatches';
        $payload = json_encode(array('ref' => $ref, 'inputs' => array(
            'supplier_id' => (string)$supplier_id,
            'request_id' => $request_id,
            'config_b64' => $config_b64,
            'callback_url' => $callback,
            'source_secret' => $source_secret,
            'previous_count' => $previous_count,
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
            $this->errors[] = 'GITHUB_DISPATCH_FAILED_'.$code.($error ? '_'.$error : '');
            return;
        }
        $pending = array(
            'status' => 'queued',
            'supplier_id' => $supplier_id,
            'request_id' => $request_id,
            'mode' => 'dry-run',
            'config_sha256' => $config_sha,
            'requested_at' => date('c'),
            'report' => null,
        );
        waFiles::write($status_path, json_encode($pending, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
        $this->response = array('status' => 'ok', 'mode' => 'dry-run', 'supplier_id' => $supplier_id, 'request_id' => $request_id);
    }

    private function sortRecursive($value)
    {
        if (!is_array($value)) return $value;
        $assoc = array_keys($value) !== range(0, count($value) - 1);
        if ($assoc) ksort($value);
        foreach ($value as $k => $v) $value[$k] = $this->sortRecursive($v);
        return $value;
    }
}
