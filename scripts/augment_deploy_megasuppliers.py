from pathlib import Path

p=Path("scripts/deploy_megasuppliers.py")
s=p.read_text(encoding="utf-8")

needle='                data=text_data.encode("utf-8")\n            zout.writestr(item, data)'
extra='''                for snippet_path, method_marker in [
                    ("webasyst/megasuppliers/patches/plugin_helpers.txt", "private function getProductSupplierId("),
                    ("webasyst/megasuppliers/patches/plugin_ui_methods.txt", "public function backendProductEdit("),
                    ("webasyst/megasuppliers/patches/plugin_filter_method.txt", "public function productsCollectionFilter("),
                    ("webasyst/megasuppliers/patches/plugin_list_ui_method.txt", "public function backendProdList("),
                ]:
                    if method_marker not in text_data:
                        snippet=Path(snippet_path).read_text(encoding="utf-8")
                        pos=text_data.rfind("\\n}")
                        if pos < 0:
                            raise RuntimeError("cannot append megasuppliers plugin snippet")
                        text_data=text_data[:pos]+"\\n"+snippet+text_data[pos:]
'''

start=s.index('            elif item.filename == "megasuppliers/lib/shopMegasuppliers.plugin.php":')
end=s.index(needle,start)
if extra.strip() not in s:
    s=s[:end]+extra+s[end:]

p.write_text(s,encoding="utf-8")


s=p.read_text(encoding="utf-8")
s=s.replace(
    'rm -rf "$ROOT/wa-cache/apps/shop" || true',
    'rm -rf "$ROOT/wa-cache/apps/shop" || true\nrm -rf "$ROOT/wa-cache/apps/system/waEvent/cache" || true'
)
p.write_text(s,encoding="utf-8")

s=p.read_text(encoding="utf-8")
cache_line='rm -rf "$ROOT/wa-cache/apps/system/waEvent/cache" || true'
cache_block=cache_line + """\ncat >/tmp/ms_event_clear.php <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
waEvent::clearCache();
waEvent::reset();
wa('shop');
$e=wa('shop')->event('backend_products');
echo "event_cache_clear=ok\\n";
echo "megasuppliers_event=".(isset($e['megasuppliers-plugin'])?'yes':'no')."\\n";
PHP
chown web:web /tmp/ms_event_clear.php
su -s /bin/bash web -c 'php -d display_errors=1 -d log_errors=0 /tmp/ms_event_clear.php'
rm -f /tmp/ms_event_clear.php"""
if "ms_event_clear.php" not in s:
    s=s.replace(cache_line, cache_block)
p.write_text(s,encoding="utf-8")


s=p.read_text(encoding="utf-8")
s=s.replace(
    '                    ("products_collection.filter", "productsCollectionFilter"),\n                ]:',
    '                    ("products_collection.filter", "productsCollectionFilter"),\n                    ("backend_prod_list", "backendProdList"),\n                ]:'
)
s=s.replace(
    'text_data=text_data.replace("\'version\' => \'1.0.1\'", "\'version\' => \'1.0.5\'")',
    'text_data=text_data.replace("\'version\' => \'1.0.1\'", "\'version\' => \'1.0.6\'")'
)
s=s.replace(
    '            .\'var b=$("#s-megasuppliers-sidebar");\'\n            .\'b.find("h5.heading")',
    '            .\'var b=$("#s-megasuppliers-sidebar");\'\n            .\'var sets=$("#s-set-list-block");if(sets.length){b.insertAfter(sets);}\'\n            .\'b.find("h5.heading")'
)
p.write_text(s,encoding="utf-8")


s=p.read_text(encoding="utf-8")
s=s.replace(
    "$params['collection']->addWhere('id IN ('.implode(' UNION ', $queries).')');",
    "$params['collection']->addWhere('p.id IN ('.implode(' UNION ', $queries).')');"
)
s=s.replace(
    'text_data=text_data.replace("\'version\' => \'1.0.1\'", "\'version\' => \'1.0.6\'")',
    'text_data=text_data.replace("\'version\' => \'1.0.1\'", "\'version\' => \'1.0.7\'")'
)
p.write_text(s,encoding="utf-8")


