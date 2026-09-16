"""INTENTIONALLY BROKEN: ignores uploaded bytes and always serves one canned image."""
import base64
import json
from http.server import BaseHTTPRequestHandler

CANNED = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAADCAIAAADdv/LVAAAAFElEQVR4nGNgYPjPxMDAwMTAwAAACRsBBLgubicAAAAASUVORK5CYII=")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        if not self.path.startswith("/upload"):
            self.send_error(404); return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)  # deliberately ignored
        body = json.dumps({"ok": True, "id": "demo", "url": "/files/demo"}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/files/demo":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(CANNED)))
            self.end_headers(); self.wfile.write(CANNED)
        else:
            self.send_error(404)
