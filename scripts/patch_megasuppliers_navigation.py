#!/usr/bin/env python3
import json, os, subprocess, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

API_KEY=os.environ["NETANGELS_API_KEY"].strip()
VM_ID=44780
VM_IP="45.86.180.49"
REPORT=Path(".deploy-probe/megasuppliers-navigation.txt")

def req_json(url, method="GET", data=None, headers=None):
    req=urllib.request.Request(url,data=data,method=method,headers=headers or {})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def ssh(key, command, stdin=None, check=True, timeout=150):
    p=subprocess.run([
        "ssh","-i",key,"-o","BatchMode=yes","-o","StrictHostKeyChecking=no",
        "-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=15",
        "root@"+VM_IP,command
    ],input=stdin,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("remote failed: "+p.stdout[-2000:]+" "+p.stderr[-2000:])
    return p

def main():
    REPORT.parent.mkdir(exist_ok=True)
    token_body=urllib.parse.urlencode({"api_key":API_KEY}).encode()
    _,tok=req_json("https://panel.netangels.ru/api/gateway/token/","POST",token_body,
                   {"Content-Type":"application/x-www-form-urlencoded"})
    headers={"Authorization":"Bearer "+tok["token"],"Content-Type":"application/json"}
    lines=[]
    with tempfile.TemporaryDirectory() as td:
        key=td+"/id_ed25519"
        subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",key],check=True)
        pub=Path(key+".pub").read_text().strip()
        _,created=req_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",json.dumps({"key":pub,"name":"chatgpt-megasuppliers-navigation"}).encode(),headers
        )
        kid=str(created["id"])
        try:
            for _ in range(18):
                p=ssh(key,"echo ok",check=False,timeout=25)
                if p.returncode==0: break
                time.sleep(4)
            else:
                raise RuntimeError("ssh unavailable")

            remote=r'''set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
PLUGIN="$ROOT/wa-apps/shop/plugins/megasuppliers"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACK="$ROOT/wa-data/protected/megasuppliers-backups/$STAMP-navigation"
mkdir -p "$BACK"
cp -a "$PLUGIN/lib/config/plugin.php" "$BACK/plugin.php"
cp -a "$PLUGIN/lib/shopMegasuppliers.plugin.php" "$BACK/shopMegasuppliers.plugin.php"

python3 - "$PLUGIN/lib/config/plugin.php" "$PLUGIN/lib/shopMegasuppliers.plugin.php" <<'PY'
import sys
from pathlib import Path

config=Path(sys.argv[1])
klass=Path(sys.argv[2])

c=config.read_text(encoding='utf-8')
if "'backend_prod_filters' => 'backendProdFilters'" not in c:
    c=c.replace(
        "'backend_products' => 'backendProducts',",
        "'backend_products' => 'backendProducts',\n        'backend_prod_filters' => 'backendProdFilters',"
    )
c=c.replace("'version' => '1.0.3'", "'version' => '1.0.4'")
config.write_text(c,encoding='utf-8')

s=klass.read_text(encoding='utf-8')

start=s.find("    public function backendExtendedMenu(")
end=s.find("\n    public function backendMenu()", start)
if start < 0 or end < 0:
    raise SystemExit("backendExtendedMenu block not found")
new_menu=r'''    public function backendExtendedMenu(&$params)
    {
        $url = wa('shop')->getAppUrl(null, true);
        $supplier_model = new shopMegasuppliersSupplierModel();
        $suppliers = $supplier_model->select('id,name,code')->where('active=1')->order('name')->fetchAll();

        if (class_exists('shopMainMenu') && method_exists('shopMainMenu', 'createSection')) {
            shopMainMenu::createSection(
                $params['menu'],
                'megasuppliers',
                'Поставщики',
                array(
                    'icon' => '<i class="fas fa-truck"></i>',
                    'insert_after' => 'catalog',
                    'submenu' => array()
                )
            );
            foreach ($suppliers as $supplier) {
                $params['menu']['megasuppliers']['submenu'][] = array(
                    'name' => $supplier['name'],
                    'url' => $url.'products/?megasupplier='.(int)$supplier['id']
                );
            }
            $params['menu']['megasuppliers']['submenu'][] = array(
                'name' => 'Управление поставщиками',
                'url' => $url.'?plugin=megasuppliers'
            );
        } else {
            $params['menu']['megasuppliers'] = array(
                'name' => 'Поставщики',
                'icon' => '<i class="fas fa-truck"></i>',
                'url' => $url.'?plugin=megasuppliers'
            );
        }
    }
