import copy,importlib.util,json,sys,os,tempfile,io,re,shutil,subprocess,base64
from types import SimpleNamespace
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path
import unittest
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
def load():
 spec=importlib.util.spec_from_file_location('content_rollout',BASE/'stage55_deploy_content.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
class ContentRollout(unittest.TestCase):
 def test_proposal_writes_only_allowed_fields_and_requires_positive_stock(self):
  m=load();p=json.loads((BASE/'stage55-proposal.json').read_text());m.validate_proposal(p)
  bad=copy.deepcopy(p);bad['products'][0]['set']['price']='1'
  with self.assertRaises(ValueError):m.validate_proposal(bad)
  bad=copy.deepcopy(p);bad['products'][0]['expected']['count']=None
  with self.assertRaises(ValueError):m.validate_proposal(bad)
  bad=copy.deepcopy(p);bad['products'][0]['set']['description']+='<script>alert(1)</script>'
  with self.assertRaises(ValueError):m.validate_proposal(bad)
 def test_payload_contains_only_new_asset_and_guarded_source_hashes(self):
  m=load();p=m.build_payload()
  self.assertEqual(set(p['assets']),{'wa-data/public/site/themes/pureMegapolis42/js/megapolis-delivery-v1.js'})
  self.assertEqual(len(p['expected_files']),4)
  self.assertEqual(p['delivery']['id'],12)
  self.assertEqual(p['delivery']['domain'],'profikompany.ru')
  self.assertIn('отправки',p['delivery']['new'])
 def test_rollback_is_compare_and_swap_and_refreshes_all_restored_templates(self):
  m=load()
  with self.assertRaises(ValueError):m.rollback_remote('/tmp/arbitrary')
  code=m.rollback_remote('/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage55-content-20261012')
  self.assertIn("!=row['new_sha']",code)
  self.assertIn('os.utime',code)
  self.assertIn("db_run('rollback'",code)

 def test_ambiguous_commit_timeout_has_state_before_apply_and_keeps_failed_recovery(self):
  import stage52_disable_callback
  m=load();folder='/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage55-content-20261012'
  def ssh_boundary(code):
   if 'STAGE55_PREPARED=' in code:return 'STAGE55_PREPARED='+json.dumps({'backup':folder})
   state=Path('stage55-content-state.json')
   self.assertEqual(json.loads(state.read_text())['backup'],folder)
   if 'STAGE55_APPLIED=' in code:raise TimeoutError('Connection lost after a potentially successful commit')
   raise RuntimeError('Recovery connection unavailable')
  original=os.getcwd()
  try:
   with tempfile.TemporaryDirectory() as td:
    os.chdir(td)
    with patch.object(stage52_disable_callback,'execute',ssh_boundary),patch.object(sys,'argv',['stage55_deploy_content.py']),redirect_stdout(io.StringIO()):
     with self.assertRaises(TimeoutError):m.main()
    self.assertEqual(json.loads(Path('stage55-content-state.json').read_text())['backup'],folder)
  finally:os.chdir(original)

 def test_server_advisory_lock_serializes_mutation_and_recovery(self):
  m=load();namespace={};exec(m.REMOTE_COMMON,namespace)
  with tempfile.TemporaryDirectory() as td:
   acquire=namespace['acquire_rollout_lock'];first=acquire(Path(td),timeout=0)
   try:
    with self.assertRaises(RuntimeError):acquire(Path(td),timeout=0)
   finally:os.close(first)
   second=acquire(Path(td),timeout=0);os.close(second)
  self.assertIn('_rollout_lock=acquire_rollout_lock(folder)',m.REMOTE_APPLY)
  self.assertIn('_rollout_lock=acquire_rollout_lock(folder)',m.rollback_remote('/home/web/vm-23f9aff9.na4u.ru/storefront-backups/stage55-content-20261012'))

 def test_prepared_records_compare_canonical_keys_without_scalar_coercion(self):
  m=load()
  self.assertIn("if(canon($result['records'])!==canon($input['records']))",m.PHP)

 @unittest.skipUnless(shutil.which('php'),'PHP absent locally; executable regression runs where PHP is installed')
 def test_php_record_comparison_ignores_object_order_but_keeps_types_and_list_order(self):
  m=load()
  canon_source='function canon'+m.PHP.split('function canon',1)[1].split('function scalar_equal',1)[0]
  comparison=re.search(r"if\((.*?)\)throw new Exception\('Content changed after backup; no database writes'\);",m.PHP).group(1)
  # Values are ordinary JSON from a PHP result, reserialized through unordered
  # Python 3.5 dictionaries. Only associative-key order changes in the first case.
  script='<?php\n'+canon_source+"""
$a=array('table'=>'shop_product','id'=>7,'field'=>'description','old'=>null,'new'=>'<p>Модель</p>');
$b=array('new'=>'<p>Модель</p>','old'=>null,'field'=>'description','id'=>7,'table'=>'shop_product');
$result=array('records'=>array($a));$input=array('records'=>array($b));
function changed($result,$input){return COMPARISON;}
if(changed($result,$input))exit(11);
$input['records'][0]['old']='';if(!changed($result,$input))exit(12);
$input['records'][0]=$b;$input['records'][0]['id']='7';if(!changed($result,$input))exit(13);
$c=$a;$c['id']=8;$result['records']=array($a,$c);$input['records']=array($c,$b);
if(!changed($result,$input))exit(14);
echo 'Canonical record CAS regression passed';
""".replace('COMPARISON',comparison)
  proc=subprocess.run(['php'],input=script,text=True,capture_output=True,timeout=10)
  self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)
  self.assertIn('Canonical record CAS regression passed',proc.stdout)

 def test_acceptance_metadata_mismatch_reports_actual_expected_url_without_relaxing(self):
  spec=importlib.util.spec_from_file_location('content_acceptance',BASE/'stage55_content_acceptance.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  url='https://profikompany.ru/category/stul/stulya/'
  m.assert_exact_metadata('Ожидаемый','Ожидаемый','title',url)
  with self.assertRaises(AssertionError) as caught:m.assert_exact_metadata('Динамический title','Ожидаемый','title',url)
  message=str(caught.exception)
  for value in ('Динамический title','Ожидаемый',url):self.assertIn(value,message)
  with self.assertRaises(AssertionError):m.assert_exact_metadata('Ожидаемый ','Ожидаемый','title',url)

 def test_db_boundary_serializes_reversed_category_set_to_identical_php_field_plan(self):
  m=load();namespace={};exec(m.REMOTE_COMMON,namespace);serialized=[]
  php="<?php $input=json_decode(base64_decode('DB_INPUT_B64'),true);"
  first={'php':php,'proposal':{'categories':[{'id':285,'set':{'meta_title':'Заголовок','meta_description':'Описание'}}]}}
  second=copy.deepcopy(first);second['proposal']['categories'][0]['set']={'meta_description':'Описание','meta_title':'Заголовок'}
  def php_boundary(command,**kwargs):
   if command[:2]==['php','-l']:
    script=Path(command[2]).read_text()
    encoded=re.search(r"base64_decode\('([^']+)'\)",script).group(1)
    serialized.append(base64.b64decode(encoded))
    return SimpleNamespace(returncode=0,stdout='',stderr='')
   result=base64.b64encode(b'{"records":[]}').decode()
   return SimpleNamespace(returncode=0,stdout='DB_RESULT='+result+'\n',stderr='')
  # The PHP binary is the external boundary; the real Python serializer and
  # emitted temporary script run unchanged, including nested key ordering.
  with patch.object(namespace['subprocess'],'run',php_boundary):
   namespace['db_run']('prepare',first);namespace['db_run']('prepare',second)
  self.assertEqual(len(serialized),2)
  self.assertEqual(serialized[0],serialized[1])
  plans=[list(json.loads(raw)['payload']['proposal']['categories'][0]['set']) for raw in serialized]
  self.assertEqual(plans,[['meta_description','meta_title']]*2)
