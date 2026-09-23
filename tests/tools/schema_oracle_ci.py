#!/usr/bin/env python3
"""CI-only differential oracle for RFF canonical Draft 7 schemas.

This helper is intentionally excluded from the offline/self-test dependency path.
CI may install `jsonschema` and run this script to compare it with the bundled
fastjsonschema engine on a focused corpus of security-relevant edge cases.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "runtime-feature-falsifier"
VENDOR = SKILL / "vendor"
ASSETS = SKILL / "assets"


def main() -> int:
    try:
        reference = importlib.import_module("jsonschema")
    except ImportError:
        print("SKIP: CI oracle requires the optional development dependency 'jsonschema'", file=sys.stderr)
        return 77

    sys.path.insert(0, str(VENDOR))
    import fastjsonschema  # type: ignore

    if fastjsonschema.VERSION != "2.22.2":
        raise AssertionError(f"unexpected bundled fastjsonschema {fastjsonschema.VERSION!r}")

    cases = [
        ({"enum": [1]}, [(1, True), (True, False), (1.0, True)]),
        ({"const": 1}, [(1, True), (True, False), (1.0, True)]),
        ({"enum": [False]}, [(False, True), (0, False)]),
        ({"type": "integer"}, [(1, True), (True, False), (1.0, True)]),
    ]
    for fragment, samples in cases:
        schema = {"$schema": "http://json-schema.org/draft-07/schema#", **fragment}
        fast = fastjsonschema.compile(schema, use_default=False)
        ref_cls = reference.Draft7Validator
        ref = ref_cls(schema)
        for value, expected in samples:
            fast_ok = True
            try:
                fast(value)
            except fastjsonschema.JsonSchemaException:
                fast_ok = False
            ref_ok = ref.is_valid(value)
            assert fast_ok == ref_ok == expected, (schema, value, fast_ok, ref_ok, expected)

    # Both engines must accept every shipped schema definition as Draft 7.
    # RFF deliberately forbids network resolution, so exercise the bundled engine
    # with the same local-only policy used by the runtime controller.
    def local_schema_handler(uri: str):
        name = Path(urlparse(uri).path).name
        candidate = ASSETS / name
        if not name or not candidate.is_file():
            raise ValueError(f"non-local schema reference rejected: {uri}")
        return json.loads(candidate.read_text(encoding="utf-8"))

    handlers = {"http": local_schema_handler, "https": local_schema_handler}
    for path in sorted(ASSETS.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        reference.Draft7Validator.check_schema(schema)
        fastjsonschema.compile(schema, handlers=handlers, use_default=False)

    print("PASS: bundled fastjsonschema and reference jsonschema agree on RFF Draft 7 edge cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
