"""Detect BTTLP by invoking the real CLI surface and checking the advertised effect."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from _common import PNG_A

HERE = Path(__file__).resolve().parent
APP = HERE / "bttlp_upload_app.py"


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = root / "profile.png"
        source.write_bytes(PNG_A)
        store = root / "store"
        store.mkdir()

        proc = subprocess.run(
            [sys.executable, str(APP), str(source), "--store", str(store)],
            text=True,
            capture_output=True,
        )

        assert proc.returncode == 0, "fixture did not present superficial success"
        assert "success" in proc.stdout.lower() or "updated" in proc.stdout.lower()
        assert list(store.iterdir()) == [], "counterexample disappeared: fixture unexpectedly persisted data"

    print("PASS: detected BTTLP — real CLI surface reports upload success but performs no state change")


if __name__ == "__main__":
    main()
