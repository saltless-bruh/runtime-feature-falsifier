"""INTENTIONALLY BROKEN: only a mock header can make the feature appear successful."""
import json
from http.server import BaseHTTPRequestHandler


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        if not self.path.startswith("/upload"):
            self.send_error(404); return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        mock = self.headers.get("X-Demo-Mock") == "1"
        if mock:
            status, payload = 201, {"ok": True, "id": "mock-upload", "mock": True}
        else:
            status, payload = 503, {"ok": False, "error": "real storage adapter unavailable"}
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
