#!/usr/bin/env python3
"""Read-only confirm installed rotating photo cron and its first scheduled execution."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"storefront"))
from stage52_disable_callback import execute
REMOTE=r"""set -euo pipefail
ROOT=/home/web/vm-23f9aff9.na4u.ru/www
if crontab -u web -l 2>/dev/null | grep -Fq 'megapolis-universal-yml-photo-sync'; then
 echo PHOTO_STATE_CRON=installed
else
 echo PHOTO_STATE_CRON=missing
fi
LOG="$ROOT/wa-log/shop/plugins/univimport/photo-sync.log"
if [ -f "$LOG" ]; then
 echo PHOTO_STATE_LOG_BYTES="$(stat -c '%s' "$LOG")"
 echo PHOTO_STATE_LOG_UPDATED="$(date -u -d @"$(stat -c %Y "$LOG")" +'%Y-%m-%dT%H:%M:%SZ')"
 tail -n 28 "$LOG" | grep -E '^PHOTO_CRON=|^PHOTO_PROFILE=' | tail -n 15 | while IFS= read -r line; do echo PHOTO_STATE_ENTRY="$line"; done
else
 echo PHOTO_STATE_LOG=absent
fi
if pgrep -f '^php.*universal-photo-cron.php' >/dev/null; then
 echo PHOTO_STATE_PROCESS=running
else
 echo PHOTO_STATE_PROCESS=idle
fi
"""
out=execute(REMOTE)
for line in out.splitlines():
 if line.startswith("PHOTO_STATE_"):print(line,flush=True)
if 'PHOTO_STATE_CRON=installed' not in out:raise RuntimeError('Scheduled job missing')
