#!/usr/bin/env python3
import argparse
import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path

EXPECTED_BASE_SHA256 = "5538d704c8f40a5e3b3743a3af507f3b03e14356be789dcf2d8ef427245afa42"
VERSION = "1.1.0"

HANDLERS = [
    ("routing", "routing"),
    ("backend_products", "backendProducts"),
    ("backend_product_edit", "backendProductEdit"),
    ("backend_prod_content", "backendProdContent"),
    ("products_collection.filter", "productsCollectionFilter"),
    ("backend_prod_list", "backendProdList"),
]
SNIPPETS = [
    ("patches/plugin_helpers.txt", "private function getProductSupplierId("),
    ("patches/plugin_ui_methods.txt", "public function backendProductEdit("),
    ("patches/plugin_filter_method.txt", "public function productsCollectionFilter("),
    ("patches/plugin_list_ui_method.txt", "public function backendProdList("),
    ("patches/plugin_thumb_ui.txt", "private function thumbnailEndpointUrl("),
    ("patches/import_panel_method.txt", "public function backendProducts("),
]


def insert_before_class_end(text, snippet):
    pos = text.rfind("\n}")
    if pos < 0:
        raise RuntimeError("plugin class closing brace not found")
    return text[:pos] + "\n" + snippet.rstrip() + "\n" + text[pos:]


def build(base_zip, repo_root, output, expected_sha=EXPECTED_BASE_SHA256):
    base_zip = Path(base_zip)
    repo_root = Path(repo_root)
    output = Path(output)
    digest = hashlib.sha256(base_zip.read_bytes()).hexdigest()
    if expected_sha and digest != expected_sha:
        raise RuntimeError("base package checksum mismatch: %s" % digest)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        with zipfile.ZipFile(base_zip) as z:
            names = z.namelist()
            if not names or any((not n.startswith("megasuppliers/")) or ".." in Path(n).parts for n in names):
                raise RuntimeError("unsafe base package layout")
            z.extractall(td)
        plugin = td / "megasuppliers"

        overlay = repo_root / "webasyst/megasuppliers/lib"
        if overlay.exists():
            for src in overlay.rglob("*"):
                if src.is_file():
                    dst = plugin / "lib" / src.relative_to(overlay)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

        cfg = plugin / "lib/config/plugin.php"
        s = cfg.read_text(encoding="utf-8")
        import re
        s = re.sub(r"'version'\s*=>\s*'[^']+'", "'version' => '%s'" % VERSION, s, count=1)
        for event, method in HANDLERS:
            needle = "'%s' => '%s'" % (event, method)
            if needle not in s:
                s = s.replace("'backend_prod' => 'backendProd',", "'backend_prod' => 'backendProd',\n        %s," % needle)
        cfg.write_text(s, encoding="utf-8")

        routing = plugin / "lib/config/routing.php"
        routing.write_text(
            "<?php\nreturn array(\n"
            "    'megasuppliers-api/' => 'frontend/api',\n"
            "    'megasuppliers-callback/' => 'frontend/importCallback',\n"
            "    'megasuppliers-bridge/' => 'frontend/bridge',\n"
            ");\n",
            encoding="utf-8",
        )

        cls = plugin / "lib/shopMegasuppliers.plugin.php"
        text = cls.read_text(encoding="utf-8")
        bootstrap_marker = "MEGASUPPLIERS_BACKEND_CONTROLLER_BOOTSTRAP"
        if bootstrap_marker not in text:
            bootstrap = """// MEGASUPPLIERS_BACKEND_CONTROLLER_BOOTSTRAP
require_once dirname(__FILE__).'/actions/backend/shopMegasuppliersPluginBackendImportConfig.controller.php';
require_once dirname(__FILE__).'/actions/backend/shopMegasuppliersPluginBackendImportRun.controller.php';
require_once dirname(__FILE__).'/actions/backend/shopMegasuppliersPluginBackendImportStatus.controller.php';
"""
            text = text.replace("<?php", "<?php\n" + bootstrap, 1)
        text = text.replace("public function routing($route)", "public function routing($route = array())")
        if "public function routing(" not in text:
            text = insert_before_class_end(
                text,
                """    public function routing($route = array())
    {
        if (wa()->getEnv() === 'frontend') {
            return array(
                'megasuppliers-api/' => 'frontend/api',
                'megasuppliers-callback/' => 'frontend/importCallback',
                'megasuppliers-bridge/' => 'frontend/bridge',
            );
        }
        if (wa()->getEnv() === 'backend') {
            return array(
                'megasuppliers/import-config/' => 'backend/importConfig',
                'megasuppliers/import-run/' => 'backend/importRun',
                'megasuppliers/import-status/' => 'backend/importStatus',
            );
        }
        return array();
    }
""",
            )
        for rel, marker in SNIPPETS:
            p = repo_root / "webasyst/megasuppliers" / rel
            if not p.exists():
                raise RuntimeError("missing package snippet: %s" % p)
            if marker not in text:
                text = insert_before_class_end(text, p.read_text(encoding="utf-8"))
        cls.write_text(text, encoding="utf-8")

        template = plugin / "templates/actions/backend/Backend.html"
        panel = repo_root / "webasyst/megasuppliers/patches/autoimport_panel.html"
        if not panel.exists():
            raise RuntimeError("missing autoimport panel")
        t = template.read_text(encoding="utf-8")
        marker = "    <hr>\n    <h2>Справочник поставщиков</h2>"
        if 'id="s-megasuppliers-autoimport"' not in t:
            if marker not in t:
                raise RuntimeError("Backend.html insertion marker not found")
            t = t.replace(marker, panel.read_text(encoding="utf-8").rstrip() + "\n\n" + marker, 1)
        legacy_create = '<label><input type="checkbox" name="create_missing" value="1"> создавать новые товары, если артикул не найден</label>'
        safe_create = '<label><input type="checkbox" disabled> создание новых через старый импорт отключено — используйте автоматический профиль</label>'
        t = t.replace(legacy_create, safe_create)
        template.write_text(t, encoding="utf-8")

        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(plugin.rglob("*")):
                if path.is_file():
                    z.write(path, "megasuppliers/" + str(path.relative_to(plugin)))

    return output


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--output", default="dist/megasuppliers-1.1.0.zip")
    ap.add_argument("--expected-sha", default=EXPECTED_BASE_SHA256)
    args = ap.parse_args()
    out = build(args.base, args.repo_root, args.output, args.expected_sha)
    print(out)


if __name__ == "__main__":
    main()
