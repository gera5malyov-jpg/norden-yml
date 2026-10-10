#!/usr/bin/env python3
"""Guarded, scoped category buying guides without catalog/DB edits.

Adds meaningful buying advice to six existing categories with no description.
Copies active Smarty template to private backup, rolls back if smoke fails.
"""
from __future__ import annotations
import json,os,subprocess,tempfile,urllib.parse
from pathlib import Path
from stage1_readonly_audit import VM_ID,VM_IP,request_json
REMOTE=r"""set -eu
python3 - <<'PY'
import os,json,pathlib,shutil,subprocess,tempfile,time
from datetime import datetime,timezone
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
file=root/'wa-data/public/shop/themes/pureMegapolis42/category.html'
orig=file.read_text(encoding='utf-8')
anchor='\n</div>\n\n{/strip}'
if orig.count(anchor)!=1 or 'megapolis-semantic-help' in orig:
 raise RuntimeError('Category template differs from read-only audit - no writes')
block=r'''
  {* Useful category buyer guidance scoped to profikompany.ru, not a catalog data edit *}
  {if $wa->domainUrl() == 'https://profikompany.ru' && $wa->get('page', 1) == 1 && empty($category.description) && !empty($products)}
    {if $category.id == 440}
      <section class="category__description megapolis-semantic-help" aria-label="Как выбрать письменный стол">
        <h2>Как выбрать письменный стол</h2>
        <p>Для работы или учебы обратите внимание на ширину и глубину столешницы, высоту стола и свободное место для ног. Перед покупкой измерьте пространство в комнате и проверьте, помещаются ли монитор, документы и другие необходимые предметы.</p>
        <p>Материал столешницы, количество ящиков, особенности каркаса, размеры и состав поставки зависят от конкретной модели. Сравнить их можно в характеристиках выбранного письменного стола.</p>
      </section>
    {elseif $category.id == 285}
      <section class="category__description megapolis-semantic-help" aria-label="Как выбрать обеденные стулья">
        <h2>На что обратить внимание при выборе обеденных стульев</h2>
        <p>Сопоставьте высоту сиденья с высотой обеденного стола, оцените габариты и количество свободного места. Для повседневного использования важны материал каркаса, обивка и удобство ухода за ней.</p>
        <p>При сравнении моделей проверьте размеры, допустимую нагрузку и количество стульев в комплекте, если эти сведения указаны в карточке товара.</p>
      </section>
    {elseif $category.id == 4204}
      <section class="category__description megapolis-semantic-help" aria-label="Как выбрать кресло руководителя">
        <h2>Как подобрать кресло руководителя</h2>
        <p>При выборе кресла для рабочего кабинета учитывайте высоту спинки, размеры сиденья, обивку и габариты рабочего места. Важны доступные регулировки и допустимая нагрузка.</p>
        <p>Подлокотники, механизм качания и регулировка поясничной зоны есть не у каждой модели. Уточняйте комплектацию и конкретные характеристики в карточке кресла руководителя.</p>
      </section>
    {elseif $category.id == 4205}
      <section class="category__description megapolis-semantic-help" aria-label="Как выбрать кресло для персонала">
        <h2>Выбор кресла оператора для рабочего места</h2>
        <p>Для продолжительной работы за компьютером сравните высоту и глубину сиденья, форму спинки, тип обивки и диапазоны регулировок. Кресло должно подходить по размерам к рабочему столу и доступному пространству.</p>
        <p>Наличие подлокотников, поддержки поясницы и механизма качания определяется конструкцией конкретного кресла. Проверьте эти параметры, допустимую нагрузку и габариты до оформления заказа.</p>
      </section>
    {elseif $category.id == 2150}
      <section class="category__description megapolis-semantic-help" aria-label="Как выбрать распашной шкаф">
        <h2>Что проверить перед покупкой распашного шкафа</h2>
        <p>Измерьте место для шкафа с учётом свободного пространства для открывания дверей. При сравнении моделей обратите внимание на высоту, ширину, глубину, материалы корпуса и оформление фасадов.</p>
        <p>Количество полок, штанг и ящиков, возможность сборки и состав комплектации различаются. Проверяйте их по характеристикам каждого шкафа.</p>
      </section>
    {elseif $category.id == 1003}
      <section class="category__description megapolis-semantic-help" aria-label="Как выбрать диван">
        <h2>Выбор дивана для дома и офиса</h2>
        <p>Перед покупкой проверьте ширину, глубину и высоту дивана, размер дверных проёмов и место установки. Для гостиной, зоны ожидания или кабинета могут быть важны разные характеристики обивки и ухода за ней.</p>
        <p>Не все диваны раскладываются или имеют ящик для хранения. Наличие механизма трансформации и других функций уточняйте по описанию конкретной модели.</p>
      </section>
    {/if}
  {/if}
'''
updated=orig.replace(anchor,'\n'+block+anchor,1)
backups=root.parent/'storefront-backups'
backups.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=backups/('stage34-semantic-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
folder.mkdir(mode=0o700)
backup=folder/'category.html'
shutil.copy2(str(file),str(backup));os.chmod(str(backup),0o600)
written=False
try:
 stat=file.stat()
 with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=str(file.parent),prefix='.category-stage34-',delete=False) as f:
  f.write(updated);tmp=f.name
 os.chmod(tmp,stat.st_mode);os.chown(tmp,stat.st_uid,stat.st_gid)
 os.replace(tmp,str(file));written=True
 tests=[
  ('/category/stoly_1/stoly-pismennye/','Как выбрать письменный стол',True),
  ('/category/stul/stulya/','На что обратить внимание при выборе обеденных стульев',True),
  ('/category/kompyuternye-kresla/kresla-rukovoditelya/','Как подобрать кресло руководителя',True),
  ('/category/kompyuternye-kresla/kresla-operatora/','Выбор кресла оператора',True),
  ('/category/shkafy/shkafy-raspashnye/','Что проверить перед покупкой распашного шкафа',True),
  ('/category/myagkaya-mebel/divany/','Выбор дивана для дома и офиса',True),
  ('/category/kompyuternye-kresla/stulya-posetiteley/','',False)
 ]
 checks=[]
 for route,heading,expect in tests:
  u='https://profikompany.ru'+route+'?stage34_verify='+str(int(time.time()))
  p=subprocess.Popen(['curl','-ksSL','--max-time','35','-w','\n%{http_code}',u],
    stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=p.communicate(timeout=42)
  body,status=out.rsplit('\n',1) if '\n' in out else ('','')
  found='megapolis-semantic-help' in body
  ok=(p.returncode==0 and status=='200' and found==expect and
    (not expect or heading in body) and body.count('rel="canonical"')==1)
  checks.append({'route':route,'http':status,'guide_present':found,'expected':expect,'ok':ok})
 if not all(c['ok'] for c in checks):raise RuntimeError('Category guide live smoke failed '+json.dumps(checks,ensure_ascii=False))
 print('STAGE34_APPLY='+json.dumps({'ok':True,'backup':str(backup),'page_checks':checks},ensure_ascii=False))
except Exception as e:
 if written:shutil.copy2(str(backup),str(file))
 print('STAGE34_ROLLBACK='+json.dumps({'reason':str(e),'restored':written},ensure_ascii=False))
 raise
PY
"""

