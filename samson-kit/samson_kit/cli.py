import argparse, json, sys
from .config import Settings
from .http import SafeSession
from .samson_client import SamsonClient
from .kit_client import KitClient
from .sync import SyncRunner
from .report import utc_now, write_report

def build_parser():
    p=argparse.ArgumentParser(description='Samsonopt → Yandex KIT synchronizer'); p.add_argument('--dry-run',action='store_true'); p.add_argument('--max-items',type=int,default=None); p.add_argument('--report',default='samson-kit/state/last_sync.json'); return p

def main(argv=None):
    args=build_parser().parse_args(argv); settings=Settings.from_env(); http=SafeSession(); samson=SamsonClient(settings.samson_api_key,http,settings.samson_base_url); kit=KitClient(settings.kit_token,http,settings.kit_base_url); started=utc_now(); runner=SyncRunner(samson,kit,http,warehouse_name=settings.target_warehouse,dry_run=args.dry_run,max_items=args.max_items)
    try: report=runner.run()
    except Exception as exc: report={'status':'failed','dry_run':args.dry_run,'catalog_complete':False,'error_count':1,'errors':[{'sku':'BOOTSTRAP','message':str(exc)[:500]}]}
    report['started_at']=started; report['finished_at']=utc_now(); write_report(args.report,report); print(json.dumps({k:report.get(k) for k in ('status','dry_run','catalog_complete','samson_products_seen','new_products_created','price_changes','stock_changes','active_zero_to_100','withdrawn_to_zero','absent_to_zero','error_count','warning_count')},ensure_ascii=False,indent=2))
    if report.get('status')=='failed': return 1
    if args.max_items is None and not args.dry_run and not report.get('catalog_complete'): return 2
    return 0

if __name__=='__main__': sys.exit(main())
