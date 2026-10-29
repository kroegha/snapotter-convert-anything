"""Unit tests for the snapotter CLI against an in-process fake SnapOtter API.

Run: python3 -m unittest discover -s tools/snapotter-cli/tests -v
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import snapotter  # noqa: E402

KEY = "sk_test_DO-NOT-PRINT-0123456789"


class FakeApi(BaseHTTPRequestHandler):
    requests = []  # (method, path, headers, body) of every request
    release = threading.Event()  # ends the job-slow stream

    def log_message(self, *args):
        pass

    def _send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        FakeApi.requests.append((self.command, self.path, dict(self.headers), body))
        return body

    def do_POST(self):
        body = self._record()
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            return self._send(401, {"error": "Authentication required"})
        p = self.path
        if p == "/api/v1/tools/video/compress-video":
            return self._send(202, {"jobId": "job-video", "async": True})
        if p == "/api/v1/tools/image/ocr":
            return self._send(202, {"jobId": "job-ocr", "async": True})
        if p == "/api/v1/tools/image/convert":
            return self._send(200, {"jobId": "job-sync", "downloadUrl": "/api/v1/download/job-sync/photo.webp",
                                    "originalSize": 10, "processedSize": 5})
        if p == "/api/v1/tools/image/remove-background":
            if b"broken.jpg" in body:
                return self._send(422, {"error": "Processing failed", "details": "not an image"})
            return self._send(200, {"jobId": "job-bg", "downloadUrl": "/api/v1/download/job-bg/out.png"})
        if p == "/api/v1/tools/audio/transcribe-audio":
            return self._send(202, {"jobId": "job-fail", "async": True})
        if p == "/api/v1/tools/image/qr-generate":
            return self._send(200, b"\x89PNG fake", "image/png")
        if p == "/api/v1/tools/pdf/merge-pdf":
            return self._send(200, {"jobId": "job-merge", "downloadUrl": "/api/v1/download/job-merge/merged.pdf"})
        return self._send(404, {"error": "not found"})

    def do_GET(self):
        self._record()
        p = self.path
        if p == "/api/v1/health":
            return self._send(200, {"status": "ok"})
        if p == "/api/v1/openapi.yaml":
            spec = "paths:\n  /api/v1/tools/video/compress-video:\n    post: {}\n  /api/v1/tools/image/brand-new:\n    post: {}\n"
            return self._send(200, spec.encode(), "application/yaml")
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            return self._send(401, {"error": "Authentication required"})
        if p == "/api/v1/features":
            return self._send(200, {"bundles": [{"id": "transcription", "status": "installed"}]})
        if p.startswith("/api/v1/jobs/") and p.endswith("/progress"):
            job = p.split("/")[4]
            if job == "job-slow":  # one progress event, then silence: a long encode
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"phase": "processing", "percent": 10}\n\n')
                self.wfile.flush()
                FakeApi.release.wait(10)
                return
            events = [{"phase": "processing", "percent": 50, "stage": "encoding"}]
            if job == "job-fail":
                events.append({"phase": "failed", "error": "model not installed"})
            else:
                name = {"job-video": "clip_compressed.mp4", "job-ocr": "scan.txt"}[job]
                result = {"jobId": job, "downloadUrl": f"/api/v1/download/{job}/{name}"}
                if job == "job-ocr":
                    result["text"] = "Hello OCR"
                events.append({"phase": "complete", "percent": 100, "result": result})
            data = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
            return self._send(200, data, "text/event-stream")
        if p.startswith("/api/v1/download/"):
            return self._send(200, b"RESULT:" + p.encode(), "application/octet-stream")
        return self._send(404, {"error": "not found"})


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeApi.requests.clear()
        self.dir = tempfile.mkdtemp()
        key = os.path.join(self.dir, "key")
        with open(key, "w") as fh:
            fh.write(KEY + "\n")
        os.environ["SNAPOTTER_KEY_FILE"] = key
        os.environ["SNAPOTTER_URL"] = self.base

    def file(self, name, data=b"input"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = snapotter.main(list(argv))
        self.assertNotIn(KEY, out.getvalue() + err.getvalue(), "the API key leaked into output")
        return code, out.getvalue(), err.getvalue()

    # ── discovery ────────────────────────────────────────────────────────────

    def test_catalogue_has_every_tool(self):
        cat = snapotter.load_catalog()
        self.assertEqual(cat["count"], len(cat["tools"]))
        self.assertGreaterEqual(cat["count"], 250)

    def test_describe_unknown_tool_suggests(self):
        code, _, err = self.cli("describe", "compres-video")
        self.assertEqual(code, snapotter.EXIT_USAGE)
        self.assertIn("compress-video", err)

    # ── run ──────────────────────────────────────────────────────────────────

    def test_async_run_streams_progress_and_downloads_beside_input(self):
        clip = self.file("clip.mp4")
        code, out, _ = self.cli("run", "compress-video", clip, "--set", "quality=strong", "--json")
        self.assertEqual(code, 0)
        rec = json.loads(out)
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["output"], os.path.join(self.dir, "clip_compressed.mp4"))
        with open(rec["output"], "rb") as fh:
            self.assertEqual(fh.read(), b"RESULT:/api/v1/download/job-video/clip_compressed.mp4")
        post = next(r for r in FakeApi.requests if r[0] == "POST")
        self.assertIn(b'{"quality": "strong"}', post[3])
        self.assertIn(b'filename="clip.mp4"', post[3])

    def test_never_overwrites_without_force(self):
        clip = self.file("clip.mp4")
        existing = self.file("clip_compressed.mp4", b"KEEP")
        _, out, _ = self.cli("run", "compress-video", clip, "--json")
        self.assertEqual(json.loads(out)["output"], os.path.join(self.dir, "clip_compressed (1).mp4"))
        with open(existing, "rb") as fh:
            self.assertEqual(fh.read(), b"KEEP")
        _, out, _ = self.cli("run", "compress-video", clip, "--force", "--json")
        self.assertEqual(json.loads(out)["output"], existing)

    def test_result_fields_are_reported(self):
        scan = self.file("scan.png")
        _, out, _ = self.cli("run", "ocr", scan, "--json")
        self.assertEqual(json.loads(out)["result"], {"text": "Hello OCR"})

    def test_sync_tool_and_out_file(self):
        photo = self.file("photo.jpg")
        target = os.path.join(self.dir, "sub", "final.webp")
        code, out, _ = self.cli("run", "convert", photo, "--set", "format=webp", "--out", target)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), target)
        self.assertTrue(os.path.exists(target))

    def test_bad_choice_fails_before_upload(self):
        clip = self.file("clip.mp4")
        code, _, err = self.cli("run", "compress-video", clip, "--set", "quality=max")
        self.assertEqual(code, snapotter.EXIT_USAGE)
        self.assertIn("light, balanced, strong", err)
        self.assertFalse(any(r[0] == "POST" for r in FakeApi.requests))

    def test_unknown_option_fails_before_upload(self):
        clip = self.file("clip.mp4")
        code, _, err = self.cli("run", "compress-video", clip, "--set", "speed=fast")
        self.assertEqual(code, snapotter.EXIT_USAGE)
        self.assertIn("Options: quality, resolution", err)

    def test_required_setting_is_enforced(self):
        photo = self.file("photo.jpg")
        code, _, err = self.cli("run", "convert", photo)
        self.assertEqual(code, snapotter.EXIT_USAGE)
        self.assertIn("'format' is required", err)
        self.assertIn("webp", err)

    def test_server_rejection_exits_3_with_its_message(self):
        bad = self.file("broken.jpg")
        code, out, err = self.cli("run", "remove-background", bad, "--json")
        self.assertEqual(code, snapotter.EXIT_REJECTED)
        self.assertIn("Processing failed (not an image)", json.loads(out)["error"])

    def test_failed_job_exits_4(self):
        audio = self.file("talk.m4a")
        code, _, err = self.cli("run", "transcribe-audio", audio)
        self.assertEqual(code, snapotter.EXIT_FAILED)
        self.assertIn("model not installed", err)

    def test_json_body_generator(self):
        target = os.path.join(self.dir, "qr.png")
        code, out, _ = self.cli("run", "qr-generate", "--set", "text=hello", "--set", "size=300", "--out", target, "--json")
        self.assertEqual(code, 0)
        post = next(r for r in FakeApi.requests if r[0] == "POST")
        self.assertEqual(json.loads(post[3]), {"text": "hello", "size": 300})
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), b"\x89PNG fake")

    def test_generator_requires_its_required_field(self):
        code, _, err = self.cli("run", "qr-generate", "--out", os.path.join(self.dir, "q.png"))
        self.assertEqual(code, snapotter.EXIT_USAGE)
        self.assertIn("'text' is required", err)

    def test_multi_file_tool_sends_all_files(self):
        a, b = self.file("a.pdf"), self.file("b.pdf")
        code, out, _ = self.cli("run", "merge-pdf", a, b, "--json")
        self.assertEqual(code, 0)
        post = next(r for r in FakeApi.requests if r[0] == "POST")
        self.assertIn(b'filename="a.pdf"', post[3])
        self.assertIn(b'filename="b.pdf"', post[3])

    def test_single_file_tool_refuses_two_files_without_batch(self):
        a, b = self.file("a.jpg"), self.file("b.jpg")
        code, _, err = self.cli("run", "remove-background", a, b)
        self.assertEqual(code, snapotter.EXIT_USAGE)
        self.assertIn("--batch", err)

    def test_batch_continues_past_a_failure(self):
        good, bad = self.file("good.jpg"), self.file("broken.jpg")
        code, out, err = self.cli("run", "remove-background", "--batch", good, bad, "--json")
        self.assertEqual(code, snapotter.EXIT_FAILED)
        lines = [json.loads(line) for line in out.strip().splitlines()]
        self.assertEqual([r["ok"] for r in lines], [True, False])
        self.assertIn("1/2 OK", err)

    def test_detach_then_wait(self):
        clip = self.file("clip.mp4")
        _, out, _ = self.cli("run", "compress-video", clip, "--detach", "--json")
        rec = json.loads(out)
        self.assertEqual(rec["jobId"], "job-video")
        self.assertTrue(rec["detached"])
        self.assertFalse(any("/progress" in r[1] for r in FakeApi.requests))
        code, out, _ = self.cli("wait", "job-video", "--out", self.dir + os.sep, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["output"], os.path.join(self.dir, "clip_compressed.mp4"))

    def test_wait_timeout_on_quiet_stream_exits_6_on_time(self):
        import time
        start = time.time()
        try:
            code, _, err = self.cli("wait", "job-slow", "--timeout", "2")
        finally:
            FakeApi.release.set()
        self.assertEqual(code, snapotter.EXIT_PENDING)
        self.assertIn("snapotter wait job-slow", err)
        self.assertLess(time.time() - start, 5)

    # ── safety ───────────────────────────────────────────────────────────────

    def test_refuses_non_local_server_unless_explicit(self):
        os.environ["SNAPOTTER_URL"] = "http://example.com:1349"
        code, _, err = self.cli("status")
        self.assertEqual(code, snapotter.EXIT_USAGE)
        self.assertIn("refusing non-local server", err)

    def test_missing_key_file(self):
        os.environ["SNAPOTTER_KEY_FILE"] = os.path.join(self.dir, "nope")
        code, _, err = self.cli("status")
        self.assertEqual(code, snapotter.EXIT_ERROR)
        self.assertIn("API key file not found", err)

    def test_status_reports_catalogue_drift(self):
        code, out, err = self.cli("status", "--json")
        self.assertEqual(code, 0)
        rep = json.loads(out)
        self.assertEqual(rep["key"], "ok")
        self.assertEqual(rep["aiBundles"], {"transcription": "installed"})
        self.assertEqual(rep["notInCatalog"], ["image/brand-new"])
        self.assertEqual(len(rep["catalogOnly"]), 20)  # capped list of catalogue-only tools
        self.assertIn("rebuild the catalogue", err)

    def test_unreachable_server_exits_5(self):
        os.environ["SNAPOTTER_URL"] = "http://127.0.0.1:9"
        code, _, err = self.cli("status")
        self.assertEqual(code, snapotter.EXIT_UNREACHABLE)


if __name__ == "__main__":
    unittest.main()
