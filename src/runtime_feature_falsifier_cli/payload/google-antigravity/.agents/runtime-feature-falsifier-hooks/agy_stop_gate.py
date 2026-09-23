#!/usr/bin/env python3
"""Optional Antigravity Stop hook: continue while an active audit fails its gate."""
from __future__ import annotations
import json
import subprocess
import sys
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
        payload = json.load(sys.stdin)
    except Exception:
        print(json.dumps({"decision": "allow"}))
        return 0
    paths = payload.get("workspacePaths") or []
    if not paths:
        print(json.dumps({"decision": "allow"}))
        return 0
    root = Path(paths[0]).resolve()
    audit = resolve_active_audit(root)
    if audit is None:
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
