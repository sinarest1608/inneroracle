"""Token auth-gate middleware tests (isolated from the heavy app stack)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.auth import COOKIE, TokenAuthMiddleware


def make_client(token: str) -> TestClient:
    app = FastAPI()
    app.add_middleware(TokenAuthMiddleware, token=token)

    @app.get("/")
    def root():
        return PlainTextResponse("home")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/login")
    def login():
        return PlainTextResponse("login")

    @app.post("/chat")
    def chat():
        return {"ok": True}

    @app.get("/verse/{vid}")
    def verse(vid: str):
        return {"id": vid}

    return TestClient(app)


def test_gate_off_when_no_token():
    c = make_client("")
    assert c.get("/").status_code == 200
    assert c.post("/chat").status_code == 200


def test_unauth_browser_path_redirects_to_login():
    c = make_client("secret")
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_unauth_api_paths_return_401():
    c = make_client("secret")
    assert c.post("/chat").status_code == 401
    assert c.get("/verse/BG-2.47", follow_redirects=False).status_code == 401


def test_open_paths_reachable_without_token():
    c = make_client("secret")
    assert c.get("/health").status_code == 200
    assert c.get("/login").status_code == 200


def test_valid_cookie_passes():
    c = make_client("secret")
    c.cookies.set(COOKIE, "secret")
    assert c.get("/").status_code == 200
    assert c.post("/chat").status_code == 200


def test_wrong_cookie_blocked():
    c = make_client("secret")
    c.cookies.set(COOKIE, "nope")
    assert c.get("/", follow_redirects=False).status_code == 303
    assert c.post("/chat").status_code == 401
