from pathlib import Path

p=Path("scripts/deploy_megasuppliers.py")
s=p.read_text(encoding="utf-8")

needle='                data=text_data.encode("utf-8")\n            zout.writestr(item, data)'
extra='''                for snippet_path, method_marker in [
                    ("webasyst/megasuppliers/patches/plugin_helpers.txt", "private function getProductSupplierId("),
                    ("webasyst/megasuppliers/patches/plugin_ui_methods.txt", "public function backendProductEdit("),
                    ("webasyst/megasuppliers/patches/plugin_filter_method.txt", "public function productsCollectionFilter("),
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
