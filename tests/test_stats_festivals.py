"""Offline tests for anonymous verse stats and festival verses. Reuses the
push fixtures: no network, fake push service, fake model replies."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

import app.festival_pick as fp
import app.push_service as svc
from app import alerts, festivals
from app.config import settings
from app.daily_verse import rotation_verse, slots_for, verse_for, verse_of_the_day
from app.festival_pick import FestivalPicker, cancel, prepare
from app.push_db import db
from tests.test_push import (  # noqa: F401 — `env` is a fixture
    ADMIN, FCM, count, device_headers, env, send,
)

DAY = date(2026, 3, 1)
ADM = {"X-Admin-Token": ADMIN}


def stats(env, days=3650):
    return env.client.get(f"/push/stats?days={days}", headers=ADM).json()["stats"]


# ---- anonymous verse stats ------------------------------------------------------

def test_stats_sent_opened_starred(env, monkeypatch):
    monkeypatch.setattr("app.verse_stats.local_today", lambda: DAY)
    h1, h2 = device_headers(env.client, 1), device_headers(env.client, 2)
    env.push.by_endpoint[FCM + "device2"] = 500       # a failed delivery isn't "sent"
    send(env, DAY)
    (row,) = stats(env)
    assert (row["sent"], row["opened"], row["starred"]) == (1, 0, 0)
    assert row["date"] == "2026-03-01" and row["quote"] == "A plain restatement."

    entry = env.client.get("/push/inbox", headers=h1).json()["entries"][0]["id"]
    env.client.post(f"/push/inbox/{entry}/open", headers=h1)
    env.client.post(f"/push/inbox/{entry}/open", headers=h1)      # opened twice: counted once
    assert stats(env)[0]["opened"] == 1

    env.client.post(f"/push/inbox/{entry}/star", headers=h1)
    env.client.post(f"/push/inbox/{entry}/star", headers=h1)      # already starred: no change
    assert stats(env)[0]["starred"] == 1
    env.client.delete(f"/push/inbox/{entry}/star", headers=h1)
    env.client.delete(f"/push/inbox/{entry}/star", headers=h1)
    assert stats(env)[0]["starred"] == 0

    # Mark all as read is a dismissal, not an open.
    env.client.post("/push/inbox/open-all", headers=h2)
    assert stats(env)[0]["opened"] == 1


def test_stats_survive_unsubscribe_and_hold_nothing_per_device(env, monkeypatch):
    monkeypatch.setattr("app.verse_stats.local_today", lambda: DAY)
    h = device_headers(env.client)
    send(env, DAY)
    entry = env.client.get("/push/inbox", headers=h).json()["entries"][0]["id"]
    env.client.post(f"/push/inbox/{entry}/open", headers=h)
    env.client.post(f"/push/inbox/{entry}/star", headers=h)
    before = stats(env)
    assert env.client.post("/push/unsubscribe", headers=h).status_code == 204
    assert count("SELECT COUNT(*) FROM device_inbox") == 0
    assert stats(env) == before
    with db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(verse_stats)")}
    assert cols == {"id", "slot_key", "verse_id", "festival", "sent", "opened", "starred"}


def test_stats_admin_only_days_filter_and_newest_first(env, monkeypatch):
    monkeypatch.setattr("app.verse_stats.local_today", lambda: DAY + timedelta(days=10))
    device_headers(env.client)
    send(env, DAY)
    send(env, DAY + timedelta(days=9))
    assert env.client.get("/push/stats").status_code == 403
    assert [r["date"] for r in stats(env)] == ["2026-03-10", "2026-03-01"]
    assert [r["date"] for r in stats(env, days=5)] == ["2026-03-10"]


# ---- festival calendar ----------------------------------------------------------

def ics(*events):
    body = "".join(f"BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:{d.replace('-', '')}\r\n"
                   f"SUMMARY:{s}\r\nEND:VEVENT\r\n" for d, s in events)
    return "BEGIN:VCALENDAR\r\n" + body + "END:VCALENDAR\r\n"


def feed(**overrides):
    """A healthy two-year feed for every festival in the file; `overrides` maps
    a festival name to its list of (date, summary) events."""
    events = []
    for i, f in enumerate(festivals.festivals()):
        events += overrides.get(f.name, [(f"2026-{i + 1:02d}-10", f.name), (f"2027-{i + 1:02d}-05", f.name)])
    return festivals.parse_ics(ics(*events))


def dates(name):
    with db() as conn:
        return {r["year"]: (r["day"], r["suspect"]) for r in conn.execute(
            "SELECT year, day, suspect FROM festival_dates WHERE festival = ?", (name,))}


def test_parse_ics_unfolds_lines_and_reads_dates():
    text = ("BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261108\r\nSUMMARY:Diwali/\r\n Deepavali\r\n"
            "END:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART:20270322T000000Z\r\nSUMMARY:Holi\r\nEND:VEVENT\r\n")
    assert festivals.parse_ics(text) == [(date(2026, 11, 8), "Diwali/Deepavali"),
                                         (date(2027, 3, 22), "Holi")]


def test_festival_file_is_well_formed():
    fs = festivals.festivals()
    assert len(fs) >= 10 and all(f.theme and f.names for f in fs)
    assert festivals.by_name("Diwali").names >= {"diwali", "diwali/deepavali"}


def test_sync_stores_dates_and_matches_aliases(env):
    today = date(2026, 6, 1)
    with db() as conn:
        problems = festivals.sync(conn, feed(Diwali=[("2026-11-08", "Diwali/Deepavali"),
                                                     ("2027-10-29", "DEEPAVALI (tentative)")]), today)
        assert problems == []
        assert festivals.on_day(conn, date(2026, 11, 8)).name == "Diwali"
        assert festivals.on_day(conn, date(2026, 11, 9)) is None
    assert dates("Diwali") == {2026: ("2026-11-08", 0), 2027: ("2027-10-29", 0)}


def test_sync_flags_missing_duplicate_and_moved_once(env):
    today = date(2026, 6, 1)
    bad = feed(Holi=[],                                                     # missing
               Diwali=[("2026-11-08", "Diwali"), ("2026-11-09", "Deepavali"),  # twice in a year
                       ("2027-10-29", "Diwali")],
               Dussehra=[("2026-10-20", "Dussehra"), ("2027-12-15", "Dussehra")])  # moved 56 days
    with db() as conn:
        assert festivals.sync(conn, bad, today) == ["Holi", "Dussehra", "Diwali"]  # file order
        assert festivals.sync(conn, bad, today) == []           # same problem: no second alert
        assert festivals.on_day(conn, date(2026, 11, 8)) is None
        assert festivals.on_day(conn, date(2027, 12, 15)) is None
        assert festivals.on_day(conn, date(2027, 10, 29)).name == "Diwali"  # the good year still works
    assert dates("Holi")[2026] == (None, 1)
    assert dates("Dussehra") == {2026: ("2026-10-20", 0), 2027: ("2027-12-15", 1)}


def test_two_festivals_on_one_day_first_in_file_wins(env):
    first, second = festivals.festivals()[0], festivals.festivals()[1]
    both = feed(**{first.name: [("2026-08-01", first.name)], second.name: [("2026-08-01", second.name)]})
    with db() as conn:
        festivals.sync(conn, both, date(2026, 6, 1))
        assert festivals.on_day(conn, date(2026, 8, 1)).name == first.name


# ---- the pick pipeline ----------------------------------------------------------

class FakePicker(FestivalPicker):
    """Scripted model: `shortlists` are returned in order; the reviewer
    approves the first candidate in `approve` (or rejects all)."""

    def __init__(self, shortlists, approve=()):
        self.shortlists, self.approve = list(shortlists), set(approve)
        self.calls = []

    async def _ask(self, system, user):
        if isinstance(system, list):  # shortlist: instructions + cached catalogue
            self.calls.append(("shortlist", system[1]["text"], user))
            if not self.shortlists:
                raise AssertionError("unexpected shortlist call")
            return json.dumps({"picks": [{"verse_id": i, "reason": "SECRET REASON"}
                                         for i in self.shortlists.pop(0)]})
        self.calls.append(("review", system, user))
        return json.dumps({"approved": next((i for i in sorted(self.approve) if f"[{i}]" in user), None)})


FEST_DAY = date(2026, 8, 1)


@pytest.fixture
def fest(env, monkeypatch):
    """Diwali faked onto FEST_DAY; emails captured instead of sent; the link
    endpoints believe it is the day before the festival."""
    monkeypatch.setattr(fp, "local_today", lambda: FEST_DAY - timedelta(days=1))
    monkeypatch.setattr("app.push.local_today", lambda: FEST_DAY - timedelta(days=1))
    with db() as conn:
        festivals.sync(conn, feed(Diwali=[("2026-08-01", "Diwali")]), date(2026, 6, 1))
    env.mail = []

    async def notify(subject, body):
        env.mail.append((subject, body))
        return True

    monkeypatch.setattr(alerts, "notify", notify)
    monkeypatch.setattr(settings, "public_base_url", "https://example.test")
    return env


def run_prepare(env, picker, today=FEST_DAY - timedelta(days=3)):
    return asyncio.run(prepare(picker, env.store, today))


def pick_row():
    with db() as conn:
        return dict(conn.execute("SELECT festival, day, verse_id, status, cancel_hash "
                                 "FROM festival_picks ORDER BY day DESC").fetchone())


def free_verses(env, n=3):
    """Verse ids the rotation doesn't use between pick day and the festival."""
    with db() as conn:
        banned = fp.recently_sent(conn, env.store, FEST_DAY, FEST_DAY - timedelta(days=3))
    return [v.verse_id for v in env.store.iter_all()
            if v.translation.strip() and v.verse_id not in banned][:n]


