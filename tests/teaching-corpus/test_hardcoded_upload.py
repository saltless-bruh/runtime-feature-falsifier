"""Detect HARDCODED with differential/metamorphic probing."""
from _common import PNG_A, PNG_B, request, running, sha256, upload
from hardcoded_upload_app import Handler


def main():
    assert PNG_A != PNG_B
    with running(Handler) as base:
        s1, p1, _ = upload(base, "a.png", "image/png", PNG_A)
        s2, p2, _ = upload(base, "b.png", "image/png", PNG_B)
        assert s1 == s2 == 201
        r1, out1, _ = request(base + p1["url"])
        r2, out2, _ = request(base + p2["url"])
        assert r1 == r2 == 200
        assert sha256(out1) == sha256(out2), "fixture unexpectedly responds to different inputs"
        assert out1 != PNG_A and out2 != PNG_B
    print("PASS: detected HARDCODED — distinct valid inputs collapse to one canned artifact")


if __name__ == "__main__":
    main()
