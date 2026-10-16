#!/usr/bin/env python3
"""Install guarded SKU code completion after a no-write check."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
CLI="$ROOT/wa-apps/shop/plugins/univimport/lib/cli/autofill-sku-codes-289.php"
test -f "$CLI"
PHP="$(command -v php)"
"$PHP" -l "$CLI" | grep -Fq 'No syntax errors'
TMP="$(mktemp /tmp/sku-289-dry-XXXXXX)"
trap 'rm -f "$TMP"' EXIT
su -s /bin/bash web -c "'$PHP' -d memory_limit=128M '$CLI' --dry-run" >"$TMP"
grep -Fq '"status":"dry-run"' "$TMP"
grep -Fq '"profile_id":289' "$TMP"
echo SKU289_DRY_VERIFIED=yes
sed -n '/^SKU_AUTOFILL=/p' "$TMP"
su -s /bin/bash web -c "'$PHP' -d memory_limit=128M '$CLI' --apply" >"$TMP"
grep -Fq '"status":"applied"' "$TMP"
grep -Fq '"missing_after":0' "$TMP"
grep -Fq '"backup_saved":true' "$TMP"
echo SKU289_APPLY_VERIFIED=yes
sed -n '/^SKU_AUTOFILL=/p' "$TMP"
LOGDIR="$ROOT/wa-log/shop/plugins/univimport"
mkdir -p "$LOGDIR"
chown web:web "$LOGDIR"
OLD="$(mktemp /tmp/sku289-cron-old-XXXXXX)"
NEW="$(mktemp /tmp/sku289-cron-new-XXXXXX)"
trap 'rm -f "$TMP" "$OLD" "$NEW"' EXIT
crontab -u web -l >"$OLD" 2>/dev/null || :
mkdir -p /home/web/backups/univimport
SAVE="/home/web/backups/univimport/sku289-cron-before-$(date -u +%Y%m%dT%H%M%SZ)"
cp "$OLD" "$SAVE"
chmod 600 "$SAVE"
TAG='# megapolis-yml-sku289-autofill'
grep -Fv "$TAG" "$OLD" >"$NEW" || :
printf '%s\n' "*/5 * * * * $PHP -d memory_limit=128M $CLI --apply >> $LOGDIR/sku289-autofill.log 2>&1 $TAG" >>"$NEW"
crontab -u web "$NEW"
test "$(crontab -u web -l | grep -F -c "$TAG")" -eq 1
echo SKU289_CRON_INSTALLED=every_5_minutes
# Final no-write check after installation.
su -s /bin/bash web -c "'$PHP' -d memory_limit=128M '$CLI' --dry-run" >"$TMP"
grep -Fq '"missing_before":0' "$TMP"
echo SKU289_FINAL_EMPTY_CODE_COUNT=0
echo SKU289_ROLLBACK_SNAPSHOTS=kept
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith(("SKU289_","SKU_AUTOFILL=")):print(line,flush=True)
if 'SKU289_FINAL_EMPTY_CODE_COUNT=0' not in out or 'SKU289_CRON_INSTALLED=every_5_minutes' not in out:
    raise RuntimeError('SKU autofill installation not fully verified')
