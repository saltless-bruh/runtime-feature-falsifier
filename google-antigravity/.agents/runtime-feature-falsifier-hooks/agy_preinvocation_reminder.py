#!/usr/bin/env python3
"""Optional Antigravity PreInvocation reminder while an audit is active."""
from __future__ import annotations
import json
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0
    paths = payload.get("workspacePaths") or []
    if not paths:
        print("{}")
        return 0
    root = Path(paths[0]).resolve()
    active = root / ".runtime-feature-audit" / ".active.json"
    if not active.exists():
        print("{}")
        return 0
    msg = (
        "Runtime Feature Falsifier audit is ACTIVE. Keep the target source/tests/config unchanged; "
        "use the loaded runtime-feature-falsifier methodology, preserve STARTED+FINISHED attempt logging, "
        "and do not stop until auditctl gate --require-report passes. If context was compacted, re-read SKILL.md "
        "and the required reference for the current phase before continuing."
    )
    print(json.dumps({"injectSteps": [{"ephemeralMessage": msg}]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
