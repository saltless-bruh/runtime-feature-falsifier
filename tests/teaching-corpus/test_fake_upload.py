"""Detect FAKE_NOOP: success response without the claimed persisted effect."""
from _common import PNG_A, request, running, upload
from fake_upload_app import Handler


def main():
    with running(Handler) as base:
        status, payload, _ = upload(base, "a.png", "image/png", PNG_A)
        assert status == 201 and payload.get("ok") is True, "fixture did not fake success"
        read_status, _, _ = request(base + payload["url"])
        assert read_status == 404, "counterexample disappeared: data unexpectedly exists"
    print("PASS: detected FAKE_NOOP — upload reported success but read-back was absent")


if __name__ == "__main__":
    main()
