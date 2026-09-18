import argparse
import json
import sys
import tempfile

from .config import Settings
from .feed import parse_feed
from .http import SafeSession
from .kit_client import KitClient
from .report import utc_now, write_report
from .sync import SyncRunner


def build_parser():
    parser = argparse.ArgumentParser(
        description='Liga Divanov → Yandex KIT synchronizer'
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--max-items', type=int, default=None)
    parser.add_argument(
        '--report',
        default='liga-kit/state/last_sync.json',
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = utc_now()
    try:
        settings = Settings.from_env()
        http = SafeSession()
        kit = KitClient(settings.kit_token, http, settings.kit_base_url)
        with tempfile.TemporaryDirectory(prefix='liga-feed-') as td:
            feed_path = td + '/feed.xml'
            http.download_to_file(settings.feed_url, feed_path)
            snapshot = parse_feed(feed_path)
            runner = SyncRunner(
                snapshot,
                kit,
                http,
                dry_run=args.dry_run,
                max_items=args.max_items,
            )
            result = runner.run()
    except Exception as exc:
        result = {
            'status':'failed',
            'dry_run':args.dry_run,
            'catalog_complete':False,
            'offers_seen':0,
            'error_count':1,
            'errors':[{'sku':'BOOTSTRAP','message':str(exc)[:500]}],
            'warning_count':0,
            'warnings':[],
        }

    result['started_at'] = started
    result['finished_at'] = utc_now()
    write_report(args.report, result)

    keys = (
        'status',
        'dry_run',
        'catalog_complete',
        'offers_seen',
        'new_products_created',
        'price_changes',
        'spb_stock_changes',
        'msk_stock_changes',
        'unavailable_to_zero',
        'absent_to_zero',
        'error_count',
        'warning_count',
    )
    print(json.dumps(
        {key:result.get(key) for key in keys},
        ensure_ascii=False,
        indent=2,
    ))

    if result.get('status') == 'failed':
        return 1
    if (
        args.max_items is None
        and not args.dry_run
        and not result.get('catalog_complete')
    ):
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
