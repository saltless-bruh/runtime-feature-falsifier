#!/usr/bin/env python3
"""v2.9.5: lifecycle immutability, workspace transactions, genesis, and validated readers."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime-feature-falsifier" / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "teaching-corpus"))
import auditctl  # noqa: E402
import test_v290_structured_output as v290  # noqa: E402

V294_GIT_FIXTURE = ROOT / "tests" / "fixtures" / "v294-git-sealed"
AUDITCTL = ROOT / "runtime-feature-falsifier" / "scripts" / "auditctl.py"


def call(fn, **kwargs):
    ns = SimpleNamespace(format="json", **kwargs)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(ns)
    return rc, json.loads(buf.getvalue()) if buf.getvalue().strip() else {}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git_project(path: Path, filename: str = "script.sh", content: str = "#!/bin/sh\necho hi\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "rff@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "RFF"], check=True)
    target = path / filename
    target.write_text(content, encoding="utf-8")
    if filename.endswith(".sh"):
        os.chmod(target, 0o644)
    subprocess.run(["git", "-C", str(path), "add", filename], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "baseline"], check=True)
    return target


def simulate_v294_partial_migration(audit: Path, project: Path) -> None:
    paths = auditctl.audit_paths(audit)
    baseline = auditctl.load_json(paths["baseline"])
    baseline["git_root"] = str(project)
    write_json(paths["baseline"], baseline)
    context = auditctl._legacy_migration_context(paths)
    assert context and context["migration"]["from_producer_version"] == "2.9.4", context
    plan = auditctl.load_json(paths["plan"])
    result = auditctl.load_json(paths["result"])
    manifest = auditctl.load_json(paths["manifest"])
    findings = auditctl.load_json(paths["findings"])
    genesis = auditctl._build_audit_genesis(
        plan, project, baseline, provenance="legacy-baseline-unbound-at-migration"
    )
    result["producer"] = {"name": "runtime-feature-falsifier", "version": auditctl.RFF_VERSION}
    result["audit"]["gate"] = "PASSED"
    manifest["producer"] = dict(result["producer"])
    manifest["inputs"] = auditctl._expected_manifest_inputs(
        paths, genesis_override=genesis, baseline_override=baseline
    )
    manifest["outputs"] = auditctl._expected_manifest_outputs(paths, result, findings)
    manifest["seal_metadata"] = auditctl._build_seal_metadata(
        completed_at=context["completed_at_utc"],
        migration=context["migration"],
        completion_time_provenance=context["completion_time_provenance"],
    )
    write_json(paths["genesis"], genesis)
    write_json(paths["result"], result)
    write_json(paths["manifest"], manifest)
    # Deliberately leave the v2.9.4 seal in place: crash before the seal rename.
    assert auditctl._partial_migration_commit_context(paths) is not None


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # 1. SEALED is enforced by the controller, not merely documented.
        project = root / "sealed-project"; project.mkdir()
        sealed = project / "sealed-run"
        v290.make_run(project, sealed, False)
        rc, gated = call(auditctl.cmd_gate, audit_dir=sealed, require_report=True)
        assert rc == 0 and gated["ok"], gated
        ledger_before = (sealed / "attempts.jsonl").read_bytes()
        result_before = (sealed / "audit-result.json").read_bytes()
        rc, blocked = v290.start(sealed, "baseline")
        assert rc == 2 and any("sealed" in e for e in blocked["errors"]), blocked
        rc, blocked_report = call(auditctl.cmd_report, audit_dir=sealed)
        assert rc == 2 and any("sealed" in e for e in blocked_report["errors"]), blocked_report
        assert (sealed / "attempts.jsonl").read_bytes() == ledger_before
        assert (sealed / "audit-result.json").read_bytes() == result_before

        # 2. Explicit duplicate IDs and corrupt chains fail before append.
        dup_project = root / "dup-project"; dup_project.mkdir()
        dup = dup_project / "run"
        v290.make_run(dup_project, dup, False)
        first_event = json.loads((dup / "attempts.jsonl").read_text().splitlines()[0])
        count_before = len((dup / "attempts.jsonl").read_text().splitlines())
        rc, duplicate = call(
            auditctl.cmd_attempt_start,
            audit_dir=dup,
            feature_id="feature",
            probe_id="environment-start",
            attempt_id=first_event["attempt_id"],
            action="run",
            repro_command="echo runtime-probe",
            precondition=[],
            hypothesis_id=None,
            changed_variable=None,
            retry_reason="duplicate-id regression",
            escalation_stage=None,
        )
        assert rc == 2 and any("duplicate attempt_id" in e for e in duplicate["errors"]), duplicate
        assert len((dup / "attempts.jsonl").read_text().splitlines()) == count_before
        lines = (dup / "attempts.jsonl").read_text().splitlines()
        corrupt = json.loads(lines[0]); corrupt["action"] = "tampered-without-rehash"; lines[0] = json.dumps(corrupt, sort_keys=True)
        (dup / "attempts.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        rc, rejected = v290.start(dup, "baseline")
        assert rc == 2 and any("event_sha256 mismatch" in e for e in rejected["errors"]), rejected
        assert len((dup / "attempts.jsonl").read_text().splitlines()) == count_before

        # 3. Genesis binds the baseline; Git-significant chmod is part of the snapshot.
        source_project = root / "source-project"
        script = git_project(source_project)
        source_audit = source_project / "audit"
        v290.make_run(source_project, source_audit, False)
        baseline = auditctl.load_json(source_audit / "tracked-source-baseline.json")
        assert baseline["snapshot_format"] == "rff-git-worktree-v2"
        assert baseline["files"]["script.sh"]["worktree_mode"] == "100644"
        os.chmod(script, 0o755)
        rc, mode_gate = call(auditctl.cmd_gate, audit_dir=source_audit, require_report=True)
        assert rc == 4 and any("tracked source/test files changed" in e for e in mode_gate["errors"]), mode_gate
        os.chmod(script, 0o644)
        script.write_text("#!/bin/sh\necho changed\n", encoding="utf-8")
        refreshed = auditctl.tracked_source_snapshot(source_project)
        write_json(source_audit / "tracked-source-baseline.json", refreshed)
        rc, baseline_gate = call(auditctl.cmd_gate, audit_dir=source_audit, require_report=True)
        assert rc == 4 and any("immutable audit genesis" in e for e in baseline_gate["errors"]), baseline_gate
        rc, report_rewrite = call(auditctl.cmd_report, audit_dir=source_audit)
        assert rc == 2 and any("immutable audit genesis" in e for e in report_rewrite["errors"]), report_rewrite

        # 4. compare consumes only fully validated, currently sealed audits (including history).
        compare_project = root / "compare-project"; compare_project.mkdir()
        baseline_run = compare_project / "baseline"; current_run = compare_project / "current"
        v290.make_run(compare_project, baseline_run, True); assert call(auditctl.cmd_gate, audit_dir=baseline_run, require_report=True)[0] == 0
        v290.make_run(compare_project, current_run, False); assert call(auditctl.cmd_gate, audit_dir=current_run, require_report=True)[0] == 0
        original_result = auditctl.load_json(baseline_run / "audit-result.json")
        tampered = json.loads(json.dumps(original_result)); tampered["findings"][0]["fingerprint"] = "sha256:" + "0" * 64
        write_json(baseline_run / "audit-result.json", tampered)
        args = SimpleNamespace(baseline=str(baseline_run), current=str(current_run), history_dir=None, format="json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = auditctl.cmd_compare(args)
        compared = json.loads(buf.getvalue())
        assert rc == 4 and not compared["ok"] and any("baseline:" in e for e in compared["errors"]), compared
        write_json(baseline_run / "audit-result.json", original_result)

        history_root = compare_project / "history"; history_root.mkdir()
        history_run = history_root / "old"
        v290.make_run(compare_project, history_run, True); assert call(auditctl.cmd_gate, audit_dir=history_run, require_report=True)[0] == 0
        hist_result = auditctl.load_json(history_run / "audit-result.json")
        hist_result["findings"][0]["fingerprint"] = "sha256:" + "1" * 64
        write_json(history_run / "audit-result.json", hist_result)
        args.history_dir = str(history_root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = auditctl.cmd_compare(args)
        history_compare = json.loads(buf.getvalue())
        assert rc == 4 and not history_compare["ok"] and any("history:old" in e for e in history_compare["errors"]), history_compare

        # 5. The audit-wide lock serializes read-check-write: two FINISH writers cannot both win.
        race_project = root / "race-project"; race_project.mkdir()
        race = race_project / "run"
        v290.make_run(race_project, race, False)
        rc, started = call(
            auditctl.cmd_attempt_start,
            audit_dir=race,
            feature_id="feature",
            probe_id="baseline",
            attempt_id="race-id",
            action="run",
            repro_command="echo runtime-probe",
            precondition=[],
            hypothesis_id=None,
            changed_variable=None,
            retry_reason="concurrency regression",
            escalation_stage=None,
        )
        assert rc == 0, started
        cmd = [
            sys.executable, str(AUDITCTL), "attempt-finish",
            "--audit-dir", str(race), "--attempt-id", "race-id",
            "--observed", "race finish", "--side-effect-check", "effect observed",
            "--result", "SURVIVED", "--failure-pattern", "NONE_OBSERVED",
            "--confidence", "HIGH", "--severity", "MEDIUM", "--notes", "race",
        ]
        p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        p2 = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        o1, e1 = p1.communicate(timeout=30); o2, e2 = p2.communicate(timeout=30)
        assert sorted([p1.returncode, p2.returncode]) == [0, 2], (p1.returncode, o1, e1, p2.returncode, o2, e2)
        race_events = auditctl.read_events(race / "attempts.jsonl")
        assert auditctl._ledger_integrity_errors(race / "attempts.jsonl", race_events) == []
        _attempts, race_fold_errors = auditctl.fold_attempts(race_events)
        assert race_fold_errors == [], race_fold_errors

        # 6. A failed retry of a partial migration must never promote/install a current seal.
        assert V294_GIT_FIXTURE.exists(), "missing genuine v2.9.4 Git-backed sealed fixture"
        migration_project = root / "migration-project"
        tracked = git_project(migration_project, filename="tracked.txt", content="original\n")
        migration = migration_project / "audit"
        shutil.copytree(V294_GIT_FIXTURE, migration)
        simulate_v294_partial_migration(migration, migration_project)
        old_seal = auditctl.load_json(migration / ".complete.json")
        assert old_seal["producer"]["version"] == "2.9.4"
        tracked.write_text("changed\n", encoding="utf-8")
        rc, failed = call(auditctl.cmd_gate, audit_dir=migration, require_report=True)
        assert rc == 4 and not failed["ok"] and any("tracked source/test files changed" in e for e in failed["errors"]), failed
        assert auditctl.load_json(migration / ".complete.json")["producer"]["version"] == "2.9.4"
        rc, presented = call(auditctl.cmd_present, audit_dir=migration, presentation="json")
        assert rc == 4 and not presented["ok"], presented
        tracked.write_text("original\n", encoding="utf-8")
        rc, recovered = call(auditctl.cmd_gate, audit_dir=migration, require_report=True)
        assert rc == 0 and recovered["ok"], recovered
        assert auditctl.load_json(migration / ".complete.json")["producer"]["version"] == auditctl.RFF_VERSION == "2.9.5"
        assert auditctl.validate_structured_outputs(auditctl.audit_paths(migration)) == []

        # 7. The pre-seal directory durability barrier occurs before the seal rename.
        commit_dir = root / "commit-order"; commit_dir.mkdir()
        cp = auditctl.audit_paths(commit_dir)
        events: list[tuple[str, str]] = []
        original_replace = auditctl.os.replace
        original_fsync_dir = auditctl._fsync_directory
        def recording_replace(src, dst):
            events.append(("replace", Path(dst).name))
            return original_replace(src, dst)
        def recording_fsync_dir(path):
            events.append(("fsync-dir", Path(path).name))
            return original_fsync_dir(path)
        auditctl.os.replace = recording_replace
        auditctl._fsync_directory = recording_fsync_dir
        try:
            auditctl._commit_gate_files(
                cp,
                {"result": True},
                {"manifest": True},
                {"seal": True},
                genesis={"genesis": True},
            )
        finally:
            auditctl.os.replace = original_replace
            auditctl._fsync_directory = original_fsync_dir
        seal_replace = events.index(("replace", ".complete.json"))
        preseal_fsync = max(i for i, event in enumerate(events[:seal_replace]) if event[0] == "fsync-dir")
        assert preseal_fsync < seal_replace
        assert events[-1][0] == "fsync-dir", events

    print("PASS: v2.9.5 enforces sealed immutability, atomic workspace transitions, genesis source integrity, validated comparison, and seal-safe recovery")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