def test_pick_three_days_ahead_and_email_never_reveals_verse(fest):
    a, b, _ = free_verses(fest)
    picker = FakePicker([[a, b, "BG-99.99"]], approve=[b])
    assert run_prepare(fest, picker, FEST_DAY - timedelta(days=4)) == 0   # too early
    assert run_prepare(fest, picker) == 1
    row = pick_row()
    assert (row["festival"], row["day"], row["verse_id"], row["status"]) == \
        ("Diwali", "2026-08-01", b, "picked")

    (subject, body), = fest.mail
    assert subject == "Diwali on 1 August: verse picked and checked, all set."
    verse = fest.store.get(b)
    for secret in (b, b.split("-")[1], verse.translation, "SECRET REASON"):
        assert secret not in subject + body
    assert "https://example.test/push/festival/cancel/" in body and "2026" in body

    # the reviewer saw the surviving verses but none of the first model's reasons
    review = next(c for c in picker.calls if c[0] == "review")
    assert f"[{a}]" in review[2] and "BG-99.99" not in review[2] and "SECRET REASON" not in review[2]
    assert run_prepare(fest, picker) == 0                                 # already locked in


def test_code_checks_reject_recent_and_last_years_verse(fest):
    a, b, c = free_verses(fest)
    with db() as conn:   # a went out 10 days before the festival; b was last year's Diwali verse
        conn.execute("INSERT INTO push_daily_runs(run_date, verse_id, status, started_at, heartbeat_at) "
                     "VALUES ('2026-07-22#08:30', ?, 'completed', 'x', 'x')", (a,))
        conn.execute("INSERT INTO festival_picks(festival, day, verse_id, status, created_at) "
                     "VALUES ('Diwali', '2025-10-20', ?, 'picked', 'x')", (b,))
        soon = rotation_verse(conn, fest.store, slots_for(FEST_DAY - timedelta(days=1))[0]).verse_id
    picker = FakePicker([[a, b, soon], [c]], approve=[a, b, soon, c])
    run_prepare(fest, picker)
    catalogue = picker.calls[0][1]
    for banned in (a, b, soon):
        assert f"\n{banned}:" not in catalogue          # never offered…
    reviews = [x[2] for x in picker.calls if x[0] == "review"]
    assert len(reviews) == 1 and f"[{c}]" in reviews[0]  # …and thrown out by the code checks
    assert all(f"[{x}]" not in reviews[0] for x in (a, b, soon))  # when picked anyway
    assert pick_row()["verse_id"] == c


