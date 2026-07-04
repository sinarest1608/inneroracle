"""Cloudflare Turnstile verification + a stateless signed "human" cookie.

The gate proves a visitor is human once (invisible Turnstile), then issues a
short-lived HMAC-signed cookie so /chat calls don't re-verify every request. No
database — the cookie carries its own expiry and signature.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

import httpx

from app.config import settings

COOKIE = "io_human"
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_token(token: str, remoteip: str | None = None) -> bool:
    """Validate a Turnstile response token with Cloudflare."""
    if not token:
        return False
    data = {"secret": settings.turnstile_secret, "response": token}
    if remoteip:
        data["remoteip"] = remoteip
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(SITEVERIFY_URL, data=data)
        return bool(resp.json().get("success"))
    except Exception:  # noqa: BLE001 — network hiccup => treat as unverified
        return False


def _sign(expiry: int) -> str:
    mac = hmac.new(
        settings.session_secret.encode(), str(expiry).encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(mac).decode()


def sign_cookie(ttl_seconds: int = 12 * 3600) -> str:
    expiry = int(time.time()) + ttl_seconds
    return f"{expiry}.{_sign(expiry)}"


def valid_cookie(value: str) -> bool:
    if not value or "." not in value:
        return False
    expiry_str, sig = value.split(".", 1)
    if not expiry_str.isdigit() or int(expiry_str) < time.time():
        return False
    return secrets.compare_digest(sig, _sign(int(expiry_str)))
