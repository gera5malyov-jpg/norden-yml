#!/usr/bin/env python3
"""Install only our new standalone Shop-Script plugin. No product imports."""
import base64
import hashlib
import io
import json
import pathlib
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute

PLUGIN = pathlib.Path("webasyst/univimport")
assert (PLUGIN / "lib/config/plugin.php").is_file()
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(PLUGIN.rglob("*")):
        if f.is_file() and not f.relative_to(PLUGIN).parts[0] in ("tests",):
            z.write(f, "univimport/" + f.relative_to(PLUGIN).as_posix())
payload = buf.getvalue()
digest = hashlib.sha256(payload).hexdigest()
encoded = base64.b64encode(payload).decode("ascii")
print("UNIVIMPORT_PACKAGE=" + json.dumps({"bytes": len(payload), "sha256": digest}), flush=True)

remote = r"""set -eEuo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
PLUGINS="$ROOT/wa-apps/shop/plugins"
TARGET="$PLUGINS/univimport"
YML="$PLUGINS/yml"
CFG="$ROOT/wa-config/apps/shop/plugins.php"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACK="/home/web/backups/univimport/$STAMP"
STAGE="$(mktemp -d /tmp/univimport-XXXXXX)"
mkdir -p "$BACK"
test -f "$CFG"
test -d "$PLUGINS"
cp -p "$CFG" "$BACK/plugins.php"
OLD=0
if [ -e "$TARGET" ]; then
  OLD=1
  tar -czf "$BACK/univimport-before.tgz" -C "$PLUGINS" univimport
fi
yml_hash() {
  if [ -d "$YML" ]; then
    find "$YML" -type f -print0 | sort -z | xargs -0 -r sha256sum | sha256sum | awk '{print $1}'
  else
    echo 'not_installed'
  fi
}
YML_BEFORE="$(yml_hash)"
ROLLBACK_NEEDED=0
rollback() {
  rc=$?
  trap - ERR
  if [ "$ROLLBACK_NEEDED" = 1 ]; then
    cp -p "$BACK/plugins.php" "$CFG"
    rm -rf "$TARGET"
    if [ "$OLD" = 1 ]; then tar -xzf "$BACK/univimport-before.tgz" -C "$PLUGINS"; fi
    echo "UNIVIMPORT_ROLLBACK=restored"
  fi
  rm -rf "$STAGE"
  exit "$rc"
}
trap rollback ERR
base64 -d >"$STAGE/bundle.zip" <<'PAYLOAD_B64'
PAYLOAD_HERE
PAYLOAD_B64
test "$(sha256sum "$STAGE/bundle.zip" | cut -d' ' -f1)" = 'EXPECTED_SHA'
unzip -q "$STAGE/bundle.zip" -d "$STAGE/files"
test -f "$STAGE/files/univimport/lib/config/plugin.php"
if [ "$(php -r 'echo PHP_MAJOR_VERSION;')" -lt 7 ]; then exit 9; fi
find "$STAGE/files/univimport" -type f -name '*.php' -print0 | xargs -0 -n1 php -l >"$BACK/php-lint.txt"
echo "UNIVIMPORT_PRECHECK=php_lint_ok"
ROLLBACK_NEEDED=1
if [ "$OLD" = 1 ]; then mv "$TARGET" "$BACK/univimport-old"; fi
cp -a "$STAGE/files/univimport" "$TARGET"
chown -R web:web "$TARGET"
cat >"$STAGE/register.php" <<'REGISTER_PHP'
<?php
$conf = '/home/web/vm-23f9aff9.na4u.ru/www/wa-config/apps/shop/plugins.php';
$plugins = include $conf;
if (!is_array($plugins)) { throw new RuntimeException('Shop-Script plugin registry not an array'); }
$plugins['univimport'] = true;
$tmp = $conf.'.univimport-new';
if (file_put_contents($tmp, "<?php\nreturn ".var_export($plugins, true).";\n") === false) {
    throw new RuntimeException('Cannot write Shop-Script plugin registry');
}
if (!rename($tmp, $conf)) { throw new RuntimeException('Cannot replace registry'); }
REGISTER_PHP
chown web:web "$STAGE/register.php"
su -s /bin/bash web -c "php -d display_errors=1 '$STAGE/register.php'"
php -r '
$root="/home/web/vm-23f9aff9.na4u.ru/www";
$plugins=include $root."/wa-config/apps/shop/plugins.php";
if (empty($plugins["univimport"])) exit(2);
if (!is_file($root."/wa-apps/shop/plugins/univimport/lib/config/plugin.php")) exit(3);
$config=include $root."/wa-apps/shop/plugins/univimport/lib/config/plugin.php";
if ($config["name"] !== "Импорт универсальный") exit(4);
echo "UNIVIMPORT_REGISTRY=enabled version=".$config["version"]."\n";
'
test "$(yml_hash)" = "$YML_BEFORE"
echo "UNIVIMPORT_YML_PRO=unchanged"
HTTP="$(curl -ksSL --max-time 30 -o /dev/null -w '%{http_code}' https://profikompany.ru/)"
test "$HTTP" = 200
echo "UNIVIMPORT_STOREFRONT=http_$HTTP"
ROLLBACK_NEEDED=0
trap - ERR
rm -rf "$STAGE"
echo "UNIVIMPORT_INSTALL=success"
echo "UNIVIMPORT_BACKUP=$BACK"
"""
remote = remote.replace("PAYLOAD_HERE", encoded).replace("EXPECTED_SHA", digest)
out = execute(remote)
if "UNIVIMPORT_INSTALL=success" not in out or "UNIVIMPORT_YML_PRO=unchanged" not in out:
    raise RuntimeError("Install did not report verifiable success")
