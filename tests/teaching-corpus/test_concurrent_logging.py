#!/usr/bin/env python3
"""Regression: concurrent auditctl writers serialize without corrupting the JSONL chain."""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CTL=ROOT/"runtime-feature-falsifier"/"scripts"/"auditctl.py"
def run(*a): return subprocess.run([sys.executable,str(CTL),*a],text=True,capture_output=True)
def main()->int:
  with tempfile.TemporaryDirectory() as td:
    project=Path(td)/"project"; project.mkdir(); audit=project/".runtime-feature-audit"
    assert run("init","--audit-dir",str(audit),"--target-root",str(project)).returncode==0
    plan=json.loads((audit/"audit-plan.json").read_text()); plan["target"]["startup_path"]="concurrent teaching startup"
    probes=[{"probe_id":"environment-start","probe_intent":"environment_start","contract_relation":"ENVIRONMENT","expected":"runtime starts","effect_checks":[],"required":True},
            {"probe_id":"baseline","probe_intent":"baseline_valid","contract_relation":"VALID","expected":"baseline works","effect_checks":[],"required":True}]
    probes += [{"probe_id":f"parallel-{i}","probe_intent":"valid_variation","contract_relation":"VALID","expected":f"parallel probe {i} works","effect_checks":[],"required":False} for i in range(12)]
    plan["features"]=[{"feature_id":"parallel-feature","claim":"parallel feature works","claim_source":"teaching","entry_points":["CLI"],"expected_end_effects":["observable completion"],"input_sensitive":False,"stateful":False,"dependency_sensitive":False,"probes":probes}]
    (audit/"audit-plan.json").write_text(json.dumps(plan,indent=2)+"\n"); assert run("validate-plan","--audit-dir",str(audit)).returncode==0
    # startup first
    s=run("attempt-start","--audit-dir",str(audit),"--feature-id","parallel-feature","--probe-id","environment-start","--repro-command","parallel-runtime --serve"); aid=json.loads(s.stdout)["attempt_id"]
    assert run("attempt-finish","--audit-dir",str(audit),"--attempt-id",aid,"--observed","reachable","--result","SURVIVED","--failure-pattern","NONE_OBSERVED","--confidence","HIGH").returncode==0
    # baseline first too, so final gate can pass if wanted
    s=run("attempt-start","--audit-dir",str(audit),"--feature-id","parallel-feature","--probe-id","baseline"); aid=json.loads(s.stdout)["attempt_id"]
    assert run("attempt-finish","--audit-dir",str(audit),"--attempt-id",aid,"--observed","works","--result","SURVIVED","--failure-pattern","NONE_OBSERVED","--confidence","HIGH").returncode==0
    procs=[]
    for i in range(12):
      procs.append(subprocess.Popen([sys.executable,str(CTL),"attempt-start","--audit-dir",str(audit),"--feature-id","parallel-feature","--probe-id",f"parallel-{i}","--attempt-id",f"parallel-att-{i}"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
    for p in procs:
      out,err=p.communicate(); assert p.returncode==0, out+err
    # verify chain immediately; summary reads and validates folding
    summary=run("summary","--audit-dir",str(audit)); assert summary.returncode==0, summary.stdout
    events=[json.loads(x) for x in (audit/"attempts.jsonl").read_text().splitlines() if x.strip()]
    seq=[e["sequence"] for e in events]; assert seq==list(range(1,len(events)+1)), seq
    # close all parallel attempts concurrently too
    procs=[]
    for i in range(12):
      procs.append(subprocess.Popen([sys.executable,str(CTL),"attempt-finish","--audit-dir",str(audit),"--attempt-id",f"parallel-att-{i}","--observed",f"parallel {i} complete","--result","SURVIVED","--failure-pattern","NONE_OBSERVED","--confidence","HIGH"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
    for p in procs:
      out,err=p.communicate(); assert p.returncode==0, out+err
    events=[json.loads(x) for x in (audit/"attempts.jsonl").read_text().splitlines() if x.strip()]
    seq=[e["sequence"] for e in events]; assert seq==list(range(1,len(events)+1)), seq
    assert not [e for e in events if not e.get("event_sha256")]
  print("PASS: concurrent controller writers serialize cleanly and preserve a contiguous hash-chain sequence")
  return 0
if __name__=="__main__": raise SystemExit(main())
