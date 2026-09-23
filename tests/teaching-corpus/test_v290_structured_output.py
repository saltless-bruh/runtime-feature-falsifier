#!/usr/bin/env python3
"""v2.9 regression: canonical result, manifest, presentation, policy, comparison/export."""
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
    with contextlib.redirect_stdout(buf): rc=fn(ns)
    out=json.loads(buf.getvalue()) if buf.getvalue().strip() else {}
    return rc,out

def start(audit,pid):
    return call(auditctl.cmd_attempt_start,audit_dir=audit,feature_id='feature',probe_id=pid,attempt_id=None,action='run',repro_command='echo runtime-probe',precondition=[],hypothesis_id=None,changed_variable=None,retry_reason=None,escalation_stage=None)

def finish(audit,aid,result='SURVIVED',pattern='NONE_OBSERVED',severity='MEDIUM',observed='observed'):
    ev=[]
    if result=='FALSIFIED':
        evidence=audit/'evidence'/f'{aid}.txt'; evidence.write_text('runtime evidence\n'); ev=[f'evidence/{aid}.txt']
    return call(auditctl.cmd_attempt_finish,audit_dir=audit,attempt_id=aid,observed=observed,side_effect_check='runtime effect observed',persistence_check=None,http_status=None,exit_code=0,evidence=ev,result=result,failure_pattern=pattern,confidence='HIGH',severity=severity,notes='')

