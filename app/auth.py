"""Human gate for the money-spending endpoint.

A pure-ASGI middleware (not BaseHTTPMiddleware, which buffers and breaks the /chat
SSE stream). When Turnstile is configured, POSTs to /chat require a valid signed
`io_human` cookie (issued by /verify-human after an invisible Turnstile check).
Everything else — the page, static assets, /verse, /health — stays open so the
widget can load and run. Only /chat costs Anthropic money, so only /chat is gated.
"""

from __future__ import annotations

from http.cookies import SimpleCookie

from app.turnstile import COOKIE, valid_cookie

GATED_PREFIXES = ("/chat",)


class HumanGateMiddleware:
    def __init__(self, app, enabled: bool):
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not self.enabled
            or not scope["path"].startswith(GATED_PREFIXES)
            or self._has_cookie(scope)
        ):
            await self.app(scope, receive, send)
            return
        await _json_401(send)

    def _has_cookie(self, scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"cookie":
                jar = SimpleCookie()
                jar.load(value.decode("latin-1"))
                return COOKIE in jar and valid_cookie(jar[COOKIE].value)
        return False


async def _json_401(send):
    body = b'{"error":"verification required"}'
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})