def test_all_rejected_twice_falls_back_to_rotation(fest):
    a, b, c = free_verses(fest)
    picker = FakePicker([[a, b], [c]], approve=[])
    run_prepare(fest, picker)
    assert pick_row()["status"] == "fallback" and pick_row()["verse_id"] is None
    assert "Do not pick any of these" in picker.calls[2][2] and a in picker.calls[2][2]  # the retry
    assert fest.mail == [("Diwali: no verse passed review, so it'll use the normal rotation.",
                          fest.mail[0][1])]
    slot = slots_for(FEST_DAY)[0]
    with db() as conn:
        assert verse_for(conn, fest.store, slot) == rotation_verse(conn, fest.store, slot)
    assert run_prepare(fest, picker) == 0


def test_model_failure_leaves_no_row_so_the_next_day_retries(fest):
    class Broken(FestivalPicker):
        def __init__(self):
            pass

        async def _ask(self, system, user):
            raise TimeoutError

    with pytest.raises(TimeoutError):
        run_prepare(fest, Broken())
    assert count("SELECT COUNT(*) FROM festival_picks") == 0
    a = free_verses(fest)[0]
    assert run_prepare(fest, FakePicker([[a]], approve=[a]), FEST_DAY - timedelta(days=2)) == 1


def test_festival_day_sends_locked_verse_with_greeting_label_and_stats_flag(fest, monkeypatch):
    monkeypatch.setattr("app.verse_stats.local_today", lambda: FEST_DAY)
    a = free_verses(fest)[0]
    run_prepare(fest, FakePicker([[a]], approve=[a]))
    h = device_headers(fest.client)
    morning, evening = slots_for(FEST_DAY)

    # a surprise until it arrives: nothing public shows it before the send time
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(settings.push_timezone)
    with db() as conn:
        before = verse_of_the_day(conn, fest.store, datetime(2026, 8, 1, 0, 5, tzinfo=tz))
        assert before["verse_id"] != a and all(s["verse_id"] != a for s in before["slots"])

    send(fest, FEST_DAY, 0)
    send(fest, FEST_DAY, 1)
    p = fest.push.payloads[FCM + "device1"]            # the evening send: rotation, no greeting
    assert not p["title"].startswith("Happy")
    entries = fest.client.get("/push/inbox", headers=h).json()["entries"]
    evening_e, morning_e = entries
    assert (morning_e["verse_id"], morning_e["title"], morning_e["festival"]) == (a, "Happy Diwali", "Diwali")
    assert evening_e["festival"] is None and evening_e["verse_id"] != a
    assert count("SELECT title FROM push_deliveries WHERE run_date = ?", morning.key) == "Happy Diwali"
    by_time = {r["time"]: r for r in stats(fest)}
    assert by_time[morning.time]["festival"] == "Diwali" and by_time[evening.time]["festival"] is None
    with db() as conn:
        after = verse_of_the_day(conn, fest.store, datetime(2026, 8, 1, 9, 0, tzinfo=tz))
    assert (after["verse_id"], after["festival"], after["title"]) == (a, "Diwali", "Happy Diwali")


