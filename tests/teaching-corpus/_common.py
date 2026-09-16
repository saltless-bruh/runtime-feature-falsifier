from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def running(handler_cls):
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(url: str, *, method="GET", body: bytes | None = None, headers=None):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def upload(base: str, filename: str, mime: str, data: bytes, headers=None):
    q = urllib.parse.urlencode({"filename": filename, "mime": mime})
    all_headers = {"Content-Type": "application/octet-stream"}
    all_headers.update(headers or {})
    status, body, response_headers = request(f"{base}/upload?{q}", method="POST", body=data, headers=all_headers)
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        payload = {"raw": body.decode("utf-8", errors="replace")}
    return status, payload, response_headers


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


PNG_A = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8z8DAwMDAxMDAwMDAAAANHQEDasKb6QAAAABJRU5ErkJggg==")
PNG_B = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAMAAAABCAIAAACUgoPjAAAADUlEQVR4nGNk+M8AAQAIDAEBPalNXQAAAABJRU5ErkJggg==")
PNG_C = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAADCAIAAADdv/LVAAAAFElEQVR4nGNgYPjPxMDAwMTAwAAACRsBBLgubicAAAAASUVORK5CYII=")
