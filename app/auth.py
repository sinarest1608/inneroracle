"""Shared-token auth gate.

A pure ASGI middleware (not Starlette's BaseHTTPMiddleware, which buffers the
response body and breaks the /chat SSE stream). When `settings.access_token` is
set, every request must carry a matching `io_token` cookie; otherwise browser
navigations are redirected to /login and API calls get a 401.
"""

from __future__ import annotations

import secrets
from http.cookies import SimpleCookie

COOKIE = "io_token"
# Reachable without a token (the login flow itself, health checks, favicon).
OPEN_PATHS = {"/login", "/logout", "/health", "/favicon.ico"}
# Unauthenticated calls to these get a JSON 401 instead of an HTML redirect.
API_PREFIXES = ("/chat", "/verse")


class TokenAuthMiddleware:
    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.token:
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if path in OPEN_PATHS or self._authed(scope):
            await self.app(scope, receive, send)
            return

        if path.startswith(API_PREFIXES):
            await _send(send, 401, b'{"error":"unauthorized"}', "application/json")
        else:
            nxt = path or "/"
            await _send(send, 303, b"", "text/plain", extra=[(b"location", f"/login?next={nxt}".encode())])

    def _authed(self, scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"cookie":
                jar = SimpleCookie(); jar.load(value.decode("latin-1"))
                tok = jar[COOKIE].value if COOKIE in jar else ""
                return bool(tok) and secrets.compare_digest(tok, self.token)
        return False


async def _send(send, status: int, body: bytes, ctype: str, extra=None):
    headers = [(b"content-type", ctype.encode()), (b"content-length", str(len(body)).encode())]
    if extra:
        headers.extend(extra)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