def test_wrong_date_link_cancels_once_without_showing_the_verse(fest):
    a = free_verses(fest)[0]
    run_prepare(fest, FakePicker([[a]], approve=[a]))
    token = fest.mail[0][1].rsplit("/", 1)[1].strip()
    url = f"/push/festival/cancel/{token}"

    page = fest.client.get(url)                           # looking doesn't cancel (mail scanners)
    assert page.status_code == 200 and "Diwali" in page.text and a not in page.text
    assert pick_row()["status"] == "picked"

    done = fest.client.post(url)
    assert done.status_code == 200 and "Cancelled" in done.text and a not in done.text
    assert "That day will use the normal verse." in done.text
    assert pick_row()["status"] == "cancelled" and pick_row()["cancel_hash"] is None
    assert dates("Diwali")[2026] == ("2026-08-01", 1)     # date marked suspect for the next sync
    assert fest.client.post(url).status_code == 404       # works only once
    assert fest.client.get(url).status_code == 404
    assert fest.client.post("/push/festival/cancel/" + "x" * 43).status_code == 404

    slot = slots_for(FEST_DAY)[0]
    with db() as conn:
        assert verse_for(conn, fest.store, slot) == rotation_verse(conn, fest.store, slot)
        # the next sync keeps the reported date suspect while the feed still says the same
        nxt = ("2027-07-25", "Diwali")
        assert festivals.sync(conn, feed(Diwali=[("2026-08-01", "Diwali"), nxt]), date(2026, 7, 1)) == []
        assert festivals.on_day(conn, FEST_DAY) is None
        festivals.sync(conn, feed(Diwali=[("2026-08-03", "Diwali"), nxt]), date(2026, 7, 1))
        assert festivals.on_day(conn, date(2026, 8, 3)).name == "Diwali"


