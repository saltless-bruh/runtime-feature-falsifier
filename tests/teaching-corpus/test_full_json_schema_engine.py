#!/usr/bin/env python3
"""Regression: structural validation is delegated to a real Draft 7 JSON Schema engine."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]
CTL = ROOT / "runtime-feature-falsifier" / "scripts" / "auditctl.py"


def main() -> int:
    spec = importlib.util.spec_from_file_location("rff_auditctl_schema_test", CTL)
    ctl = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(ctl)

    # These keywords were intentionally outside the old hand-written subset.
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["mode", "items"],
        "properties": {
            "mode": {"oneOf": [{"const": "safe"}, {"const": "strict"}]},
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
                "items": {"type": "integer", "not": {"const": 13}},
            },
        },
        "additionalProperties": False,
    }
    assert ctl.validate_schema({"mode": "safe", "items": [1, 2]}, schema) == []
    cases = [
        {"mode": "other", "items": [1]},       # oneOf
        {"mode": "safe", "items": [1, 2, 3]}, # maxItems
        {"mode": "safe", "items": [1, 1]},    # uniqueItems
        {"mode": "safe", "items": [13]},      # not
    ]
    for case in cases:
        errors = ctl.validate_schema(case, schema)
        assert errors, f"standards-compliant validator unexpectedly accepted {case!r}"

    # v2.22.x fixes JSON equality so Python bool/int equality cannot satisfy
    # JSON Schema enum/const accidentally (True != 1 and False != 0 in JSON).
    equality_regressions = [
        ({"enum": [1]}, True),
        ({"const": 1}, True),
        ({"enum": [False]}, 0),
        ({"const": False}, 0),
    ]
    for fragment, instance in equality_regressions:
        typed = {"$schema": "http://json-schema.org/draft-07/schema#", **fragment}
        assert ctl.validate_schema(instance, typed), (fragment, instance)
    assert ctl.validate_schema(1, {"$schema": "http://json-schema.org/draft-07/schema#", "enum": [1]}) == []
    assert ctl.validate_schema(False, {"$schema": "http://json-schema.org/draft-07/schema#", "const": False}) == []

    assert ctl.fastjsonschema.VERSION == "2.22.2", ctl.fastjsonschema.VERSION
    vendor_path = Path(ctl.fastjsonschema.__file__).resolve()
    assert ROOT / "runtime-feature-falsifier" / "vendor" in vendor_path.parents, vendor_path
    legacy_draft7 = {
        "audit-plan.schema.json",
        "attempt-event.schema.json",
        "attempt-batch.schema.json",
        "feature-inventory.schema.json",
        "hypothesis-event.schema.json",
    }
    canonical_draft7 = {
        "audit-result.schema.json",
        "finding.schema.json",
        "findings.schema.json",
        "feature-result.schema.json",
        "artifact-reference.schema.json",
        "result-manifest.schema.json",
    }
    assets = ROOT / "runtime-feature-falsifier" / "assets"
    for name in legacy_draft7:
        data = json.loads((assets / name).read_text(encoding="utf-8"))
        assert data.get("$schema") == "http://json-schema.org/draft-07/schema#", name
    for name in canonical_draft7:
        data = json.loads((assets / name).read_text(encoding="utf-8"))
        assert data.get("$schema") == "http://json-schema.org/draft-07/schema#", name
    print("PASS: vendored fastjsonschema 2.22.2 enforces Draft 7 and JSON bool/number equality correctly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
