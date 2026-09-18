import json
import os
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def write_report(path, report):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write('\n')
    os.replace(temp, path)