def test_wrong_date_link_expires_after_the_festival_day(fest):
    a = free_verses(fest)[0]
    run_prepare(fest, FakePicker([[a]], approve=[a]))
    token = fest.mail[0][1].rsplit("/", 1)[1].strip()
    with db() as conn:
        assert cancel(conn, token, FEST_DAY + timedelta(days=1))[0] == "expired"
        assert cancel(conn, token, FEST_DAY)[0] == "cancelled"


def test_wrong_date_link_is_dead_once_the_festival_verse_has_been_sent(fest):
    a = free_verses(fest)[0]
    run_prepare(fest, FakePicker([[a]], approve=[a]))
    url = "/push/festival/cancel/" + fest.mail[0][1].rsplit("/", 1)[1].strip()
    device_headers(fest.client)
    send(fest, FEST_DAY, 0)
    slot = slots_for(FEST_DAY)[0]

    assert fest.client.get(url).status_code == 410        # no button to press
    with db() as conn:
        assert cancel(conn, url.rsplit("/", 1)[1], FEST_DAY)[0] == "expired"
        assert verse_for(conn, fest.store, slot).verse_id == a    # still what people received
    assert pick_row()["status"] == "picked"
    assert dates("Diwali")[2026] == ("2026-08-01", 0)     # the date isn't marked suspect


def test_cancel_drops_a_quote_already_written_for_the_festival_verse(fest):
    a = free_verses(fest)[0]
    run_prepare(fest, FakePicker([[a]], approve=[a]))
    slot = slots_for(FEST_DAY)[0]
    asyncio.run(svc.ensure_quote(fest.store, fest.writer, slot, fest.store.get(a)))
    token = fest.mail[0][1].rsplit("/", 1)[1].strip()
    with db() as conn:
        cancel(conn, token, FEST_DAY)
    device_headers(fest.client)
    send(fest, FEST_DAY, 0)
    with db() as conn:
        rot = rotation_verse(conn, fest.store, slot).verse_id
    assert count("SELECT verse_id FROM daily_quotes WHERE quote_date = ?", slot.key) == rot


def test_email_is_off_unless_configured_and_never_raises(env, monkeypatch):
    assert alerts.send("subject", "body") is False
    monkeypatch.setattr(settings, "alert_smtp_user", "u@example.test")
    monkeypatch.setattr(settings, "alert_smtp_password", "pw")

    def boom(*a, **k):
        raise OSError("connection refused to smtp with password pw")

    monkeypatch.setattr(alerts.smtplib, "SMTP", boom)
    assert alerts.send("subject", "body") is False


# ---- auto-updates: build id, versioned assets, /version ----------------------------

def main_client(monkeypatch, tmp_path):
    """The real app's static routes, against a throwaway web/ directory."""
    import app.main as main
    import app.version as ver
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="/ui/styles.css" />'
        '<link rel="manifest" href="/ui/manifest.webmanifest" /></head>'
        '<body><script src="/ui/pushstore.js"></script>'
        '<script type="module" src="/ui/app.js"></script></body></html>')
    (web / "app.js").write_text("import './dom.js';")
    (web / "styles.css").write_text("body{}")
    (web / "secret.txt").write_text("no")
    monkeypatch.setattr(ver, "WEB_DIR", web)
    ver._cached_build.cache_clear()
    from fastapi.testclient import TestClient
    return TestClient(main.app), ver, web


def test_index_points_at_versioned_assets_and_version_endpoint(monkeypatch, tmp_path):
    client, ver, web = main_client(monkeypatch, tmp_path)
    build = ver.build_id()
    page = client.get("/ui/")
    assert page.headers["cache-control"] == "no-cache"            # the HTML is never cached
    assert f'href="/ui/v/{build}/styles.css"' in page.text
    assert f'src="/ui/v/{build}/app.js"' in page.text and f'src="/ui/v/{build}/pushstore.js"' in page.text
    assert 'href="/ui/manifest.webmanifest"' in page.text          # name and icons stay put
    assert f'<meta name="app-build" content="{build}"' in page.text
    v = client.get("/version")
    assert v.json() == {"build": build, "version": ver.app_version()}
    assert v.headers["cache-control"] == "no-store" and ver.app_version()

    js = client.get(f"/ui/v/{build}/app.js")
    assert js.status_code == 200 and "immutable" in js.headers["cache-control"]
    old = client.get("/ui/v/000000000000/app.js")                   # a page from before the deploy
    assert old.status_code == 200 and old.headers["cache-control"] == "no-cache"
    for bad in ("secret.txt", "..%2Fapp%2Fconfig.py", "index.html", ".hidden.js", "nope.js"):
        assert client.get(f"/ui/v/{build}/{bad}").status_code == 404

    (web / "app.js").write_text("import './dom.js'; // changed")   # a deploy changes the build
    ver._cached_build.cache_clear()
    assert ver.build_id() != build
    ver._cached_build.cache_clear()