s=p.read_text(encoding="utf-8")
s=s.replace(
    """            $queries = $this->supplierProductSubqueries($id, $row['code']);
            $count_row = $model->query('SELECT COUNT(*) c FROM ('.implode(' UNION ', $queries).') ms_products')->fetch();
            $count = $count_row ? (int)$count_row['c'] : 0;""",
    """            $count = (int)$model->query(
                'SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product '
                .'WHERE supplier_id='.(int)$id.' AND product_id>0'
            )->fetchField();"""
)
s=s.replace(
    """        $queries = $this->supplierProductSubqueries($supplier_id, $supplier['code']);
        $collection->addWhere('id IN ('.implode(' UNION ', $queries).')');""",
    """        $collection->addWhere(
            'EXISTS (SELECT 1 FROM shop_megasuppliers_product ms '
            .'WHERE ms.product_id=p.id AND ms.supplier_id='.(int)$supplier_id.')'
        );"""
)
s=s.replace(
    """                $queries = $this->supplierProductSubqueries($supplier_id, $supplier['code']);
                $params['collection']->addWhere('p.id IN ('.implode(' UNION ', $queries).')');""",
    """                $params['collection']->addWhere(
                    'EXISTS (SELECT 1 FROM shop_megasuppliers_product ms '
                    .'WHERE ms.product_id=p.id AND ms.supplier_id='.(int)$supplier_id.')'
                );"""
)
s=s.replace(
    """foreach ($sql as $q) {
    if (!$mysqli->query($q)) {
        fwrite(STDERR, "DB schema failed: ".$mysqli->error+"\\n");
        exit(4);
    }
}
$now = date('Y-m-d H:i:s');""".replace("+","."), 
    """foreach ($sql as $q) {
    if (!$mysqli->query($q)) {
        fwrite(STDERR, "DB schema failed: ".$mysqli->error."\\n");
        exit(4);
    }
}
$idx=$mysqli->query("SHOW INDEX FROM shop_megasuppliers_product WHERE Key_name='supplier_product'");
if (!$idx || !$idx->num_rows) {
    if (!$mysqli->query("ALTER TABLE shop_megasuppliers_product ADD KEY supplier_product (supplier_id, product_id)")) {
        fwrite(STDERR, "DB index failed: ".$mysqli->error."\\n");
        exit(4);
    }
}
$now = date('Y-m-d H:i:s');"""
)
s=s.replace(
    'text_data=text_data.replace("\'version\' => \'1.0.1\'", "\'version\' => \'1.0.7\'")',
    'text_data=text_data.replace("\'version\' => \'1.0.1\'", "\'version\' => \'1.0.8\'")'
)
p.write_text(s,encoding="utf-8")


# Remove eager supplier counters from the legacy sidebar too. The supplier list
# is navigation; counts are not needed for filtering and cause N aggregate queries.
s=p.read_text(encoding="utf-8")
s=s.replace(
    """    public function backendProducts($params = array())
    {
        $model = new waModel();
        $rows = (new shopMegasuppliersSupplierModel())->select('id,name,code')->where('active=1')->order('name')->fetchAll();""",
    """    public function backendProducts($params = array())
    {
        $rows = (new shopMegasuppliersSupplierModel())->select('id,name,code')->where('active=1')->order('name')->fetchAll();"""
)
s=s.replace(
    """            $count = (int)$model->query(
                'SELECT COUNT(DISTINCT product_id) FROM shop_megasuppliers_product '
                .'WHERE supplier_id='.(int)$id.' AND product_id>0'
            )->fetchField();
""",
    ""
)
s=s.replace(
    """                .'<span class="count">'.$count.'</span>'
                .'<a href="#/products/hash=megasuppliers/'.$id.'/">'""",
    """                .'<a href="#/products/hash=megasuppliers/'.$id.'/">'"""
)
p.write_text(s,encoding="utf-8")


# Package both lightweight backend controllers.
s=p.read_text(encoding="utf-8")
old='''        controller_path=Path("webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendAssignSupplier.controller.php")
        if controller_path.exists():
            zout.writestr(
                "megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendAssignSupplier.controller.php",
                controller_path.read_bytes()
            )
'''
new='''        for controller_path in [
            Path("webasyst/megasuppliers/lib/actions/backend/shopMegasuppliersPluginBackendAssignSupplier.controller.php"),
        ]:
            if controller_path.exists():
                zout.writestr(
                    "megasuppliers/lib/actions/backend/" + controller_path.name,
                    controller_path.read_bytes()
                )
'''
if old in s:
    s=s.replace(old,new,1)
p.write_text(s,encoding="utf-8")
