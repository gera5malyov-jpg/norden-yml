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
