#!/usr/bin/env python3
"""Optional Antigravity PreInvocation reminder while an RFF audit is active."""
from __future__ import annotations
import json, sys
from pathlib import Path

def resolve_active_audit(root: Path) -> Path | None:
    output = ".runtime-feature-audit"
    cfg = root / ".rff.toml"
    if cfg.is_file():
        try:
            import tomllib
            data=tomllib.loads(cfg.read_text(encoding="utf-8"))
            value=(data.get("audit") or {}).get("output_dir")
            if isinstance(value,str) and value.strip(): output=value.strip()
        except Exception:
            return None
    out=(root/output).resolve(strict=False)
    pointer=out/"active.json"
    if pointer.is_file():
        try:
            info=json.loads(pointer.read_text(encoding="utf-8"))
            run=(out/str(info.get("path",""))).resolve(strict=False)
            run.relative_to(out)
            if (run/".active.json").is_file(): return run
        except Exception:
            return None
    if (out/".active.json").is_file(): return out
    return None

def main()->int:
    try: payload=json.load(sys.stdin)
    except Exception:
        print("{}"); return 0
    paths=payload.get("workspacePaths") or []
    if not paths:
        print("{}"); return 0
    root=Path(paths[0]).resolve()
    if resolve_active_audit(root) is None:
        print("{}"); return 0
    msg=(
      "Runtime Feature Falsifier audit is ACTIVE. Keep target source/tests/config unchanged; "
      "use `rff audit ...` for CONTROL operations, distinguish TARGET commands from RFF tools, "
      "and do not manually write canonical result/report files. Finish with `rff audit report`, "
      "`rff audit gate`, then ground the final reply in `rff audit present`."
    )
    print(json.dumps({"injectSteps":[{"ephemeralMessage":msg}]})); return 0

if __name__=="__main__": raise SystemExit(main())
