#!/usr/bin/env python3
"""Regression: bundled JSON schemas are consumed by runtime validation."""
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
    plan=json.loads((audit/'audit-plan.json').read_text())
    # Semantic code would stringify 123 as non-empty; the bundled schema must reject its type.
    plan['target']['startup_path']=123; plan['target']['runtime_identity_expectation']='schema teaching runtime'; plan['target']['collision_surfaces']=['temporary workspace']
    plan['features']=[{
      'feature_id':'schema-feature','claim':'schema feature works','claim_source':'teaching','entry_points':['CLI'],
      'expected_end_effects':['observable completion'],'input_sensitive':False,'stateful':False,'dependency_sensitive':False,
      'probes':[
        {'probe_id':'environment-start','probe_intent':'environment_start','contract_relation':'ENVIRONMENT','expected':'runtime starts','effect_checks':[],'required':True},
        {'probe_id':'runtime-identity','probe_intent':'runtime_identity','contract_relation':'ENVIRONMENT','expected':'runtime identity matches','effect_checks':['process identity matches'],'required':True},
        {'probe_id':'environment-collision','probe_intent':'environment_collision','contract_relation':'ENVIRONMENT','expected':'environment isolated','effect_checks':['no competing consumer'],'required':True},
        {'probe_id':'baseline','probe_intent':'baseline_valid','contract_relation':'VALID','expected':'baseline works','effect_checks':[],'required':True},
      ]
    }]
    (audit/'audit-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    bad=run('validate-plan','--audit-dir',str(audit))
    assert bad.returncode!=0, bad.stdout
    assert 'startup_path' in bad.stdout and 'must be string' in bad.stdout, bad.stdout
  print('PASS: auditctl consumes bundled audit-plan JSON schema during validation')
  return 0
if __name__=='__main__': raise SystemExit(main())
