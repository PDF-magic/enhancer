#!/usr/bin/env python3
"""Local-only PDF Magic web toolbench and streaming file-import endpoint."""

import json
import re
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
UPLOAD_DIR = ROOT / ".pdfmagic-uploads"
HOST = "127.0.0.1"
PORT = 4173
CHUNK_SIZE = 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".json", ".jsonl"}


def safe_filename(raw):
    """Avoid traversal and preserve a legible name after a random prefix."""
    basename = raw.replace("\\", "/").rsplit("/", 1)[-1]
    ext = Path(basename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Only PDF, JSON, and JSONL files can be imported.")
    stem = re.sub(r"[^\w .()\-]", "_", basename[: -len(ext)]).strip(" .")[:100]
    return (stem or "document") + ext


class ToolbenchHandler(SimpleHTTPRequestHandler):
    upload_dir = UPLOAD_DIR

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def respond_json(self, status, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def trusted_request(self):
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        return host in {f"localhost:{port}", f"127.0.0.1:{port}"}

    def do_GET(self):
        if urlsplit(self.path).path == "/api/status":
            if not self.trusted_request():
                return self.respond_json(403, {"error": "Untrusted host."})
            return self.respond_json(200, {"upload": True})
        return super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path != "/api/upload":
            return self.respond_json(404, {"error": "Not found."})
        port = self.server.server_address[1]
        origin = self.headers.get("Origin", "")
        if (not self.trusted_request() or origin not in {
            f"http://localhost:{port}", f"http://127.0.0.1:{port}"
        }):
            return self.respond_json(403, {"error": "Local, same-origin uploads only."})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/octet-stream":
            return self.respond_json(415, {"error": "Expected a raw file upload."})
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.respond_json(411, {"error": "Content-Length is required."})
        if size <= 0:
            return self.respond_json(400, {"error": "Cannot upload an empty file."})
        filename = parse_qs(urlsplit(self.path).query).get("name", [""])[0]
        try:
            safe_name = safe_filename(filename)
        except ValueError as error:
            return self.respond_json(400, {"error": str(error)})

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        destination = self.upload_dir / f"{uuid.uuid4().hex}-{safe_name}"
        remaining = size
        try:
            with destination.open("xb") as output:
                while remaining:
                    data = self.rfile.read(min(CHUNK_SIZE, remaining))
                    if not data:
                        raise ConnectionError("Upload interrupted.")
                    output.write(data)
                    remaining -= len(data)
        except (OSError, ConnectionError):
            destination.unlink(missing_ok=True)
            return self.respond_json(400, {"error": "Upload failed or was interrupted."})
        return self.respond_json(201, {"path": str(destination.resolve()), "name": safe_name})


def main():
    server = ThreadingHTTPServer((HOST, PORT), ToolbenchHandler)
    print(f"PDF Magic running at http://{HOST}:{PORT}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
