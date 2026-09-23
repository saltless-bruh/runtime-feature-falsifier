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
def resolve_active_audit(root: Path) -> Path | None:
    """Resolve v2.9 configurable output root + active run pointer."""
    output = ".runtime-feature-audit"
    cfg = root / ".rff.toml"
    if cfg.is_file():
        try:
            import tomllib
            data = tomllib.loads(cfg.read_text(encoding="utf-8"))
            candidate = (data.get("audit") or {}).get("output_dir")
            if isinstance(candidate, str) and candidate.strip():
                output = candidate.strip()
        except Exception:
            return None
    out = (root / output).resolve(strict=False)
    try:
        out.relative_to(root.resolve())
    except ValueError:
        return None
    pointer = out / "active.json"
    if pointer.is_file():
        try:
            info = json.loads(pointer.read_text(encoding="utf-8"))
            run = (out / str(info.get("path", ""))).resolve(strict=False)
            run.relative_to(out)
            if (run / ".active.json").is_file():
                return run
        except Exception:
            return None
    # Legacy flat workspace compatibility.
    if (out / ".active.json").is_file():
        return out
    return None

PROTECTED_AUDIT_FILES = {'findings.json', 'feature-matrix.md', 'system-coverage.md', 'audit-summary.md', 'tracked-source-baseline.json', '.active.json', 'audit-result.json', '.report-state.json', '.complete.json', 'hypothesis-ledger.jsonl', 'attempts.jsonl', 'audit-report.md', 'result-manifest.json'}
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
        audit = resolve_active_audit(cwd)
        if audit is None:
            return False
        p = Path(path)
        if not p.is_absolute():
            p = cwd / p
        p = p.resolve(strict=False)
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

    audit = resolve_active_audit(cwd)
    if audit is None:
        return 0

    if tool in {"Write", "Edit", "NotebookEdit"}:
        path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or ""
        if not path or not inside_audit(str(path), cwd):
            deny(f"Runtime Feature Auditor blocks {tool} outside the active RFF run; audit source/tests must remain unchanged.")
        p = Path(str(path))
        if not p.is_absolute():
            p = cwd / p
        if p.resolve(strict=False).parent == audit and p.name in PROTECTED_AUDIT_FILES:
            deny("Runtime Feature Auditor protects canonical attempt/baseline/lifecycle files from direct edits; use `rff audit ...`.")
        return 0

    if tool == "Bash":
        command = str(inp.get("command") or "")
        if MUTATING_SHELL.search(command):
            # Allow explicit audit-workspace manipulation, but never assume a mixed command is safe.
            if not any(str(x) in command for x in (audit, audit.parent, audit.parent.parent)):
                deny("Runtime Feature Auditor blocked an obvious source-mutating shell command. Use the real runtime as-is; write only audit artifacts through the RFF control plane.")
        # Explicit redirection to a path outside the audit workspace is also suspicious.
        redirects = re.findall(r"(?:>|>>|2>)\s*([^\s;&|]+)", command)
        for target in redirects:
            target = target.strip("'\"")
            if target not in {"/dev/null", "/dev/stderr", "/dev/stdout"} and not inside_audit(target, cwd):
                deny(f"Runtime Feature Auditor blocked shell redirection outside the active RFF run: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
