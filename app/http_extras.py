"""Per-path response headers and the access-log filter.

The headers middleware is raw ASGI on purpose: Starlette's BaseHTTPMiddleware
buffers response bodies, which breaks /chat's SSE streaming (tasks/lessons.md).
It only touches the exact static paths below and passes everything else through.
"""

from __future__ import annotations

import logging

ADMIN_HEADERS = [
    (b"content-security-policy",
     b"default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-robots-tag", b"noindex"),
    (b"cache-control", b"no-store"),
]
SW_HEADERS = [
    (b"content-type", b"application/javascript"),
    (b"cache-control", b"no-cache"),
]
# App code: browsers must revalidate on every load (a cheap 304 when unchanged),
# so a deploy reaches everyone without anyone force-refreshing. Without a
# Cache-Control header, browsers may reuse an old copy for hours or days.
REVALIDATE = [(b"cache-control", b"no-cache")]
_APP_CODE = (".html", ".js", ".css", ".webmanifest")


def _headers_for(path: str) -> list[tuple[bytes, bytes]] | None:
    if path == "/ui/sw.js":
        return SW_HEADERS
    if path.startswith("/ui/admin/"):
        return ADMIN_HEADERS
    if path.startswith("/ui/") and (path.endswith("/") or path.endswith(_APP_CODE)):
        return REVALIDATE
    return None


class PathHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        extra = _headers_for(scope.get("path", "")) if scope["type"] == "http" else None
        if extra is None:
            return await self.app(scope, receive, send)
        names = {k for k, _ in extra}

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in names]
                message = {**message, "headers": kept + extra}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class DropPushAccessLogs(logging.Filter):
    """Keep /push/* (sign-ups, check-ins) out of uvicorn's access log, which
    records the client IP and the query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            return not args[2].startswith("/push/")
        return True


def install_access_log_filter() -> None:
    lg = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, DropPushAccessLogs) for f in lg.filters):
        lg.addFilter(DropPushAccessLogs())
