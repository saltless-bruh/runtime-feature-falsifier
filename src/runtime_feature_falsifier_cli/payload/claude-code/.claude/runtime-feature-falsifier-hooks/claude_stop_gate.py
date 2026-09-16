#!/usr/bin/env python3
"""Claude Code Stop hook: refuse completion until auditctl's final gate passes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    # Required by Claude hook guidance to avoid a Stop-hook feedback loop.
    if payload.get("stop_hook_active") is True:
        return 0
    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()
    auditctl = Path(__file__).resolve().with_name("auditctl.py")
    proc = subprocess.run(
        [sys.executable, str(auditctl), "gate", "--audit-dir", str(cwd / ".runtime-feature-audit"), "--require-report"],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return 0
    reason = (proc.stdout or proc.stderr or "audit gate failed").strip()
    print("Runtime Feature Auditor cannot finish: deterministic audit gate failed. Resolve/record the incomplete audit state, regenerate the report if needed, then run the gate again.\n" + reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
