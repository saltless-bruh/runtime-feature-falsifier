#!/usr/bin/env python3
"""Regression: SYSTEM mode must not silently drop an in-scope discovered feature."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

DIST_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = DIST_ROOT / "runtime-feature-falsifier"
CTL = SKILL_ROOT / "scripts" / "auditctl.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CTL), *args], text=True, capture_output=True)


def feature(fid: str, *, include_startup: bool = False) -> dict:
    return {
        "feature_id": fid,
        "claim": f"{fid} performs its advertised runtime behavior",
        "claim_source": "system teaching inventory",
        "entry_points": [f"public:{fid}"],
        "expected_end_effects": [f"{fid} end effect is observable"],
        "input_sensitive": False,
        "stateful": False,
        "dependency_sensitive": False,
        "probes": ([{
            "probe_id": "environment-start",
            "probe_intent": "environment_start",
            "contract_relation": "ENVIRONMENT",
            "entry_point": "system startup",
            "expected": "system runtime starts",
            "effect_checks": [],
            "required": True,
        }] if include_startup else []) + [{
            "probe_id": f"{fid}-baseline",
            "probe_intent": "baseline_valid",
            "contract_relation": "VALID",
            "entry_point": f"public:{fid}",
            "expected": f"{fid} completes its advertised baseline behavior",
            "effect_checks": [],
            "required": True,
        }],
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        project = Path(td) / "project"
        project.mkdir()
        audit = project / ".runtime-feature-audit"
        proc = run(
            "init", "--audit-dir", str(audit), "--target-root", str(project),
            "--project-name", "system-gate-teaching", "--environment", "local",
            "--scope", "whole project", "--mode", "system",
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout

        plan_path = audit / "audit-plan.json"
        inventory_path = audit / "feature-inventory.json"
        plan = json.loads(plan_path.read_text())
        plan["target"]["startup_path"] = "system teaching startup"
        plan["features"] = [feature("feature-a", include_startup=True)]
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")

        inventory = {
            "schema_version": "1.0",
            "audit_id": plan["audit_id"],
            "discovery_status": "COMPLETE",
            "discovery_sources": [
                {"kind": "product-docs", "location": "README feature list"},
                {"kind": "runtime-surface", "location": "public CLI/API discovery"},
            ],
            "discovery_blockers": [],
            "workflow_coverage": {"applicable": False, "reason": "Teaching features are independent."},
            "features": [
                {"feature_id": "feature-a", "kind": "CAPABILITY", "claim": "A", "claim_sources": ["docs"], "surfaces": ["public:a"], "disposition": "IN_SCOPE"},
                {"feature_id": "feature-b", "kind": "CAPABILITY", "claim": "B", "claim_sources": ["docs"], "surfaces": ["public:b"], "disposition": "IN_SCOPE"},
            ],
        }
        inventory_path.write_text(json.dumps(inventory, indent=2) + "\n")

        incomplete = run("validate-plan", "--audit-dir", str(audit))
        assert incomplete.returncode != 0, "SYSTEM validation incorrectly accepted a sampled plan"
        assert "feature-b" in incomplete.stdout and "missing from audit plan" in incomplete.stdout, incomplete.stdout

        plan["features"].append(feature("feature-b"))
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
        complete = run("validate-plan", "--audit-dir", str(audit))
        assert complete.returncode == 0, complete.stderr + complete.stdout
        result = json.loads(complete.stdout)
        assert result["system_inventory"]["unmapped"] == []
        assert result["system_inventory"]["in_scope_count"] == 2

    print("PASS: SYSTEM inventory gate rejects representative sampling and requires every in-scope discovered feature")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
