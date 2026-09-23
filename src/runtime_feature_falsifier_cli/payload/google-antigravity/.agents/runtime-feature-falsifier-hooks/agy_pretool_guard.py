#!/usr/bin/env python3
"""Optional Antigravity PreToolUse guard for an active Runtime Feature Falsifier audit.

Workspace hooks affect all Antigravity conversations in a repository, so this guard
is intentionally dormant unless `.runtime-feature-audit/.active.json` exists.
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


def workspace(payload: dict) -> Path:
    paths = payload.get("workspacePaths") or []
    if paths:
        return Path(paths[0]).resolve()
    call = payload.get("toolCall") or {}
    args = call.get("args") or {}
    cwd = args.get("Cwd") or os.getcwd()
    return Path(cwd).resolve()


def inside(path: str, root: Path) -> bool:
    try:
        p = Path(path)
        if not p.is_absolute():
            p = root / p
        p = p.resolve(strict=False)
        return p == root or root in p.parents
    except Exception:
        return False


def protected_audit_path(path: str, audit: Path, ws: Path) -> bool:
    try:
        p = Path(path)
        if not p.is_absolute():
            p = ws / p
        p = p.resolve(strict=False)
        return p.parent == audit and p.name in PROTECTED_AUDIT_FILES
    except Exception:
        return False


def emit(decision: str, reason: str = "") -> int:
    out = {"decision": decision}
    if reason:
        out["reason"] = reason
    print(json.dumps(out))
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return emit("allow")

    ws = workspace(payload)
    audit = resolve_active_audit(ws)
    if audit is None:
        return emit("allow")

    call = payload.get("toolCall") or {}
    tool = str(call.get("name") or "")
    args = call.get("args") or {}

    if tool in {"write_to_file", "replace_file_content", "multi_replace_file_content"}:
        target = str(args.get("TargetFile") or "")
        if not target or not inside(target, audit):
            return emit("deny", f"Runtime Feature Falsifier audit is active: {tool} may not modify project source/tests; write only audit artifacts under the active RFF run.")
        if protected_audit_path(target, audit, ws):
            return emit("deny", "Runtime Feature Falsifier protects its canonical log/baseline/lifecycle files from direct edits; use `rff audit ...`.")
        return emit("allow")

    if tool == "run_command":
        command = str(args.get("CommandLine") or "")
        # auditctl is the sanctioned writer for canonical audit state.
        sanctioned = ("rff audit" in command) or ("auditctl.py" in command)
        if any(name in command for name in PROTECTED_AUDIT_FILES) and not sanctioned:
            return emit("deny", "Runtime Feature Falsifier protects canonical audit state from shell mutation; use `rff audit ...`.")
        if MUTATING_SHELL.search(command) and not any(str(audit_part) in command for audit_part in (audit, audit.parent, audit.parent.parent)):
            return emit("deny", "Runtime Feature Falsifier blocked an obvious source-mutating command while the audit is active.")
        redirects = re.findall(r"(?:>|>>|2>)\s*([^\s;&|]+)", command)
        for target in redirects:
            target = target.strip("'\"")
            if target in {"/dev/null", "/dev/stderr", "/dev/stdout"}:
                continue
            if not inside(target, audit):
                return emit("deny", f"Runtime Feature Falsifier blocked shell redirection outside the active RFF run: {target}")
            if protected_audit_path(target, audit, ws) and not sanctioned:
                return emit("deny", "Runtime Feature Falsifier protects canonical audit state from redirection; use `rff audit ...`.")
    return emit("allow")


if __name__ == "__main__":
    raise SystemExit(main())
