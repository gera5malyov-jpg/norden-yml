#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile, urllib.parse, urllib.request

API_KEY=os.environ.get("NETANGELS_API_KEY","").strip()
VM_ID=44780
VM_IP="45.86.180.49"
ROOT="/home/web/vm-23f9aff9.na4u.ru/www"
REPORT=".deploy-probe/megasuppliers-server-probe.txt"

if not API_KEY:
    raise SystemExit("NETANGELS_API_KEY is not set")

def request_json(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def run():
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    lines=[]
    def add(s):
        lines.append(str(s))
        print(s)

    token_body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=request_json(
        "https://panel.netangels.ru/api/gateway/token/",
        "POST",
        token_body,
        {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"megasuppliers-probe/1.0"},
    )
    token=tok.get("token")
    if not token:
        raise RuntimeError("token missing")
    headers={
        "Authorization":f"Bearer {token}",
        "Content-Type":"application/json",
        "Accept":"application/json",
        "User-Agent":"megasuppliers-probe/1.0",
    }

    with tempfile.TemporaryDirectory() as td:
        key_path=os.path.join(td,"megasuppliers_ed25519")
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key_path],check=True)
        pub=open(key_path+".pub","r",encoding="utf-8").read().strip()
        payload=json.dumps({"key":pub,"name":"chatgpt-megasuppliers-probe-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode()
        key_id=None
        try:
            _,created=request_json(
                f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
                "POST",payload,headers
            )
            key_id=created.get("id")
            if not key_id:
                raise RuntimeError("SSH key id missing")

            remote_cmd=f"""set -eu
ROOT={ROOT}
PLUGIN="$ROOT/wa-apps/shop/plugins/megasuppliers"
echo "hostname=$(hostname)"
echo "root_exists=$(test -d "$ROOT" && echo yes || echo no)"
ls -ld "$ROOT" "$ROOT/wa-apps" "$ROOT/wa-apps/shop" "$ROOT/wa-apps/shop/plugins" 2>&1 || true
echo "--- php ---"
php -v 2>&1 | head -n 3 || true
echo "--- plugin ---"
if [ -e "$PLUGIN" ]; then
  echo "plugin_exists=yes"
  ls -ld "$PLUGIN"
  find "$PLUGIN" -maxdepth 3 -type f -printf '%u:%g %m %p\\n' | head -n 60
  echo "--- plugin php lint ---"
  find "$PLUGIN" -name '*.php' -print0 | while IFS= read -r -d '' f; do php -l "$f" || true; done
else
  echo "plugin_exists=no"
fi
echo "--- plugin config registration ---"
if [ -f "$ROOT/wa-config/apps/shop/plugins.php" ]; then
  grep -n "megasuppliers" "$ROOT/wa-config/apps/shop/plugins.php" || true
  ls -l "$ROOT/wa-config/apps/shop/plugins.php"
  echo "--- plugins.php contents ---"
  cat "$ROOT/wa-config/apps/shop/plugins.php"
else
  echo "plugins.php not found"
fi
echo "--- megasuppliers DB tables ---"
cat >/tmp/ms_table_probe.php <<'PHP'
<?php
$root = '/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null, new SystemConfig());
wa('shop');
$m=new waModel();
foreach (array('shop_megasuppliers_supplier','shop_megasuppliers_product','shop_megasuppliers_import','shop_megasuppliers_meta') as $t) {
  try {
    $r=$m->query("SHOW TABLES LIKE s:table", array('table'=>$t))->fetch();
    echo $t."=".($r ? "yes" : "no")."\\n";
  } catch (Exception $e) {
    echo $t."=ERROR ".$e->getMessage()."\\n";
  }
}
try {
  $p=wa('shop')->getPlugin('megasuppliers', true);
  echo "plugin_class=".get_class($p)." version=".$p->getVersion()."\\n";
} catch (Exception $e) {
  echo "plugin_init_error=".$e->getMessage()."\\n";
}
PHP
chown web:web /tmp/ms_table_probe.php
su -s /bin/bash web -c 'php /tmp/ms_table_probe.php' || true
rm -f /tmp/ms_table_probe.php
echo "--- existing plugin ownership sample ---"
find "$ROOT/wa-apps/shop/plugins" -mindepth 1 -maxdepth 1 -type d -printf '%u:%g %m %p\\n' | head -n 12
echo "--- bootstrap diagnostic ---"
cat >/tmp/ms_bootstrap_diag.php <<'PHP'
<?php
chdir('/home/web/vm-23f9aff9.na4u.ru/www');
require_once '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/SystemConfig.class.php';
waSystem::getInstance(null, new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('nordenstock', true);
echo get_class($p),"\\n";
PHP
chown web:web /tmp/ms_bootstrap_diag.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_bootstrap_diag.php'; echo "bootstrap_exit=$?" || true
rm -f /tmp/ms_bootstrap_diag.php
echo "--- web user php modules ---"
su -s /bin/bash web -c 'php -m | grep -E "mysqli|pdo_mysql|zip|SimpleXML|mbstring" || true'
echo "--- nordenstock config ---"
for f in "$ROOT/wa-apps/shop/plugins/nordenstock/lib/config/plugin.php" "$ROOT/wa-apps/shop/plugins/nordenstock/lib/config/db.php" "$ROOT/wa-apps/shop/plugins/nordenstock/lib/config/install.php"; do
  if [ -f "$f" ]; then echo "### $f"; sed -n '1,260p' "$f"; fi
done
echo "--- sample plugin backend actions ---"
find "$ROOT/wa-apps/shop/plugins" -path '*/lib/actions/backend/*.php' -type f | head -n 5 | while read f; do echo "### $f"; sed -n '1,180p' "$f"; done
echo "--- framework plugin installer references ---"
grep -RIn "function.*install.*Plugin\|install.php\|lib/config/db.php" "$ROOT/wa-system" "$ROOT/wa-installer/lib" 2>/dev/null | head -n 120 || true
echo "--- waPlugin install implementation ---"
sed -n '1,310p' "$ROOT/wa-system/plugin/waPlugin.class.php"
echo "--- create plugin CLI install helper ---"
sed -n '140,235p' "$ROOT/wa-system/webasyst/lib/cli/webasystCreatePlugin.cli.php"
echo "--- shop getPlugin references ---"
grep -RIn "getPlugin(" "$ROOT/wa-apps/shop/lib" 2>/dev/null | head -n 60 || true
echo "--- system routing ---"
if [ -f "$ROOT/wa-config/routing.php" ]; then
  cat "$ROOT/wa-config/routing.php"
fi
echo "--- megasuppliers routing ---"
for f in "$PLUGIN/lib/config/plugin.php" "$PLUGIN/lib/config/routing.php" "$PLUGIN/lib/config/install.php" "$PLUGIN/lib/shopMegasuppliers.plugin.php" "$PLUGIN/lib/models/shopMegasuppliersMeta.model.php" "$PLUGIN/lib/models/shopMegasuppliersSupplier.model.php" "$PLUGIN/lib/models/shopMegasuppliersProduct.model.php" "$PLUGIN/lib/classes/shopMegasuppliersImportService.class.php" "$PLUGIN/lib/actions/backend/shopMegasuppliersPluginBackend.action.php" "$PLUGIN/lib/actions/frontend/shopMegasuppliersPluginFrontendApi.controller.php" "$PLUGIN/lib/actions/backend/shopMegasuppliersPluginBackendImport.controller.php"; do
  if [ -f "$f" ]; then echo "### $f"; sed -n '1,320p' "$f"; fi
done
echo "--- recent megasuppliers/webasyst errors ---"
for log in "$ROOT/wa-log/shop/plugins/megasuppliers.log" "$ROOT/wa-log/php.log" "$ROOT/wa-log/error.log" "$ROOT/wa-log/webasyst.log"; do
  if [ -f "$log" ]; then
    echo "### $log"
    tail -n 120 "$log" | grep -i -E "megasuppliers|fatal|exception|error" | tail -n 80 || true
  fi
done

echo "--- shop plugin dispatcher ---"
grep -RIn "waRequest::.*plugin\|getPlugin(.*true\|PluginBackend" "$ROOT/wa-apps/shop/lib/actions/backend" "$ROOT/wa-apps/shop/lib/config" 2>/dev/null | head -n 180 || true
echo "--- working plugin backend structure ---"
for f in "$ROOT/wa-apps/shop/plugins/yml/lib/shopYml.plugin.php" "$ROOT/wa-apps/shop/plugins/yml/lib/actions/backend/shopYmlPluginBackendSetup.action.php"; do
  if [ -f "$f" ]; then echo "### $f"; sed -n '1,260p' "$f"; fi
done
echo "--- compare existing plugin backend routing ---"
for f in "$ROOT/wa-apps/shop/plugins/yml/lib/shopYml.plugin.php" "$ROOT/wa-apps/shop/plugins/plugincontrol/lib/shopPlugincontrol.plugin.php"; do
  if [ -f "$f" ]; then
    echo "### $f"
    grep -n -E "backendMenu|backendExtendedMenu|getAppUrl|plugin=" "$f" | head -n 80 || true
  fi
done
find "$ROOT/wa-apps/shop/plugins/yml/lib/actions/backend" -maxdepth 1 -type f -printf '%f\n' 2>/dev/null | sort | head -n 80

echo "--- existing supplier metadata sources ---"
cat >/tmp/ms_supplier_sources.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();

echo "[types]\n";
$types=$m->query("SELECT t.id,t.name,COUNT(p.id) c FROM shop_type t LEFT JOIN shop_product p ON p.type_id=t.id GROUP BY t.id,t.name HAVING c>0 ORDER BY c DESC,t.name LIMIT 200")->fetchAll();
foreach($types as $r){ echo $r['id']."\t".$r['c']."\t".$r['name']."\n"; }

echo "[features]\n";
$features=$m->query("SELECT id,code,name,type,multiple,selectable FROM shop_feature WHERE LOWER(name) LIKE '%постав%' OR LOWER(code) LIKE '%supplier%' OR LOWER(code) LIKE '%postav%' ORDER BY id")->fetchAll();
foreach($features as $r){ echo json_encode($r,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n"; }

echo "[product_params_supplier_like]\n";
$params=$m->query("SELECT name,COUNT(*) c FROM shop_product_params WHERE LOWER(name) LIKE '%постав%' OR LOWER(name) LIKE '%supplier%' GROUP BY name ORDER BY c DESC LIMIT 100")->fetchAll();
foreach($params as $r){ echo $r['name']."\t".$r['c']."\n"; }
PHP
chown web:web /tmp/ms_supplier_sources.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_supplier_sources.php' || true
rm -f /tmp/ms_supplier_sources.php

echo "--- supplier metadata details ---"
cat >/tmp/ms_supplier_details.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();

echo "[matching_types]\n";
$rows=$m->query("SELECT t.id,t.name,COUNT(p.id) c FROM shop_type t LEFT JOIN shop_product p ON p.type_id=t.id WHERE LOWER(t.name) LIKE '%сезон%' OR LOWER(t.name) LIKE '%your%' OR LOWER(t.name) LIKE '%никит%' OR LOWER(t.name) LIKE '%fh%' OR LOWER(t.name) LIKE '%afina%' OR LOWER(t.name) LIKE '%kenner%' OR LOWER(t.name) LIKE '%levmar%' OR LOWER(t.name) LIKE '%левмар%' OR LOWER(t.name) LIKE '%norden%' OR LOWER(t.name) LIKE '%deep%' OR LOWER(t.name) LIKE '%treez%' OR LOWER(t.name) LIKE '%red%' OR LOWER(t.name) LIKE '%алет%' OR LOWER(t.name) LIKE '%aletan%' OR LOWER(t.name) LIKE '%b2b%' OR LOWER(t.name) LIKE '%в2в%' OR LOWER(t.name) LIKE '%андрей%' GROUP BY t.id,t.name ORDER BY t.name")->fetchAll();
foreach($rows as $r){ echo $r['id']."\t".$r['c']."\t".$r['name']."\n"; }

echo "[feature_681_values]\n";
$f=(new shopFeatureModel())->getById(681);
if($f){
  $vm=shopFeatureModel::getValuesModel($f['type']);
  $vals=$vm->select('id,value')->where('feature_id=681')->fetchAll('id');
  foreach($vals as $id=>$v){
    $cnt=$m->query("SELECT COUNT(DISTINCT product_id) c FROM shop_product_features WHERE feature_id=681 AND feature_value_id=i:id",array('id'=>$id))->fetchField('c');
    echo $id."\t".$cnt."\t".$v['value']."\n";
  }
}
PHP
chown web:web /tmp/ms_supplier_details.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_supplier_details.php' || true
rm -f /tmp/ms_supplier_details.php

echo "--- supplier mapping counts ---"
cat >/tmp/ms_supplier_counts.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$rows=$m->query("SELECT s.id,s.name,s.code,COUNT(DISTINCT CASE WHEN mp.product_id>0 THEN mp.product_id END) c FROM shop_megasuppliers_supplier s LEFT JOIN shop_megasuppliers_product mp ON mp.supplier_id=s.id GROUP BY s.id,s.name,s.code ORDER BY s.name")->fetchAll();
foreach($rows as $r){ echo $r['id']."\t".$r['code']."\t".$r['name']."\t".$r['c']."\n"; }
PHP
chown web:web /tmp/ms_supplier_counts.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_supplier_counts.php' || true
rm -f /tmp/ms_supplier_counts.php

echo "--- navigation/filter runtime diagnostic ---"
cat >/tmp/ms_nav_runtime.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('megasuppliers',true);
$cfg=include $root.'/wa-apps/shop/plugins/megasuppliers/lib/config/plugin.php';
echo "version=".ifset($cfg['version'])."\n";
echo "handlers=".json_encode(ifset($cfg['handlers'],array()),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
$direct=$p->backendProducts(array());
echo "direct_sidebar=".(isset($direct['sidebar_section'])?'yes':'no')." len=".strlen(ifset($direct['sidebar_section'],''))."\n";
$evt=wa('shop')->event('backend_products');
echo "event_plugins=".implode(',',array_keys($evt))."\n";
echo "event_sidebar=".(isset($evt['megasuppliers']['sidebar_section'])?'yes':'no')." len=".strlen(ifset($evt['megasuppliers']['sidebar_section'],''))."\n";

$sm=new shopMegasuppliersSupplierModel();
$s4=$sm->getByField('code','4SIS');
echo "4sis_id=".ifset($s4['id'])."\n";
if($s4){
    $_GET['megasupplier']=(int)$s4['id'];
    $_REQUEST['megasupplier']=(int)$s4['id'];
    $col=new shopProductsCollection('');
    $before=$col->count();
    $params=array('filter'=>null,'filter_options'=>array(),'collection'=>$col);
    $p->backendProdFilters($params);
    $after=$col->count();
    echo "manual_filter_before=".$before." after=".$after."\n";
    $products=$col->getProducts('id,name',0,20,false);
    echo "manual_filter_ids=".implode(',',array_keys($products))."\n";
    $test=$m=new waModel();
    $pid=$m->query("SELECT p.id FROM shop_product_skus s JOIN shop_product p ON p.id=s.product_id WHERE s.sku='AF-31655692' LIMIT 1")->fetchField();
    echo "test_4sis_pid=".$pid." included=".(isset($products[$pid])?'first20':'not_first20')."\n";
}
PHP
chown web:web /tmp/ms_nav_runtime.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_nav_runtime.php' || true
rm -f /tmp/ms_nav_runtime.php

echo "--- backend action cli diagnostic ---"
cat >/tmp/ms_backend_probe.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
try {
    $p=wa('shop')->getPlugin('megasuppliers',true);
    echo "plugin=".get_class($p)."\n";
    echo "backend_class_exists=".(class_exists('shopMegasuppliersPluginBackendAction')?'yes':'no')."\n";
    echo "supplier_model_exists=".(class_exists('shopMegasuppliersSupplierModel')?'yes':'no')."\n";
    $m=new shopMegasuppliersSupplierModel();
    echo "supplier_count=".$m->countAll()."\n";
    $sidebar=$p->backendProducts(array());
    $html=is_array($sidebar)&&isset($sidebar['sidebar_section'])?$sidebar['sidebar_section']:'';
    echo "sidebar_render=".($html!==''?'yes':'no')."\n";
    echo "sidebar_filter_link=".(strpos($html,'#/products/hash=megasuppliers/')!==false?'yes':'no')."\n";
    echo "sidebar_norden=".(strpos($html,'Norden')!==false?'yes':'no')."\n";
} catch (Throwable $e) {
    echo "backend_probe_error=".get_class($e).": ".$e->getMessage()."\n".$e->getTraceAsString()."\n";
}
PHP
chown web:web /tmp/ms_backend_probe.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_backend_probe.php' || true
rm -f /tmp/ms_backend_probe.php

echo "--- api bridge diagnostic ---"
if [ -f "$ROOT/megasuppliers-api/index.php" ]; then
  php -l "$ROOT/megasuppliers-api/index.php" || true
  REQUEST_METHOD=POST php -d display_errors=1 -d log_errors=0 "$ROOT/megasuppliers-api/index.php" 2>&1 || true
fi
echo "--- megasuppliers templates ---"
find "$PLUGIN/templates" -maxdepth 3 -type f -print 2>/dev/null | while read f; do echo "### $f"; sed -n '1,320p' "$f"; done
echo "--- megasuppliers backend direct probe ---"
cat >/tmp/ms_backend_probe.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('megasuppliers',true);
echo "plugin=".get_class($p)."\n";
echo "backend_action_class=".(class_exists('shopMegasuppliersPluginBackendAction')?'yes':'no')."\n";
$m=new shopMegasuppliersMetaModel();
$row=$m->getById('api_key');
echo "meta_read=".(is_array($row)&&!empty($row['value'])?'yes':'no')."\n";
$s=new shopMegasuppliersSupplierModel();
echo "supplier_count=".$s->countAll()."\n";
$i=new shopMegasuppliersImportModel();
echo "import_count=".$i->countAll()."\n";
PHP
chown web:web /tmp/ms_backend_probe.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_backend_probe.php' || true
rm -f /tmp/ms_backend_probe.php

echo "--- megasuppliers performance benchmark (read-only) ---"
cat >/tmp/ms_perf_probe.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$p=wa('shop')->getPlugin('megasuppliers',true);

echo "[indexes]\n";
foreach($m->query("SHOW INDEX FROM shop_megasuppliers_product")->fetchAll() as $r){
    echo $r['Key_name']."\t".$r['Seq_in_index']."\t".$r['Column_name']."\n";
}
echo "[sizes]\n";
echo "mapping_rows=".$m->query("SELECT COUNT(*) FROM shop_megasuppliers_product")->fetchField()."\n";
echo "mapped_products=".$m->query("SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product WHERE product_id>0")->fetchField()."\n";

$top=$m->query("SELECT supplier_id,COUNT(DISTINCT product_id) c FROM shop_megasuppliers_product WHERE product_id>0 GROUP BY supplier_id ORDER BY c DESC LIMIT 1")->fetch();
$sid=$top?(int)$top['supplier_id']:0;
echo "benchmark_supplier_id=".$sid." products=".($top?(int)$top['c']:0)."\n";

$t=microtime(true);
$rows=$m->query("SELECT supplier_id,COUNT(DISTINCT product_id) c FROM shop_megasuppliers_product WHERE product_id>0 GROUP BY supplier_id")->fetchAll();
echo "group_counts_ms=".round((microtime(true)-$t)*1000,2)." suppliers=".count($rows)."\n";

$t=microtime(true);
$suppliers=(new shopMegasuppliersSupplierModel())->select('id')->where('active=1')->fetchAll();
foreach($suppliers as $r){
    $id=(int)$r['id'];
    $m->query("SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product WHERE supplier_id=i:id AND product_id>0",array('id'=>$id))->fetchField();
}
echo "per_supplier_counts_ms=".round((microtime(true)-$t)*1000,2)." suppliers=".count($suppliers)."\n";

if(method_exists($p,'backendProdList')){
    $t=microtime(true);
    $x=$p->backendProdList(array());
    echo "backendProdList_ms=".round((microtime(true)-$t)*1000,2)." len=".strlen(isset($x['header_left'])?$x['header_left']:'')."\n";
}
if(method_exists($p,'backendProducts')){
    $t=microtime(true);
    $x=$p->backendProducts(array());
    echo "backendProducts_ms=".round((microtime(true)-$t)*1000,2)." len=".strlen(isset($x['sidebar_section'])?$x['sidebar_section']:'')."\n";
}

if($sid){
    $q1="SELECT COUNT(*) FROM shop_product p WHERE EXISTS (SELECT 1 FROM shop_megasuppliers_product ms WHERE ms.product_id=p.id AND ms.supplier_id=".$sid.")";
    $q2="SELECT COUNT(*) FROM shop_product p WHERE p.id IN (SELECT product_id FROM shop_megasuppliers_product WHERE supplier_id=".$sid." AND product_id>0)";
    foreach(array('exists'=>$q1,'in'=>$q2) as $name=>$q){
        $times=array(); $val=0;
        for($i=0;$i<3;$i++){
            $t=microtime(true); $val=(int)$m->query($q)->fetchField(); $times[]=round((microtime(true)-$t)*1000,2);
        }
        echo "filter_".$name."_count=".$val." ms=".implode(',',$times)."\n";
        echo "explain_".$name."=".json_encode($m->query("EXPLAIN ".$q)->fetchAll(),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n";
    }
}
PHP
chown web:web /tmp/ms_perf_probe.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_perf_probe.php' || true
rm -f /tmp/ms_perf_probe.php

echo "--- recent megasuppliers/errors in wa-log ---"
find "$ROOT/wa-log" -type f -mmin -60 -print0 2>/dev/null | while IFS= read -r -d '' f; do
  hits=$(grep -Ein "megasuppliers|Fatal error|Uncaught|Exception|Unknown field|Smarty" "$f" 2>/dev/null | tail -n 80 || true)
  if [ -n "$hits" ]; then
    echo "### $f"
    echo "$hits"
  fi
done

"""
            ok=False
            for user in ("root","web"):
                p=subprocess.run([
                    "ssh","-i",key_path,
                    "-o","BatchMode=yes",
                    "-o","StrictHostKeyChecking=no",
                    "-o","UserKnownHostsFile=/dev/null",
                    "-o","ConnectTimeout=12",
                    f"{user}@{VM_IP}","bash -s"
                ],input=remote_cmd,text=True,capture_output=True,timeout=120)
                add(f"ssh_user={user} exit={p.returncode}")
                if p.stdout:
                    add(p.stdout)
                if p.stderr:
                    add("stderr="+p.stderr[-800:])
                if p.returncode==0:
                    ok=True
                    break
            if not ok:
                raise RuntimeError("No SSH user succeeded")
        finally:
            if key_id:
                try:
                    request_json(
                        f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/",
                        "DELETE",None,headers
                    )
                    add("temp_key_removed=yes")
                except Exception as e:
                    add("WARNING temp_key_removed=no "+repr(e))

    open(REPORT,"w",encoding="utf-8").write("\n".join(lines)+"\n")

if __name__=="__main__":
    try:
        run()
    except Exception as e:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT,"a",encoding="utf-8") as f:
            f.write("ERROR: "+repr(e)+"\n")
        print("ERROR:",repr(e),file=sys.stderr)