def run():
 key=os.environ.get('NETANGELS_API_KEY','').strip()
 if not key:raise RuntimeError('Missing NETANGELS_API_KEY secret')
 token=request_json('https://panel.netangels.ru/api/gateway/token/','POST',
   urllib.parse.urlencode({'api_key':key}).encode(),{'Content-Type':'application/x-www-form-urlencoded'}).get('token')
 if not token:raise RuntimeError('NetAngels gateway token missing')
 hdr={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
 with tempfile.TemporaryDirectory(prefix='stage34-guides-') as td:
  ssh=td+'/id'
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',ssh],check=True)
  pub=Path(ssh+'.pub').read_text().strip()
  row=request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/create/'.format(VM_ID),'POST',json.dumps({'key':pub,'name':'storefront-stage34-'+os.getenv('GITHUB_RUN_ID','manual')}).encode(),hdr)
  kid=row.get('id')
  if not kid:raise RuntimeError('Temporary SSH key registration failed')
  try:
   p=subprocess.run(['ssh','-i',ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=no',
     '-o','UserKnownHostsFile=/dev/null','-o','ConnectTimeout=15','root@'+VM_IP,'bash -s'],
     input=REMOTE,text=True,capture_output=True,timeout=240)
   for line in p.stdout.splitlines():
    if line.startswith('STAGE34_'):print(line,flush=True)
   if p.returncode:raise RuntimeError('SEO update failed '+p.stdout[-1300:]+' '+p.stderr[-400:])
  finally:
   try:
    request_json('https://api-ms.netangels.ru/api/v1/cloud/vms/{}/ssh/{}/'.format(VM_ID,kid),'DELETE',None,hdr)
    print('temporary_ssh_key_removed=true',flush=True)
   except Exception:print('WARNING: SSH key cleanup needs review')
if __name__=='__main__':run()
