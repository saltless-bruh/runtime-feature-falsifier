"""INTENTIONALLY BROKEN: reports success but stores nothing."""
import json
from http.server import BaseHTTPRequestHandler


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        if not self.path.startswith("/upload"):
            self.send_error(404); return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)  # consume input, then discard it
        body = json.dumps({"ok": True, "id": "upload-123", "url": "/files/upload-123"}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/files/"):
            self.send_error(404, "not stored")
        else:
            self.send_error(404)
