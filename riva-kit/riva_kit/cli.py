import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

from liga_kit.http import SafeSession
from liga_kit.kit_client import KitClient

from .feed import parse_categories
from .sync import SyncRunner


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_report(path, data):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def build_parser():
    parser = argparse.ArgumentParser(description='Riva → Yandex KIT synchronizer')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--skip-items', type=int, default=0)
    parser.add_argument('--max-items', type=int, default=None)
    parser.add_argument('--max-new', type=int, default=None)
    parser.add_argument('--new-only', action='store_true')
    parser.add_argument('--report', default='riva-kit/state/last_sync.json')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = utc_now()
    result = None
    try:
        kit_token = os.environ.get('YANDEX_KIT_TOKEN', '').strip()
        feed_url = os.environ.get('RIVA_FEED_URL', '').strip()
        if not kit_token:
            raise RuntimeError('YANDEX_KIT_TOKEN is not configured')
        if not feed_url:
            raise RuntimeError('RIVA_FEED_URL is not configured')

        # KIT can temporarily throttle large variant-index reads.  Riva has a
        # large catalog, so use a slower client and a longer retry window here
        # without changing the request pace of the other supplier integrations.
        http = SafeSession(max_attempts=10, timeout=(15, 120))
        kit = KitClient(kit_token, http, min_request_interval=1.50, read_request_interval=0.25)
        with tempfile.TemporaryDirectory(prefix='riva-feed-') as td:
            feed_path = os.path.join(td, 'feed.xml')
            http.download_to_file(feed_url, feed_path)
            categories = parse_categories(feed_path)
            if not categories:
                raise RuntimeError('Riva feed has no categories')
            runner = SyncRunner(
                feed_path,
                categories,
                kit,
                http,
                dry_run=args.dry_run,
                skip_items=args.skip_items,
                max_items=args.max_items,
                max_new=args.max_new,
                new_only=args.new_only,
            )
            result = runner.run()
    except Exception as exc:
        result = {
            'status': 'failed',
            'dry_run': args.dry_run,
            'catalog_complete': False,
            'offers_seen': 0,
            'error_count': 1,
            'errors': [{'sku': 'BOOTSTRAP', 'message': str(exc)[:1000]}],
            'warning_count': 0,
            'warnings': [],
        }

    result['started_at'] = started
    result['finished_at'] = utc_now()
    write_report(args.report, result)

    keys = (
        'status',
        'dry_run',
        'skip_items',
        'max_new',
        'new_only',
        'catalog_complete',
        'offers_seen',
        'in_stock_offers',
        'zero_stock_offers',
        'existing_variants_seen',
        'new_products_planned',
        'new_products_created',
        'new_limit_skipped',
        'price_changes',
        'spb_stock_changes',
        'msk_stock_changes',
        'absent_to_zero',
        'error_count',
        'warning_count',
    )
    print(json.dumps(
        {key: result.get(key) for key in keys},
        ensure_ascii=False,
        indent=2,
    ))

    if result.get('status') == 'failed':
        return 1
    if args.max_items is None and args.max_new is None and args.skip_items == 0 and not result.get('catalog_complete'):
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
