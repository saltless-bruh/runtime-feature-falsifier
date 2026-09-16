#!/usr/bin/env python3
"""Merge Runtime Feature Falsifier Antigravity hooks into a workspace hooks.json."""
from __future__ import annotations
import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

NAMES = {
    "runtime-feature-falsifier-source-guard",
    "runtime-feature-falsifier-context-reminder",
    "runtime-feature-falsifier-stop-gate",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--hooks-dir", type=Path, required=True)
    args = ap.parse_args()
    target = args.target
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if target.exists():
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit(f"existing hooks file must contain a JSON object: {target}")
    hooks = args.hooks_dir.resolve()
    def cmd(name: str) -> str:
        argv = [sys.executable, str(hooks / name)]
        return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    ours = {
        "runtime-feature-falsifier-source-guard": {
            "PreToolUse": [{
                "matcher": "write_to_file|replace_file_content|multi_replace_file_content|run_command",
                "hooks": [{"type": "command", "command": cmd("agy_pretool_guard.py"), "timeout": 5}],
            }],
        },
        "runtime-feature-falsifier-context-reminder": {
            "PreInvocation": [{"type": "command", "command": cmd("agy_preinvocation_reminder.py"), "timeout": 5}],
        },
        "runtime-feature-falsifier-stop-gate": {
            "Stop": [{"type": "command", "command": cmd("agy_stop_gate.py"), "timeout": 30}],
        },
    }
    data.update(ours)
    stage = target.with_name(target.name + ".stage")
    stage.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    stage.replace(target)
    print(json.dumps({"ok": True, "hooks_file": str(target), "installed": sorted(NAMES)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
