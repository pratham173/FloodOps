"""Loopback-only FloodOps API and static web server."""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from application import Application
from infrastructure.repository import Repository

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
repo = Repository()
app = Application(repo)


class Handler(BaseHTTPRequestHandler):
    server_version = "FloodOps/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def respond(self, status: int, body: object, content_type: str = "application/json; charset=utf-8") -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode() if content_type.startswith("application/json") else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(encoded)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 64_000:
            raise ValueError("Request body is too large")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                self.respond(200, repo.state())
                return
            if path == "/api/sessions":
                self.respond(200, repo.sessions())
                return
            file_path = (WEB / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
            if not file_path.is_relative_to(WEB.resolve()) or not file_path.is_file():
                self.respond(404, {"error": {"code":"not_found", "message":"Page not found"}})
                return
            self.respond(200, file_path.read_bytes(), mimetypes.guess_type(file_path)[0] or "application/octet-stream")
        except Exception:
            self.respond(500, {"error": {"code":"internal_error", "message":"Could not load application data"}})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.body()
            if path == "/api/algorithm":
                repo.set_algorithm(payload.get("algorithm", ""))
                result = {"state": repo.state()}
            elif path == "/api/dispatch":
                result = app.run_dispatch()
            elif path == "/api/simulation":
                result = {"state": app.toggle(payload.get("status", ""))}
            elif path == "/api/step":
                result = app.advance()
            elif path == "/api/events":
                key = self.headers.get("Idempotency-Key") or payload.get("idempotency_key", "")
                if not isinstance(key, str) or not key or len(key) > 120:
                    raise ValueError("Provide an Idempotency-Key of 1–120 characters")
                result = {"state":app.inject_event(payload.get("kind", ""),payload.get("payload", {}),key)}
            elif path == "/api/incidents":
                result = {"state":app.create_incident(payload)}
            elif path == "/api/reset":
                result = {"state":repo.reset(payload.get("scenario","baseline"),payload.get("seed",20261015))}
            else:
                self.respond(404, {"error":{"code":"not_found","message":"API operation not found"}})
                return
            self.respond(200, result)
        except ValueError as exc:
            self.respond(422, {"error":{"code":"validation_error","message":str(exc)}})
        except KeyError as exc:
            self.respond(404, {"error":{"code":"not_found","message":str(exc).strip("'")}})
        except Exception as exc:
            print(f"API error: {type(exc).__name__}: {exc}")
            self.respond(409, {"error":{"code":"conflict","message":"The change conflicts with the current simulation state"}})


if __name__ == "__main__":
    address = "127.0.0.1"
    port = 8000
    print(f"FloodOps running at http://{address}:{port} (local synthetic simulation)")
    ThreadingHTTPServer((address, port), Handler).serve_forever()
