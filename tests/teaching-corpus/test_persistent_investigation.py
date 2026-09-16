#!/usr/bin/env python3
"""Regression: persistent investigation must gain information and resolve hypotheses."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

DIST_ROOT = Path(__file__).resolve().parents[2]
CTL = DIST_ROOT / "runtime-feature-falsifier" / "scripts" / "auditctl.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CTL), *args], text=True, capture_output=True)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        project = Path(td) / "project"
        project.mkdir()
        audit = project / ".runtime-feature-audit"
        init = run(
            "init", "--audit-dir", str(audit), "--target-root", str(project),
            "--project-name", "persistent-investigation", "--scope", "upload",
        )
        assert init.returncode == 0, init.stderr + init.stdout
        plan_path = audit / "audit-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["target"]["startup_path"] = "teaching uploader runtime"
        plan["features"] = [{
            "feature_id": "upload-image",
            "claim": "Different valid images are actually uploaded and retrievable",
            "claim_source": "teaching contract",
            "entry_points": ["POST /upload"],
            "expected_end_effects": ["uploaded bytes can be retrieved"],
            "input_sensitive": False,
            "stateful": False,
            "dependency_sensitive": False,
            "probes": [{
                "probe_id": "environment-start",
                "probe_intent": "environment_start",
                "contract_relation": "ENVIRONMENT",
                "expected": "teaching runtime starts",
                "effect_checks": [],
                "required": True,
            }, {
                "probe_id": "upload-baseline",
                "probe_intent": "baseline_valid",
                "contract_relation": "VALID",
                "expected": "upload persists the supplied image",
                "effect_checks": ["read-back exists"],
                "required": True,
            }],
        }]
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
        valid = run("validate-plan", "--audit-dir", str(audit))
        assert valid.returncode == 0, valid.stderr + valid.stdout

        startup = run(
            "attempt-start", "--audit-dir", str(audit),
            "--feature-id", "upload-image", "--probe-id", "environment-start",
            "--action", "start teaching runtime", "--repro-command", "python teaching runtime",
        )
        assert startup.returncode == 0, startup.stdout
        startup_id = json.loads(startup.stdout)["attempt_id"]
        startup_finish = run(
            "attempt-finish", "--audit-dir", str(audit), "--attempt-id", startup_id,
            "--observed", "teaching runtime available", "--side-effect-check", "runtime accepted probe",
            "--result", "SURVIVED", "--failure-pattern", "NONE_OBSERVED", "--confidence", "HIGH",
        )
        assert startup_finish.returncode == 0, startup_finish.stdout

        start1 = run(
            "attempt-start", "--audit-dir", str(audit),
            "--feature-id", "upload-image", "--probe-id", "upload-baseline",
            "--action", "upload image A",
        )
        assert start1.returncode == 0, start1.stdout
        aid1 = json.loads(start1.stdout)["attempt_id"]
        finish1 = run(
            "attempt-finish", "--audit-dir", str(audit), "--attempt-id", aid1,
            "--observed", "success response returned a canned object",
            "--side-effect-check", "read-back did not depend on uploaded bytes",
            "--result", "FALSIFIED", "--failure-pattern", "HARDCODED_SUSPECTED",
            "--confidence", "MEDIUM",
        )
        assert finish1.returncode == 0, finish1.stdout

        opened = run(
            "hypothesis-open", "--audit-dir", str(audit),
            "--feature-id", "upload-image",
            "--statement", "uploader ignores input bytes and returns a canned artifact",
            "--trigger-attempt-id", aid1,
            "--next-probe", "upload distinct image B and compare read-back",
            "--max-attempts", "1",
        )
        assert opened.returncode == 0, opened.stdout
        hid = json.loads(opened.stdout)["hypothesis_id"]

        # Blind identical retry must be rejected.
        blind = run(
            "attempt-start", "--audit-dir", str(audit),
            "--feature-id", "upload-image", "--probe-id", "upload-baseline",
            "--hypothesis-id", hid,
        )
        assert blind.returncode != 0, "blind repeated probe was incorrectly accepted"
        assert "changed-variable" in blind.stdout or "retry-reason" in blind.stdout, blind.stdout

        # Information-gaining retry is accepted and consumes the hypothesis budget.
        start2 = run(
            "attempt-start", "--audit-dir", str(audit),
            "--feature-id", "upload-image", "--probe-id", "upload-baseline",
            "--hypothesis-id", hid,
            "--changed-variable", "input bytes changed from image A to distinct image B",
            "--escalation-stage", "3",
        )
        assert start2.returncode == 0, start2.stdout
        aid2 = json.loads(start2.stdout)["attempt_id"]
        finish2 = run(
            "attempt-finish", "--audit-dir", str(audit), "--attempt-id", aid2,
            "--observed", "distinct image B produced the same persisted bytes",
            "--side-effect-check", "read-back hash matched image A result rather than image B",
            "--result", "FALSIFIED", "--failure-pattern", "HARDCODED",
            "--confidence", "HIGH",
        )
        assert finish2.returncode == 0, finish2.stdout

        over_budget = run(
            "attempt-start", "--audit-dir", str(audit),
            "--feature-id", "upload-image", "--probe-id", "upload-baseline",
            "--hypothesis-id", hid,
            "--changed-variable", "third distinct image C",
        )
        assert over_budget.returncode != 0, "hypothesis attempt budget was not enforced"
        assert "budget exhausted" in over_budget.stdout.lower(), over_budget.stdout

        supported = run(
            "hypothesis-update", "--audit-dir", str(audit),
            "--hypothesis-id", hid, "--status", "SUPPORTED",
            "--evidence-for", "two materially distinct images yielded identical persisted bytes",
            "--next-probe", "classify implementation after runtime counterexample",
            "--changed-variable", "observation moved from response to persisted read-back",
        )
        assert supported.returncode == 0, supported.stdout

        run("report", "--audit-dir", str(audit))
        unresolved_gate = run("gate", "--audit-dir", str(audit), "--require-report")
        assert unresolved_gate.returncode != 0, "gate accepted unresolved SUPPORTED hypothesis"
        assert "unresolved hypothesis" in unresolved_gate.stdout, unresolved_gate.stdout

        confirmed = run(
            "hypothesis-update", "--audit-dir", str(audit),
            "--hypothesis-id", hid, "--status", "CONFIRMED",
            "--evidence-for", "runtime differential/read-back evidence rules out input-sensitive persistence",
            "--notes", "counterexample confirmed; source inspection may classify but is not required for runtime verdict",
        )
        assert confirmed.returncode == 0, confirmed.stdout

        stale_gate = run("gate", "--audit-dir", str(audit), "--require-report")
        assert stale_gate.returncode != 0, "gate accepted report generated before final hypothesis update"
        assert "stale" in stale_gate.stdout.lower(), stale_gate.stdout

        report = run("report", "--audit-dir", str(audit))
        assert report.returncode == 0, report.stdout
        gate = run("gate", "--audit-dir", str(audit), "--require-report")
        assert gate.returncode == 0, gate.stderr + gate.stdout
        result = json.loads(gate.stdout)
        assert result["investigation"]["active_count"] == 0
        assert result["investigation"]["hypothesis_count"] == 1

    print("PASS: persistent investigation rejects blind retries, enforces budget, and blocks unresolved hypotheses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
