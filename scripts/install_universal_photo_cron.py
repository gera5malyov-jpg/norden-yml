#!/usr/bin/env python3
"""Deploy exactly one user-level cron line for all YML PRO photo profiles.

Uses existing temporary NetAngels SSH access and stores a rollback of crontab.
The original YML PRO configuration, media library and data are not touched.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute

REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
CLI="$ROOT/wa-apps/shop/plugins/univimport/lib/cli/universal-photo-cron.php"
test -f "$CLI"
php -l "$CLI" >/dev/null
command -v crontab >/dev/null
PHP_BIN="$(command -v php)"
LOGDIR="$ROOT/wa-log/shop/plugins/univimport"
mkdir -p "$LOGDIR"
chown web:web "$LOGDIR"
OLD="$(mktemp /tmp/yml-crontab-old-XXXXXX)"
trap 'rm -f "$OLD"' EXIT
crontab -u web -l >"$OLD" 2>/dev/null || : 
BACK="/home/web/backups/univimport/universal-cron-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACK"
cp "$OLD" "$BACK/web-crontab-before.txt"
chmod 600 "$BACK/web-crontab-before.txt"
TAG='# megapolis-universal-yml-photo-sync'
LINE="17 * * * * $PHP_BIN -d memory_limit=768M $CLI --apply >> $LOGDIR/photo-sync.log 2>&1 $TAG"
NEW="$(mktemp /tmp/yml-crontab-new-XXXXXX)"
trap 'rm -f "$OLD" "$NEW"' EXIT
grep -v -F "$TAG" "$OLD" >"$NEW" || :
printf '%s\n' "$LINE" >>"$NEW"
crontab -u web "$NEW"
crontab -u web -l | grep -Fq "$TAG"
test "$(crontab -u web -l | grep -F -c "$TAG")" -eq 1
echo PHOTOCRON_INSTALLED=one_hourly_rotating_job
echo PHOTOCRON_INTERVAL=each_profile_every_6_hours
echo PHOTOCRON_BACKUP=stored
echo PHOTOCRON_PHP="$(basename "$PHP_BIN")"
# Validate actual PHP/bootstrap with an existing profile without any product writes.
F="$(mktemp /tmp/yml-cron-preview-XXXXXX.log)"
trap 'rm -f "$OLD" "$NEW" "$F"' EXIT
su -s /bin/bash web -c "$PHP_BIN -d memory_limit=768M '$CLI' --preview --profile=20" >"$F" 2>&1
grep -Fq '"status":"finished"' "$F"
grep -Fq '"updated":0' "$F"
echo PHOTOCRON_PREVIEW=deephouse_readonly_passed
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet cron; then echo PHOTOCRON_DAEMON=active
  else echo PHOTOCRON_DAEMON=not_confirmed
  fi
fi
"""
out=execute(REMOTE)
for line in out.splitlines():
    if line.startswith("PHOTOCRON_"):print(line,flush=True)
if "PHOTOCRON_INSTALLED=one_hourly_rotating_job" not in out or "PHOTOCRON_PREVIEW=deephouse_readonly_passed" not in out:
    raise RuntimeError("Cron deployment or dry-run could not be verified")
