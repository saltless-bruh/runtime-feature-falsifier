#!/usr/bin/env python3
"""INTENTIONALLY BROKEN CLI: looks implemented, prints success, performs no effect."""
from __future__ import annotations

import argparse


def main() -> int:
    p = argparse.ArgumentParser(description="Demo profile-image uploader")
    p.add_argument("image")
    p.add_argument("--store", required=True, help="Directory where a genuine uploader would persist the image")
    args = p.parse_args()

    # BTTLP: the command surface and polished success message exist, but the
    # advertised operation is not wired to any implementation.
    print(f"Uploaded {args.image} successfully")
    print("Profile image updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
