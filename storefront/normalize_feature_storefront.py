#!/usr/bin/env python3
"""Safe storefront-only feature name and boolean normalization. Leaves DB, types and feeds untouched."""
from stage52_disable_callback import execute
REMOTE=r'''set -eu
python3 - <<'PY'
import os,re,json,pathlib,shutil,tempfile,hashlib,datetime,subprocess
root=pathlib.Path('/home/web/vm-23f9aff9.na4u.ru/www')
theme=root/'wa-data/public/shop/themes/pureMegapolis42'
files=[theme/'product.html',theme/'product.info.html',theme/'products.features.html']
marker='megapolis-feature-normalize-20261019'
name_pairs=json.loads("[[\"РазмерГлубина\",\"Глубина\"],[\"РазмерВысота\",\"Высота\"],[\"РазмерШирина\",\"Ширина\"],[\"БРЕНД\",\"Бренд\"],[\"Х а р а к т е р и с т и к и\",\"Характеристики\"],[\"Общие. Комната использования\",\"Помещение\"],[\"Высотамм\",\"Высота, мм\"],[\"Длинамм\",\"Длина, мм\"],[\"Ширинамм\",\"Ширина, мм\"],[\"Материалкаркаса\",\"Материал каркаса\"],[\"Материалстолешницы\",\"Материал столешницы\"],[\"Цветкорпуса\",\"Цвет корпуса\"],[\"Цветфасадов\",\"Цвет фасадов\"],[\"Сидушка материал\",\"Материал сиденья\"],[\"Спинка материал\",\"Материал спинки\"],[\"Подлокотники материал\",\"Материал подлокотников\"],[\"Ширина см\",\"Ширина, см\"],[\"Длина см\",\"Длина, см\"],[\"Высота см\",\"Высота, см\"],[\"Вес,кг\",\"Вес, кг\"],[\"Высота спального места,см\",\"Высота спального места, см\"],[\"высота упаковки\",\"Высота упаковки\"],[\"глубина упаковки\",\"Глубина упаковки\"],[\"ширина упаковки\",\"Ширина упаковки\"],[\"гарантия\",\"Гарантия\"]]")
boolcodes=json.loads("[\"trebuetsya_sborka\",\"sborka\",\"izdeliya_stopiruyutsya\",\"vozmozhnost_regulirovki_vysoty_stoleshnitsy\",\"vozmozhnost_regulirovki_ugla_naklona_stoleshnitsy\",\"vozmozhnost_regulirovki_vysoty_stula\",\"transformatsiya_stola_v_molbert\",\"nalichie_nadstroyki\",\"reguliruemaya_spinka\",\"matras_v_komplekte\",\"komplektatsiya_lampochkami\",\"redkit_6c668fa282ac91a8\",\"redkit_e3d32b27f3e8ca6b\"]")
list_txt=", ".join(repr(c) for c in boolcodes)
declare="{* "+marker+"; rendering only, source values unchanged *}\n{$_megapolis_bool_feature_codes = ["+list_txt+"]}\n"
labelvar='$features[$f_code].name'
labelmap="{if "+labelvar+" == '"+name_pairs[0][0]+"'}"+name_pairs[0][1]
for old,new in name_pairs[1:]:
 labelmap+="{elseif "+labelvar+" == '"+old+"'}"+new
labelmap+="{else}{"+labelvar+"|escape}{/if}"
booldisplay=("{if !is_array($f_value) && in_array($f_code, $_megapolis_bool_feature_codes)"
  " && strtolower(trim($f_value)) == 'true'}Да"
  "{elseif !is_array($f_value) && in_array($f_code, $_megapolis_bool_feature_codes)"
  " && strtolower(trim($f_value)) == 'false'}Нет"
  "{else}{$f_value}{/if}")
badnames="['model','tsvet_karkasa']"
# Never replace invalid supplier color/model/collection tokens with a fabricated specification.
suppression=("(!is_array($f_value) && "
 "((in_array($f_code, ['model','tsvet_karkasa']) && in_array(strtolower(trim($f_value)), ['true','false']))"
 " || in_array(strtolower(trim($f_value)), ['nan','undefined','null'])))")
before={};after={};counts={}
for p in files:
 s=p.read_text('utf8')
 if marker in s:raise RuntimeError('Already applied '+p.name)
 old=s
 if p.name=='product.html':
  anchor='<article class="product-page"'
  if s.count(anchor)!=1:raise RuntimeError('Unexpected article root')
  s=s.replace(anchor,declare+anchor,1)
 else:
  s=declare+s
 n=s.count('{'+labelvar+'|escape}')
 if n<1 or n>5:raise RuntimeError('Unexpected feature name anchors '+p.name+': '+str(n))
 s=s.replace('{'+labelvar+'|escape}',labelmap)
 if p.name=='products.features.html':
  raw='{$f_value|escape}'
  if s.count(raw)!=1:raise RuntimeError('List value anchor missing')
  s=s.replace(raw,booldisplay.replace('{$f_value}','{$f_value|escape}'))
  item='{if (!$f_code|in_array:$_disabled_features && empty($_enabled_features)) || (!empty($_enabled_features) && $f_code|in_array:$_enabled_features)}'
  if s.count(item)!=1:raise RuntimeError('List filter anchor changed')
  s=s.replace(item,item[:-1]+' && !'+suppression+'}',1)
 else:
  raw='{$f_value}'
  c=s.count(raw)
  if c<1 or c>6:raise RuntimeError('Unexpected feature value anchors '+p.name+': '+str(c))
  s=s.replace(raw,booldisplay)
  if p.name=='product.info.html':
   anchor='{if $features[$f_code].type == \'divider\'}'
   if s.count(anchor)!=2:raise RuntimeError('Unexpected full-feature loops')
   s=s.replace(anchor,'{if $features[$f_code].type != \'divider\' && '+suppression+'}\n                  {* invalid supplier token omitted *}\n                {elseif $features[$f_code].type == \'divider\'}')
  else:
   anchor="{if $f_value@iteration < 10 && $features[$f_code].type != 'divider'}"
   if s.count(anchor)!=1:raise RuntimeError('Unexpected initial feature list')
   s=s.replace(anchor,anchor[:-1]+' && !'+suppression+'}',1)
  # The fallback summary in product.html may contain dirty value strings. The universal conversion still handles booleans.
 before[p]=old;after[p]=s;counts[p.name]={'name_anchors':n,'value_anchors':c if p.name!='products.features.html' else 1}
print('FEATURE_NORMALIZE_PREFLIGHT='+json.dumps({'files':counts,'dictionary_size':len(name_pairs),'booleans':len(boolcodes)}),flush=True)
backup=root.parent/'storefront-backups'/('feature-display-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
backup.mkdir(parents=True,mode=0o700)
for i,p in enumerate(files):
 shutil.copy2(str(p),str(backup/(str(i)+'.bak')));os.chmod(str(backup/(str(i)+'.bak')),0o600)
written=[]
try:
 for p in files:
  if p.read_text('utf8')!=before[p]:raise RuntimeError('Concurrent change '+p.name)
  st=p.stat()
  with tempfile.NamedTemporaryFile('w',encoding='utf8',delete=False,dir=str(p.parent),prefix='.feature-display-') as f:
   f.write(after[p]);temp=f.name
  os.chown(temp,st.st_uid,st.st_gid);os.chmod(temp,st.st_mode&0o777)
  os.replace(temp,str(p));written.append(p);os.utime(str(p),None)
 testcases=[('/stul-kenner-313sd-chernyy-zhemchug-e10-334-322382/','kenner'),
   ('/category/kompyuternye-kresla/','category'),('/','home'),
   ('/stol-obedennyy-02-02a-razbornyy-karkas-konyak/','quote'),('/order/','order')]
 for route,kind in testcases:
  req=subprocess.Popen(['curl','-ksSL','--compressed','--max-time','35','-w','\n%{http_code}', 'https://profikompany.ru'+route],
   stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
  out,err=req.communicate(timeout=42)
  body,code=out.rsplit('\n',1) if '\n' in out else ('','')
  good=req.returncode==0 and code=='200' and len(body)>3000
  details={'route':route,'http':code,'ok':bool(good)}
  if kind=='kenner':
   # Rendered HTML must contain the human-readable boolean and normalized label.
   details['boolean_yes']=bool(re.search(r'Требуется сборка.{0,600}?Да',body,re.S))
   details['feature_excerpt']=(re.search(r'Требуется сборка.{0,850}',body,re.S).group(0)[:280] if 'Требуется сборка' in body else 'NOT_PRESENT')
   details['normalized_depth']='РазмерГлубина' not in body and 'Глубина' in body
   good=good and details['boolean_yes'] and details['normalized_depth']
  if kind=='order':good=good and 'noindex,follow' in body
  if kind=='quote':good=good and 'Цена по запросу' in body and '<meta itemprop="price" content="0">' not in body
  details['ok']=bool(good)
  print('FEATURE_NORMALIZE_TEST='+json.dumps(details,ensure_ascii=False),flush=True)
  if not good:raise RuntimeError('Live test failed '+route)
 print('FEATURE_NORMALIZE_APPLIED='+json.dumps({'success':True,'backup':str(backup),'files':len(files),
  'database_unchanged':True,'supplier_imports_unchanged':True},ensure_ascii=False),flush=True)
except Exception as e:
 for p in reversed(written):
  if p.read_text('utf8')!=after[p]:raise RuntimeError('Concurrent change prevents safe rollback '+p.name)
  shutil.copy2(str(backup/(str(files.index(p))+'.bak')),str(p));os.utime(str(p),None)
 print('FEATURE_NORMALIZE_ROLLBACK='+json.dumps({'restored':len(written),'reason':str(e)[:650]},ensure_ascii=False),flush=True)
 raise
PY
'''
if __name__=='__main__':
 out=execute(REMOTE)
 for ln in out.splitlines():
  if ln.startswith('FEATURE_NORMALIZE_'):print(ln,flush=True)
 if 'FEATURE_NORMALIZE_APPLIED=' not in out:raise RuntimeError('Unverified feature deployment')
