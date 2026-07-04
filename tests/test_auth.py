"""Human-gate middleware + Turnstile cookie tests (no live Cloudflare calls)."""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

import app.turnstile as ts
from app.auth import HumanGateMiddleware
from app.turnstile import COOKIE, sign_cookie, valid_cookie


def make_client(enabled: bool) -> TestClient:
    app = FastAPI()
    app.add_middleware(HumanGateMiddleware, enabled=enabled)

    @app.get("/")
    def root():
        return PlainTextResponse("home")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/chat")
    def chat():
        return {"ok": True}

    @app.get("/verse/{vid}")
    def verse(vid: str):
        return {"id": vid}

    return TestClient(app)


# ---- signed cookie ----

def test_cookie_round_trip(monkeypatch):
    monkeypatch.setattr(ts.settings, "session_secret", "s3cret", raising=False)
    assert valid_cookie(sign_cookie(60)) is True


def test_expired_cookie_rejected(monkeypatch):
    monkeypatch.setattr(ts.settings, "session_secret", "s3cret", raising=False)
    assert valid_cookie(sign_cookie(-1)) is False


def test_forged_cookie_rejected(monkeypatch):
    monkeypatch.setattr(ts.settings, "session_secret", "s3cret", raising=False)
    forged = f"{int(time.time()) + 999}.not-a-real-signature"
    assert valid_cookie(forged) is False


# ---- gate behaviour ----

def test_gate_off_leaves_chat_open():
    c = make_client(enabled=False)
    assert c.post("/chat").status_code == 200


def test_gate_on_blocks_chat_without_cookie():
    c = make_client(enabled=True)
    assert c.post("/chat").status_code == 401


def test_gate_on_allows_chat_with_valid_cookie(monkeypatch):
    monkeypatch.setattr(ts.settings, "session_secret", "s3cret", raising=False)
    c = make_client(enabled=True)
    c.cookies.set(COOKIE, sign_cookie(60))
    assert c.post("/chat").status_code == 200


def test_gate_only_covers_chat():
    c = make_client(enabled=True)   # everything but /chat stays open
    assert c.get("/").status_code == 200
    assert c.get("/health").status_code == 200
    assert c.get("/verse/BG-2.47").status_code == 200
