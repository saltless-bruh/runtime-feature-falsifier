#!/usr/bin/env python3
"""v2.9.2 regressions: strict JSON, semantic consistency, provenance, fail-closed policy."""
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


def main() -> int:
    # Strict control-plane parsing: duplicate names and JS-only constants fail closed.
    for raw in ('{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}'):
        try:
            auditctl.strict_json_loads(raw, "adversarial")
        except ValueError:
            pass
        else:
            raise AssertionError(f"strict parser accepted invalid I-JSON input: {raw}")

    # Canonical result profile is float-free and constrained to interoperable integers.
    for value in ({"x": 1.5}, {"x": 9007199254740992}, {"x": -9007199254740992}):
        try:
            auditctl.jcs_bytes(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"JCS profile accepted unsafe numeric value: {value}")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "project"
        project.mkdir()

        # Reproduce the v2.9.1 denormalized-count bypass: mutate verdict_counts and
        # legitimately recompute the result digest. v2.9.2 must still fail closed.
        audit = project / "semantic-bypass"
        v290.make_run(project, audit, True)
        paths = auditctl.audit_paths(audit)
        result = auditctl.load_json(paths["result"])
        schema = auditctl.load_schema("audit-result.schema.json")

        bad_schema = copy.deepcopy(result)
        bad_schema["verdict_counts"] = {}
        assert auditctl.validate_schema(bad_schema, schema, "audit-result"), "closed schema accepted empty verdict_counts"

        forged = copy.deepcopy(result)
        forged["verdict_counts"] = {k: 0 for k in forged["verdict_counts"]}
        # Structurally valid but semantically false: the feature remains FALSIFIED.
        assert auditctl.validate_schema(forged, schema, "audit-result") == []
        semantic_errors = auditctl._result_contract_errors(forged)
        assert any("verdict_counts" in e for e in semantic_errors), semantic_errors

        wrong_coverage = copy.deepcopy(result)
        wrong_coverage["coverage"]["audited"] = 0
        assert auditctl.validate_schema(wrong_coverage, schema, "audit-result") == []
        coverage_errors = auditctl._result_contract_errors(wrong_coverage)
        assert any("coverage.audited" in e for e in coverage_errors), coverage_errors

        dangling = copy.deepcopy(result)
        dangling["findings"][0]["feature_id"] = "unknown-feature"
        assert auditctl.validate_schema(dangling, schema, "audit-result") == []
        dangling_errors = auditctl._result_contract_errors(dangling)
        assert any("unknown feature_id" in e for e in dangling_errors), dangling_errors
        paths["result"].write_text(json.dumps(forged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = auditctl.load_json(paths["manifest"])
        manifest["outputs"]["audit_result_jcs_sha256"] = auditctl.canonical_sha256(forged)
        paths["manifest"].write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        errors = auditctl.validate_structured_outputs(paths)
        assert any("verdict_counts" in e or "authoritative audit state" in e for e in errors), errors
        rc, policy = call(auditctl.cmd_policy, audit_dir=audit, fail_on=["FALSIFIED"])
        assert rc == 4 and not policy["policy_passed"], policy

        # Fresh run: manifest input provenance is an assertion, not inert metadata.
        audit2 = project / "provenance-bypass"
        v290.make_run(project, audit2, False)
        paths2 = auditctl.audit_paths(audit2)
        manifest2 = auditctl.load_json(paths2["manifest"])
        manifest2["inputs"]["plan_sha256"] = "0" * 64
        manifest2["inputs"]["inventory_sha256"] = "2" * 64
        manifest2["inputs"]["attempt_chain_head"] = "1" * 64
        manifest2["inputs"]["hypothesis_chain_head"] = "3" * 64
        paths2["manifest"].write_text(json.dumps(manifest2, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        errors2 = auditctl.validate_structured_outputs(paths2)
        for field in ("plan_sha256", "inventory_sha256", "attempt_chain_head", "hypothesis_chain_head"):
            assert any(f"input provenance mismatch: {field}" in e for e in errors2), errors2
        rc2, gate2 = call(auditctl.cmd_gate, audit_dir=audit2, require_report=True)
        assert rc2 == 4 and not gate2["ok"], gate2

        # Cross-document audit identity must be bound.
        audit3 = project / "identity-bypass"
        v290.make_run(project, audit3, False)
        paths3 = auditctl.audit_paths(audit3)
        findings = auditctl.load_json(paths3["findings"])
        findings["audit_id"] = "other-audit"
        paths3["findings"].write_text(json.dumps(findings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest3 = auditctl.load_json(paths3["manifest"])
        manifest3["outputs"]["findings_jcs_sha256"] = auditctl.canonical_sha256(findings)
        paths3["manifest"].write_text(json.dumps(manifest3, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        errors3 = auditctl.validate_structured_outputs(paths3)
        assert any("findings.json audit_id does not match" in e for e in errors3), errors3

    print("PASS: v2.9.2 strict JSON, semantic projections, provenance inputs, identity binding, and fail-closed policy work")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
