#!/usr/bin/env python3
"""v2.9 regression: project config, safe output roots, UUIDv7 run pointers, and location freeze."""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
ENV=dict(os.environ); ENV['PYTHONPATH']=str(ROOT/'src'); ENV['PYTHONDONTWRITEBYTECODE']='1'
def run(project,*args): return subprocess.run([sys.executable,'-m','runtime_feature_falsifier_cli',*args],cwd=project,env=ENV,text=True,capture_output=True)
def main()->int:
  with tempfile.TemporaryDirectory() as td:
    project=Path(td)/'project'; project.mkdir(); (project/'.git').mkdir()
    bad=run(project,'config','set','audit.output_dir','one/two/three/four/five'); assert bad.returncode!=0 and 'deeper than 4' in (bad.stderr+bad.stdout)
    ok=run(project,'config','set','audit.output_dir','.artifacts/quality/rff'); assert ok.returncode==0,ok.stderr
    first=run(project,'audit','init','--project-name','demo','--environment','local','--scope','demo','--mode','feature'); assert first.returncode==0,first.stderr
    init=json.loads(first.stdout); run_dir=Path(init['run_dir']); assert run_dir.parent.name=='runs' and run_dir.name.startswith('rff-')
    where=run(project,'audit','where','--format','json'); w=json.loads(where.stdout); assert Path(w['active_run'])==run_dir
    blocked=run(project,'config','set','audit.output_dir','.other/rff'); assert blocked.returncode!=0 and 'RFF_OUTPUT_LOCATION_CHANGED' in (blocked.stderr+blocked.stdout)
    abandon=run(project,'audit','abandon','--reason','test-only'); assert abandon.returncode==0,abandon.stderr
    changed=run(project,'config','set','audit.output_dir','.other/rff'); assert changed.returncode==0,changed.stderr
    second=run(project,'audit','init','--project-name','demo2','--environment','local','--scope','demo','--mode','feature'); assert second.returncode==0,second.stderr
    s=json.loads(second.stdout); assert '.other/rff' in s['output_root'].replace('\\','/')
    cfg=(project/'.rff.toml').read_text(); assert '[audit]' in cfg and 'output_dir' in cfg
  print('PASS: v2.9 configurable output roots, depth guard, active-location freeze, abandon, and immutable run creation work')
  return 0
if __name__=='__main__': raise SystemExit(main())
