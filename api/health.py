from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(
            200,
            {
                "status": "ok",
                "service": "alphapulse-edge-health",
                "database_configured": bool(str(os.getenv("DATABASE_URL") or "").strip()),
                "telegram_env_configured": bool(str(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()),
                "source": {
                    "repository": "/".join(
                        p
                        for p in (
                            str(os.getenv("VERCEL_GIT_REPO_OWNER") or "").strip(),
                            str(os.getenv("VERCEL_GIT_REPO_SLUG") or "").strip(),
                        )
                        if p
                    )
                    or "unknown",
                    "ref": str(os.getenv("VERCEL_GIT_COMMIT_REF") or "unknown"),
                    "sha": str(os.getenv("VERCEL_GIT_COMMIT_SHA") or "unknown"),
                },
            },
        )

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
