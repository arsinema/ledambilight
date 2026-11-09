import hashlib
import http.server
import os
import socketserver
import sys
import tempfile
import threading
import unittest
from unittest import mock

from ledapp import updater, updates


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


def asset(name, url, data=b"", sha=None):
    return updates.ReleaseAsset(
        name=name, url=url, size=len(data),
        sha256=sha if sha is not None else hashlib.sha256(data).hexdigest(),
    )


class UpdaterTests(unittest.TestCase):
    def test_check_for_update_parses_assets(self):
        payload = {
            "tag_name": "v1.5.0", "html_url": "https://example/rel",
            "body": "notes",
            "assets": [{
                "name": "LEDAmbilight.exe",
                "browser_download_url": "https://github.com/x/y/releases/download/v1.5.0/LEDAmbilight.exe",
                "size": 123, "digest": "sha256:" + "a" * 64,
            }],
        }
        with mock.patch.object(updates.requests, "get", return_value=FakeResponse(payload)):
            info = updates.check_for_update("1.4.1")
        self.assertTrue(info.available)
        self.assertEqual(info.latest, "1.5.0")
        self.assertEqual(len(info.assets), 1)
        self.assertEqual(info.assets[0].sha256, "a" * 64)
        self.assertEqual(info.assets[0].size, 123)

    def test_select_asset_and_validation(self):
        good = asset(
            "LEDAmbilight.exe",
            "https://github.com/arsinema/ledambilight/releases/download/v1.5.0/LEDAmbilight.exe",
            b"x" * 10,
        )
        info = updates.UpdateInfo("1.4.1", "1.5.0", True, "u", assets=(good,))
        self.assertIs(updater.select_asset(info), good)
        updater._validate_asset(good)
        with mock.patch.object(updater, "self_update_supported", return_value=True):
            self.assertTrue(updater.can_self_update(info))
        bad = updates.ReleaseAsset(good.name, "https://evil.example/x.exe", 10, "a" * 64)
        with self.assertRaises(updater.UpdateError):
            updater._validate_asset(bad)
        no_hash = updates.ReleaseAsset(good.name, good.url, 10, "")
        with self.assertRaises(updater.UpdateError):
            updater._validate_asset(no_hash)

    def test_update_script_contains_rollback(self):
        text = updater.build_update_script(
            r"C:\Apps\LEDAmbilight.exe", r"C:\Temp\LEDAmbilight.exe", r"C:\Temp\up.ps1"
        )
        self.assertIn("Copy-Item", text)
        self.assertIn("$old", text)
        self.assertIn("Start-Process", text)
        self.assertIn("Start-Sleep", text)

    def test_download_local_http_with_progress_and_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            data = os.urandom(64 * 1024)
            path = os.path.join(folder, "payload.bin")
            with open(path, "wb") as f:
                f.write(data)

            class Handler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *a, **k):
                    super().__init__(*a, directory=folder, **k)
                def log_message(self, *a):
                    pass

            server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                port = server.server_address[1]
                url = f"http://127.0.0.1:{port}/payload.bin"
                good = asset("LEDAmbilight.exe", url, data)
                info = updates.UpdateInfo("1.4.1", "1.5.0", True, "u", assets=(good,))
                progress = []
                with mock.patch.object(updater, "ALLOWED_HOSTS", updater.ALLOWED_HOSTS + ("127.0.0.1",)):
                    out = updater.download_update(
                        info, os.path.join(folder, "out"),
                        progress=lambda d, t: progress.append((d, t)),
                    )
                self.assertTrue(os.path.isfile(out))
                with open(out, "rb") as handle:
                    self.assertEqual(handle.read(), data)
                self.assertTrue(progress)
                self.assertEqual(progress[-1][0], len(data))
            finally:
                server.shutdown()
                server.server_close()

    def test_download_rejects_bad_hash(self):
        class BadResponse:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def raise_for_status(self): pass
            def iter_content(self, chunk_size=1): yield b"abc"

        with tempfile.TemporaryDirectory() as folder:
            good = asset("LEDAmbilight.exe", "https://github.com/x", b"abc", sha="0" * 64)
            info = updates.UpdateInfo("1.4.1", "1.5.0", True, "u", assets=(good,))
            with mock.patch.object(updater.requests, "get", return_value=BadResponse()):
                with self.assertRaises(updater.UpdateError):
                    updater.download_update(info, os.path.join(folder, "out"))

    def test_onefile_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = os.path.join(folder, "LEDAmbilight.exe")
            open(exe, "wb").close()
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", exe):
                self.assertEqual(updater.app_distribution(), "onefile")
                self.assertTrue(updater.self_update_supported())
            os.makedirs(os.path.join(folder, "_internal"))
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", exe):
                self.assertEqual(updater.app_distribution(), "onedir")
                self.assertFalse(updater.self_update_supported())

    def test_download_can_be_cancelled(self):
        class SlowResponse:
            headers = {"Content-Length": "3"}
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def raise_for_status(self): pass
            def iter_content(self, chunk_size=1): yield b"abc"

        with tempfile.TemporaryDirectory() as folder:
            good = asset("LEDAmbilight.exe", "https://github.com/x", b"abc")
            info = updates.UpdateInfo("1.4.1", "1.5.0", True, "u", assets=(good,))
            cancel = threading.Event()
            cancel.set()
            with mock.patch.object(updater.requests, "get", return_value=SlowResponse()):
                with self.assertRaises(updater.UpdateCancelled):
                    updater.download_update(info, os.path.join(folder, "out"), cancel=cancel)

    def test_source_not_self_updatable(self):
        self.assertEqual(updater.app_distribution(), "source")
        self.assertFalse(updater.self_update_supported())


if __name__ == "__main__":
    unittest.main()
