"""Offline tests for daily-verse push (Phase 5 of the push plan).

No network: pywebpush's `webpush` and the Anthropic client are replaced with
fakes. Each test gets its own push.db and verses.db under tmp_path.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.push as push_api
import app.push_service as svc
from app.config import Settings, settings
from app.daily_verse import (
    Slot, current_slot, fallback_quote, slots_for, speaker_map, sync_rotation, verse_for,
    verse_of_the_day,
)
from app.http_extras import DropPushAccessLogs, PathHeaders
from app.models import Verse
from app.push_config import admin_token_ok, validate_push_settings
from app.push_db import db, migrate
from app.push_scheduler import PushScheduler, backup, purge
from app.push_validation import (
    InvalidSubscription, canonical_ip, rate_keys, validate_endpoint, validate_keys,
)
from app.quote import QuoteWriter, parse_reply
from app.store import VerseStore
from pywebpush import WebPushException

ADMIN = "a" * 40
FCM = "https://fcm.googleapis.com/fcm/send/"


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def p256dh() -> str:
    k = ec.generate_private_key(ec.SECP256R1()).public_key()
    return b64(k.public_bytes(serialization.Encoding.X962,
                              serialization.PublicFormat.UncompressedPoint))


def sub(n: int, keys: tuple[str, str] | None = None) -> dict:
    pk, au = keys or (p256dh(), b64(bytes([n % 256]) * 16))
    return {"endpoint": f"{FCM}device{n}", "keys": {"p256dh": pk, "auth": au}}


def vapid_pair() -> tuple[str, str]:
    k = ec.generate_private_key(ec.SECP256R1())
    priv = b64(k.private_numbers().private_value.to_bytes(32, "big"))
    pub = b64(k.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint))
    return pub, priv


VERSES = [
    Verse(verse_id=f"BG-2.{i}", book="bhagavad_gita", chapter=2, verse_no=str(i),
          translation=f"Translation of verse {i}.", commentary="Commentary.")
    for i in range(1, 11)
] + [Verse(verse_id="BG-2.99", book="bhagavad_gita", chapter=2, verse_no="99", translation="")]


class FakePush:
    """Stands in for pywebpush.webpush; records calls, answers per endpoint."""

    def __init__(self):
        self.calls: list[str] = []
        self.by_endpoint: dict[str, object] = {}

    def __call__(self, subscription_info, data, vapid_private_key, vapid_claims, ttl, timeout):
        assert timeout == svc.SEND_TIMEOUT and ttl == svc.TTL
        ep = subscription_info["endpoint"]
        self.calls.append(ep)
        r = self.by_endpoint.get(ep, 201)
        if isinstance(r, Exception):
            raise r
        if r >= 300:
            raise WebPushException("Push failed", response=SimpleNamespace(status_code=r))
        return SimpleNamespace(status_code=r)


class FakeWriter:
    def __init__(self):
        self.calls = 0

    async def write(self, verse):
        self.calls += 1
        return {"verse_id": verse.verse_id, "speaker": None, "speaker_source": "neutral",
                "quote": "A plain restatement.", "quote_source": "ai", "model": "m"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    pub, priv = vapid_pair()
    for k, v in dict(push_db_path=str(tmp_path / "state" / "push.db"),
                     sqlite_path=str(tmp_path / "verses.db"), push_admin_token=ADMIN,
                     push_hmac_key="h" * 40, vapid_public_key=pub, vapid_private_key=priv,
                     vapid_subject="mailto:t@example.com", push_rotation_seed="seed",
                     push_rotation_start="2026-01-01", push_subscribe_rate=5,
                     push_subscribe_rate_wide=30, push_signup_budget_per_hour=100,
                     push_max_devices=5000).items():
        monkeypatch.setattr(settings, k, v)
    VerseStore(settings.sqlite_path).upsert(VERSES)
    store = VerseStore(settings.sqlite_path, readonly=True)
    migrate()
    with db() as conn:
        sync_rotation(conn, store, "seed")
    fake = FakePush()
    monkeypatch.setattr(svc, "webpush", fake)
    monkeypatch.setattr(svc, "CONFIRM_RETRY_WAITS", (0, 0, None))
    writer = FakeWriter()
    monkeypatch.setattr(push_api, "_store", lambda: store)
    monkeypatch.setattr(push_api, "_writer", lambda: writer)
    speaker_map.cache_clear()
    app = FastAPI()
    app.include_router(push_api.router)
    client = TestClient(app)
    return SimpleNamespace(store=store, push=fake, writer=writer, client=client)


def signup(client, n=1, ip="203.0.113.5", keys=None):
    return client.post("/push/subscribe", json=sub(n, keys), headers={"CF-Connecting-IP": ip})


def count(sql, *args):
    with db() as conn:
        return conn.execute(sql, args).fetchone()[0]


# ---- auth and config [R1] -----------------------------------------------------

def test_startup_validation_fails_closed_on_unset_or_weak(env):
    assert validate_push_settings(settings) == []
    problems = validate_push_settings(Settings(_env_file=None, push_db_path=settings.push_db_path))
    assert any("PUSH_ADMIN_TOKEN" in p for p in problems)
    assert any("PUSH_HMAC_KEY" in p for p in problems)
    assert any("VAPID_PRIVATE_KEY" in p for p in problems)
    other_pub, _ = vapid_pair()
    s = settings.model_copy(update={"vapid_public_key": other_pub})
    assert any("doesn't match" in p for p in validate_push_settings(s))
    s = settings.model_copy(update={"push_admin_token": "short"})
    assert validate_push_settings(s)


def test_admin_token_check_rejects_empty_configured_token():
    assert admin_token_ok("", "") is False
    assert admin_token_ok("x" * 10, "x" * 10) is False
    assert admin_token_ok(ADMIN, ADMIN) is True
    assert admin_token_ok(ADMIN, "") is False


@pytest.mark.parametrize("path,method", [
    ("/push/devices", "get"), ("/push/log", "get"), ("/push/signups", "get"),
    ("/push/admin-actions", "get"), ("/push/test?device_id=1", "post"),
    ("/push/send-daily", "post"), ("/push/devices/1", "delete"),
])
@pytest.mark.parametrize("token", [None, "", "wrong" * 10])
def test_admin_endpoints_403(env, path, method, token):
    headers = {} if token is None else {"X-Admin-Token": token}
    assert getattr(env.client, method)(path, headers=headers).status_code == 403


def test_admin_403_even_when_configured_token_empty(env, monkeypatch):
    monkeypatch.setattr(settings, "push_admin_token", "")
    assert env.client.get("/push/devices", headers={"X-Admin-Token": ""}).status_code == 403


# ---- validation [R4][R10] -------------------------------------------------------

@pytest.mark.parametrize("ep", [
    "https://evil.com/?fcm.googleapis.com", "https://fcm.googleapis.com.evil.com/x",
    "https://user@web.push.apple.com/x", "https://fcm.googleapis.com:8443/x",
    "http://fcm.googleapis.com/x", FCM + "x" * 1100, "", "https://evilnotify.windows.com/x",
])
def test_endpoint_rejected(ep):
    with pytest.raises(InvalidSubscription):
        validate_endpoint(ep)


def test_endpoint_accepted():
    for ep in (FCM + "abc", "https://web.push.apple.com/QG", "https://wns2-par02p.notify.windows.com/w"):
        validate_endpoint(ep)


@pytest.mark.parametrize("pk,auth", [
    ("", b64(b"a" * 16)), (b64(b"\x04" + b"\x01" * 64), b64(b"a" * 16)),
    ("P256", b64(b"a" * 15)), ("P256", b64(b"a" * 17)),
])
def test_bad_keys_rejected(pk, auth):
    with pytest.raises(InvalidSubscription):
        validate_keys(p256dh() if pk == "P256" else pk, auth)


# ---- client IP + limits [V1][S4][F3] ----------------------------------------------

def test_rate_keys_and_canonical_ip():
    assert canonical_ip("2001:DB8::1") == canonical_ip("2001:db8::1")
    assert rate_keys("203.0.113.5") == ("203.0.113.5", None)
    a, b = rate_keys("2001:db8:1:2::1"), rate_keys("2001:db8:1:2::ffff")
    assert a == b and a[0].endswith("/64") and a[1].endswith("/48")


def test_spoofed_xff_ignored_and_unknown_bucket(env):
    for n in range(5):
        r = env.client.post("/push/subscribe", json=sub(n),
                            headers={"X-Forwarded-For": f"6.6.6.{n}, 1.2.3.4"})
        assert r.status_code == 200
    r = env.client.post("/push/subscribe", json=sub(99), headers={"X-Forwarded-For": "7.7.7.7"})
    assert r.status_code == 429  # all share the strict "unknown" bucket


def test_narrow_and_wide_ipv6_limits(env):
    for n in range(5):
        assert signup(env.client, n, ip=f"2001:db8:aa:1::{n + 1}").status_code == 200
    assert signup(env.client, 50, ip="2001:db8:aa:1::99").status_code == 429  # same /64
    ok = 5
    for n in range(100, 140):  # different /64s, same /48
        if signup(env.client, n, ip=f"2001:db8:aa:{n}::1").status_code == 200:
            ok += 1
    assert ok == 30


def test_global_budget_and_free_reregistration(env, monkeypatch):
    monkeypatch.setattr(settings, "push_signup_budget_per_hour", 3)
    keys = {}
    for n in range(3):
        s = sub(n)
        keys[n] = (s["endpoint"], s["keys"]["p256dh"], s["keys"]["auth"])
        assert env.client.post("/push/subscribe", json=s,
                               headers={"CF-Connecting-IP": f"198.51.100.{n}"}).status_code == 200
    assert signup(env.client, 9, ip="198.51.100.9").status_code == 429
    ep, pk, au = keys[0]
    r = env.client.post("/push/subscribe", json={"endpoint": ep, "keys": {"p256dh": pk, "auth": au}},
                        headers={"CF-Connecting-IP": "198.51.100.200"})
    assert r.status_code == 200  # re-registration still works over budget
    assert count("SELECT SUM(count) FROM signup_rejections WHERE result='budget_exceeded'") == 1


def test_rejections_counted_not_logged(env):
    for _ in range(200):
        r = env.client.post("/push/subscribe", json={"endpoint": "https://evil.com/x",
                                                     "keys": {"p256dh": "x", "auth": "y"}},
                            headers={"CF-Connecting-IP": "192.0.2.1"})
        assert r.status_code == 422
    assert count("SELECT COUNT(*) FROM signup_attempts") == 0
    assert count("SELECT COUNT(*) FROM signup_rejections") == 1
    assert count("SELECT count FROM signup_rejections") == 200


# ---- sign-up [V2][V3][R9][V4] ----------------------------------------------------

def test_failed_confirmation_stores_nothing_but_is_charged(env):
    env.push.by_endpoint[FCM + "device1"] = 404
    assert signup(env.client, 1).status_code == 422
    assert count("SELECT COUNT(*) FROM push_subscriptions") == 0
    assert count("SELECT COUNT(*) FROM signup_attempts WHERE result='confirm_failed' AND charged=1") == 1


def test_confirmation_retries_transient_410(env, monkeypatch):
    monkeypatch.setattr(svc, "CONFIRM_RETRY_WAITS", (0, 0, None))
    answers = iter([410, 410, 201])
    real = env.push.__call__

    def flaky(**kw):
        env.push.by_endpoint[kw["subscription_info"]["endpoint"]] = next(answers)
        return real(**kw)

    monkeypatch.setattr(svc, "webpush", flaky)
    assert signup(env.client, 1).status_code == 200
    assert count("SELECT COUNT(*) FROM push_deliveries WHERE kind='confirm'") == 1


def test_signup_stores_hash_only_and_device_endpoints(env):
    r = signup(env.client, 1)
    assert r.status_code == 200
    d = r.json()
    with db() as conn:
        row = conn.execute("SELECT * FROM push_subscriptions").fetchone()
    assert d["device_secret"] not in tuple(str(v) for v in row)
    h = {"X-Device-Id": str(d["device_id"]), "X-Device-Secret": d["device_secret"]}
    assert env.client.get("/push/device/check", headers=h).status_code == 204
    bad = {**h, "X-Device-Secret": "nope"}
    assert env.client.get("/push/device/check", headers=bad).status_code == 401
    assert env.client.post("/push/unsubscribe", headers=bad).status_code == 401
    assert env.client.post("/push/device/update", json=sub(2), headers=bad).status_code == 401
    assert env.client.post("/push/unsubscribe", headers=h).status_code == 204
    assert env.client.get("/push/device/check", headers=h).status_code == 404


def test_existing_endpoint_takeover_refused_and_reregister_free(env):
    s = sub(1)
    keys = (s["keys"]["p256dh"], s["keys"]["auth"])
    first = signup(env.client, 1, keys=keys).json()
    assert signup(env.client, 1).status_code == 409  # same endpoint, different keys
    again = signup(env.client, 1, keys=keys)
    assert again.status_code == 200
    assert again.json()["device_id"] == first["device_id"]
    assert again.json()["device_secret"] != first["device_secret"]
    assert count("SELECT COUNT(*) FROM push_subscriptions") == 1
    assert count("SELECT charged FROM signup_attempts WHERE result='reregistered'") == 0


def test_update_to_another_rows_endpoint_conflicts(env):
    a = signup(env.client, 1).json()
    signup(env.client, 2)
    h = {"X-Device-Id": str(a["device_id"]), "X-Device-Secret": a["device_secret"]}
    assert env.client.post("/push/device/update", json=sub(2), headers=h).status_code == 409
    assert count("SELECT COUNT(*) FROM push_subscriptions") == 2


def test_device_numbers_never_reused(env):
    signup(env.client, 1)
    last = signup(env.client, 2).json()["device_id"]
    r = env.client.delete(f"/push/devices/{last}", headers={"X-Admin-Token": ADMIN})
    assert r.status_code == 200
    assert signup(env.client, 3).json()["device_id"] == last + 1


# ---- sending [R4][G3][D4][V7][F2] ---------------------------------------------------

def add_devices(n):
    with db() as conn:
        for i in range(n):
            s = sub(i)
            conn.execute("INSERT INTO push_subscriptions(endpoint, p256dh, auth, secret_hash, "
                         "created_at) VALUES (?, ?, ?, 'h', '2026-01-01T00:00:00Z')",
                         (s["endpoint"], s["keys"]["p256dh"], s["keys"]["auth"]))


def test_bad_devices_dont_stop_the_loop_and_410_deletes(env):
    add_devices(5)
    env.push.by_endpoint.update({FCM + "device0": IndexError(), FCM + "device1": ValueError(),
                                 FCM + "device2": 410})
    assert asyncio.run(svc.run_daily(env.store, env.writer, slots_for(date(2026, 3, 1))[0])) == "sent"
    assert len(env.push.calls) == 5
    assert count("SELECT COUNT(*) FROM push_subscriptions") == 4
    assert count("SELECT COUNT(*) FROM push_deliveries WHERE status='delivered'") == 2
    assert count("SELECT status FROM push_daily_runs") == "completed"
    with db() as conn:
        errs = {r[0] for r in conn.execute("SELECT error_type FROM push_deliveries")}
    assert errs <= {None, "IndexError", "ValueError", "WebPushException"}
    assert asyncio.run(svc.run_daily(env.store, env.writer, slots_for(date(2026, 3, 1))[0])) == "already_ran"


def test_concurrent_workers_send_once_per_device(env):
    add_devices(8)
    day = slots_for(date(2026, 3, 2))[1]

    async def both():
        return await asyncio.gather(svc.run_daily(env.store, env.writer, day),
                                    svc._send_daily_unclaimed(day, svc.Message("daily", "t", "b", "/ui/")))

    asyncio.run(both())
    assert count("SELECT COUNT(*) FROM push_deliveries WHERE kind='daily'") == 8
    assert len(env.push.calls) == 8


def test_crash_resume_marks_pending_unknown_and_sends_rest(env):
    add_devices(4)
    from app.daily_verse import local_today
    day = local_today()
    key = slots_for(day)[0].key
    with db() as conn:
        conn.execute("INSERT INTO push_daily_runs(run_date, verse_id, status, started_at, "
                     "heartbeat_at) VALUES (?, 'BG-2.1', 'started', '2000-01-01T00:00:00Z', "
                     "'2000-01-01T00:00:00Z')", (key,))
        msg = svc.Message("daily", "t", "b", "/ui/")
        svc.insert_delivery(conn, msg, 1, "pending", key)
        svc.insert_delivery(conn, msg, 2, "delivered", key)
    assert asyncio.run(svc.resume_stale(env.store, env.writer, day)) == 1
    assert asyncio.run(svc.resume_stale(env.store, env.writer, day)) == 0  # already taken
    assert sorted(env.push.calls) == [FCM + "device2", FCM + "device3"]
    assert count("SELECT status FROM push_deliveries WHERE device_id=1") == "unknown"
    assert count("SELECT status FROM push_daily_runs") == "completed"


def test_forced_send_reaches_everyone_once_a_day(env):
    add_devices(3)
    asyncio.run(svc.run_daily(env.store, env.writer))
    env.push.calls.clear()
    r = env.client.post("/push/send-daily?force=true", headers={"X-Admin-Token": ADMIN})
    assert r.status_code == 200 and len(env.push.calls) == 3
    assert count("SELECT COUNT(*) FROM push_deliveries WHERE kind='manual'") == 3
    r = env.client.post("/push/send-daily?force=true", headers={"X-Admin-Token": ADMIN})
    assert r.status_code == 429
    assert count("SELECT COUNT(*) FROM admin_actions WHERE action='force_send'") == 1


def test_logs_never_contain_endpoints_or_raw_errors(env, caplog):
    add_devices(2)
    env.push.by_endpoint[FCM + "device0"] = ConnectionError(f"failed url: {FCM}device0")
    with caplog.at_level(logging.INFO):
        asyncio.run(svc.run_daily(env.store, env.writer, slots_for(date(2026, 3, 3))[0]))
    assert "fcm.googleapis.com" not in caplog.text and "failed url" not in caplog.text
    with db() as conn:
        dump = "\n".join(conn.iterdump())
    assert dump.count("fcm.googleapis.com") == 2  # only the subscriptions table itself


# ---- rotation + verse of the day [G2][R3] ---------------------------------------------

def test_rotation_every_verse_once_and_append_keeps_positions(env):
    with db() as conn:  # 2 sends a day: 10 verses take 5 days, each shown once
        seen = [verse_for(conn, env.store, s).verse_id
                for i in range(5) for s in slots_for(date(2026, 1, 1) + timedelta(days=i))]
        before = [r[0] for r in conn.execute("SELECT verse_id FROM verse_rotation ORDER BY position")]
    assert sorted(seen) == sorted(v.verse_id for v in VERSES if v.translation)
    VerseStore(settings.sqlite_path).upsert([Verse(verse_id="BG-3.1", book="bhagavad_gita",
                                                   chapter=3, verse_no="1", translation="New.")])
    with db() as conn:
        assert sync_rotation(conn, env.store, "seed") == 1
        after = [r[0] for r in conn.execute("SELECT verse_id FROM verse_rotation ORDER BY position")]
    assert after[:10] == before and after[10] == "BG-3.1"


def test_missing_verse_skipped(env):
    with db() as conn:
        conn.execute("INSERT INTO verse_rotation(position, verse_id, added_at) VALUES (100, 'GONE', 'x')")
        for i in range(20):
            for slot in slots_for(date(2026, 1, 1) + timedelta(days=i)):
                assert verse_for(conn, env.store, slot) is not None


def test_verse_of_the_day_never_calls_model(env):
    r = env.client.get("/verse-of-the-day")
    assert r.status_code == 200 and env.writer.calls == 0
    assert r.headers["cache-control"] == "max-age=600"
    body = r.json()
    assert body["quote_source"] == "fallback" and body["url"].startswith("/ui/?verse=BG-2.")
    assert count("SELECT COUNT(*) FROM daily_quotes") == 0


# ---- quote ---------------------------------------------------------------------------

V = VERSES[0]


@pytest.mark.parametrize("text", [
    "not json", '{"quote": ""}', '{"quote": "' + "x" * 141 + '"}', '{"quote": "two\\nlines"}',
    '{"quote": "see https://x.y"}', '{"quote": "<b>hi</b>"}', '["quote"]',
])
def test_quote_validation_rejects(text):
    assert parse_reply(text, V, None) is None


def test_quote_speaker_only_from_map():
    # Even a confident model answer never names a speaker; only the confirmed map does.
    ok = '{"quote": "Act without clinging.", "speaker": "Krishna", "speaker_confidence": "high"}'
    q = parse_reply(ok, V, None)
    assert q["speaker"] is None and q["speaker_source"] == "neutral"
    mapped = parse_reply(ok.replace("Krishna", "Arjuna"), V, "Krishna")
    assert mapped["speaker"] == "Krishna" and mapped["speaker_source"] == "map"


def test_speaker_map_bg_confirmed_only(tmp_path, monkeypatch):
    import json
    import app.daily_verse as dv
    p = tmp_path / "map.json"
    p.write_text(json.dumps({
        "BG-2.47": {"speaker": "Krishna", "status": "confirmed"},
        "BG-2.4": {"speaker": "Arjuna", "status": "review"},
        "AG-1.2": {"speaker": "Ashtavakra", "status": "confirmed"},
    }))
    monkeypatch.setattr(dv, "SPEAKER_MAP_PATH", p)
    dv.speaker_map.cache_clear()
    try:
        assert dv.speaker_map() == {"BG-2.47": "Krishna"}
    finally:
        dv.speaker_map.cache_clear()


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.messages = self

    async def create(self, **kw):
        assert "temperature" not in kw  # rejected by the API since 2026-10-21
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=r)])


def test_quote_writer_retry_then_fallback(env):
    w = QuoteWriter(FakeClient(["bad", '{"quote": "Act without clinging."}']))
    assert asyncio.run(w.write(V))["quote_source"] == "ai"
    w = QuoteWriter(FakeClient([TimeoutError(), RuntimeError()]))
    q = asyncio.run(w.write(V))
    assert q == fallback_quote(V) and q["quote_source"] == "fallback"


# ---- admin log, CSV, bulk delete -----------------------------------------------------

def test_csv_escapes_formulas_and_limit_capped(env):
    with db() as conn:
        svc.insert_delivery(conn, svc.Message("test", "=HYPERLINK(1)", "+x", "/ui/"), None, "failed")
    r = env.client.get("/push/log?format=csv", headers={"X-Admin-Token": ADMIN})
    assert "'=HYPERLINK(1)" in r.text and ",'+x," in r.text
    assert env.client.get("/push/log?limit=1001", headers={"X-Admin-Token": ADMIN}).status_code == 422
    assert env.client.get("/push/log?from=1' OR '1", headers={"X-Admin-Token": ADMIN}).status_code == 422


def test_bulk_delete_needs_matching_count(env):
    add_devices(3)
    h = {"X-Admin-Token": ADMIN}
    body = {"created_from": "2026-01-01", "created_to": "2026-01-01", "expected_count": 2}
    r = env.client.post("/push/devices/bulk-delete", json=body, headers=h)
    assert r.status_code == 409 and count("SELECT COUNT(*) FROM push_subscriptions") == 3
    r = env.client.post("/push/devices/bulk-delete", json={**body, "expected_count": 3}, headers=h)
    assert r.json() == {"deleted": 3}


def test_signups_ip_lookup_by_hash_after_purge(env):
    signup(env.client, 1, ip="2001:DB8::1")
    with db() as conn:
        conn.execute("UPDATE signup_attempts SET at = '2000-01-01T00:00:00Z'")
    purge()  # 26 years > 90 days: deleted
    assert count("SELECT COUNT(*) FROM signup_attempts") == 0
    signup(env.client, 2, ip="2001:DB8::1")
    with db() as conn:
        old = (date.today() - timedelta(days=10)).isoformat() + "T00:00:00Z"
        conn.execute("UPDATE signup_attempts SET at = ?", (old,))
    purge()
    assert count("SELECT COUNT(*) FROM signup_attempts WHERE ip IS NULL") == 1
    r = env.client.get("/push/signups?ip=2001:db8::1&from=2000-01-01", headers={"X-Admin-Token": ADMIN})
    assert [a["ip"] for a in r.json()["attempts"]] == ["hashed"]


# ---- storage, backups, headers, access log -----------------------------------------

def test_verses_db_readonly(env):
    with pytest.raises(sqlite3.OperationalError):
        env.store._conn.execute("DELETE FROM verses")


def test_backup_has_no_raw_ip_bytes_and_prunes(env, tmp_path):
    signup(env.client, 1, ip="203.0.113.77")
    out = backup()
    assert out is not None and b"203.0.113.77" not in out.read_bytes()
    bdir = out.parent
    (bdir / "push-x.db.tmp").write_bytes(b"203.0.113.77")  # leftover from a crash
    for i in range(9):
        (bdir / f"push-2020-01-0{i + 1}.db").write_bytes(b"")
    backup()
    assert not list(bdir.glob("*.tmp"))
    assert len(list(bdir.glob("push-*.db"))) == 7


def _asgi_capture(app, path):
    msgs = []

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(m):
            msgs.append(m)

        await app({"type": "http", "path": path, "method": "GET", "headers": []}, receive, send)

    asyncio.run(run())
    return msgs


async def _streaming_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/event-stream")]})
    for i in range(3):
        await send({"type": "http.response.body", "body": f"data: {i}\n\n".encode(),
                    "more_body": i < 2})


def test_path_headers_scoped_and_stream_untouched():
    mw = PathHeaders(_streaming_app)
    chat = _asgi_capture(mw, "/chat")
    assert [m["type"] for m in chat].count("http.response.body") == 3  # not buffered
    assert dict(chat[0]["headers"]) == {b"content-type": b"text/event-stream"}
    admin = dict(_asgi_capture(mw, "/ui/admin/log.html")[0]["headers"])
    assert admin[b"content-security-policy"].startswith(b"default-src 'self'")
    assert admin[b"referrer-policy"] == b"no-referrer"
    sw = dict(_asgi_capture(mw, "/ui/sw.js")[0]["headers"])
    assert sw[b"cache-control"] == b"no-cache"


def test_access_log_filter_drops_push_paths():
    f = DropPushAccessLogs()
    mk = lambda p: logging.LogRecord("uvicorn.access", 20, "", 0, '%s - "%s %s"',  # noqa: E731
                                     ("1.2.3.4", "POST", p), None)
    assert f.filter(mk("/push/subscribe")) is False
    assert f.filter(mk("/chat")) is True


# ---- scheduler timezone (regression: jobs ran in the machine's UTC) -------------

def test_scheduled_jobs_use_push_timezone_not_machine_zone(env, monkeypatch):
    import os
    import time as _time
    from datetime import datetime
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(settings, "push_timezone", "America/Los_Angeles")
    monkeypatch.setattr(settings, "push_daily_times", "08:30,17:30")
    monkeypatch.setenv("TZ", "UTC")  # like the Docker container
    _time.tzset()
    try:
        async def jobs():
            sched = PushScheduler(env.store, env.writer)
            await sched.startup()
            try:
                return {j.id: j for j in sched.sched.get_jobs()}
            finally:
                sched.shutdown()

        js = asyncio.run(jobs())
        la = ZoneInfo("America/Los_Angeles")
        for job_id in ("send-08:30", "send-17:30", "quote-08:30", "quote-17:30",
                       "stale", "purge", "backup"):
            assert str(js[job_id].trigger.timezone) == "America/Los_Angeles", job_id
        for job_id, hm in (("send-08:30", (8, 30)), ("send-17:30", (17, 30)),
                           ("quote-08:30", (8, 20)), ("quote-17:30", (17, 20))):
            nxt = js[job_id].next_run_time.astimezone(la)
            assert (nxt.hour, nxt.minute) == hm, job_id
    finally:
        monkeypatch.delenv("TZ")
        _time.tzset()


# ---- two sends a day --------------------------------------------------------------

def test_morning_and_evening_are_separate_runs_with_different_verses(env):
    add_devices(2)
    morning, evening = slots_for(date(2026, 3, 4))
    assert asyncio.run(svc.run_daily(env.store, env.writer, morning)) == "sent"
    assert asyncio.run(svc.run_daily(env.store, env.writer, evening)) == "sent"
    assert asyncio.run(svc.run_daily(env.store, env.writer, evening)) == "already_ran"
    assert count("SELECT COUNT(*) FROM push_deliveries WHERE kind='daily'") == 4
    with db() as conn:
        verses = [r[0] for r in conn.execute("SELECT verse_id FROM push_daily_runs ORDER BY id")]
        quotes = [r[0] for r in conn.execute("SELECT quote_date FROM daily_quotes ORDER BY id")]
    assert len(set(verses)) == 2
    assert quotes == ["2026-03-04#08:30", "2026-03-04#17:30"]


def test_current_slot_before_first_send_is_yesterdays_evening():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    la = ZoneInfo(settings.push_timezone)
    assert current_slot(datetime(2026, 3, 5, 7, 0, tzinfo=la)).key == "2026-03-04#17:30"
    assert current_slot(datetime(2026, 3, 5, 9, 0, tzinfo=la)).key == "2026-03-05#08:30"
    assert current_slot(datetime(2026, 3, 5, 18, 0, tzinfo=la)).key == "2026-03-05#17:30"


def test_verse_of_the_day_lists_todays_sends_so_far(env):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    evening = datetime(2026, 3, 6, 18, 0, tzinfo=ZoneInfo(settings.push_timezone))
    with db() as conn:
        v = verse_of_the_day(conn, env.store, evening)
    assert v["time"] == "17:30" and [s["time"] for s in v["slots"]] == ["08:30", "17:30"]
    assert v["slots"][0]["verse_id"] != v["slots"][1]["verse_id"]


def test_catchup_counts_legacy_date_only_run_as_morning(env):
    from app.push_scheduler import _ran
    morning, evening = slots_for(date(2026, 3, 7))
    with db() as conn:
        conn.execute("INSERT INTO push_daily_runs(run_date, verse_id, status, started_at, "
                     "heartbeat_at) VALUES ('2026-03-07', 'BG-2.1', 'completed', 'x', 'x')")
    assert _ran(morning) is True and _ran(evening) is False


def test_confirmation_mentions_both_times(env):
    assert svc.schedule_phrase() == "A verse around 8:30 am and 5:30 pm, every day"


def test_confirmation_row_shows_device_number(env):
    d = signup(env.client, 1).json()
    assert count("SELECT device_id FROM push_deliveries WHERE kind='confirm'") == d["device_id"]
    env.push.by_endpoint[FCM + "device9"] = 404
    signup(env.client, 9)  # failed confirmation: no device, row stays without a number
    assert count("SELECT COUNT(*) FROM push_deliveries WHERE kind='confirm' AND device_id IS NULL") == 1
