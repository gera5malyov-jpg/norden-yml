<?php
class shopMegasuppliersPluginBackendImportRunController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->isAdmin('shop')) throw new waRightsException('Access denied');
        $supplier_id = waRequest::post('supplier_id', 0, waRequest::TYPE_INT);
        $mode = waRequest::post('mode', 'dry-run', waRequest::TYPE_STRING_TRIM);
        if (!$supplier_id || $mode !== 'dry-run') {
            $this->errors[] = 'DRY_RUN_ONLY';
            return;
        }
        $dir = wa()->getDataPath('plugins/megasuppliers/import-config', false, 'shop', true);
        $path = $dir.'/'.$supplier_id.'.json';
        if (!file_exists($path)) { $this->errors[]='CONFIG_NOT_FOUND'; return; }
        $config = json_decode(file_get_contents($path), true);
        if (!is_array($config)) { $this->errors[]='INVALID_CONFIG'; return; }

        $repo = (string)wa()->getSetting('github_repo', 'gera5malyov-jpg/norden-yml', 'shop.megasuppliers');
        $token = (string)wa()->getSetting('github_token', '', 'shop.megasuppliers');
        if (!$token) { $this->errors[]='GITHUB_TOKEN_NOT_CONFIGURED'; return; }

        // Config is stored in Webasyst; workflow receives only supplier id and fetches
        // configuration through the protected plugin endpoint in the next stage.
        $url = 'https://api.github.com/repos/'.rawurlencode(explode('/', $repo)[0]).'/'.rawurlencode(explode('/', $repo)[1]).'/actions/workflows/supplier-engine-dry-run.yml/dispatches';
        $payload = json_encode(array('ref'=>'main','inputs'=>array('supplier_id'=>(string)$supplier_id)));
        $ch=curl_init($url);
        curl_setopt_array($ch,array(
            CURLOPT_POST=>true, CURLOPT_RETURNTRANSFER=>true,
            CURLOPT_HTTPHEADER=>array('Accept: application/vnd.github+json','Authorization: Bearer '.$token,'X-GitHub-Api-Version: 2022-11-28','User-Agent: Webasyst-Megasuppliers'),
            CURLOPT_POSTFIELDS=>$payload, CURLOPT_TIMEOUT=>30
        ));
        $body=curl_exec($ch); $code=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE); curl_close($ch);
        if ($code!==204) { $this->errors[]='GITHUB_DISPATCH_FAILED_'.$code; return; }
        $this->response=array('status'=>'ok','mode'=>'dry-run','supplier_id'=>$supplier_id);
    }
}
