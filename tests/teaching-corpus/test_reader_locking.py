#!/usr/bin/env python3
"""Regression: canonical JSONL readers wait for an in-progress append instead of seeing a partial line."""
from __future__ import annotations
import importlib.util
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CTL = ROOT / "runtime-feature-falsifier" / "scripts" / "auditctl.py"


def load_ctl():
    spec = importlib.util.spec_from_file_location("rff_auditctl_reader_test", CTL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ctl = load_ctl()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        log = td / "attempts.jsonl"
        lock = td / ".attempts.jsonl.lock"
        ready = td / "ready"
        writer = td / "writer.py"
        writer.write_text(
            r'''import importlib.util
import os
import sys
import time
from pathlib import Path

ctl_path = Path(sys.argv[1])
log = Path(sys.argv[2])
lock = Path(sys.argv[3])
ready = Path(sys.argv[4])
spec = importlib.util.spec_from_file_location("rff_lock_writer", ctl_path)
ctl = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ctl)
with ctl.exclusive_lock(lock):
    with log.open("w", encoding="utf-8", newline="") as fh:
        fh.write('{"sequence":1')
        fh.flush()
        os.fsync(fh.fileno())
        ready.write_text("1", encoding="utf-8")
        time.sleep(0.45)
        fh.write(',"event_sha256":"head-1"}\n')
        fh.flush()
        os.fsync(fh.fileno())
''',
            encoding="utf-8",
        )
        proc = subprocess.Popen([sys.executable, str(writer), str(CTL), str(log), str(lock), str(ready)])
        deadline = time.time() + 5
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "writer never reached partial-line state"
        started = time.monotonic()
        events = ctl.read_events(log)
        elapsed = time.monotonic() - started
        proc.wait(timeout=5)
        assert proc.returncode == 0
        assert elapsed >= 0.30, f"reader did not wait for writer lock: {elapsed:.3f}s"
        assert len(events) == 1 and events[0]["sequence"] == 1 and events[0]["event_sha256"] == "head-1", events
    print("PASS: canonical JSONL reader waits for writer lock and never observes a partial append")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
