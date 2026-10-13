"""Guarded final presentation polish; native checkout controls remain byte-identical."""
import hashlib,json,os,sys,uuid
from pathlib import Path
from stage52_disable_callback import execute
from stage55_deploy_content import PHP,DELIVERY,REMOTE_APPLY,remote_stage,rollback_remote
from stage56_delivery import patch_order
BASE=Path(__file__).parent
SHOP='wa-data/public/shop/themes/pureMegapolis42/'
OUT=Path('storefront-stage57-evidence');OUT.mkdir(exist_ok=True)
def sha(b):return hashlib.sha256(b).hexdigest()
REMOTE_PREPARE=r"""
payload=json.loads(base64.b64decode('PAYLOAD_B64').decode('utf-8'))
path=root/payload['path'];folder=pathlib.Path(payload['folder'])
if digest(path.read_bytes())!=payload['expected_sha']:raise RuntimeError('Live checkout changed; no mutations')
prepared=db_run('prepare',payload)
before=path.read_bytes();after=payload['new_source'].encode('utf-8')
if digest(before)!=payload['expected_sha']:raise RuntimeError('Concurrent checkout edit before backup')
folder.mkdir(mode=0o700)
web=pwd.getpwnam('web');os.chown(str(folder),web.pw_uid,web.pw_gid)
shutil.copy2(str(path),str(folder/'order.bak'))
if digest((folder/'order.bak').read_bytes())!=payload['expected_sha']:raise RuntimeError('Concurrent checkout edit while copying')
(folder/'order.new').write_bytes(after)
records=[{'path':payload['path'],'backup':'order.bak','new_backup':'order.new','old_sha':digest(before),'new_sha':digest(after)}]
for name,obj in [('manifest.json',records),('database.json',prepared['records'])]:
 p=folder/name;p.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
for name in ('order.bak','order.new','manifest.json','database.json'):
 p=folder/name;os.chmod(str(p),0o600);os.chown(str(p),web.pw_uid,web.pw_gid)
print('STAGE57_PREPARED='+json.dumps({'backup':str(folder),'planned_db_fields':len(prepared['records'])}),flush=True)
"""
REMOTE_VERIFY=r"""
folder=pathlib.Path('BACKUP_FOLDER')
for row in json.loads((folder/'manifest.json').read_text()):
 if digest((root/row['path']).read_bytes())!=row['new_sha']:raise RuntimeError('Checkout verification mismatch')
result=json.loads((folder/'applied.json').read_text())
for route in ('/order/','/dostavka/'):
 body=http(route+'?polish57='+str(os.getpid()))
 if '2449264' not in body or '6776332' not in body:raise RuntimeError('Phone smoke failed')
 if route=='/dostavka/' and '<h2>Доставка</h2>' in body:raise RuntimeError('Duplicate heading remains')
print('STAGE57_DEPLOY='+json.dumps({'success':True,'files':1,'cms_fields':result['applied_fields'],'backup':str(folder)}),flush=True)
"""
def marker(out,prefix):
 rows=[json.loads(s.split('=',1)[1]) for s in out.splitlines() if s.startswith(prefix+'=')]
 if len(rows)!=1:raise RuntimeError('Missing remote stage marker '+prefix)
 return rows[0]
def restore(state):
 out=execute(rollback_remote(json.loads(state.read_text())['backup']))
 for s in out.splitlines():
  if s.startswith('STAGE55_'):print(s,flush=True)
 state.unlink()
def main():
 state=Path('stage57-polish-state.json')
 if '--rollback' in sys.argv:
  if not state.exists():print('No polish recovery required');return
  restore(state);return
 if state.exists():raise RuntimeError('Existing polish recovery state; review/rollback before another rollout')
 original=patch_order((BASE/'tests/fixtures/delivery-order.html').read_bytes().decode())
 anchor='<style>\n'
 if original.count(anchor)!=1:raise RuntimeError('Order style anchor changed')
 css="""<style>
{if $wa->domainUrl() == 'https://profikompany.ru'}
  @media screen and (max-width: 767px) {
    #js-order-page .cart-help { display: none; }
  }
{/if}
"""
 updated=original.replace(anchor,css,1)
 address='{if !empty($theme_settings.company__address)}'
 if updated.count(address)!=1:raise RuntimeError('Help address anchor changed')
 updated=updated.replace(address,"{if !empty($theme_settings.company__address) && ($wa->domainUrl() != 'https://profikompany.ru' || $theme_settings.company__address != $theme_settings.company__email)}",1)
 prefix='<h2></h2>\r\n\r\n<h2>Доставка</h2>\r\n\r\n'
 old=DELIVERY['new']
 if not old.startswith(prefix):raise RuntimeError('Delivery heading anchor changed')
 new=old[len(prefix):]
 folder='/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage55-content-polish-'+str(os.environ['GITHUB_RUN_ID'])+'-'+str(os.environ.get('GITHUB_RUN_ATTEMPT','1'))+'-'+uuid.uuid4().hex[:12]
 payload={'php':PHP,'proposal':{'products':[],'categories':[]},'delivery':{'id':12,'domain':'profikompany.ru','full_url':'dostavka/','old_sha':sha(old.encode()),'new':new},
          'path':SHOP+'order.html','expected_sha':sha(original.encode()),'new_source':updated,'folder':folder}
 prepared=marker(execute(remote_stage(REMOTE_PREPARE,payload)),'STAGE57_PREPARED')
 # Prepare cannot change the site. Save recovery identity before any mutation.
 state.write_text(json.dumps({'backup':prepared['backup']}))
 print('STAGE57_PREPARED='+json.dumps(prepared),flush=True)
 try:
  applied=marker(execute(remote_stage(REMOTE_APPLY,payload,prepared['backup'])),'STAGE55_APPLIED')
  print('STAGE57_APPLIED='+json.dumps(applied),flush=True)
  result=marker(execute(remote_stage(REMOTE_VERIFY,payload,prepared['backup'])),'STAGE57_DEPLOY')
  (OUT/'rollout.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
  print('STAGE57_DEPLOY='+json.dumps(result),flush=True)
 except Exception:
  try:restore(state)
  except Exception as recovery_error:
   print('STAGE57_RECOVERY_REQUIRED='+json.dumps({'backup':prepared['backup'],'reason':str(recovery_error)[-300:]}),flush=True)
  raise
if __name__=='__main__':main()
