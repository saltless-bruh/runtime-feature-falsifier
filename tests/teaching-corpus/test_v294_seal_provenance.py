#!/usr/bin/env python3
"""Manifest-bound seal metadata regression carried through v2.9.5."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime-feature-falsifier" / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "teaching-corpus"))
import auditctl  # noqa: E402
import test_v290_structured_output as v290  # noqa: E402

V293_FIXTURE = ROOT / "tests" / "fixtures" / "v293-sealed"


def call(fn, **kwargs):
    ns = SimpleNamespace(format="json", **kwargs)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(ns)
    return rc, json.loads(buf.getvalue()) if buf.getvalue().strip() else {}


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "project"
        project.mkdir()

        # Native current release: seal-local metadata must be identical to manifest.seal_metadata.
        audit = project / "native-v294"
        v290.make_run(project, audit, False)
        paths = auditctl.audit_paths(audit)
        rc, gated = call(auditctl.cmd_gate, audit_dir=audit, require_report=True)
        assert rc == 0 and gated["ok"], gated
        result = auditctl.load_json(paths["result"])
        manifest = auditctl.load_json(paths["manifest"])
        seal = auditctl.load_json(paths["complete"])
        assert result["producer"]["version"] == auditctl.RFF_VERSION == "2.9.5"
        assert manifest["seal_metadata"] == auditctl._seal_metadata_from_seal(seal)
        assert seal["completion_time_provenance"] == "v2.9.5-gate"
        assert "migration" not in seal and "migration" not in manifest["seal_metadata"]
        assert auditctl.validate_structured_outputs(paths) == []

        # The v2.9.3 gap: changing only seal-local descriptive provenance must now fail.
        original_seal = copy.deepcopy(seal)
        mutations = []
        bad = copy.deepcopy(seal); bad["completed_at_utc"] = "2000-01-01T00:00:00+00:00"; mutations.append((bad, "metadata does not match"))
        bad = copy.deepcopy(seal); bad["sealed_at_utc"] = "2000-01-01T00:00:01+00:00"; mutations.append((bad, "metadata does not match"))
        bad = copy.deepcopy(seal); bad["next_run_policy"] = "tampered"; mutations.append((bad, "metadata does not match"))
        for bad, token in mutations:
            write_json(paths["complete"], bad)
            errors = auditctl.validate_structured_outputs(paths)
            assert any(token in e for e in errors), errors
            write_json(paths["complete"], original_seal)

        # Manifest-only metadata tampering invalidates the seal's manifest subject digest.
        original_manifest = copy.deepcopy(manifest)
        bad_manifest = copy.deepcopy(manifest)
        bad_manifest["seal_metadata"]["next_run_policy"] = "manifest-tampered"
        write_json(paths["manifest"], bad_manifest)
        errors = auditctl.validate_structured_outputs(paths)
        assert any("manifest digest mismatch" in e or "metadata does not match" in e for e in errors), errors
        write_json(paths["manifest"], original_manifest)

        # Timestamp strings are semantic UTC timestamps, not arbitrary non-empty text.
        bad_seal = copy.deepcopy(original_seal)
        bad_manifest = copy.deepcopy(original_manifest)
        bad_seal["sealed_at_utc"] = "not-a-time"
        bad_manifest["seal_metadata"]["sealed_at_utc"] = "not-a-time"
        write_json(paths["manifest"], bad_manifest)
        bad_seal["subjects"]["result_manifest_jcs_sha256"] = auditctl.canonical_sha256(bad_manifest)
        write_json(paths["complete"], bad_seal)
        errors = auditctl.validate_structured_outputs(paths)
        assert any("sealed_at_utc" in e for e in errors), errors
        write_json(paths["manifest"], original_manifest)
        write_json(paths["complete"], original_seal)

        # Genuine native v2.9.3 seals remain migration inputs, never current seals.
        assert V293_FIXTURE.exists(), "missing genuine v2.9.3 sealed fixture"
        legacy = project / "v293-migration"
        shutil.copytree(V293_FIXTURE, legacy)
        lp = auditctl.audit_paths(legacy)
        old_result = auditctl.load_json(lp["result"])
        old_seal = auditctl.load_json(lp["complete"])
        assert old_result["producer"]["version"] == "2.9.3"
        old_keys = set(old_seal)
        assert old_keys == auditctl.LEGACY_V293_SEAL_KEYS or old_keys == (auditctl.LEGACY_V293_SEAL_KEYS | {"migration"})
        errors = auditctl.validate_structured_outputs(lp)
        assert errors == [auditctl.LEGACY_MIGRATION_MESSAGE], errors
        rc, migrated = call(auditctl.cmd_gate, audit_dir=legacy, require_report=True)
        assert rc == 0 and migrated["ok"], migrated
        result2 = auditctl.load_json(lp["result"])
        manifest2 = auditctl.load_json(lp["manifest"])
        seal2 = auditctl.load_json(lp["complete"])
        assert result2["producer"]["version"] == "2.9.5"
        assert seal2["completed_at_utc"] == old_seal["completed_at_utc"]
        assert seal2["completion_time_provenance"] == "v2.9.3-seal-unbound"
        assert seal2["migration"]["from_producer_version"] == "2.9.3"
        assert seal2["migration"]["from_format"] == "v2.9.3-digest-seal"
        assert manifest2["seal_metadata"] == auditctl._seal_metadata_from_seal(seal2)
        assert auditctl.validate_structured_outputs(lp) == []

        # A v2.9.3 seal timestamp is inherited as an explicitly unbound historical claim.
        assert seal2["completion_time_provenance"].endswith("-unbound")

        # Migration itself is seal-last crash recoverable: simulate result+manifest promotion
        # while the old v2.9.3 seal is still the commit record. Ordinary readers reject;
        # gate alone reconstructs the current seal after proving authoritative state.
        crash = project / "v293-migration-partial"
        shutil.copytree(V293_FIXTURE, crash)
        cp = auditctl.audit_paths(crash)
        context = auditctl._legacy_migration_context(cp)
        assert context and context["completion_time_provenance"] == "v2.9.3-seal-unbound"
        cr = auditctl.load_json(cp["result"]); cm = auditctl.load_json(cp["manifest"])
        cr["producer"] = {"name": "runtime-feature-falsifier", "version": auditctl.RFF_VERSION}
        cr["audit"]["gate"] = "PASSED"
        cm["producer"] = dict(cr["producer"])
        baseline = auditctl.load_json(cp["baseline"])
        plan = auditctl.load_json(cp["plan"])
        baseline_root = Path(baseline.get("git_root") or crash.parent)
        genesis = auditctl._build_audit_genesis(
            plan,
            baseline_root,
            baseline,
            provenance="legacy-baseline-unbound-at-migration",
        )
        cm["inputs"] = auditctl._expected_manifest_inputs(
            cp,
            genesis_override=genesis,
            baseline_override=baseline,
        )
        findings_doc = auditctl.load_json(cp["findings"])
        cm["outputs"] = auditctl._expected_manifest_outputs(cp, cr, findings_doc)
        cm["seal_metadata"] = auditctl._build_seal_metadata(
            completed_at=context["completed_at_utc"],
            migration=context["migration"],
            completion_time_provenance=context["completion_time_provenance"],
        )
        write_json(cp["genesis"], genesis)
        write_json(cp["result"], cr); write_json(cp["manifest"], cm)
        assert auditctl._partial_migration_commit_context(cp) is not None
        assert auditctl.validate_structured_outputs(cp), "partial migration unexpectedly accepted"
        rc, recovered = call(auditctl.cmd_gate, audit_dir=crash, require_report=True)
        assert rc == 0 and recovered["ok"], recovered
        recovered_seal = auditctl.load_json(cp["complete"])
        recovered_manifest = auditctl.load_json(cp["manifest"])
        assert recovered_seal["completion_time_provenance"] == "v2.9.3-seal-unbound"
        assert recovered_manifest["seal_metadata"] == auditctl._seal_metadata_from_seal(recovered_seal)
        assert auditctl.validate_structured_outputs(cp) == []

        # Re-running gate on an already-current seal is idempotent and must not rewrite time/provenance.
        before = copy.deepcopy(recovered_seal)
        rc, repeated = call(auditctl.cmd_gate, audit_dir=crash, require_report=True)
        assert rc == 0 and repeated["ok"], repeated
        assert auditctl.load_json(cp["complete"]) == before

    print("PASS: manifest-bound seal provenance, UTC timestamps, v2.9.3 migration, and partial recovery remain correct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
