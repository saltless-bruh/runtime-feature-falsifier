#!/usr/bin/env python3
"""v2.9.3: digest-bound seal, gate-state derivation, recovery, producer binding."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime-feature-falsifier" / "scripts"))
import auditctl  # noqa: E402
import test_v290_structured_output as v290  # noqa: E402


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

        # Forged PENDING -> PASSED with recomputed manifest digest must be rejected
        # by ordinary readers, but final gate may validate prerequisites and recover.
        audit = project / "partial-commit"
        v290.make_run(project, audit, False)
        paths = auditctl.audit_paths(audit)
        result = auditctl.load_json(paths["result"])
        manifest = auditctl.load_json(paths["manifest"])
        assert result["audit"]["gate"] == "PENDING" and not paths["complete"].exists()
        result["audit"]["gate"] = "PASSED"
        manifest["outputs"]["audit_result_jcs_sha256"] = auditctl.canonical_sha256(result)
        write_json(paths["result"], result)
        write_json(paths["manifest"], manifest)
        errors = auditctl.validate_structured_outputs(paths)
        assert any("claims PASSED but no audit seal exists" in e for e in errors), errors
        rc, pres = call(auditctl.cmd_present, audit_dir=audit, presentation="json")
        assert rc == 4 and not pres["ok"], pres
        rc, gate = call(auditctl.cmd_gate, audit_dir=audit, require_report=True)
        assert rc == 0 and gate["ok"], gate
        assert paths["complete"].exists()
        assert auditctl.validate_structured_outputs(paths) == []

        # Simulate crash immediately after result replacement, before manifest/seal.
        audit_crash = project / "result-only-crash"
        v290.make_run(project, audit_crash, False)
        pc = auditctl.audit_paths(audit_crash)
        crash_result = auditctl.load_json(pc["result"])
        crash_result["audit"]["gate"] = "PASSED"
        write_json(pc["result"], crash_result)  # manifest intentionally left stale
        errs = auditctl.validate_structured_outputs(pc)
        assert any("no audit seal" in e or "output digest mismatch" in e for e in errs), errs
        rc, recovered = call(auditctl.cmd_gate, audit_dir=audit_crash, require_report=True)
        assert rc == 0 and recovered["ok"], recovered
        assert auditctl.validate_structured_outputs(pc) == []

        # Seal must cryptographically bind the exact result and manifest identity.
        seal = auditctl.load_json(paths["complete"])
        original_seal = copy.deepcopy(seal)
        mutations = []
        bad = copy.deepcopy(seal); bad["audit_id"] = "other"; mutations.append((bad, "audit_id"))
        bad = copy.deepcopy(seal); bad["audit_mode"] = "SYSTEM"; mutations.append((bad, "audit_mode"))
        bad = copy.deepcopy(seal); bad["subjects"]["audit_result_jcs_sha256"] = "0" * 64; mutations.append((bad, "result digest"))
        bad = copy.deepcopy(seal); bad["subjects"]["result_manifest_jcs_sha256"] = "1" * 64; mutations.append((bad, "manifest digest"))
        for bad, token in mutations:
            write_json(paths["complete"], bad)
            errs = auditctl.validate_structured_outputs(paths)
            assert any(token in e for e in errs), (token, errs)
            write_json(paths["complete"], original_seal)

        # Seal and stored gate must agree in both directions.
        result2 = auditctl.load_json(paths["result"])
        manifest2 = auditctl.load_json(paths["manifest"])
        result2["audit"]["gate"] = "PENDING"
        manifest2["outputs"]["audit_result_jcs_sha256"] = auditctl.canonical_sha256(result2)
        write_json(paths["result"], result2)
        write_json(paths["manifest"], manifest2)
        errs = auditctl.validate_structured_outputs(paths)
        assert any("seal exists but audit-result gate is not PASSED" in e for e in errs), errs

        # Fresh unsealed run for producer cross-binding and explicit compatibility policy.
        audit2 = project / "producer-binding"
        v290.make_run(project, audit2, False)
        p2 = auditctl.audit_paths(audit2)
        r2 = auditctl.load_json(p2["result"])
        m2 = auditctl.load_json(p2["manifest"])
        m2["producer"]["version"] = "2.9.1"
        write_json(p2["manifest"], m2)
        errs = auditctl.validate_structured_outputs(p2)
        assert any("producer mismatch" in e for e in errs), errs

        m2["producer"] = dict(r2["producer"])
        r2["producer"]["version"] = "99.99.99"
        m2["producer"]["version"] = "99.99.99"
        m2["outputs"]["audit_result_jcs_sha256"] = auditctl.canonical_sha256(r2)
        write_json(p2["result"], r2)
        write_json(p2["manifest"], m2)
        errs = auditctl.validate_structured_outputs(p2)
        assert any("not an explicitly supported 2.9 patch" in e for e in errs), errs

    print("PASS: v2.9.3 digest-bound gate seal, recovery, and producer cross-binding work")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