def test_service_worker_takes_over_at_once_and_is_never_cached():
    from pathlib import Path
    from app.http_extras import SW_HEADERS, _headers_for
    sw = Path("web/sw.js").read_text()
    assert "self.skipWaiting()" in sw and "clients.claim()" in sw
    assert "addEventListener('fetch'" not in sw                     # still no fetch handler / cache
    assert _headers_for("/ui/sw.js") == SW_HEADERS
    assert _headers_for("/ui/v/abc/app.js") is None


# ---- what's new: testers, stages, announce -------------------------------------------

import app.releases as rel  # noqa: E402
import app.version as version_mod  # noqa: E402

NOTES = ({"version": "2", "date": "", "title": "Second", "items": ["b"], "hints": []},
         {"version": "1", "date": "", "title": "First", "items": ["a"], "hints": []})


@pytest.fixture
def notes(env, monkeypatch):
    monkeypatch.setattr(version_mod, "releases", lambda: NOTES)
    monkeypatch.setattr(rel, "releases", lambda: NOTES)
    monkeypatch.setattr(rel, "latest_release", lambda: rel.releases()[0])
    return env


def seen_by(env, headers=None):
    r = env.client.get("/push/releases/latest", headers=headers or {}).json()["release"]
    return r and r["version"]


def announce(env, audience, version="2", **extra):
    return env.client.post("/push/announce", headers=ADM,
                           json={"version": version, "audience": audience, **extra})


def test_deploy_announces_nothing_then_testers_then_all(notes):
    me, friend = device_headers(notes.client, 1), device_headers(notes.client, 2)
    notes.push.calls.clear()
    assert seen_by(notes) is None and seen_by(notes, me) is None      # deployed, not announced

    r = notes.client.put("/push/testers", headers=ADM, json={"device_ids": [1]})
    assert r.json()["testers"] == [1] and r.json()["release"]["stage"] == "draft"
    assert announce(notes, "testers").json()["devices"] == 1
    assert notes.push.calls == [FCM + "device1"]                       # only my phone
    p = notes.push.payloads[FCM + "device1"]
    assert p["body"] == "New update: open InnerOracle to see what's new." and p["url"] == "/ui/"
    assert p["tag"] == "update" and "inbox_id" not in p
    assert seen_by(notes, me) == "2" and seen_by(notes, friend) is None and seen_by(notes) is None

    notes.push.calls.clear()
    assert announce(notes, "all").json()["stage"] == "all"
    assert sorted(notes.push.calls) == [FCM + "device1", FCM + "device2"]
    assert seen_by(notes, friend) == "2" and seen_by(notes) == "2"
    # an announcement is not a verse: no inbox entry, no stats
    assert count("SELECT COUNT(*) FROM device_inbox") == 0
    assert count("SELECT COUNT(*) FROM verse_stats") == 0
    assert count("SELECT COUNT(*) FROM admin_actions WHERE action = 'announce'") == 2
    assert count("SELECT COUNT(*) FROM push_deliveries WHERE kind = 'announce'") == 3


def test_announce_protections(notes, monkeypatch):
    device_headers(notes.client, 1)
    assert announce(notes, "testers").status_code == 422                # no testers set
    assert announce(notes, "all", version="1").status_code == 409       # not the latest release
    assert announce(notes, "all", version="9").status_code == 409
    assert notes.client.post("/push/announce", json={"version": "2", "audience": "all"}).status_code == 403

    notes.client.put("/push/testers", headers=ADM, json={"device_ids": [1]})
    assert announce(notes, "testers").status_code == 200
    again = announce(notes, "testers")
    assert again.status_code == 409 and again.json()["needs_confirm"] is True
    assert announce(notes, "testers", repeat=True).status_code == 200   # re-testing, confirmed

    assert announce(notes, "all").status_code == 200
    assert announce(notes, "all").status_code == 409                    # once per release
    assert announce(notes, "testers", repeat=True).json()["stage"] == "all"   # never moves back

    newer = ({"version": "3", "date": "", "title": "Third", "items": [], "hints": []}, *NOTES)
    monkeypatch.setattr(rel, "releases", lambda: newer)
    assert announce(notes, "all", version="3").status_code == 429       # within 24 hours
    with db() as conn:
        conn.execute("UPDATE release_stages SET all_sent_at = '2020-01-01T00:00:00Z'")
    assert announce(notes, "all", version="3").status_code == 200


