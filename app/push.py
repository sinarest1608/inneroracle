"""Push notification API: public sign-up, device-scoped calls, admin endpoints.

Mounted only when PUSH_ENABLED. No endpoint accepts notification text or links:
every payload is built server-side from verse data or fixed messages. Tokens and
secrets are only ever read from headers, never query strings (uvicorn logs those).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.daily_verse import verse_of_the_day
from app.push_config import admin_token_ok
from app.push_db import audit, db, utcnow, write_tx
from app.push_service import (
    ForcedAlreadyToday, force_send, run_daily, send_confirmation, send_to_device,
    test_message,
)
from app.push_validation import (
    InvalidSubscription, canonical_ip, client_ip, keyed_hash, rate_keys,
    validate_subscription,
)
from app.quote import QuoteWriter
from app.store import VerseStore

router = APIRouter()

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@lru_cache(maxsize=1)
def _store() -> VerseStore:
    return VerseStore(settings.sqlite_path, readonly=True)


@lru_cache(maxsize=1)
def _writer() -> QuoteWriter:
    return QuoteWriter()


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _hour_ago() -> str:
    return (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- models ------------------------------------------------------------------

class SubKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=200)


class Subscription(BaseModel):
    endpoint: str = Field(max_length=2048)
    keys: SubKeys


class BulkDelete(BaseModel):
    created_from: str = Field(max_length=20)
    created_to: str = Field(max_length=20)
    expected_count: int = Field(ge=0)


# ---- public: VAPID key + sign-up ------------------------------------------------

@router.get("/push/vapid-public-key")
def vapid_public_key() -> dict:
    return {"key": settings.vapid_public_key}


def _reject(bucket: str, result: str) -> None:
    """Rejections are counted, never logged as rows (a flood can't bloat the table)."""
    hour = datetime.now(UTC).strftime("%Y-%m-%dT%H:00:00Z")
    with db() as conn:
        conn.execute(
            "INSERT INTO signup_rejections(hour, bucket_hash, result, count) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(hour, bucket_hash, result) DO UPDATE SET count = count + 1",
            (hour, bucket, result),
        )


def _new_secret(conn: sqlite3.Connection, device_id: int) -> str:
    secret = secrets.token_urlsafe(32)
    conn.execute("UPDATE push_subscriptions SET secret_hash = ? WHERE id = ?",
                 (_hash_secret(secret), device_id))
    return secret


@router.post("/push/subscribe")
async def subscribe(sub: Subscription, request: Request) -> JSONResponse:
    key = settings.push_hmac_key
    ip = client_ip(request)
    narrow, wide = rate_keys(ip)
    bucket_hash = keyed_hash(key, narrow)
    wide_hash = keyed_hash(key, wide) if wide else None
    reject_bucket = wide_hash or bucket_hash  # IPv6 rejections counted per /48
    ip_hash = keyed_hash(key, ip)
    raw_ip = ip if ip != "unknown" else None
    endpoint, p256dh, auth = sub.endpoint, sub.keys.p256dh, sub.keys.auth

    try:
        validate_subscription(endpoint, p256dh, auth)
    except InvalidSubscription:
        _reject(reject_bucket, "invalid")
        raise HTTPException(422, "invalid subscription") from None

    def attempt(conn: sqlite3.Connection, result: str, charged: bool) -> int:
        return conn.execute(
            "INSERT INTO signup_attempts(at, ip, ip_hash, bucket_hash, wide_hash, result, "
            "charged) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (utcnow(), raw_ip, ip_hash, bucket_hash, wide_hash, result, int(charged)),
        ).lastrowid

    def reregister(conn: sqlite3.Connection, row: sqlite3.Row) -> JSONResponse | None:
        """Same endpoint + same keys = this browser after clearing storage: free.
        Different keys = someone who only knows the endpoint: refuse."""
        if row["p256dh"] != p256dh or row["auth"] != auth:
            return None
        secret = _new_secret(conn, row["id"])
        attempt(conn, "reregistered", False)
        return JSONResponse({"device_id": row["id"], "device_secret": secret})

    with db() as conn:
        # 1. existing endpoint → re-register (free) or 409; never replace keys here
        with write_tx(conn):
            row = conn.execute("SELECT id, p256dh, auth FROM push_subscriptions "
                               "WHERE endpoint = ?", (endpoint,)).fetchone()
            if row is not None:
                resp = reregister(conn, row)
        if row is not None:
            if resp is None:
                _reject(reject_bucket, "conflict")
                raise HTTPException(409, "subscription conflict")
            return resp

        # 2. new endpoint → limits, then reserve the charge atomically
        since = _hour_ago()
        with write_tx(conn):
            charged = lambda col, val: conn.execute(  # noqa: E731
                f"SELECT COUNT(*) FROM signup_attempts WHERE {col} = ? AND charged = 1 "
                "AND at >= ?", (val, since)).fetchone()[0]
            if charged("bucket_hash", bucket_hash) >= settings.push_subscribe_rate or (
                wide_hash and charged("wide_hash", wide_hash) >= settings.push_subscribe_rate_wide
            ):
                limited = "rate_limited"
            elif conn.execute("SELECT COUNT(*) FROM signup_attempts WHERE charged = 1 "
                              "AND at >= ?", (since,)).fetchone()[0] \
                    >= settings.push_signup_budget_per_hour or \
                    conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] \
                    >= settings.push_max_devices:
                limited = "budget_exceeded"
            else:
                limited = None
                attempt_id = attempt(conn, "confirm_failed", True)  # upgraded on success
        if limited:
            _reject(reject_bucket, limited)
            raise HTTPException(429, "too many sign-ups, try again later")

    # 3. confirmation push: only a subscription the push service accepts is stored
    o = await send_confirmation(endpoint, p256dh, auth)
    if o.status != "delivered":
        raise HTTPException(422, "confirmation push failed")

    with db() as conn, write_tx(conn):
        row = conn.execute("SELECT id, p256dh, auth FROM push_subscriptions "
                           "WHERE endpoint = ?", (endpoint,)).fetchone()
        if row is not None:  # a concurrent sign-up for the same endpoint won
            resp = reregister(conn, row)
            if resp is None:
                raise HTTPException(409, "subscription conflict")
            return resp
        if conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] \
                >= settings.push_max_devices:
            raise HTTPException(429, "too many sign-ups, try again later")
        secret = secrets.token_urlsafe(32)
        device_id = conn.execute(
            "INSERT INTO push_subscriptions(endpoint, p256dh, auth, secret_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (endpoint, p256dh, auth, _hash_secret(secret), utcnow())
        ).lastrowid
        conn.execute("UPDATE signup_attempts SET result = 'created' WHERE id = ?", (attempt_id,))
    return JSONResponse({"device_id": device_id, "device_secret": secret})


