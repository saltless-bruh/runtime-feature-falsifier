#!/usr/bin/env python3
"""Regression: batch logging preserves two-phase chronology and is atomic on validation failure."""
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CTL = ROOT / "runtime-feature-falsifier" / "scripts" / "auditctl.py"

def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CTL), *args], text=True, capture_output=True)

def write_plan(audit: Path) -> None:
    plan = json.loads((audit / "audit-plan.json").read_text())
    plan["target"]["startup_path"] = "start batch teaching runtime"
    plan["target"]["runtime_identity_expectation"] = "batch teaching runtime"
    plan["target"]["collision_surfaces"] = ["temporary audit directory"]
    plan["features"] = [{
        "feature_id": "batch-feature",
        "claim": "batch feature works through its public runtime surface",
        "claim_source": "teaching contract",
        "entry_points": ["CLI batch-feature"],
        "expected_end_effects": ["command produces expected public output"],
        "input_sensitive": False,
        "stateful": False,
        "dependency_sensitive": False,
        "probes": [
            {"probe_id":"environment-start","probe_intent":"environment_start","contract_relation":"ENVIRONMENT","expected":"runtime starts","effect_checks":[],"required":True},
            {"probe_id":"runtime-identity","probe_intent":"runtime_identity","contract_relation":"ENVIRONMENT","expected":"batch teaching runtime identity matches","effect_checks":["process identity matches"],"required":True},
            {"probe_id":"environment-collision","probe_intent":"environment_collision","contract_relation":"ENVIRONMENT","expected":"audit environment isolated","effect_checks":["no competing consumer shares state"],"required":True},
            {"probe_id":"baseline","probe_intent":"baseline_valid","contract_relation":"VALID","expected":"baseline completes","effect_checks":[],"required":True},
            {"probe_id":"variation","probe_intent":"valid_variation","contract_relation":"VALID","expected":"variation completes","effect_checks":[],"required":False},
        ],
    }]
    (audit / "audit-plan.json").write_text(json.dumps(plan, indent=2)+"\n")

def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        project = Path(td) / "project"; project.mkdir()
        audit = project / ".runtime-feature-audit"
        init = run("init","--audit-dir",str(audit),"--target-root",str(project),"--project-name","batch-test")
        assert init.returncode == 0, init.stdout
        write_plan(audit)
        valid = run("validate-plan","--audit-dir",str(audit)); assert valid.returncode == 0, valid.stdout

        bad = {
            "schema_version":"1.0","phase":"START","attempts":[
                {"attempt_id":"bad-good","feature_id":"batch-feature","probe_id":"baseline"},
                {"attempt_id":"bad-missing","feature_id":"batch-feature"}
            ]
        }
        bad_path = project / "bad.json"; bad_path.write_text(json.dumps(bad))
        rejected = run("attempt-batch","--audit-dir",str(audit),"--input",str(bad_path))
        assert rejected.returncode != 0, rejected.stdout
        assert not (audit / "attempts.jsonl").exists() or not (audit / "attempts.jsonl").read_text().strip(), "invalid batch partially mutated log"

        startup = run("attempt-start","--audit-dir",str(audit),"--feature-id","batch-feature","--probe-id","environment-start","--attempt-id","batch-startup","--action","start runtime","--repro-command","batch-runtime --serve")
        assert startup.returncode == 0, startup.stdout
        startup_finish = run("attempt-finish","--audit-dir",str(audit),"--attempt-id","batch-startup","--observed","runtime reachable","--side-effect-check","process accepted connection","--result","SURVIVED","--failure-pattern","NONE_OBSERVED","--confidence","HIGH")
        assert startup_finish.returncode == 0, startup_finish.stdout


        for pid, observed, side in [("runtime-identity","batch runtime identity matches","process identity matched"),("environment-collision","batch audit environment isolated","no competing consumer")]:
            pre=run("attempt-start","--audit-dir",str(audit),"--feature-id","batch-feature","--probe-id",pid,"--repro-command",f"check {pid}")
            assert pre.returncode == 0, pre.stdout
            pre_id=json.loads(pre.stdout)["attempt_id"]
            assert run("attempt-finish","--audit-dir",str(audit),"--attempt-id",pre_id,"--observed",observed,"--side-effect-check",side,"--result","SURVIVED","--failure-pattern","NONE_OBSERVED","--confidence","HIGH").returncode == 0

        starts = {
            "schema_version":"1.0","phase":"START","attempts":[
                {"attempt_id":"batch-baseline","feature_id":"batch-feature","probe_id":"baseline","action":"invoke public baseline"},
                {"attempt_id":"batch-variation","feature_id":"batch-feature","probe_id":"variation","action":"invoke public variation"}
            ]
        }
        sp = project / "starts.json"; sp.write_text(json.dumps(starts))
        sr = run("attempt-batch","--audit-dir",str(audit),"--input",str(sp)); assert sr.returncode == 0, sr.stdout
        sobj=json.loads(sr.stdout); assert sobj["count"] == 2 and sobj["chain_heads"]["attempts"]

        finishes = {
            "schema_version":"1.0","phase":"FINISH","attempts":[
                {"attempt_id":"batch-baseline","observed":"public baseline completed","result":"SURVIVED","failure_pattern":"NONE_OBSERVED","confidence":"HIGH"},
                {"attempt_id":"batch-variation","observed":"public variation completed","result":"SURVIVED","failure_pattern":"NONE_OBSERVED","confidence":"HIGH"}
            ]
        }
        fp=project/"finishes.json"; fp.write_text(json.dumps(finishes))
        fr=run("attempt-batch","--audit-dir",str(audit),"--input",str(fp)); assert fr.returncode == 0, fr.stdout
        fobj=json.loads(fr.stdout); assert fobj["count"] == 2 and fobj["chain_heads"]["attempts"]

        events=[json.loads(x) for x in (audit/"attempts.jsonl").read_text().splitlines() if x.strip()]
        assert [e["sequence"] for e in events] == list(range(1, len(events)+1))
        assert len(events) == 10
        run("report","--audit-dir",str(audit))
        gate=run("gate","--audit-dir",str(audit),"--require-report"); assert gate.returncode == 0, gate.stdout
    print("PASS: batch logging is two-phase, hash-chained, atomic on validation failure, and gate-compatible")
    return 0
if __name__ == "__main__": raise SystemExit(main())