def test_tester_numbers_must_be_real_devices_and_die_with_the_device(notes):
    h = device_headers(notes.client, 1)
    bad = notes.client.put("/push/testers", headers=ADM, json={"device_ids": [1, 7]})
    assert bad.status_code == 422 and "#7" in bad.json()["detail"]
    assert notes.client.get("/push/release", headers=ADM).json()["testers"] == []
    notes.client.put("/push/testers", headers=ADM, json={"device_ids": [1]})
    assert notes.client.put("/push/testers", json={"device_ids": [1]}).status_code == 403
    notes.client.post("/push/unsubscribe", headers=h)
    assert notes.client.get("/push/release", headers=ADM).json()["testers"] == []


def test_releases_latest_ignores_bad_device_headers(notes):
    h = device_headers(notes.client, 1)
    notes.client.put("/push/testers", headers=ADM, json={"device_ids": [1]})
    announce(notes, "testers")
    assert seen_by(notes, h) == "2"
    assert seen_by(notes, {**h, "X-Device-Secret": "wrong"}) is None    # no error, just not a tester
    assert seen_by(notes, {"X-Device-Id": "abc", "X-Device-Secret": "x"}) is None


def test_release_notes_file_is_well_formed():
    version_mod._cached_releases.cache_clear()
    notes_ = version_mod.releases()
    assert notes_ and all(r["version"] and r["title"] and r["items"] for r in notes_)
    html = open("web/index.html").read()
    for h in notes_[0]["hints"]:   # every hint has somewhere to point (the star is added by inbox.js)
        assert f'data-hint="{h["target"]}"' in html or h["target"] == "favourites-star"


# ---- local testing ---------------------------------------------------------------

def test_local_dev_refused_next_to_production_state(env, monkeypatch, tmp_path):
    from app.push_config import validate_push_settings
    monkeypatch.delenv("TUNNEL_TOKEN", raising=False)
    assert validate_push_settings(settings) == []
    monkeypatch.setattr(settings, "local_dev", True)          # env's database is named push.db
    assert any("LOCAL_DEV needs its own PUSH_DB_PATH" in p for p in validate_push_settings(settings))
    monkeypatch.setattr(settings, "push_db_path", str(tmp_path / "state" / "push.local.db"))
    assert validate_push_settings(settings) == []
    monkeypatch.setenv("TUNNEL_TOKEN", "x")
    assert any("TUNNEL_TOKEN" in p for p in validate_push_settings(settings))