def make_run(project:Path,audit:Path,falsify:bool):
    rc,_=call(auditctl.cmd_init,audit_dir=audit,audit_id=audit.name,project_name='demo',environment='local',scope='feature',mode='feature',target_root=project,force=False); assert rc==0
    plan=json.loads((audit/'audit-plan.json').read_text())
    plan['target'].update(startup_path='demo --serve',runtime_identity_expectation='demo service',collision_surfaces=['isolated-db'])
    plan['features']=[{'feature_id':'feature','claim':'feature has real effect','claim_source':'acceptance','entry_points':['CLI'],'expected_end_effects':['effect'],'input_sensitive':False,'stateful':True,'dependency_sensitive':False,'probes':[
      {'probe_id':'environment-start','probe_intent':'environment_start','contract_relation':'ENVIRONMENT','expected':'starts','effect_checks':[],'required':True},
      {'probe_id':'runtime-identity','probe_intent':'runtime_identity','contract_relation':'ENVIRONMENT','expected':'identity','effect_checks':['identity'],'required':True},
      {'probe_id':'environment-collision','probe_intent':'environment_collision','contract_relation':'ENVIRONMENT','expected':'isolated','effect_checks':['isolated'],'required':True},
      {'probe_id':'baseline','probe_intent':'baseline_valid','contract_relation':'VALID','expected':'effect','effect_checks':['effect'],'required':True},
      {'probe_id':'persistence','probe_intent':'persistence','contract_relation':'VALID','expected':'persists','effect_checks':['persists'],'required':True},
      {'probe_id':'roundtrip','probe_intent':'round_trip','contract_relation':'VALID','expected':'roundtrip','effect_checks':['roundtrip'],'required':True}
    ]}]
    (audit/'audit-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    assert auditctl.validate_plan_obj(plan)['ok'],auditctl.validate_plan_obj(plan)
    for pid in ['environment-start','runtime-identity','environment-collision','baseline','persistence','roundtrip']:
        rc,out=start(audit,pid); assert rc==0,out
        if falsify and pid=='baseline': rc2,out2=finish(audit,out['attempt_id'],'FALSIFIED','PLACEHOLDER','CRITICAL','HTTP 200 but required state absent')
        else: rc2,out2=finish(audit,out['attempt_id'])
        assert rc2==0,out2
    rc,out=call(auditctl.cmd_report,audit_dir=audit); assert rc==0,out
    return out

def main()->int:
  with tempfile.TemporaryDirectory() as td:
    root=Path(td); project=root/'project'; project.mkdir()
    a1=project/'run1'; make_run(project,a1,True)
    result=json.loads((a1/'audit-result.json').read_text())
    # Runtime and release validation use the same vendored Draft 7 engine.
    schema = auditctl.load_schema("audit-result.schema.json")
    assert auditctl.validate_schema(result, schema, "audit-result") == []
    # Adversarial nested mutations which the old handwritten subset missed.
    import copy
    bad = copy.deepcopy(result); bad["findings"][0]["severity"] = "BANANA"
    assert auditctl.validate_schema(bad, schema, "audit-result")
    bad = copy.deepcopy(result); bad["features"] = "not-an-array"
    assert auditctl.validate_schema(bad, schema, "audit-result")
    bad = copy.deepcopy(result); bad["unexpected_contract_field"] = True
    assert auditctl.validate_schema(bad, schema, "audit-result")
    bad = copy.deepcopy(result); bad["findings"][0]["fingerprint"] = "sha256:not-a-digest"
    assert auditctl.validate_schema(bad, schema, "audit-result")
    assert result['schema_version']=='1.0.0'
    assert result['audit']['gate']=='PENDING'
    assert result['verdict_counts']['FALSIFIED']==1
    assert result['findings'][0]['severity']=='CRITICAL'
    assert result['findings'][0]['implementation_pattern']=='PLACEHOLDER'
    assert result['integrity']['canonicalization']=='RFC8785-JCS-float-free-profile'
    assert not auditctl.validate_structured_outputs(auditctl.audit_paths(a1))
    # The final gate itself must fail closed on nested schema violations, not only
    # the lower-level validator helper. Restore the exact generated result after
    # each mutation so every case exercises the same fresh, unsealed audit.
    original_result_text=(a1/'audit-result.json').read_text()
    mutations=[]
    bad=copy.deepcopy(result); bad['findings'][0]['severity']='BANANA'; mutations.append(bad)
    bad=copy.deepcopy(result); bad['features']='not-an-array'; mutations.append(bad)
    bad=copy.deepcopy(result); bad['unexpected_contract_field']=True; mutations.append(bad)
    bad=copy.deepcopy(result); bad['findings'][0]['fingerprint']='sha256:not-a-digest'; mutations.append(bad)
    for bad in mutations:
        (a1/'audit-result.json').write_text(json.dumps(bad,indent=2)+'\n')
        rc,bad_gate=call(auditctl.cmd_gate,audit_dir=a1,require_report=True)
        assert rc!=0,bad_gate
        assert any('audit-result' in e for e in bad_gate.get('errors',[])),bad_gate
        (a1/'audit-result.json').write_text(original_result_text)
    # Canonical JSON hash must ignore insertion order and use JCS key ordering.
    assert auditctl.canonical_sha256({'b':1,'a':2})==auditctl.canonical_sha256({'a':2,'b':1})
    rc,g=call(auditctl.cmd_gate,audit_dir=a1,require_report=True); assert rc==0,g
    assert json.loads((a1/'audit-result.json').read_text())['audit']['gate']=='PASSED'
    rc,pres=call(auditctl.cmd_present,audit_dir=a1,presentation='json'); assert rc==0 and pres['verdict_counts']['FALSIFIED']==1
    rc,pol=call(auditctl.cmd_policy,audit_dir=a1,fail_on=['FALSIFIED']); assert rc==10 and not pol['policy_passed']
    sarif=a1/'out.sarif'; rc,exp=call(auditctl.cmd_export,audit_dir=a1,export_format='sarif',output=str(sarif)); assert rc==0 and sarif.exists()
    # Tampering with canonical result must be caught by the manifest.
    tampered=json.loads((a1/'audit-result.json').read_text()); tampered['verdict_counts']['FALSIFIED']=99; (a1/'audit-result.json').write_text(json.dumps(tampered))
    errs=auditctl.validate_structured_outputs(auditctl.audit_paths(a1)); assert any('digest mismatch' in e for e in errs),errs

    a2=project/'run2'; make_run(project,a2,False); rc,g=call(auditctl.cmd_gate,audit_dir=a2,require_report=True); assert rc==0,g
    args=SimpleNamespace(baseline=str(a1),current=str(a2),history_dir=None,format='json')
    # Restore run1 before compare.
    # Re-report is forbidden after seal in normal workflow, so use its manifest-backed pre-tamper semantic fingerprint from findings projection.
    r1=json.loads((a1/'findings.json').read_text()); r1res=json.loads((a1/'audit-result.json').read_text()); r1res['verdict_counts']['FALSIFIED']=1; r1res['findings']=r1['findings']; (a1/'audit-result.json').write_text(json.dumps(r1res,indent=2))
    buf=io.StringIO()
    with contextlib.redirect_stdout(buf): rc=auditctl.cmd_compare(args)
    comp=json.loads(buf.getvalue()); assert rc==0 and comp['resolved'],comp
  print('PASS: v2.9 canonical results, JCS manifest, gate promotion, policy, SARIF, tamper detection, and comparison work')
  return 0
if __name__=='__main__': raise SystemExit(main())
