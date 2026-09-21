#!/usr/bin/env python3
"""Execute all intentionally broken teaching fixtures."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = [
    "test_fake_upload.py",
    "test_todo_upload.py",
    "test_hardcoded_upload.py",
    "test_mock_only_upload.py",
    "test_bttlp_upload.py",
    "test_logged_input_matrix.py",
    "test_system_inventory_gate.py",
    "test_persistent_investigation.py",
    "test_batch_logging.py",
    "test_concurrent_logging.py",
    "test_schema_consumption.py",
    "test_startup_repro_gate.py",
    "test_reader_locking.py",
    "test_full_json_schema_engine.py",
    "test_field_hardening.py",
]


def main() -> int:
    failures = 0
    for case in CASES:
        print(f"\n== {case} ==", flush=True)
        proc = subprocess.run([sys.executable, str(ROOT / case)], cwd=ROOT)
        if proc.returncode != 0:
            failures += 1
    print(f"\nsummary: {len(CASES) - failures}/{len(CASES)} example detectors passed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
