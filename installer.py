#!/usr/bin/env python3
"""Source-tree launcher for the rff CLI (does not install a global uv tool)."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from runtime_feature_falsifier_cli import main  # noqa: E402

argv = sys.argv[1:]
if argv and argv[0] == "--detect":
    argv = ["detect", *argv[1:]]
else:
    known = {"init", "detect", "doctor", "integration", "self", "-h", "--help", "--version"}
    if argv and argv[0] not in known:
        argv = ["init", *argv]
    elif not argv:
        argv = ["init"]
raise SystemExit(main(argv))