def test_dev_helpers_send_real_looking_pushes_with_a_mocked_model(env, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.local_dev as dev
    from app.quote import QuoteWriter

    monkeypatch.setattr(settings, "mock_model", True)
    h = device_headers(env.client)
    app = FastAPI()
    app.include_router(dev.router)
    app.state.push_scheduler = SimpleNamespace(
        store=env.store, writer=QuoteWriter(client=object()), picker=FestivalPicker(client=object()))
    client = TestClient(app)

    r = client.post("/dev/test-push").json()
    assert r["devices"] == 1 and r["delivered"] == 1
    assert client.post("/dev/test-push").json()["delivered"] == 1
    assert env.client.get("/push/inbox", headers=h).json()["unread"] == 2     # a new entry each time

    f = client.post("/dev/festival-today").json()
    assert f["festival"] == "Diwali" and f["picked"] is True
    assert env.push.payloads[FCM + "device1"]["title"] == "Happy Diwali"
    newest = env.client.get("/push/inbox", headers=h).json()["entries"][0]
    assert newest["festival"] == "Diwali" and newest["verse_id"] == f["verse_id"]
    assert client.post("/dev/festival-today?name=Nope").status_code == 404

    assert env.client.get("/push/releases/latest").json()["release"] is None
    assert client.post("/dev/release-all").json()["stage"] == "all"       # no push, just visible
    assert env.client.get("/push/releases/latest").json()["release"]["version"] == version_mod.app_version()


def test_dev_helpers_refuse_other_websites():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.local_dev as dev

    app = FastAPI()
    app.include_router(dev.router)
    client = TestClient(app)        # no scheduler: a call that gets through answers 503
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                    {"Origin": "https://evil.example"}):
        assert client.post("/dev/test-push", headers=headers).status_code == 403
    for headers in ({}, {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"}):
        assert client.post("/dev/test-push", headers=headers).status_code == 503


def test_malformed_device_ids_are_refused_not_crashed_on(env):
    for bad in ("9" * 30, "1.0", "-1", " 1"):
        h = {"X-Device-Id": bad, "X-Device-Secret": "x"}
        assert env.client.get("/push/inbox", headers=h).status_code == 401
        assert env.client.get("/push/releases/latest", headers=h).json() == {"release": None}


def test_forced_send_counts_as_sent_only_where_it_is_the_first_send(env, monkeypatch):
    monkeypatch.setattr("app.verse_stats.local_today", lambda: DAY)
    device_headers(env.client)
    force = lambda: env.client.post("/push/send-daily?force=true", headers=ADM)  # noqa: E731
    assert force().status_code == 200
    (row,) = stats(env)
    assert row["sent"] == 1
    with db() as conn:                       # a later forced re-send of the same slot
        conn.execute("DELETE FROM admin_actions")
    force()
    assert stats(env)[0]["sent"] == 1


def test_dev_helpers_are_not_mounted_without_local_dev():
    import app.main as main
    assert not settings.local_dev
    assert not any(getattr(r, "path", "").startswith("/dev") for r in main.app.routes)


# ---- deploying onto the production database ------------------------------------------

def test_upgrade_keeps_every_existing_device(env, tmp_path, monkeypatch):
    """A database created by the previously deployed code, with subscribers in
    it, comes through the new migration and a first send with every device,
    its number, keys and secret intact."""
    import sqlite3
    from pathlib import Path

    from app.push_db import migrate

    path = tmp_path / "prod" / "push.db"
    path.parent.mkdir()
    old = sqlite3.connect(path)
    old.executescript(Path("tests/fixtures/push_schema_b333567.sql").read_text())
    for i in (1, 2, 3):
        old.execute("INSERT INTO push_subscriptions(endpoint, p256dh, auth, secret_hash, created_at) "
                    "VALUES (?, ?, ?, ?, '2026-10-24T09:13:00Z')",
                    (f"{FCM}old{i}", f"p{i}", f"a{i}", f"hash{i}"))
    old.execute("DELETE FROM push_subscriptions WHERE id = 2")      # a gap: numbers are never reused
    old.execute("INSERT INTO push_deliveries(sent_at, kind, device_id, status) "
                "VALUES ('2026-11-16T16:30:00Z', 'daily', 1, 'delivered')")
    old.commit()
    before = old.execute("SELECT * FROM push_subscriptions ORDER BY id").fetchall()
    old.close()

    monkeypatch.setattr(settings, "push_db_path", str(path))
    migrate()
    migrate()                                                       # every restart runs it again
    with db() as conn:
        from app.daily_verse import sync_rotation
        sync_rotation(conn, env.store, "seed")
        after = [tuple(r) for r in conn.execute("SELECT * FROM push_subscriptions ORDER BY id")]
        assert after == before and [r[0] for r in after] == [1, 3]
        assert count("SELECT COUNT(*) FROM push_deliveries") == 1

    send(env, DAY)                                                  # the first send after the deploy
    with db() as conn:
        rows = conn.execute("SELECT id, endpoint, p256dh, auth, secret_hash, created_at "
                            "FROM push_subscriptions ORDER BY id").fetchall()
        assert [tuple(r) for r in rows] == [b[:6] for b in before]
        assert sorted(env.push.calls) == [FCM + "old1", FCM + "old3"]
        assert [r[0] for r in conn.execute("SELECT device_id FROM device_inbox ORDER BY 1")] == [1, 3]
        new = conn.execute("INSERT INTO push_subscriptions(endpoint, p256dh, auth, secret_hash, "
                           "created_at) VALUES ('e', 'p', 'a', 'h', 'x')").lastrowid
        assert new == 4                                             # new sign-ups continue the numbering
