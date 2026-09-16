#!/usr/bin/env python3
"""Optional Antigravity Stop hook: continue while an active audit fails its gate."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print(json.dumps({"decision": "allow"}))
        return 0
    paths = payload.get("workspacePaths") or []
    if not paths:
        print(json.dumps({"decision": "allow"}))
        return 0
    root = Path(paths[0]).resolve()
    audit = root / ".runtime-feature-audit"
    if not (audit / ".active.json").exists():
        print(json.dumps({"decision": "allow"}))
        return 0
    auditctl = root / ".agents" / "skills" / "runtime-feature-falsifier" / "scripts" / "auditctl.py"
    if not auditctl.exists():
        print(json.dumps({
            "decision": "continue",
            "reason": "Runtime Feature Falsifier audit is active but auditctl.py is unavailable at the installed workspace skill path. Restore/reinstall the skill before finishing."
        }))
        return 0
    proc = subprocess.run(
        [sys.executable, str(auditctl), "gate", "--audit-dir", str(audit), "--require-report"],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        print(json.dumps({"decision": "allow"}))
        return 0
    reason = (proc.stdout or proc.stderr or "audit gate failed").strip()
    print(json.dumps({
        "decision": "continue",
        "reason": "Runtime Feature Falsifier cannot finish yet. Resolve or explicitly record the incomplete audit state, regenerate the report if needed, and pass the deterministic gate.\n" + reason[:6000]
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
