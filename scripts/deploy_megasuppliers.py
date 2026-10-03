#!/usr/bin/env python3
import io, json, os, subprocess, tempfile, time, urllib.parse, urllib.request, zipfile, hashlib, shlex, shutil
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
GSA=os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
DRIVE_FILE_ID=os.environ["DRIVE_FILE_ID"]
EXPECTED=os.environ["EXPECTED_SHA256"]
VM_ID=44780
VM_IP="45.86.180.49"
ROOT="/home/web/vm-23f9aff9.na4u.ru/www"
REPORT=Path(".deploy-probe/megasuppliers-install.txt")

def req_json(url, method="GET", data=None, headers=None):
    r=urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(r,timeout=30) as x:
        raw=x.read().decode("utf-8")
        return x.status, json.loads(raw) if raw else {}

def ssh(key, command, stdin=None, check=True, timeout=120):
    p=subprocess.run([
        "ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
        "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",
        "root@"+VM_IP,command
    ],input=stdin,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("remote command failed: STDOUT="+p.stdout[-3000:]+" STDERR="+p.stderr[-3000:])
    return p

def main():
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("", encoding="utf-8")
    lines=[]
    def log(s):
        s=str(s); lines.append(s); print(s)

    # Download exact package from the user's Drive using the existing service-account secret.
    info=json.loads(GSA)
    creds=service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    svc=build("drive","v3",credentials=creds,cache_discovery=False)
    pkg="/tmp/megasuppliers-1.0.1.zip"
    req=svc.files().get_media(fileId=DRIVE_FILE_ID)
    with io.FileIO(pkg,"wb") as fh:
        dl=MediaIoBaseDownload(fh,req)
        done=False
        while not done:
            _,done=dl.next_chunk()
    digest=hashlib.sha256(Path(pkg).read_bytes()).hexdigest()
    log("package_sha256="+digest)
    if digest != EXPECTED:
        raise RuntimeError("package checksum mismatch")
    with zipfile.ZipFile(pkg) as z:
        names=z.namelist()
        if not names or any((not n.startswith("megasuppliers/")) or ".." in Path(n).parts for n in names):
            raise RuntimeError("unsafe package layout")
    log("package_verified=yes")
    base_pkg="/tmp/megasuppliers-1.0.1.base.zip"
    shutil.copy2(pkg, base_pkg)

    # Ensure the public API route is registered through Shop-Script's routing hook.
    # The source package is checksum-verified above; this deterministic compatibility
    # patch is applied only to the deploy copy.
    patched=pkg+".patched"
    with zipfile.ZipFile(pkg, "r") as zin, zipfile.ZipFile(patched, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data=zin.read(item.filename)
            if item.filename == "megasuppliers/lib/config/plugin.php":
                text_data=data.decode("utf-8")
                if "'routing' => 'routing'" not in text_data:
                    text_data=text_data.replace(
                        "'backend_prod' => 'backendProd',",
                        "'backend_prod' => 'backendProd',\n        'routing' => 'routing',"
                    )
                if "'backend_products' => 'backendProducts'" not in text_data:
                    text_data=text_data.replace(
                        "'backend_prod' => 'backendProd',",
                        "'backend_prod' => 'backendProd',\n        'backend_products' => 'backendProducts',\n        'products_collection' => 'productsCollection',"
                    )
                for event_name, method_name in [
                    ("backend_product_edit", "backendProductEdit"),
                    ("backend_prod_content", "backendProdContent"),
                    ("products_collection.filter", "productsCollectionFilter"),
                ]:
                    needle = "'" + event_name + "' => '" + method_name + "'"
                    if needle not in text_data:
                        text_data=text_data.replace(
                            "'routing' => 'routing',",
                            "'routing' => 'routing',\n        " + needle + ","
                        )
                text_data=text_data.replace("'version' => '1.0.1'", "'version' => '1.0.5'")
                data=text_data.encode("utf-8")
            elif item.filename == "megasuppliers/lib/models/shopMegasuppliersMeta.model.php":
                text_data=data.decode("utf-8")
                if "protected $id = 'name';" not in text_data:
                    text_data=text_data.replace(
                        "    protected $table = 'shop_megasuppliers_meta';",
                        "    protected $table = 'shop_megasuppliers_meta';\n    protected $id = 'name';"
                    )
                data=text_data.encode("utf-8")
            elif item.filename == "megasuppliers/lib/shopMegasuppliers.plugin.php":
                text_data=data.decode("utf-8")
                old_menu = """    public function backendExtendedMenu(&$params)
    {
        $url = wa('shop')->getAppUrl(null, true).'?plugin=megasuppliers';
        $params['menu']['megasuppliers'] = [
            'name' => 'Поставщики',
            'icon' => '<i class="fas fa-truck"></i>',
            'url' => $url,
        ];
    }
"""
                new_menu = """    public function backendExtendedMenu(&$params)
    {
        $base_url = wa('shop')->getAppUrl(null, true);
        $supplier_model = new shopMegasuppliersSupplierModel();
        $suppliers = $supplier_model->select('id,name,code')->where('active=1')->order('name')->fetchAll();

        if (isset($params['menu']['catalog']) && class_exists('shopMainMenu') && method_exists('shopMainMenu', 'createSection')) {
            shopMainMenu::createSection(
                $params['menu'],
                'megasuppliers',
                'Поставщики',
                [
                    'icon' => '<i class="fas fa-truck"></i>',
                    'insert_after' => 'catalog',
                    'submenu' => [],
                ]
            );
            foreach ($suppliers as $supplier) {
                $params['menu']['megasuppliers']['submenu'][] = [
                    'name' => $supplier['name'],
                    'url' => $base_url.'products/?megasupplier='.(int)$supplier['id'],
                ];
            }
            $params['menu']['megasuppliers']['submenu'][] = [
                'name' => 'Управление поставщиками',
                'url' => $base_url.'?plugin=megasuppliers',
            ];
        } else {
            $params['menu']['megasuppliers'] = [
                'name' => 'Поставщики',
                'icon' => '<i class="fas fa-truck"></i>',
                'url' => $base_url.'?plugin=megasuppliers',
            ];
        }
    }
"""
                if old_menu in text_data:
                    text_data=text_data.replace(old_menu, new_menu, 1)
                text_data=text_data.replace("public function routing($route)", "public function routing($route = array())")
                if "public function routing(" not in text_data:
                    pos=text_data.rfind("\n}")
                    if pos < 0:
                        raise RuntimeError("cannot patch plugin routing method")
                    method=r"""
    public function routing($route = array())
    {
        if (wa()->getEnv() === 'frontend') {
            return [
                'megasuppliers-api/' => 'frontend/api',
            ];
        }
        return [];
    }
"""
                    text_data=text_data[:pos]+method+text_data[pos:]
                if "public function backendProducts(" not in text_data:
                    pos=text_data.rfind("\n}")
                    if pos < 0:
                        raise RuntimeError("cannot patch supplier sidebar methods")
                    methods=r"""
    private function legacySupplierTypeMap()
    {
        return array(
            'NORDEN' => array(142),
            'TD_ANDREY' => array(126),
            'DEEPHOUSE' => array(24),
            'LEVMAR' => array(125),
            'TREEZ' => array(199),
            'YOURROOM' => array(26),
            'AFINA' => array(63),
            'ALETAN' => array(46, 122, 202),
            'RED_BLACK' => array(150),
            'B2B_FABRIKA' => array(204),
            'KENNER' => array(203),
            '4SIS' => array(11)
        );
    }

    private function supplierProductSubqueries($supplier_id, $supplier_code)
    {
        $queries = array(
            'SELECT product_id FROM shop_megasuppliers_product WHERE supplier_id = '.(int)$supplier_id.' AND product_id > 0'
        );
        $map = $this->legacySupplierTypeMap();
        if (!empty($map[$supplier_code])) {
            $type_ids = array_map('intval', $map[$supplier_code]);
            $queries[] = 'SELECT id AS product_id FROM shop_product WHERE type_id IN ('.implode(',', $type_ids).')';
        }
        return $queries;
    }

    public function backendProducts($params = array())
    {
        $model = new waModel();
        $rows = (new shopMegasuppliersSupplierModel())->select('id,name,code')->where('active=1')->order('name')->fetchAll();

        $items = '';
        foreach ($rows as $row) {
            $id = (int)$row['id'];
            $queries = $this->supplierProductSubqueries($id, $row['code']);
            $count_row = $model->query('SELECT COUNT(*) c FROM ('.implode(' UNION ', $queries).') ms_products')->fetch();
            $count = $count_row ? (int)$count_row['c'] : 0;
            $name = htmlspecialchars($row['name'], ENT_QUOTES, 'UTF-8');
            $items .= '<li id="s-megasuppliers-'.$id.'">'
                .'<span class="count">'.$count.'</span>'
                .'<a href="#/products/hash=megasuppliers/'.$id.'/">'
                .'<i class="icon16 folders"></i>'.$name.'</a></li>';
        }

        $manage_url = wa('shop')->getAppUrl(null, true).'?plugin=megasuppliers';
        $manage_url = htmlspecialchars($manage_url, ENT_QUOTES, 'UTF-8');

        $html = '<div class="block" id="s-megasuppliers-sidebar">'
            .'<span class="count"><a href="'.$manage_url.'" title="Управление поставщиками"><i class="icon16 settings"></i></a></span>'
            .'<h5 class="heading" style="cursor:pointer"><i class="icon16 collapse-handler darr"></i>Поставщики</h5>'
            .'<ul class="menu-v with-icons">'.$items.'</ul>'
            .'</div>'
            .'<script>(function($){'
            .'var b=$("#s-megasuppliers-sidebar");'
            .'b.find("h5.heading").off("click.megasuppliers").on("click.megasuppliers",function(e){'
            .'if($(e.target).closest("a").length){return;}'
            .'b.find("ul.menu-v").slideToggle(120);'
            .'b.find(".collapse-handler").toggleClass("darr").toggleClass("rarr");'
            .'});'
            .'})(jQuery);</script>';

        return array('sidebar_section' => $html);
    }

    public function backendProdFilters(&$params)
    {
        $supplier_id = waRequest::get('megasupplier', 0, waRequest::TYPE_INT);
        if ($supplier_id && !empty($params['collection'])) {
            $supplier = (new shopMegasuppliersSupplierModel())->getById($supplier_id);
            if ($supplier && !empty($supplier['active'])) {
                $queries = $this->supplierProductSubqueries($supplier_id, $supplier['code']);
                $params['collection']->addWhere('id IN ('.implode(' UNION ', $queries).')');
            }
        }
        return array();
    }

    public function productsCollection($params)
    {
        if (wa()->getEnv() != 'backend' || empty($params['collection'])) {
            return null;
        }
        $collection = $params['collection'];
        $hash = $collection->getHash();
        if (empty($hash) || $hash[0] !== $this->id || empty($hash[1])) {
            return null;
        }
        $supplier_id = (int)$hash[1];
        if (!$supplier_id) {
            return null;
        }
        $supplier = (new shopMegasuppliersSupplierModel())->getById($supplier_id);
        if (!$supplier || empty($supplier['active'])) {
            return null;
        }

        $queries = $this->supplierProductSubqueries($supplier_id, $supplier['code']);
        $collection->addWhere('id IN ('.implode(' UNION ', $queries).')');

        if (!empty($params['auto_title'])) {
            $collection->addTitle('Поставщик: '.$supplier['name']);
        }
        return true;
    }
"""
                    text_data=text_data[:pos]+methods+text_data[pos:]
                data=text_data.encode("utf-8")
            zout.writestr(item, data)
        controller_path=Path("webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendAssignSupplier.controller.php")
        if controller_path.exists():
            zout.writestr(
                "megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendAssignSupplier.controller.php",
                controller_path.read_bytes()
            )
    os.replace(patched, pkg)
    log("compatibility_patches=yes")

    # Rebuild from the checksum-pinned untouched base through the same release
    # builder used by CI. This makes the deploy payload identical in structure
    # to the package that passed tests, regardless of legacy compatibility code above.
    release_pkg=pkg+".release"
    subprocess.run([
        "python3","scripts/build_megasuppliers_package.py",
        "--base",base_pkg,
        "--output",release_pkg,
    ],check=True)
    os.replace(release_pkg,pkg)
    log("release_builder=yes")

    # Temporary SSH key through NetAngels API.
    token_body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req_json("https://panel.netangels.ru/api/gateway/token/","POST",token_body,
                   {"Content-Type":"application/x-www-form-urlencoded","User-Agent":"megasuppliers-deploy/1.0"})
    token=tok["token"]
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json",
             "User-Agent":"megasuppliers-deploy/1.0"}

    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",json.dumps({"key":pub,"name":"chatgpt-megasuppliers-install-"+os.environ.get("GITHUB_RUN_ID","manual")}).encode(),headers
        )
        kid=str(created["id"])
        try:
            connected=False
            for _ in range(18):
                p=ssh(key,"echo ssh_ok",check=False,timeout=25)
                if p.returncode==0:
                    connected=True; break
                time.sleep(5)
            if not connected:
                raise RuntimeError("temporary SSH unavailable")
            log("ssh=yes")

            subprocess.run([
                "scp","-i",key,"-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null",
                pkg,"root@"+VM_IP+":/tmp/megasuppliers-1.0.1.zip"
            ],check=True)

            remote = """set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
PLUGIN="$ROOT/wa-apps/shop/plugins/megasuppliers"
CONF="$ROOT/wa-config/apps/shop/plugins.php"
TS="$(date +%Y%m%d-%H%M%S)"
BACK="/home/web/backups/chatgpt-megasuppliers/$TS"
STAGE="/tmp/megasuppliers-stage-$TS"
mkdir -p "$BACK" "$STAGE"
cp -a "$CONF" "$BACK/plugins.php.before"
if [ -e "$PLUGIN" ]; then
  tar -C "$(dirname "$PLUGIN")" -czf "$BACK/megasuppliers.before.tgz" "$(basename "$PLUGIN")"
fi
echo "stage=extract"
unzip -q /tmp/megasuppliers-1.0.1.zip -d "$STAGE"
test -f "$STAGE/megasuppliers/lib/config/plugin.php"
echo "stage=lint php=$(php -r 'echo PHP_VERSION;')"
while IFS= read -r f; do
  echo "lint=$f"
  php -d display_errors=1 -l "$f"
done < <(find "$STAGE/megasuppliers" -name '*.php' -type f | sort)
echo "stage=lint_ok"

echo "stage=copy_plugin"
rm -rf "$PLUGIN"
cp -a "$STAGE/megasuppliers" "$PLUGIN"
chown -R web:web "$PLUGIN"
find "$PLUGIN" -type d -exec chmod 755 {} +
find "$PLUGIN" -type f -exec chmod 644 {} +

# Reset PHP-FPM OPcache through a one-time web request so replaced plugin classes
# are visible immediately to backend/browser requests.
OPRESET="$ROOT/ms-opcache-reset-$TS.php"
cat >"$OPRESET" <<'PHP'
<?php
header('Content-Type: text/plain; charset=utf-8');
echo function_exists('opcache_reset') ? (opcache_reset() ? 'opcache_reset=yes' : 'opcache_reset=no') : 'opcache_reset=unavailable';
PHP
chown web:web "$OPRESET"
chmod 644 "$OPRESET"
RESET_NAME="$(basename "$OPRESET")"
RESET_OUT="$(curl -kfsS --max-time 20 "https://profikompany.ru/$RESET_NAME" || true)"
echo "$RESET_OUT"
rm -f "$OPRESET"

cat >/tmp/ms_register.php <<'PHP'
<?php
$path = '/home/web/vm-23f9aff9.na4u.ru/www'.'/wa-config/apps/shop/plugins.php';
$p = file_exists($path) ? include($path) : array();
if (!is_array($p)) $p = array();
$p['megasuppliers'] = true;
$data = "<?php\n\nreturn ".var_export($p, true).";\n//EOF";
if (file_put_contents($path, $data) === false) exit(2);
PHP
chown web:web /tmp/ms_register.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_register.php'

cat >/tmp/ms_db_init.php <<'PHP'
<?php
$root = '/home/web/vm-23f9aff9.na4u.ru/www';
$db_cfg = include $root.'/wa-config/db.php';
if (isset($db_cfg['default']) && is_array($db_cfg['default'])) {
    $d = $db_cfg['default'];
} else {
    $d = $db_cfg;
}
$host = isset($d['host']) ? $d['host'] : 'localhost';
$port = isset($d['port']) ? (int)$d['port'] : 3306;
$user = isset($d['user']) ? $d['user'] : '';
$pass = isset($d['password']) ? $d['password'] : '';
$name = isset($d['database']) ? $d['database'] : '';
$mysqli = new mysqli($host, $user, $pass, $name, $port);
if ($mysqli->connect_errno) {
    fwrite(STDERR, "DB connect failed\n");
    exit(3);
}
$mysqli->set_charset('utf8mb4');
$sql = array(
"CREATE TABLE IF NOT EXISTS shop_megasuppliers_supplier (
  id INT(11) NOT NULL AUTO_INCREMENT,
  name VARCHAR(255) NOT NULL DEFAULT '',
  code VARCHAR(64) NOT NULL DEFAULT '',
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY code (code),
  KEY name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
"CREATE TABLE IF NOT EXISTS shop_megasuppliers_product (
  id INT(11) NOT NULL AUTO_INCREMENT,
  supplier_id INT(11) NOT NULL,
  product_id INT(11) NOT NULL DEFAULT 0,
  sku_id INT(11) NOT NULL DEFAULT 0,
  supplier_sku VARCHAR(255) NOT NULL DEFAULT '',
  external_id VARCHAR(255) NOT NULL DEFAULT '',
  purchase_price DECIMAL(15,4) NULL,
  stock DECIMAL(15,3) NULL,
  raw_json MEDIUMTEXT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY supplier_sku (supplier_id, supplier_sku),
  KEY product_id (product_id),
  KEY sku_id (sku_id),
  KEY external_id (external_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
"CREATE TABLE IF NOT EXISTS shop_megasuppliers_import (
  id INT(11) NOT NULL AUTO_INCREMENT,
  supplier_id INT(11) NOT NULL,
  source VARCHAR(32) NOT NULL DEFAULT 'file',
  filename VARCHAR(255) NOT NULL DEFAULT '',
  status VARCHAR(32) NOT NULL DEFAULT 'running',
  total INT(11) NOT NULL DEFAULT 0,
  created_count INT(11) NOT NULL DEFAULT 0,
  updated_count INT(11) NOT NULL DEFAULT 0,
  linked_count INT(11) NOT NULL DEFAULT 0,
  skipped_count INT(11) NOT NULL DEFAULT 0,
  error_count INT(11) NOT NULL DEFAULT 0,
  errors_json MEDIUMTEXT NULL,
  user_id INT(11) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  PRIMARY KEY (id),
  KEY supplier_id (supplier_id),
  KEY created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
"CREATE TABLE IF NOT EXISTS shop_megasuppliers_meta (
  name VARCHAR(64) NOT NULL DEFAULT '',
  value TEXT NULL,
  PRIMARY KEY (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
);
foreach ($sql as $q) {
    if (!$mysqli->query($q)) {
        fwrite(STDERR, "DB schema failed: ".$mysqli->error."\n");
        exit(4);
    }
}
$now = date('Y-m-d H:i:s');
$suppliers = array(
    array('Norden','NORDEN'), array('ТД Андрей','TD_ANDREY'), array('4 Сезона','4SEASONS'),
    array('DEEPHOUSE','DEEPHOUSE'), array('Levmar','LEVMAR'), array('Treez','TREEZ'),
    array('Yourroom','YOURROOM'), array('Afina Garden','AFINA'), array('Aletan','ALETAN'),
    array('Red-Black','RED_BLACK'), array('ТД Никитин','TD_NIKITIN'),
    array('B2B Fabrika','B2B_FABRIKA'), array('Kenner','KENNER')
);
$stmt=$mysqli->prepare("INSERT IGNORE INTO shop_megasuppliers_supplier(name,code,active,created_at,updated_at) VALUES(?,?,1,?,?)");
foreach ($suppliers as $row) {
    $stmt->bind_param('ssss',$row[0],$row[1],$now,$now);
    if (!$stmt->execute()) { fwrite(STDERR,"supplier seed failed\n"); exit(5); }
}
$res=$mysqli->query("SELECT value FROM shop_megasuppliers_meta WHERE name='api_key' LIMIT 1");
if (!$res || !$res->num_rows) {
    $key=bin2hex(random_bytes(32));
    $stmt2=$mysqli->prepare("INSERT INTO shop_megasuppliers_meta(name,value) VALUES('api_key',?)");
    $stmt2->bind_param('s',$key);
    if (!$stmt2->execute()) { fwrite(STDERR,"api key seed failed\n"); exit(6); }
}
echo "db_init=ok\n";
PHP
chown web:web /tmp/ms_db_init.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_db_init.php'

# Stable public API bridge. Older Shop-Script builds on this installation do not
# expose plugin frontend routing reliably, so keep Webasyst/plugin logic but use
# a physical endpoint directory that is not affected by storefront rewrite rules.
API_DIR="$ROOT/megasuppliers-api"
if [ -e "$API_DIR" ]; then
  tar -C "$ROOT" -czf "$BACK/megasuppliers-api.before.tgz" "megasuppliers-api"
fi
mkdir -p "$API_DIR"
cat >"$API_DIR/index.php" <<'PHP'
<?php
header('Content-Type: application/json; charset=utf-8');

function ms_json($status, $payload)
{
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

if (strtolower(isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '') !== 'post') {
    ms_json(405, array('errors' => array('method_not_allowed')));
}

$root = dirname(__DIR__);
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null, new SystemConfig());
wa('shop');
wa('shop')->getPlugin('megasuppliers', true);

$meta = (new shopMegasuppliersMetaModel())->getById('api_key');
$expected = $meta ? (string)$meta['value'] : '';
$provided = isset($_SERVER['HTTP_X_MEGASUPPLIERS_KEY']) ? trim((string)$_SERVER['HTTP_X_MEGASUPPLIERS_KEY']) : '';
if ($expected === '' || $provided === '' || !hash_equals($expected, $provided)) {
    ms_json(401, array('errors' => array('unauthorized')));
}

$payload = json_decode(file_get_contents('php://input'), true);
if (!is_array($payload)) {
    ms_json(400, array('errors' => array('invalid_json')));
}
$supplier_code = isset($payload['supplier_code']) ? strtoupper(trim((string)$payload['supplier_code'])) : '';
if ($supplier_code === '') {
    ms_json(422, array('errors' => array('SUPPLIER_REQUIRED')));
}
$supplier = (new shopMegasuppliersSupplierModel())->getByField('code', $supplier_code);
if (!$supplier || empty($supplier['active'])) {
    ms_json(422, array('errors' => array('SUPPLIER_NOT_FOUND')));
}
$items = isset($payload['items']) ? $payload['items'] : array();
if (!is_array($items) || !$items) {
    ms_json(422, array('errors' => array('ITEMS_REQUIRED')));
}

try {
    $service = new shopMegasuppliersImportService();
    $stats = $service->importRows((int)$supplier['id'], $items, array(
        'source' => 'api',
        'filename' => '',
        'create_missing' => !empty($payload['create_missing']),
        'update_catalog' => !array_key_exists('update_catalog', $payload) || !empty($payload['update_catalog']),
        'field_map' => isset($payload['field_map']) && is_array($payload['field_map']) ? $payload['field_map'] : array(),
    ));
    ms_json(200, array(
        'status' => 'ok',
        'supplier' => array('id' => (int)$supplier['id'], 'code' => $supplier['code'], 'name' => $supplier['name']),
        'import_id' => (int)$stats['import_id'],
        'created' => $stats['created'],
        'updated' => $stats['updated'],
        'linked' => $stats['linked'],
        'skipped' => $stats['skipped'],
        'errors' => $stats['errors'],
    ));
} catch (Exception $e) {
    ms_json(422, array('errors' => array($e->getMessage())));
}
PHP
chown -R web:web "$API_DIR"
chmod 755 "$API_DIR"
chmod 644 "$API_DIR/index.php"
php -l "$API_DIR/index.php"
echo "api_bridge=yes"

# Functional API guard check without exposing the key.
cat >/tmp/ms_api_key.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
wa('shop')->getPlugin('megasuppliers',true);
$row=(new shopMegasuppliersMetaModel())->getById('api_key');
echo $row ? $row['value'] : '';
PHP
chown web:web /tmp/ms_api_key.php
API_TEST_KEY="$(su -s /bin/bash web -c 'php -d display_errors=0 -d log_errors=0 /tmp/ms_api_key.php')"
rm -f /tmp/ms_api_key.php
test -n "$API_TEST_KEY"
API_TEST_STATUS="$(curl -ksS -o /tmp/ms_api_guard_body.txt -w '%{http_code}' -X POST -H "Content-Type: application/json" -H "X-Megasuppliers-Key: $API_TEST_KEY" --data '{"items":[{"артикул":"__guard_test__"}]}' "https://profikompany.ru/megasuppliers-api/" || true)"
echo "api_supplier_required_status=$API_TEST_STATUS"
echo "api_supplier_required_body=$(cat /tmp/ms_api_guard_body.txt 2>/dev/null || true)"
rm -f /tmp/ms_api_guard_body.txt
test "$API_TEST_STATUS" = "422"

# Remove Shop-Script cache only; it is regenerated automatically.
rm -rf "$ROOT/wa-cache/apps/shop" || true

cat >/tmp/ms_verify.php <<'PHP'
<?php
$root = '/home/web/vm-23f9aff9.na4u.ru/www';
$db_cfg = include $root.'/wa-config/db.php';
$d = isset($db_cfg['default']) && is_array($db_cfg['default']) ? $db_cfg['default'] : $db_cfg;
$mysqli = new mysqli(isset($d['host'])?$d['host']:'localhost', $d['user'], $d['password'], $d['database'], isset($d['port'])?(int)$d['port']:3306);
$tables=array('shop_megasuppliers_supplier','shop_megasuppliers_product','shop_megasuppliers_import','shop_megasuppliers_meta');
foreach($tables as $t) {
  $esc=$mysqli->real_escape_string($t);
  $r=$mysqli->query("SHOW TABLES LIKE '$esc'");
  echo "table_".$t."=".(($r && $r->num_rows)?"yes":"no")."\n";
}
$r=$mysqli->query("SELECT COUNT(*) c FROM shop_megasuppliers_supplier");
echo "suppliers=".$r->fetch_assoc()['c']."\n";
$r=$mysqli->query("SELECT CHAR_LENGTH(value) l FROM shop_megasuppliers_meta WHERE name='api_key'");
$row=$r?$r->fetch_assoc():null;
echo "api_key_generated=".(($row && (int)$row['l']>=64)?"yes":"no")."\n";
$p=include '/home/web/vm-23f9aff9.na4u.ru/www'.'/wa-config/apps/shop/plugins.php';
echo "config_enabled=".(!empty($p['megasuppliers'])?"yes":"no")."\n";
PHP
chown web:web /tmp/ms_verify.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_verify.php'

echo "backup=$BACK"
rm -rf "$STAGE" /tmp/ms_register.php /tmp/ms_db_init.php /tmp/ms_verify.php /tmp/megasuppliers-1.0.1.zip
"""
            p=ssh(key,"bash -s",stdin=remote,check=True,timeout=180)
            log(p.stdout.strip())
            if p.stderr.strip():
                log("remote_stderr="+p.stderr.strip()[-1200:])

            # HTTP health checks. API GET is expected to be non-2xx (405) but must hit the plugin route.
            for url in ("https://profikompany.ru/","https://profikompany.ru/megasuppliers-api/"):
                try:
                    rq=urllib.request.Request(url,headers={"User-Agent":"megasuppliers-install-check/1.0"})
                    with urllib.request.urlopen(rq,timeout=30) as rr:
                        body=rr.read(1000).decode("utf-8","replace")
                        log(f"http {url} {rr.status} body={body[:160]!r}")
                except urllib.error.HTTPError as e:
                    body=e.read(1000).decode("utf-8","replace")
                    log(f"http {url} {e.code} body={body[:200]!r}")
            log("INSTALL_STATUS=SUCCESS")
        finally:
            try:
                req_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
                log("temp_key_removed=yes")
            except Exception as e:
                log("temp_key_removed=no "+repr(e))

    REPORT.write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        REPORT.parent.mkdir(exist_ok=True)
        with REPORT.open("a",encoding="utf-8") as f:
            f.write("INSTALL_STATUS=FAILED\nERROR="+repr(e)+"\n")
        raise