'''
s=s[:start]+new_menu+s[end:]

# Add 4SIS legacy mapping if not already present
s=s.replace(
    "'KENNER' => array(203)\n        );",
    "'KENNER' => array(203),\n            '4SIS' => array(11)\n        );"
)

# Replace old sidebar method with a classic Shop-Script-compatible block
start=s.find("    public function backendProducts(")
end=s.find("\n    public function productsCollection(", start)
if start < 0 or end < 0:
    raise SystemExit("backendProducts block not found")
new_backend=r'''    public function backendProducts($params = array())
    {
        if (!empty($params)) {
            return array();
        }

        $model = new waModel();
        $rows = (new shopMegasuppliersSupplierModel())
            ->select('id,name,code')
            ->where('active=1')
            ->order('name')
            ->fetchAll();

        $items = '';
        foreach ($rows as $row) {
            $id = (int)$row['id'];
            $queries = $this->supplierProductSubqueries($id, $row['code']);
            $count_row = $model->query(
                'SELECT COUNT(*) c FROM ('.implode(' UNION ', $queries).') ms_products'
            )->fetch();
            $count = $count_row ? (int)$count_row['c'] : 0;
            $name = htmlspecialchars($row['name'], ENT_QUOTES, 'UTF-8');

            $items .= '<li id="s-megasuppliers-'.$id.'">'
                .'<span class="count">'.$count.'</span>'
                .'<a href="#/products/hash=megasuppliers/'.$id.'/">'
                .'<i class="icon16 folders"></i>'
                .'<span class="name">'.$name.'</span></a></li>';
        }

        $html = '<div class="block" id="s-megasuppliers-sidebar">'
            .'<h5 class="heading"><i class="icon16 darr collapse-handler" id="s-megasuppliers-list-handler"></i>Поставщики</h5>'
            .'<div class="s-collection-list" id="s-megasuppliers-list">'
            .'<ul class="menu-v with-icons">'.$items.'</ul>'
            .'</div></div>'
            .'<script>(function($){'
            .'$("#s-megasuppliers-list-handler").off("click.megasuppliers").on("click.megasuppliers",function(){'
            .'$("#s-megasuppliers-list").slideToggle(120);'
            .'$(this).toggleClass("darr").toggleClass("rarr");'
            .'});'
            .'})(jQuery);</script>';

        return array('sidebar_section' => $html);
    }
'''
s=s[:start]+new_backend+s[end:]

# Add new-UI request filtering handler before the final class brace
if "public function backendProdFilters(" not in s:
    pos=s.rfind("\n}")
    method=r'''
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
'''
    s=s[:pos]+method+s[pos:]

klass.write_text(s,encoding='utf-8')
PY

php -l "$PLUGIN/lib/config/plugin.php"
php -l "$PLUGIN/lib/shopMegasuppliers.plugin.php"
chown web:web "$PLUGIN/lib/config/plugin.php" "$PLUGIN/lib/shopMegasuppliers.plugin.php"
rm -rf "$ROOT/wa-cache/apps/shop" || true

cat >/tmp/ms_nav_verify.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$p=wa('shop')->getPlugin('megasuppliers',true);
$r=$p->backendProducts(array());
echo "old_sidebar=".(isset($r['sidebar_section']) && strpos($r['sidebar_section'],'Поставщики')!==false?'yes':'no')."\n";
echo "old_norden=".(strpos($r['sidebar_section'],'Norden')!==false?'yes':'no')."\n";
$m=new shopMegasuppliersSupplierModel();
echo "supplier_4sis=".($m->getByField('code','4SIS')?'yes':'no')."\n";
PHP
chown web:web /tmp/ms_nav_verify.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_nav_verify.php'
rm -f /tmp/ms_nav_verify.php

echo "backup=$BACK"
'''
            p=ssh(key,"bash -s",stdin=remote,check=True,timeout=150)
            lines.append(p.stdout.strip())
            if p.stderr.strip():
                lines.append("stderr="+p.stderr.strip()[-1200:])
        finally:
            req_json(f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{kid}/","DELETE",None,headers)
    REPORT.write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__=="__main__":
    main()