# ---- device-scoped -------------------------------------------------------------

def device(
    x_device_id: str | None = Header(default=None),
    x_device_secret: str | None = Header(default=None),
) -> sqlite3.Row:
    if not x_device_id or not x_device_secret or not x_device_id.isdigit() \
            or len(x_device_secret) > 200:
        raise HTTPException(401, "device credentials required")
    with db() as conn:
        row = conn.execute("SELECT * FROM push_subscriptions WHERE id = ?",
                           (int(x_device_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "unknown device")
    if not secrets.compare_digest(row["secret_hash"], _hash_secret(x_device_secret)):
        raise HTTPException(401, "bad device credentials")
    return row


@router.post("/push/device/update")
async def device_update(sub: Subscription, dev: sqlite3.Row = Depends(device)) -> dict:
    endpoint, p256dh, auth = sub.endpoint, sub.keys.p256dh, sub.keys.auth
    try:
        validate_subscription(endpoint, p256dh, auth)
    except InvalidSubscription:
        raise HTTPException(422, "invalid subscription") from None
    if endpoint != dev["endpoint"]:
        with db() as conn:
            if conn.execute("SELECT 1 FROM push_subscriptions WHERE endpoint = ?",
                            (endpoint,)).fetchone():
                raise HTTPException(409, "endpoint belongs to another device")
        if (await send_confirmation(endpoint, p256dh, auth)).status != "delivered":
            raise HTTPException(422, "confirmation push failed")
    with db() as conn:
        try:
            conn.execute("UPDATE push_subscriptions SET endpoint = ?, p256dh = ?, auth = ?, "
                         "failure_count = 0 WHERE id = ?", (endpoint, p256dh, auth, dev["id"]))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "endpoint belongs to another device") from None
    return {"device_id": dev["id"]}


@router.post("/push/unsubscribe", status_code=204)
def unsubscribe(dev: sqlite3.Row = Depends(device)) -> Response:
    with db() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (dev["id"],))
    return Response(status_code=204)


