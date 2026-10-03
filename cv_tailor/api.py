"""The companion's loopback HTTP API for the browser extension: pairing, auth, CORS and routes."""

from __future__ import annotations

import hmac
import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from . import __version__
from .applicant import PROFILE_FIELDS
from .companion import TailoringCompanion
from .docx_ops import PlanError
from .intake import optional_string, required_string
from .logs import event


class CompanionServer(ThreadingHTTPServer):
    """HTTP server that carries the companion, so request handlers can reach it."""

    def __init__(self, address: tuple[str, int], app: TailoringCompanion) -> None:
        super().__init__(address, CompanionHandler)
        self.app = app


class CompanionHandler(BaseHTTPRequestHandler):
    server_version = "CVTailorCompanion/1.0"

    @property
    def app(self) -> TailoringCompanion:
        return cast(CompanionServer, self.server).app

    def do_OPTIONS(self) -> None:
        if not self._extension_origin():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/health":
                # Unauthenticated, so it reports only what helps diagnose a setup: no paths, no job data.
                self._send_json(
                    {"status": "ok", "api_version": 1, "version": __version__, "renderer": self.app.renderer.name}
                )
                return
            if not self._authorized():
                self._send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            if path == "/jobs":
                self._send_json({"jobs": self.app.list_jobs()})
                return
            match = re.fullmatch(r"/jobs/([^/]+)", path)
            if match:
                self._send_json(self.app.get_job(match.group(1)))
                return
            if path == "/profile":
                self._send_json({"profile": self.app.profile.load(), "fields": PROFILE_FIELDS})
                return
            match = re.fullmatch(r"/jobs/([^/]+)/file", path)
            if match:
                what = parse_qs(urlparse(self.path).query).get("what", [""])[0]
                self._send_json(self.app.job_file(match.group(1), what))
                return
            if path == "/applications":
                self._send_json(self.app.knowledge.applications_summary())
                return
            if path == "/knowledge":
                query = parse_qs(urlparse(self.path).query).get("q", [""])[0][:200]
                self._send_json({"items": self.app.knowledge.search(query)})
                return
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except FileNotFoundError as error:
            self._send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            event("http_error", logging.ERROR, method=self.command, path=urlparse(self.path).path,
                  error=type(error).__name__, detail=error)
            self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/pair":
                if not self._extension_origin():
                    self._send_json({"error": "Pairing is available only to a Chrome extension"}, HTTPStatus.FORBIDDEN)
                    return
                self._send_json({"token": self.app.token})
                return
            if not self._authorized():
                self._send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return
            body = self._read_body()
            if path == "/jobs":
                self._send_json(self.app.create_job(body), HTTPStatus.ACCEPTED)
                return
            if path == "/profile":
                values = body.get("values")
                if not isinstance(values, dict):
                    raise ValueError("values must be an object")
                self._send_json({"profile": self.app.profile.save(values)})
                return
            if path == "/knowledge/notes":
                new_id = self.app.knowledge.add(
                    "note", "added in extension", required_string(body, "text", 4000),
                    optional_string(body.get("topic"), 200),
                )
                self._send_json({"id": new_id}, HTTPStatus.CREATED)
                return
            if path == "/knowledge/import-cvs":
                self._send_json(
                    self.app.knowledge.import_cvs(self.app.cv_library, exclude={self.app.master_cv}, require_cv_in_name=False)
                )
                return
            if path == "/knowledge/import-chat":
                added = self.app.knowledge.import_chat_export(
                    optional_string(body.get("label"), 100) or "unnamed",
                    required_string(body, "text", 1_500_000),
                )
                self._send_json({"added": added})
                return
            match = re.fullmatch(r"/knowledge/(\d+)/(retire|correct)", path)
            if match:
                record_id = int(match.group(1))
                if match.group(2) == "retire":
                    if not self.app.knowledge.retire(record_id):
                        raise FileNotFoundError("No active knowledge record with that id")
                    self._send_json({"retired": record_id})
                else:
                    new_id = self.app.knowledge.correct(record_id, required_string(body, "text", 4000))
                    self._send_json({"id": new_id})
                return
            match = re.fullmatch(r"/jobs/([^/]+)/(proceed|cover-letter|application|open)", path)
            if match:
                job_id, action = match.groups()
                if action == "proceed":
                    self._send_json(self.app.proceed_low_fit(job_id), HTTPStatus.ACCEPTED)
                elif action == "cover-letter":
                    self._send_json(self.app.request_cover_letter(job_id), HTTPStatus.ACCEPTED)
                elif action == "application":
                    self._send_json(self.app.set_application_status(job_id, str(body.get("status", ""))))
                else:
                    self._send_json(self.app.open_file(job_id, str(body.get("what", ""))))
                return
            match = re.fullmatch(r"/jobs/([^/]+)/answers", path)
            if match:
                self._send_json(
                    self.app.submit_answers(match.group(1), body.get("answers")),
                    HTTPStatus.ACCEPTED,
                )
                return
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, PlanError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except FileNotFoundError as error:
            self._send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            event("http_error", logging.ERROR, method=self.command, path=urlparse(self.path).path,
                  error=type(error).__name__, detail=error)
            self._send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if length <= 0 or length > 2_000_000:
            raise ValueError("Request body must contain no more than 2 MB")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be valid UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def _authorized(self) -> bool:
        # Chrome's privileged extension GET requests may omit Origin. The
        # bearer token authenticates job requests; reject an explicit website
        # origin, but do not confuse a missing header with an invalid token.
        origin = self.headers.get("Origin", "")
        if origin and not self._extension_origin():
            return False
        authorization = self.headers.get("Authorization", "")
        expected = f"Bearer {self.app.token}"
        return hmac.compare_digest(authorization, expected)

    def _extension_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        return origin.startswith("chrome-extension://") and len(origin) < 200

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if self._extension_origin():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format_string: str, *args: Any) -> None:
        return
