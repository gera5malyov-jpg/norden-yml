import copy,importlib.util,json,sys,os,tempfile,io
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