@router.get("/push/device/check", status_code=204)
def device_check(dev: sqlite3.Row = Depends(device)) -> Response:
    with db() as conn:
        conn.execute("UPDATE push_subscriptions SET last_check_at = ? WHERE id = ?",
                     (utcnow(), dev["id"]))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


# ---- admin ---------------------------------------------------------------------

def admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not admin_token_ok(settings.push_admin_token, x_admin_token):
        raise HTTPException(403, "forbidden")


def _local(ts: str | None) -> tuple[str, str]:
    if not ts:
        return "", ""
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    loc = dt.astimezone(ZoneInfo(settings.push_timezone))
    return loc.date().isoformat(), loc.strftime("%H:%M:%S")


def _range(frm: str | None, to: str | None) -> tuple[str, str]:
    for d in (frm, to):
        if d is not None and not _DATE.match(d):
            raise HTTPException(422, "dates must be YYYY-MM-DD")
    return (f"{frm}T00:00:00Z" if frm else "0000"), (f"{to}T23:59:59Z" if to else "9999")


def _csv_cell(v: object) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _csv(rows: list[dict], name: str) -> Response:
    buf = io.StringIO()
    if rows:
        w = csv.writer(buf)
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow([_csv_cell(v) for v in r.values()])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"',
                             "Cache-Control": "no-store"})


@router.post("/push/test", dependencies=[Depends(admin)])
async def push_test(device_id: int = Query(ge=1)) -> dict:
    with db() as conn:
        row = conn.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions "
                           "WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown device")
        audit(conn, "test_send", f"device={device_id}")
    o = await send_to_device(row, test_message())
    return {"device_id": device_id, "status": o.status, "http_status": o.http_status}


@router.post("/push/send-daily", dependencies=[Depends(admin)])
async def push_send_daily(force: bool = False) -> dict:
    if force:
        try:
            n = await force_send(_store(), _writer())
        except ForcedAlreadyToday:
            raise HTTPException(429, "a forced send already ran today") from None
        return {"result": "forced", "devices": n,
                "note": "re-sent today's verse to ALL devices as kind=manual"}
    return {"result": await run_daily(_store(), _writer())}


@router.get("/push/devices", dependencies=[Depends(admin)])
def push_devices() -> dict:
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, created_at, last_success_at, last_check_at, failure_count "
            "FROM push_subscriptions ORDER BY id")]
    return {"total": len(rows), "devices": rows}


@router.delete("/push/devices/{device_id}", dependencies=[Depends(admin)])
def push_delete_device(device_id: int) -> dict:
    with db() as conn, write_tx(conn):
        n = conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (device_id,)).rowcount
        if not n:
            raise HTTPException(404, "unknown device")
        audit(conn, "delete_device", f"device={device_id}")
    return {"deleted": device_id}


