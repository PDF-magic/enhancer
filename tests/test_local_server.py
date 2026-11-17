"""Test local PDF Magic uploads, origin checks, and directory isolation."""

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from serve import ToolbenchHandler, safe_filename


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)

        class TestHandler(ToolbenchHandler):
            upload_dir = folder

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, data=None, origin=None, host=None):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream"
        if origin is not None:
            headers["Origin"] = origin
        client.request(method, path, body=data, headers=headers)
        response = client.getresponse()
        result = (response.status, json.loads(response.read()))
        client.close()
        return result

    def test_status(self):
        self.assertEqual(self.request("GET", "/api/status"), (200, {"upload": True}))

    def test_upload_stages_bytes_without_changing_name_or_source(self):
        payload = b"%PDF-1.7\n" + b"X" * 65550
        status, body = self.request(
            "POST", "/api/upload?name=My%20Book.pdf",
            data=payload, origin=f"http://127.0.0.1:{self.port}",
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["name"], "My Book.pdf")
        path = Path(body["path"])
        self.assertEqual(path.parent, Path(self.temp.name))
        self.assertEqual(path.read_bytes(), payload)

    def test_uploads_never_overwrite_one_another(self):
        kwargs = {"data": b"{}", "origin": f"http://127.0.0.1:{self.port}"}
        first = self.request("POST", "/api/upload?name=a.json", **kwargs)
        second = self.request("POST", "/api/upload?name=a.json", **kwargs)
        self.assertNotEqual(first[1]["path"], second[1]["path"])

    def test_cross_site_request_cannot_stage_files(self):
        status, _ = self.request(
            "POST", "/api/upload?name=a.pdf",
            data=b"sample", origin="https://evil.example",
        )
        self.assertEqual(status, 403)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_missing_origin_is_rejected(self):
        status, _ = self.request("POST", "/api/upload?name=a.pdf", data=b"sample")
        self.assertEqual(status, 403)

    def test_dns_rebinding_host_is_rejected(self):
        status, _ = self.request(
            "POST", "/api/upload?name=a.pdf", data=b"sample",
            origin=f"http://127.0.0.1:{self.port}", host="evil.example",
        )
        self.assertEqual(status, 403)

    def test_bad_extensions_rejected(self):
        status, _ = self.request(
            "POST", "/api/upload?name=malicious.sh", data=b"sample",
            origin=f"http://127.0.0.1:{self.port}",
        )
        self.assertEqual(status, 400)

    def test_filename_cannot_escape_staging_directory(self):
        self.assertEqual(safe_filename("../../unsafe.pdf"), "unsafe.pdf")
        self.assertEqual(safe_filename(r"C:\\tmp\\scan.jsonl"), "scan.jsonl")
        self.assertRaises(ValueError, safe_filename, "../script.py")


if __name__ == "__main__":
    unittest.main()
