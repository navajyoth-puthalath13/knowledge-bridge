"""Per-session protection for the local API.

The Electron main process generates a random token at launch and passes it to
the Python service through the environment. Every endpoint except /health
requires `Authorization: Bearer <token>`. Requests must also target a loopback
Host header, which blocks DNS-rebinding attacks from web pages. The renderer
never sees the token: it talks to the main process over IPC, and main makes
the HTTP calls.
"""
from __future__ import annotations

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}


class TokenAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in LOOPBACK_HOSTS:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        if request.url.path != "/health":
            auth = request.headers.get("authorization", "")
            supplied = auth[7:] if auth.startswith("Bearer ") else ""
            if not self.token or not hmac.compare_digest(supplied.encode(), self.token.encode()):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)
