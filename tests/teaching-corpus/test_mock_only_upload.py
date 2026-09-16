"""Detect MOCK_ONLY by comparing intended runtime path with explicit mock mode."""
from _common import PNG_A, running, upload
from mock_only_upload_app import Handler


def main():
    with running(Handler) as base:
        prod_status, prod_payload, _ = upload(base, "a.png", "image/png", PNG_A)
        mock_status, mock_payload, _ = upload(base, "a.png", "image/png", PNG_A, {"X-Demo-Mock": "1"})
        assert prod_status == 503 and prod_payload.get("ok") is False
        assert mock_status == 201 and mock_payload.get("mock") is True
    print("PASS: detected MOCK_ONLY — intended path fails while explicit mock mode fakes success")


if __name__ == "__main__":
    main()
