#!/usr/bin/env python3
"""Regression: startup health and dependency-sensitive reproduction metadata are enforced."""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CTL=ROOT/'runtime-feature-falsifier'/'scripts'/'auditctl.py'
def run(*a): return subprocess.run([sys.executable,str(CTL),*a],text=True,capture_output=True)
def main()->int:
  with tempfile.TemporaryDirectory() as td:
    project=Path(td)/'project'; project.mkdir(); audit=project/'.runtime-feature-audit'
    assert run('init','--audit-dir',str(audit),'--target-root',str(project)).returncode==0
    plan=json.loads((audit/'audit-plan.json').read_text()); plan['target']['startup_path']='real-service --serve'
    plan['features']=[{
      'feature_id':'provider-feature','claim':'public feature uses its intended provider','claim_source':'teaching','entry_points':['POST /provider'],
      'expected_end_effects':['provider-backed output returned'],'input_sensitive':False,'stateful':False,'dependency_sensitive':True,
      'probes':[
        {'probe_id':'environment-start','probe_intent':'environment_start','contract_relation':'ENVIRONMENT','expected':'runtime starts','effect_checks':[],'required':True},
        {'probe_id':'baseline','probe_intent':'baseline_valid','contract_relation':'VALID','expected':'baseline works','effect_checks':[],'required':True},
        {'probe_id':'provider-authenticity','probe_intent':'dependency_authenticity','contract_relation':'VALID','expected':'intended provider participates','effect_checks':[],'required':True},
      ]
    }]
    (audit/'audit-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    assert run('validate-plan','--audit-dir',str(audit)).returncode==0

    early=run('attempt-start','--audit-dir',str(audit),'--feature-id','provider-feature','--probe-id','baseline','--repro-command','curl /provider')
    assert early.returncode!=0 and 'startup health' in early.stdout.lower(), early.stdout
    no_repro=run('attempt-start','--audit-dir',str(audit),'--feature-id','provider-feature','--probe-id','environment-start')
    assert no_repro.returncode!=0 and 'repro-command' in no_repro.stdout, no_repro.stdout
    start=run('attempt-start','--audit-dir',str(audit),'--feature-id','provider-feature','--probe-id','environment-start','--repro-command','real-service --serve')
    assert start.returncode==0, start.stdout; aid=json.loads(start.stdout)['attempt_id']
    assert run('attempt-finish','--audit-dir',str(audit),'--attempt-id',aid,'--observed','runtime healthy','--result','SURVIVED','--failure-pattern','NONE_OBSERVED','--confidence','HIGH').returncode==0

    dep_no_repro=run('attempt-start','--audit-dir',str(audit),'--feature-id','provider-feature','--probe-id','baseline')
    assert dep_no_repro.returncode!=0 and 'repro-command' in dep_no_repro.stdout, dep_no_repro.stdout
    dep=run('attempt-start','--audit-dir',str(audit),'--feature-id','provider-feature','--probe-id','baseline','--repro-command','curl -X POST http://localhost/provider')
    assert dep.returncode==0, dep.stdout
  print('PASS: startup health ordering and dependency-sensitive repro metadata are mechanically enforced')
  return 0
if __name__=='__main__': raise SystemExit(main())
