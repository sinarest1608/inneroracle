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
