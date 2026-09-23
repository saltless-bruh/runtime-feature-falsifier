#!/usr/bin/env python3
"""Claude Stop guard for an active Runtime Feature Falsifier audit."""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
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

def main() -> int:
    try:
        payload=json.load(sys.stdin)
    except Exception:
        return 0
    root=Path(payload.get("cwd") or os.getcwd()).resolve()
    audit=resolve_active_audit(root)
    if audit is None:
        return 0
    auditctl=root/".claude"/"skills"/"runtime-feature-falsifier"/"scripts"/"auditctl.py"
    if not auditctl.exists():
        print("Runtime Feature Falsifier audit is active but its installed controller is unavailable.", file=sys.stderr)
        return 2
    proc=subprocess.run([sys.executable,str(auditctl),"gate","--audit-dir",str(audit),"--require-report"],capture_output=True,text=True)
    if proc.returncode:
        print("Runtime Feature Falsifier cannot finish yet.\n"+(proc.stdout or proc.stderr)[:6000], file=sys.stderr)
        return 2
    return 0

if __name__=="__main__": raise SystemExit(main())
