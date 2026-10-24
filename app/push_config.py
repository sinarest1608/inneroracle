"""Fail-closed checks for the push feature's secrets.

`secrets.compare_digest("", "")` is True, so an unset token must never reach a
comparison: startup refuses to run with weak settings, and every admin check
re-validates the configured token at request time as well.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid

from app.config import Settings

MIN_SECRET_LEN = 32


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def validate_push_settings(s: Settings) -> list[str]:
    """Return every problem with the push config (empty list = OK). Never
    includes secret values in the messages."""
    problems: list[str] = []
    if len(s.push_admin_token) < MIN_SECRET_LEN:
        problems.append(f"PUSH_ADMIN_TOKEN must be at least {MIN_SECRET_LEN} characters")
    if len(s.push_hmac_key) < MIN_SECRET_LEN:
        problems.append(f"PUSH_HMAC_KEY must be at least {MIN_SECRET_LEN} characters")
    if not s.vapid_subject.startswith(("mailto:", "https://")):
        problems.append("VAPID_SUBJECT must be a mailto: or https:// URL")
    if not s.push_rotation_seed:
        problems.append("PUSH_ROTATION_SEED must be set")
    try:
        ZoneInfo(s.push_timezone)
    except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError, ValueError, …
        problems.append(f"PUSH_TIMEZONE isn't a valid time zone name: {s.push_timezone!r} "
                        "(e.g. America/Los_Angeles — underscores, no spaces)")
    times = [t.strip() for t in s.push_daily_times.split(",") if t.strip()]
    if not times or len(times) > 4 or len(set(times)) != len(times) or not all(
            re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t) for t in times):
        problems.append(f"PUSH_DAILY_TIMES must be 1-4 distinct HH:MM times, comma-separated "
                        f"(got {s.push_daily_times!r})")
    try:
        date.fromisoformat(s.push_rotation_start)
    except ValueError:
        problems.append("PUSH_ROTATION_START must be a YYYY-MM-DD date")

    priv = pub = None
    try:
        priv = Vapid.from_string(s.vapid_private_key)
    except Exception:  # noqa: BLE001 — any parse failure means unusable
        problems.append("VAPID_PRIVATE_KEY is missing or doesn't parse")
    try:
        pub = _b64url_decode(s.vapid_public_key)
        if len(pub) != 65 or pub[0] != 4:
            raise ValueError
    except Exception:  # noqa: BLE001
        problems.append("VAPID_PUBLIC_KEY is missing or isn't a 65-byte P-256 point")
        pub = None
    if priv is not None and pub is not None:
        derived = priv.private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        if derived != pub:
            problems.append("VAPID_PUBLIC_KEY doesn't match VAPID_PRIVATE_KEY")

    db_dir = Path(s.push_db_path).parent
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(db_dir, os.W_OK):
            raise PermissionError
    except OSError:
        problems.append(f"PUSH_DB_PATH directory isn't writable: {db_dir}")
    return problems


def admin_token_ok(configured: str, given: str | None) -> bool:
    if len(configured) < MIN_SECRET_LEN or not given:
        return False
    return secrets.compare_digest(configured.encode(), given.encode())
