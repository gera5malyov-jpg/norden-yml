#!/usr/bin/env python3
"""Inspect YML importer structure without outputting source text."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "storefront"))
from stage52_disable_callback import execute
remote = r"""set -e
p=/home/web/vm-23f9aff9.na4u.ru/www/wa-apps/shop/plugins/yml
test -d "$p"
echo YML_AUDIT_FILES=$(find "$p" -type f | wc -l)
echo YML_AUDIT_PHP=$(find "$p" -name '*.php' -type f | wc -l)
echo YML_AUDIT_SIZES=$(du -sh "$p" | awk '{print $1}')
for f in "$p/lib/config/plugin.php" "$p/lib/shopYml.plugin.php"; do
  if [ -f "$f" ]; then echo YML_AUDIT_EXISTS=$(basename "$f"); fi
done
find "$p/lib" -maxdepth 3 -type d | sed "s|$p/||" | head -30 | while IFS= read -r f; do echo YML_AUDIT_DIR="$f"; done
"""
out = execute(remote)
for line in out.splitlines():
    if line.startswith("YML_AUDIT_"):
        print(line)
