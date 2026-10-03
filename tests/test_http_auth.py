import json
import sys
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_tailor import __version__
from cv_tailor.api import CompanionHandler


class CompanionHttpAuthTests(unittest.TestCase):
    """Exercise the real HTTP handler without starting Word or an LLM."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.token = "test-token-used-only-by-the-isolated-http-server"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), CompanionHandler)
        cls.server.app = SimpleNamespace(
            token=cls.token,
            renderer=SimpleNamespace(name="libreoffice"),
            list_jobs=lambda: [],
            create_job=Mock(return_value={"job_id": "test-job", "state": "queued"}),
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, method, path, headers=None, body=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            value = json.loads(payload) if payload else None
            return response.status, value, dict(response.getheaders())
        finally:
            connection.close()

    def test_health_needs_no_token_and_reports_only_version_and_renderer(self) -> None:
        status, body, _ = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(
            body, {"status": "ok", "api_version": 1, "version": __version__, "renderer": "libreoffice"}
        )

    def test_get_jobs_accepts_bearer_when_chrome_omits_origin(self) -> None:
        status, body, _ = self.request(
            "GET", "/jobs", {"Authorization": f"Bearer {self.token}"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"jobs": []})

    def test_get_jobs_accepts_bearer_with_extension_origin(self) -> None:
        status, _, headers = self.request(
            "GET",
            "/jobs",
            {
                "Origin": "chrome-extension://test-extension",
                "Authorization": f"Bearer {self.token}",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            headers.get("Access-Control-Allow-Origin"),
            "chrome-extension://test-extension",
        )

    def test_job_endpoints_still_reject_missing_and_wrong_tokens(self) -> None:
        for headers in ({}, {"Authorization": "Bearer incorrect-token"}):
            with self.subTest(headers=headers):
                status, _, _ = self.request("GET", "/jobs", headers)
                self.assertEqual(status, 401)

    def test_job_endpoints_reject_ordinary_website_origin_even_with_token(self) -> None:
        status, _, headers = self.request(
            "GET",
            "/jobs",
            {
                "Origin": "https://example.com",
                "Authorization": f"Bearer {self.token}",
            },
        )
        self.assertEqual(status, 401)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_pairing_requires_extension_origin(self) -> None:
        for origin in (None, "https://example.com", "null"):
            with self.subTest(origin=origin):
                headers = {"Origin": origin} if origin else {}
                status, _, _ = self.request("POST", "/pair", headers)
                self.assertEqual(status, 403)

    def test_extension_can_pair_then_get_jobs_without_origin(self) -> None:
        status, pairing, _ = self.request(
            "POST", "/pair", {"Origin": "chrome-extension://test-extension"}
        )
        self.assertEqual(status, 200)
        status, body, _ = self.request(
            "GET", "/jobs", {"Authorization": f"Bearer {pairing['token']}"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"jobs": []})

    def test_authenticated_post_without_origin_reaches_job_handler(self) -> None:
        status, body, _ = self.request(
            "POST",
            "/jobs",
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            body=json.dumps({"description": "stub handled only by the mock"}),
        )
        self.assertEqual(status, 202)
        self.assertEqual(body["state"], "queued")


if __name__ == "__main__":
    unittest.main()
