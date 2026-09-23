#!/usr/bin/env python3
"""v2.9.3 regression: migrate a real untouched v2.9.2 sealed audit without rewriting history."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime-feature-falsifier" / "scripts"))
import auditctl  # noqa: E402

FIXTURES = [
    (ROOT / "tests" / "fixtures" / "v292-legacy-sealed", "legacy-completion-marker"),
    (ROOT / "tests" / "fixtures" / "v292-digest-sealed", "digest-bound-pre-v2.9.3-seal"),
]


def call(fn, **kwargs):
    ns = SimpleNamespace(format="json", **kwargs)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(ns)
    return rc, json.loads(buf.getvalue()) if buf.getvalue().strip() else {}


def exercise_fixture(fixture: Path, expected_format: str, td: str) -> None:
    assert fixture.exists(), f"missing historical v2.9.2 fixture: {fixture.name}"
    original_seal = json.loads((fixture / ".complete.json").read_text(encoding="utf-8"))
    original_result = json.loads((fixture / "audit-result.json").read_text(encoding="utf-8"))
    assert original_result["producer"]["version"] == "2.9.2"
    if expected_format == "legacy-completion-marker":
        assert set(original_seal) == auditctl.LEGACY_COMPLETION_MARKER_KEYS
    else:
        assert set(original_seal) == auditctl.LEGACY_DIGEST_SEAL_KEYS

    audit = Path(td) / fixture.name
    shutil.copytree(fixture, audit)
    paths = auditctl.audit_paths(audit)

    # Readers recognize historical formats explicitly instead of calling them either
    # current strong seals or generic corruption.
    errors = auditctl.validate_structured_outputs(paths)
    assert errors == [auditctl.LEGACY_MIGRATION_MESSAGE], errors
    rc, presented = call(auditctl.cmd_present, audit_dir=audit, presentation="json")
    assert rc == 4 and not presented["ok"], presented
    assert any("migrate" in e.lower() for e in presented["errors"]), presented
    rc, policy = call(auditctl.cmd_policy, audit_dir=audit, fail_on=["FALSIFIED"])
    assert rc == 4 and not policy["policy_passed"], policy

    # Gate is the only migration authority. It fully revalidates plan/ledgers/
    # provenance, then emits current producer metadata + the current strong seal.
    rc, gated = call(auditctl.cmd_gate, audit_dir=audit, require_report=True)
    assert rc == 0 and gated["ok"], gated
    assert auditctl.validate_structured_outputs(paths) == []

    result = auditctl.load_json(paths["result"])
    manifest = auditctl.load_json(paths["manifest"])
    seal = auditctl.load_json(paths["complete"])
    assert result["producer"]["version"] == auditctl.RFF_VERSION
    assert manifest["producer"] == result["producer"] == seal["producer"]
    assert seal["canonicalization"] == "RFC8785-JCS-float-free-profile"
    assert seal["completed_at_utc"] == original_seal["completed_at_utc"]
    assert seal["sealed_at_utc"] and seal["sealed_at_utc"] != ""
    expected_provenance = (
        "legacy-marker-unverified"
        if expected_format == "legacy-completion-marker"
        else "pre-v2.9.3-seal-unbound"
    )
    assert seal["completion_time_provenance"] == expected_provenance
    assert seal["migration"] == {
        "from_producer_version": "2.9.2",
        "from_format": expected_format,
    }
    assert manifest["seal_metadata"] == auditctl._seal_metadata_from_seal(seal)

    # Normal readers work after migration.
    rc, presented = call(auditctl.cmd_present, audit_dir=audit, presentation="json")
    assert rc == 0 and presented["audit"]["gate"] == "PASSED", presented
    rc, policy = call(auditctl.cmd_policy, audit_dir=audit, fail_on=["FALSIFIED"])
    assert rc == 0 and policy["policy_passed"], policy
    args = SimpleNamespace(baseline=str(audit), current=str(audit), history_dir=None, format="json")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = auditctl.cmd_compare(args)
    comparison = json.loads(buf.getvalue())
    assert rc == 0 and comparison.get("ok", True), comparison


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        for fixture, expected_format in FIXTURES:
            exercise_fixture(fixture, expected_format, td)

        # A malformed historical marker is corruption, not a migration opportunity.
        fixture = FIXTURES[0][0]
        bad = Path(td) / "bad-legacy"
        shutil.copytree(fixture, bad)
        bad_marker = json.loads((bad / ".complete.json").read_text(encoding="utf-8"))
        bad_marker["audit_id"] = "wrong-audit"
        (bad / ".complete.json").write_text(json.dumps(bad_marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        bad_errors = auditctl.validate_structured_outputs(auditctl.audit_paths(bad))
        assert any("legacy completion marker audit_id does not match" in e for e in bad_errors), bad_errors
        assert not any("run `rff audit gate`" in e for e in bad_errors), bad_errors

    print("PASS: current RFF safely migrates released and pre-release v2.9.2 seals with bound provenance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
