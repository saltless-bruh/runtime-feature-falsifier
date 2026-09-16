"""Detect TODO through the real runtime entry point."""
from _common import PNG_A, running, upload
from todo_upload_app import Handler


def main():
    with running(Handler) as base:
        status, payload, _ = upload(base, "a.png", "image/png", PNG_A)
        assert status == 501
        assert "todo" in str(payload).lower() or "not implemented" in str(payload).lower()
    print("PASS: detected TODO — exposed upload entry point returns not-implemented")


if __name__ == "__main__":
    main()
