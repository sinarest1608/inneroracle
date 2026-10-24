"""Input checks for push sign-ups: subscription shape and the real client IP.

Subscriptions are checked strictly up front because pywebpush's own key check is
incomplete (an empty p256dh raises IndexError, a non-curve point ValueError) and
one bad row must never reach the send loop.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Request

MAX_ENDPOINT_LEN = 1024
PUSH_HOSTS = {"web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com"}
PUSH_HOST_SUFFIXES = (".notify.windows.com",)

UNKNOWN_IP = "unknown"


class InvalidSubscription(ValueError):
    pass


def _b64url_decode(s: str) -> bytes:
    if not isinstance(s, str) or not s or len(s) > 200:
        raise InvalidSubscription("bad base64url")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except Exception as e:  # noqa: BLE001
        raise InvalidSubscription("bad base64url") from e


def validate_endpoint(endpoint: str) -> None:
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT_LEN:
        raise InvalidSubscription("endpoint length")
    try:
        u = urlsplit(endpoint)
        port = u.port
    except ValueError as e:
        raise InvalidSubscription("endpoint shape") from e
    if u.scheme != "https" or u.username or u.password or port not in (None, 443):
        raise InvalidSubscription("endpoint shape")
    host = (u.hostname or "").lower()
    if host not in PUSH_HOSTS and not host.endswith(PUSH_HOST_SUFFIXES):
        raise InvalidSubscription("endpoint host")


def validate_keys(p256dh: str, auth: str) -> None:
    raw = _b64url_decode(p256dh)
    if len(raw) != 65 or raw[0] != 0x04:
        raise InvalidSubscription("p256dh")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as e:
        raise InvalidSubscription("p256dh") from e
    if len(_b64url_decode(auth)) != 16:
        raise InvalidSubscription("auth")


def validate_subscription(endpoint: str, p256dh: str, auth: str) -> None:
    validate_endpoint(endpoint)
    validate_keys(p256dh, auth)


# ---- client IP + rate-limit keys --------------------------------------------

def canonical_ip(raw: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((raw or "").strip()))
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    """The visitor's IP as Cloudflare saw it. Never `request.client.host`: with
    --forwarded-allow-ips "*" that is the client-controlled leftmost
    X-Forwarded-For entry. Missing/invalid header → one shared strict bucket."""
    return canonical_ip(request.headers.get("cf-connecting-ip")) or UNKNOWN_IP


def rate_keys(ip: str) -> tuple[str, str | None]:
    """(narrow, wide): IPv4 → (addr, None); IPv6 → (/64, /48)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip, None
    if addr.version == 4:
        return str(addr), None
    return (str(ipaddress.ip_network(f"{addr}/64", strict=False)),
            str(ipaddress.ip_network(f"{addr}/48", strict=False)))


def keyed_hash(key: str, value: str) -> str:
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()
