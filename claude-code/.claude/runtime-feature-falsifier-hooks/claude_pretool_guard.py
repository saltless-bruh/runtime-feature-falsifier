#!/usr/bin/env python3
"""Claude Code PreToolUse guard for the companion runtime-feature-auditor agent.

Deterministically blocks direct Write/Edit/NotebookEdit outside the audit
workspace and blocks obvious source-mutating shell commands. This is a guardrail,
not a complete OS sandbox; auditctl's tracked-source hash gate catches mutations
that bypass command-pattern screening.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

AUDIT_DIRNAME = ".runtime-feature-audit"
PROTECTED_AUDIT_FILES = {"attempts.jsonl", "hypothesis-ledger.jsonl", ".report-state.json", "tracked-source-baseline.json", ".active.json", ".complete.json"}
MUTATING_SHELL = re.compile(
    r"(?:^|[;&|]\s*)(?:apply_patch\b|git\s+(?:checkout|restore|reset|clean)\b|"
    r"sed\s+-i\b|perl\s+-p?i\b|truncate\b|rm\s+-[^\n]*r[^\n]*f\b|"
    r"(?:cp|mv)\s+|tee\s+|(?:python|python3|py(?:\s+-3)?)\s+[^\n]*(?:write_text|write_bytes|open\([^\n]*['\"]w))"
)


def deny(reason: str) -> None:
    print(reason, file=sys.stderr)
    raise SystemExit(2)


def inside_audit(path: str, cwd: Path) -> bool:
    try:
        p = Path(path)
        if not p.is_absolute():
            p = cwd / p
        p = p.resolve(strict=False)
        audit = (cwd / AUDIT_DIRNAME).resolve(strict=False)
        return p == audit or audit in p.parents
    except Exception:
        return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()

    if tool in {"Write", "Edit", "NotebookEdit"}:
        path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or ""
        if not path or not inside_audit(str(path), cwd):
            deny(f"Runtime Feature Auditor blocks {tool} outside {AUDIT_DIRNAME}; audit source/tests must remain unchanged.")
        p = Path(str(path))
        if not p.is_absolute():
            p = cwd / p
        if p.resolve(strict=False).parent == (cwd / AUDIT_DIRNAME).resolve(strict=False) and p.name in PROTECTED_AUDIT_FILES:
            deny("Runtime Feature Auditor protects canonical attempt/baseline/lifecycle files from direct edits; use auditctl.py.")
        return 0

    if tool == "Bash":
        command = str(inp.get("command") or "")
        if MUTATING_SHELL.search(command):
            # Allow explicit audit-workspace manipulation, but never assume a mixed command is safe.
            if AUDIT_DIRNAME not in command:
                deny("Runtime Feature Auditor blocked an obvious source-mutating shell command. Use the real runtime as-is; write only audit artifacts under .runtime-feature-audit.")
        # Explicit redirection to a path outside the audit workspace is also suspicious.
        redirects = re.findall(r"(?:>|>>|2>)\s*([^\s;&|]+)", command)
        for target in redirects:
            target = target.strip("'\"")
            if target not in {"/dev/null", "/dev/stderr", "/dev/stdout"} and not inside_audit(target, cwd):
                deny(f"Runtime Feature Auditor blocked shell redirection outside {AUDIT_DIRNAME}: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
