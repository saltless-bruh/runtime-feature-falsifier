#!/usr/bin/env python3
"""Regression: startup, runtime identity, collision isolation, and dependency repro metadata are enforced."""
from __future__ import annotations
import contextlib, io, json, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'runtime-feature-falsifier'/'scripts'))
import auditctl  # noqa: E402

def call(fn, **kwargs):
    ns=SimpleNamespace(format='json', **kwargs)
    buf=io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc=fn(ns)
    out=json.loads(buf.getvalue()) if buf.getvalue().strip() else {}
    return rc,out

def start(audit, fid, pid, repro=None):
    return call(auditctl.cmd_attempt_start,audit_dir=audit,feature_id=fid,probe_id=pid,attempt_id=None,action=None,repro_command=repro,precondition=[],hypothesis_id=None,changed_variable=None,retry_reason=None,escalation_stage=None)

def finish(audit, aid, observed, side):
    return call(auditctl.cmd_attempt_finish,audit_dir=audit,attempt_id=aid,observed=observed,side_effect_check=side,persistence_check=None,http_status=None,exit_code=None,evidence=[],result='SURVIVED',failure_pattern='NONE_OBSERVED',confidence='HIGH',notes='')

def main()->int:
  with tempfile.TemporaryDirectory() as td:
    project=Path(td)/'project'; project.mkdir(); audit=project/'.runtime-feature-audit'
    rc,_=call(auditctl.cmd_init,audit_dir=audit,audit_id=None,project_name='current-project',environment='local',scope='provider feature',mode='feature',target_root=project,force=False)
    assert rc==0
    plan=json.loads((audit/'audit-plan.json').read_text())
    plan['target']['startup_path']='real-service --serve'
    plan['target']['runtime_identity_expectation']='real-service FastAPI/uvicorn process'
    plan['target']['collision_surfaces']=['provider sandbox account','temporary database schema']
    plan['features']=[{
      'feature_id':'provider-feature','claim':'public feature uses its intended provider','claim_source':'teaching','entry_points':['POST /provider'],
      'expected_end_effects':['provider-backed output returned'],'input_sensitive':False,'stateful':False,'dependency_sensitive':True,
      'probes':[
        {'probe_id':'environment-start','probe_intent':'environment_start','contract_relation':'ENVIRONMENT','expected':'runtime starts','effect_checks':[],'required':True},
        {'probe_id':'runtime-identity','probe_intent':'runtime_identity','contract_relation':'ENVIRONMENT','expected':'intended service process serves the port','effect_checks':['process/server identity matches declared expectation'],'required':True},
        {'probe_id':'environment-collision','probe_intent':'environment_collision','contract_relation':'ENVIRONMENT','expected':'provider/database audit resources are isolated','effect_checks':['no competing test/live consumer shares mutable resources'],'required':True},
        {'probe_id':'baseline','probe_intent':'baseline_valid','contract_relation':'VALID','expected':'baseline works','effect_checks':[],'required':True},
        {'probe_id':'provider-authenticity','probe_intent':'dependency_authenticity','contract_relation':'VALID','expected':'intended provider participates','effect_checks':[],'required':True},
      ]
    }]
    (audit/'audit-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    assert auditctl.validate_plan_obj(plan)['ok']

    rc,out=start(audit,'provider-feature','baseline','curl /provider'); assert rc!=0 and 'startup health' in ' '.join(out['errors']).lower()
    rc,out=start(audit,'provider-feature','environment-start'); assert rc!=0 and 'repro-command' in ' '.join(out['errors'])
    rc,out=start(audit,'provider-feature','environment-start','real-service --serve'); assert rc==0; aid=out['attempt_id']; assert finish(audit,aid,'runtime healthy','runtime accepted connection')[0]==0

    rc,out=start(audit,'provider-feature','baseline','curl /provider'); assert rc!=0 and 'runtime identity' in ' '.join(out['errors']).lower()
    rc,out=start(audit,'provider-feature','runtime-identity'); assert rc!=0 and 'repro-command' in ' '.join(out['errors'])
    rc,out=start(audit,'provider-feature','runtime-identity','ps aux && curl -si /healthz'); assert rc==0; iid=out['attempt_id']; assert finish(audit,iid,'uvicorn real-service process serves target','process command line and server signature match')[0]==0

    rc,out=start(audit,'provider-feature','baseline','curl /provider'); assert rc!=0 and 'collision' in ' '.join(out['errors']).lower()
    rc,out=start(audit,'provider-feature','environment-collision'); assert rc!=0 and 'repro-command' in ' '.join(out['errors'])
    rc,out=start(audit,'provider-feature','environment-collision','inspect provider account and database schema consumers'); assert rc==0; cid=out['attempt_id']; assert finish(audit,cid,'sandbox resources isolated','no competing consumers found')[0]==0

    rc,out=start(audit,'provider-feature','baseline'); assert rc!=0 and 'repro-command' in ' '.join(out['errors'])
    rc,out=start(audit,'provider-feature','baseline','curl -X POST http://localhost/provider'); assert rc==0
  print('PASS: startup, runtime identity, environment isolation, and dependency-sensitive repro gates are mechanically enforced')
  return 0
if __name__=='__main__': raise SystemExit(main())
