#!/usr/bin/env python3
from stage52_disable_callback import execute
REMOTE = r"""set -eu
python3 - <<'PY'
import subprocess,json
checks=[['mysql','--protocol=socket','-uroot','-N','-B','-e','SELECT 1'],
        ['mysql','-N','-B','-e','SELECT 1']]
for i,cmd in enumerate(checks):
 p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
 out,err=p.communicate(timeout=9)
 print('PERF67_ACCESS='+json.dumps({'method':i,'success':p.returncode==0 and out.strip()=='1'}),flush=True)
PY
"""
if __name__=='__main__':
 result=execute(REMOTE)
 for s in result.splitlines():
  if s.startswith('PERF67_ACCESS='):print(s,flush=True)
