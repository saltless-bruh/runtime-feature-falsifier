#!/usr/bin/env python3
"""Regression: structural validation is delegated to a real Draft 7 JSON Schema engine."""
from __future__ import annotations
import importlib.util
from pathlib import Path

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

    vendor_path = Path(ctl.fastjsonschema.__file__).resolve()
    assert ROOT / "runtime-feature-falsifier" / "vendor" in vendor_path.parents, vendor_path
    for schema_path in (ROOT / "runtime-feature-falsifier" / "assets").glob("*.schema.json"):
        data = schema_path.read_text(encoding="utf-8")
        assert '"$schema": "http://json-schema.org/draft-07/schema#"' in data, schema_path
    print("PASS: vendored fastjsonschema enforces Draft 7 keywords beyond the former mini-validator subset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
