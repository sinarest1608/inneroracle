"""Build id, release notes and the versioned index page.

The build id is a hash of everything the browser loads (web/ plus the release
notes), so it changes exactly when a deploy changes the app. Scripts and styles
are served under /ui/v/<build>/…, which makes every deploy a new set of URLs:
those can be cached forever, while the HTML that points at them is never cached.
Because the app's modules import each other by relative path, they all pick up
the versioned prefix from the entry script.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings

WEB_DIR = Path("web")
RELEASES_PATH = Path(__file__).parent / "resources" / "releases.json"
ASSET_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(js|css)$")
_ASSET_REF = re.compile(r'\b(href|src)="/ui/([A-Za-z0-9_.-]+\.(?:js|css))"')
_HINT_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _compute_build() -> str:
    h = hashlib.sha256()
    files = sorted(p for p in WEB_DIR.rglob("*") if p.is_file()) if WEB_DIR.is_dir() else []
    for p in [*files, RELEASES_PATH]:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        h.update(p.as_posix().encode() + b"\0" + data + b"\0")
    return h.hexdigest()[:12]


_cached_build = lru_cache(maxsize=1)(_compute_build)


def build_id() -> str:
    """Fixed for the life of the process; recomputed on every call in local
    development, where files change under a running server."""
    return _compute_build() if settings.local_dev else _cached_build()


def _text(v: object, limit: int) -> str | None:
    return v.strip()[:limit] if isinstance(v, str) and v.strip() else None


def _load_releases() -> tuple[dict, ...]:
    try:
        data = json.loads(RELEASES_PATH.read_text())
    except (OSError, ValueError):
        return ()
    out = []
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict):
            continue
        version, title = _text(r.get("version"), 40), _text(r.get("title"), 120)
        if not version or not title or any(o["version"] == version for o in out):
            continue
        hints = [{"id": h["id"], "target": h["target"], "text": _text(h.get("text"), 200)}
                 for h in r.get("hints", [])
                 if isinstance(h, dict) and all(isinstance(h.get(k), str) and _HINT_KEY.match(h[k])
                                                for k in ("id", "target")) and _text(h.get("text"), 200)]
        out.append({"version": version, "date": _text(r.get("date"), 20) or "", "title": title,
                    "items": [t for i in r.get("items", []) if (t := _text(i, 300))][:12],
                    "hints": hints[:12]})
    return tuple(out)


_cached_releases = lru_cache(maxsize=1)(_load_releases)


def releases() -> tuple[dict, ...]:
    """Release notes, newest first (the order of the file)."""
    return _load_releases() if settings.local_dev else _cached_releases()


def latest_release() -> dict | None:
    r = releases()
    return r[0] if r else None


def app_version() -> str:
    r = latest_release()
    return r["version"] if r else ""


def render_index() -> str | None:
    """web/index.html with its scripts and styles pointed at this build, and the
    build id and version stamped in for the page's update check."""
    try:
        page = (WEB_DIR / "index.html").read_text()
    except OSError:
        return None
    build = build_id()
    page = _ASSET_REF.sub(lambda m: f'{m.group(1)}="/ui/v/{build}/{m.group(2)}"', page)
    stamp = (f'<meta name="app-build" content="{build}" />\n'
             f'<meta name="app-version" content="{html.escape(app_version(), quote=True)}" />\n')
    return page.replace("</head>", stamp + "</head>", 1)


def asset_path(name: str) -> Path | None:
    if not ASSET_NAME.match(name) or ".." in name:
        return None
    p = WEB_DIR / name
    return p if p.is_file() else None
