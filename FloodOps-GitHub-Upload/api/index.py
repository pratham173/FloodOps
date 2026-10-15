"""Vercel Function adapter for the existing FloodOps HTTP handler.

The local server remains unchanged. Vercel rewrites /api/* to this single
function and passes the original route in the request_path query parameter.
"""
from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

# Function instances have writable temporary storage, not a durable project
# filesystem. This keeps the synthetic demo runnable on Vercel while making
# the storage limitation explicit in the deployment guide.
os.environ.setdefault("FLOODOPS_DB_PATH", "/tmp/floodops.sqlite3")

from server import Handler as LocalHandler  # noqa: E402


class handler(LocalHandler):
    def _restore_api_path(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        original_path = query.get("request_path", [""])[0]
        if not original_path.startswith("/api/"):
            self.respond(400, {"error": {"code": "invalid_route", "message": "Missing API route"}})
            return False
        self.path = original_path
        return True

    def do_GET(self) -> None:
        if self._restore_api_path():
            super().do_GET()

    def do_POST(self) -> None:
        if self._restore_api_path():
            super().do_POST()
