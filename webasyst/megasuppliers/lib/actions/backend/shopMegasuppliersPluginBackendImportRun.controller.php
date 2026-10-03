<?php
class shopMegasuppliersPluginBackendImportRunController extends waJsonController
{
    public function execute()
    {
        if (!wa()->getUser()->isAdmin('shop')) throw new waRightsException('Access denied');
        $supplier_id=waRequest::post('supplier_id',0,waRequest::TYPE_INT);
        if (!$supplier_id || waRequest::post('mode','dry-run',waRequest::TYPE_STRING_TRIM)!=='dry-run') {
            $this->errors[]='DRY_RUN_ONLY'; return;
        }
        $dir=wa()->getDataPath('plugins/megasuppliers/import-config',false,'shop',true);
        $path=$dir.'/'.$supplier_id.'.json';
        if (!file_exists($path)) { $this->errors[]='CONFIG_NOT_FOUND'; return; }
        $config=json_decode(file_get_contents($path),true);
        if (!is_array($config)) { $this->errors[]='INVALID_CONFIG'; return; }

        $repo=(string)wa()->getSetting('github_repo','gera5malyov-jpg/norden-yml','shop.megasuppliers');
        $token=(string)wa()->getSetting('github_token','','shop.megasuppliers');
        $callback_secret=(string)wa()->getSetting('callback_secret','','shop.megasuppliers');
        if (!$token || !$callback_secret) { $this->errors[]='BRIDGE_NOT_CONFIGURED'; return; }

        // Pass config as base64 JSON: no GitHub token is exposed to browser and no
        // supplier credentials should be stored in config; secrets remain GitHub secrets.
        $config_b64=base64_encode(json_encode($config,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES));
        if (strlen($config_b64)>50000) { $this->errors[]='CONFIG_TOO_LARGE'; return; }
        $callback=wa('shop')->getAppUrl(null,true).'?plugin=megasuppliers&module=backend&action=importCallback';

        $parts=explode('/',$repo,2);
        if (count($parts)!==2) { $this->errors[]='INVALID_GITHUB_REPO'; return; }
        $url='https://api.github.com/repos/'.rawurlencode($parts[0]).'/'.rawurlencode($parts[1]).'/actions/workflows/supplier-engine-dry-run.yml/dispatches';
        $payload=json_encode(array('ref'=>'supplier-plugin-finish-20261003','inputs'=>array(
            'supplier_id'=>(string)$supplier_id,'config_b64'=>$config_b64,
            'callback_url'=>$callback,'callback_secret'=>$callback_secret
        )));
        $ch=curl_init($url);
        curl_setopt_array($ch,array(CURLOPT_POST=>true,CURLOPT_RETURNTRANSFER=>true,
            CURLOPT_HTTPHEADER=>array('Accept: application/vnd.github+json','Authorization: Bearer '.$token,'X-GitHub-Api-Version: 2022-11-28','User-Agent: Webasyst-Megasuppliers'),
            CURLOPT_POSTFIELDS=>$payload,CURLOPT_TIMEOUT=>30));
        curl_exec($ch); $code=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE); curl_close($ch);
        if ($code!==204) { $this->errors[]='GITHUB_DISPATCH_FAILED_'.$code; return; }
        $this->response=array('status'=>'ok','mode'=>'dry-run','supplier_id'=>$supplier_id);
    }
}
