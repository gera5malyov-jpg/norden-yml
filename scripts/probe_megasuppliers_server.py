#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

API_KEY = os.environ.get("NETANGELS_API_KEY", "").strip()
VM_ID = 44780
VM_IP = "45.86.180.49"
REPORT = ".deploy-probe/megasuppliers-server-probe.txt"

if not API_KEY:
    raise SystemExit("NETANGELS_API_KEY is not set")


def request_json(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return response.status, json.loads(raw) if raw else {}


def run():
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    token_body = urllib.parse.urlencode({"api_key": API_KEY}).encode()
    _, token_payload = request_json(
        "https://panel.netangels.ru/api/gateway/token/",
        "POST",
        token_body,
        {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "tetchair-dry-run/1.0"},
    )
    token = token_payload.get("token")
    if not token:
        raise RuntimeError("NetAngels token missing")
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "tetchair-dry-run/1.0",
    }

    php = r'''<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');

function ms_sort_recursive($value) {
    if (!is_array($value)) return $value;
    $keys=array_keys($value);
    $assoc=$keys !== array_keys($keys);
    if ($assoc) ksort($value);
    foreach ($value as $k=>$v) $value[$k]=ms_sort_recursive($v);
    return $value;
}

$supplier_id=15;
$config_dir=wa()->getDataPath('plugins/megasuppliers/import-config',false,'shop',true);
$config_path=$config_dir.'/'.$supplier_id.'.json';
if (!file_exists($config_path)) { fwrite(STDERR,"CONFIG_NOT_FOUND\n"); exit(2); }
$config=json_decode(file_get_contents($config_path),true);
if (!is_array($config)) { fwrite(STDERR,"INVALID_CONFIG\n"); exit(3); }

$type_id=(int)ifset($config['webasyst']['type_id']);
$purchase_field=(string)ifset($config['mapping']['purchase_price']);
$formulas=ifset($config['rules']['price_formulas'],array());
if ($type_id !== 1917 || $purchase_field !== 'purchase_price') {
    fwrite(STDERR,"TETCHAIR_PROFILE_MISMATCH\n");
    exit(4);
}
$expected_formulas=array(
    'purchase_price'=>'purchase_price',
    'price'=>'purchase_price * 1.25',
    'compare_price'=>'purchase_price * 1.80'
);
if ($formulas !== $expected_formulas) {
    fwrite(STDERR,"TETCHAIR_FORMULAS_MISMATCH ".json_encode($formulas,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n");
    exit(5);
}

$run_controller=$root.'/wa-apps/shop/plugins/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendImportRun.controller.php';
$controller_text=file_exists($run_controller)?file_get_contents($run_controller):'';
$dispatch_fix=(strpos($controller_text,'findDispatchRun')!==false && strpos($controller_text,'dispatch_recovered')!==false);
if (!$dispatch_fix) {
    fwrite(STDERR,"DISPATCH_FIX_NOT_DEPLOYED\n");
    exit(6);
}

$status_dir=wa()->getDataPath('plugins/megasuppliers/import-status',false,'shop',true);
waFiles::create($status_dir);
$status_path=$status_dir.'/'.$supplier_id.'.json';
$previous=file_exists($status_path)?json_decode(file_get_contents($status_path),true):array();
if (!is_array($previous)) $previous=array();

$active=(string)ifset($previous['status'],'');
if (in_array($active,array('queued','running'),true)) {
    $requested_at=strtotime((string)ifset($previous['requested_at'],''));
    if ($requested_at && time()-$requested_at < 350*60) {
        echo json_encode(array(
            'status'=>'already_active',
            'request_id'=>(string)ifset($previous['request_id'],''),
            'mode'=>(string)ifset($previous['mode'],''),
            'dispatch_fix_present'=>$dispatch_fix,
            'type_id'=>$type_id,
            'purchase_price_field'=>$purchase_field,
            'formulas'=>$formulas
        ),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
        exit(0);
    }
}

$plugin=wa('shop')->getPlugin('megasuppliers');
$repo=trim((string)$plugin->getSettings('github_repo'));
$ref=trim((string)$plugin->getSettings('github_ref'));
$github_token=trim((string)$plugin->getSettings('github_token'));
$callback_secret=trim((string)$plugin->getSettings('callback_secret'));
if ($repo==='') $repo='gera5malyov-jpg/norden-yml';
if ($ref==='') $ref='main';
if ($github_token==='' || $callback_secret==='') {
    fwrite(STDERR,"BRIDGE_NOT_CONFIGURED\n");
    exit(7);
}

$config_json=json_encode($config,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
$config_b64=base64_encode($config_json);
$config_sha=hash('sha256',json_encode(ms_sort_recursive($config),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES));
$last_dry=!empty($previous['last_dry_run']) && is_array($previous['last_dry_run'])?$previous['last_dry_run']:null;
$previous_count='';
if ($last_dry && empty($last_dry['report']['blocked']) && !empty($last_dry['report']['products'])) {
    $previous_count=(string)(int)$last_dry['report']['products'];
}

try {
    $request_id='tetchair-'.date('YmdHis').'-'.bin2hex(random_bytes(6));
} catch (Exception $e) {
    $request_id='tetchair-'.date('YmdHis').'-'.substr(sha1(uniqid('',true)),0,12);
}
$root_url='https://profikompany.ru';
$callback=$root_url.'/megasuppliers-callback/';
$bridge=$root_url.'/megasuppliers-bridge/';
$source_secret=trim((string)ifset($config['source']['location_secret']));

$pending=array(
    'status'=>'queued',
    'supplier_id'=>$supplier_id,
    'request_id'=>$request_id,
    'mode'=>'dry-run',
    'config_sha256'=>$config_sha,
    'requested_at'=>date('c'),
    'report'=>null,
    'last_dry_run'=>$last_dry,
    'manual_probe'=>'tetchair-price-dispatch-20261007'
);
if (waFiles::write($status_path,json_encode($pending,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT))===false) {
    fwrite(STDERR,"STATUS_WRITE_FAILED\n");
    exit(8);
}

$parts=explode('/',$repo,2);
if (count($parts)!==2) { fwrite(STDERR,"INVALID_GITHUB_REPO\n"); exit(9); }
$url='https://api.github.com/repos/'.rawurlencode($parts[0]).'/'.rawurlencode($parts[1]).'/actions/workflows/supplier-engine-dry-run.yml/dispatches';
$payload=json_encode(array('ref'=>$ref,'inputs'=>array(
    'mode'=>'dry-run',
    'supplier_id'=>(string)$supplier_id,
    'request_id'=>$request_id,
    'config_b64'=>$config_b64,
    'callback_url'=>$callback,
    'bridge_url'=>$bridge,
    'webasyst_base_url'=>$root_url,
    'source_secret'=>$source_secret,
    'previous_count'=>$previous_count,
    'approved_source_sha256'=>''
)));
$ch=curl_init($url);
curl_setopt_array($ch,array(
    CURLOPT_POST=>true,
    CURLOPT_RETURNTRANSFER=>true,
    CURLOPT_HTTPHEADER=>array(
        'Accept: application/vnd.github+json',
        'Authorization: Bearer '.$github_token,
        'X-GitHub-Api-Version: 2022-11-28',
        'User-Agent: Webasyst-Megasuppliers-Tetchair-DryRun'
    ),
    CURLOPT_POSTFIELDS=>$payload,
    CURLOPT_TIMEOUT=>30
));
$body=curl_exec($ch);
$code=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);
$error=curl_error($ch);
curl_close($ch);

if ($code!==204) {
    $pending['dispatch_http_code']=$code;
    $pending['dispatch_error']=$error;
    if ($code>=500 && $code<=599) {
        $pending['dispatch_uncertain']=true;
        waFiles::write($status_path,json_encode($pending,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
    } else {
        $pending['status']='dispatch_failed';
        $pending['finished_at']=date('c');
        $pending['report']=array('status'=>'failed','blocked'=>true,'errors'=>array('GITHUB_DISPATCH_FAILED_'.$code));
        waFiles::write($status_path,json_encode($pending,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT));
        fwrite(STDERR,"GITHUB_DISPATCH_FAILED_".$code."\n");
        exit(10);
    }
}

echo json_encode(array(
    'status'=>'dispatched',
    'supplier_id'=>$supplier_id,
    'request_id'=>$request_id,
    'dispatch_http_code'=>$code,
    'dispatch_uncertain'=>($code!==204),
    'dispatch_fix_present'=>$dispatch_fix,
    'type_id'=>$type_id,
    'purchase_price_field'=>$purchase_field,
    'formulas'=>$formulas,
    'previous_count'=>$previous_count
),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
?>'''

    with tempfile.TemporaryDirectory() as td:
        key_path = os.path.join(td, "id")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key_path], check=True)
        pub = open(key_path + ".pub", encoding="utf-8").read().strip()
        _, created = request_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",
            json.dumps({"key": pub, "name": "chatgpt-tetchair-dryrun-" + os.environ.get("GITHUB_RUN_ID", "manual")}).encode(),
            headers,
        )
        key_id = created.get("id")
        if not key_id:
            raise RuntimeError("SSH key id missing")
        try:
            remote = (
                "cat >/tmp/tetchair_dry_run.php <<'PHP'\n"
                + php
                + "\nPHP\n"
                + "chown web:web /tmp/tetchair_dry_run.php\n"
                + "su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/tetchair_dry_run.php'\n"
                + "rc=$?\nrm -f /tmp/tetchair_dry_run.php\nexit $rc\n"
            )
            proc = subprocess.run(
                [
                    "ssh", "-i", key_path,
                    "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null",
                    "-o", "ConnectTimeout=15",
                    "root@" + VM_IP,
                    "bash -s",
                ],
                input=remote,
                text=True,
                capture_output=True,
                timeout=120,
            )
            output = (proc.stdout or "").strip()
            error = (proc.stderr or "").strip()
            with open(REPORT, "w", encoding="utf-8") as fh:
                fh.write(output + "\n")
                if error:
                    fh.write("stderr=" + error[-1200:] + "\n")
            print(output)
            if error:
                print(error, file=sys.stderr)
            if proc.returncode:
                raise RuntimeError("remote dry-run dispatch failed with exit %d" % proc.returncode)
        finally:
            try:
                request_json(
                    f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/",
                    "DELETE",
                    None,
                    headers,
                )
            except Exception as exc:
                print("temporary SSH key cleanup warning:", repr(exc), file=sys.stderr)


if __name__ == "__main__":
    run()