def _bound(value: str, end: bool) -> str:
    if _DATE.match(value):
        return f"{value}T23:59:59Z" if end else f"{value}T00:00:00Z"
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", value):
        return value
    raise HTTPException(422, "use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ (UTC)")


@router.post("/push/devices/bulk-delete", dependencies=[Depends(admin)])
def push_bulk_delete(body: BulkDelete) -> dict:
    lo, hi = _bound(body.created_from, False), _bound(body.created_to, True)
    with db() as conn, write_tx(conn):
        n = conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE created_at >= ? "
                         "AND created_at <= ?", (lo, hi)).fetchone()[0]
        if n != body.expected_count:
            raise HTTPException(409, f"range holds {n} devices, not {body.expected_count}")
        conn.execute("DELETE FROM push_subscriptions WHERE created_at >= ? AND created_at <= ?",
                     (lo, hi))
        audit(conn, "bulk_delete", f"from={lo} to={hi} count={n}")
    return {"deleted": n}


@router.get("/push/log", dependencies=[Depends(admin)])
def push_log(
    frm: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    device_id: int | None = None,
    kind: Literal["daily", "confirm", "test", "manual"] | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    format: Literal["json", "csv"] = "json",
) -> Response:
    lo, hi = _range(frm, to)
    sql = ("SELECT id, sent_at, kind, verse_id, speaker, speaker_source, quote_source, "
           "title, body, device_id, status, http_status, error_type FROM push_deliveries "
           "WHERE sent_at >= ? AND sent_at <= ?")
    args: list = [lo, hi]
    if device_id is not None:
        sql += " AND device_id = ?"
        args.append(device_id)
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db() as conn:
        rows = []
        for r in conn.execute(sql, args):
            d = dict(r)
            d["local_date"], d["local_time"] = _local(d["sent_at"])
            rows.append(d)
    return _csv(rows, "push-log") if format == "csv" else JSONResponse({"entries": rows})


@router.get("/push/signups", dependencies=[Depends(admin)])
def push_signups(
    frm: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    ip: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=1000),
    format: Literal["json", "csv"] = "json",
) -> Response:
    lo, hi = _range(frm, to)
    sql = ("SELECT id, at, result, charged, ip FROM signup_attempts "
           "WHERE at >= ? AND at <= ?")
    args: list = [lo, hi]
    if ip is not None:
        canon = canonical_ip(ip)
        if canon is None:
            raise HTTPException(422, "not an IP address")
        sql += " AND ip_hash = ?"
        args.append(keyed_hash(settings.push_hmac_key, canon))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db() as conn:
        attempts = []
        for r in conn.execute(sql, args):
            d = dict(r)
            d["ip"] = d["ip"] or "hashed"
            d["local_date"], d["local_time"] = _local(d["at"])
            attempts.append(d)
        rejections = [dict(r) for r in conn.execute(
            "SELECT hour, result, SUM(count) AS count FROM signup_rejections "
            "WHERE hour >= ? AND hour <= ? GROUP BY hour, result ORDER BY hour DESC LIMIT ?",
            (lo, hi, limit))]
    if format == "csv":
        return _csv(attempts, "push-signups")
    return JSONResponse({"attempts": attempts, "rejections": rejections})


@router.get("/push/admin-actions", dependencies=[Depends(admin)])
def push_admin_actions(limit: int = Query(default=100, ge=1, le=1000)) -> dict:
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, at, action, detail FROM admin_actions ORDER BY id DESC LIMIT ?",
            (limit,))]
    return {"actions": rows}


# ---- public read-only ----------------------------------------------------------

@router.get("/verse-of-the-day")
def get_verse_of_the_day() -> JSONResponse:
    with db() as conn:
        v = verse_of_the_day(conn, _store())
    if v is None:
        raise HTTPException(503, "verse of the day unavailable")
    return JSONResponse(v, headers={"Cache-Control": "max-age=600"})
