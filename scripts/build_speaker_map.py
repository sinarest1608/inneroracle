"""Build the hand-checked speaker map (app/resources/speaker_map.json).

Reads data/verses.db read-only and assigns each verse a speaker with a
confidence status. Signals, in reading order:

  marker      "X uvāca" at the start of a line in the Sanskrit or the
              transliteration, carried forward until the next marker.
              Narrative uses ("tam uvāca hṛṣīkeśaḥ", BG-2.10) carry no name
              before "uvāca" and are ignored.
  structure   The books' frame, hand-coded below. BG: Dhritarashtra speaks
              only BG-1.1; otherwise the chapter's default floor holder
              (ch. 1 Sanjaya, ch. 11 none, all others Krishna). AG: the
              chapter-level speaker table (ch. 6 left open: uncertain).
  translation An English "Arjuna said:" / "The Blessed Lord said:" prefix
              (this verse only, not carried).
  address     A vocative in the English translation naming the listener
              ("O son of Kuntī" -> Krishna speaks; "O Keśava" -> Arjuna;
              "O King" -> Sanjaya; AG "my son" -> Ashtavakra). This verse
              only, and only names allowed for the book.
  commentary  Commentary sentence naming the speaker ("Arjuna asks ...").
              This verse only, used only when exactly one name matches.
  reference   app/resources/speaker_changes_reference.json, carried forward.

How the reference counts (it is drafted from recall and every entry starts
"verified": false): an UNVERIFIED reference entry can only push a verse to
`review` when it disagrees; it never counts toward `confirmed`. Once the
human sets "verified": true on an entry it counts as a supporting signal
like the others.

Scoring:
  unknown    no signal at all (or a malformed verse_no row).
  review     any two signals disagree; or the verse opens a chapter without
             its own marker; or the speaker changes here without a marker in
             this verse; or only one supporting signal exists.
  confirmed  at least two supporting signals agree and none disagree.
A speaker change that IS marked in the verse itself (e.g. "arjuna uvāca" at
BG-2.4) is not sent to review for that reason alone: the marker removes the
off-by-one risk that the plan's "at a speaker change" rule guards against.

Review rows go to scripts/out/speaker_review.csv; consecutive rows in the
same chapter with the same candidate names and reason are collapsed into one
row spanning verse_id..last_verse_id. Fill `decided_speaker` (an allowlisted
name or "unknown") and run with --merge. Human decisions become `confirmed`
(or `unknown`), carry the source "human", and survive later rebuilds.

Usage:
  python scripts/build_speaker_map.py            # build map + review CSV
  python scripts/build_speaker_map.py --merge    # merge filled CSV
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "verses.db"
MAP_PATH = ROOT / "app" / "resources" / "speaker_map.json"
REF_PATH = ROOT / "app" / "resources" / "speaker_changes_reference.json"
REVIEW_PATH = ROOT / "scripts" / "out" / "speaker_review.csv"

ALLOWED: dict[str, tuple[str, ...]] = {
    "bhagavad_gita": ("Krishna", "Arjuna", "Sanjaya", "Dhritarashtra"),
    "ashtavakra_gita": ("Ashtavakra", "Janaka"),
}
BOOK_ORDER = {"bhagavad_gita": 0, "ashtavakra_gita": 1}
SUPPORT = ("marker", "structure", "translation", "address", "commentary")

# --------------------------------------------------------------------------
# Text normalization and the marker normalizer
# --------------------------------------------------------------------------


def fold(text: str) -> str:
    """Lowercase Latin text with diacritics stripped and hyphens as spaces."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    stripped = stripped.lower().replace("-", " ")
    return re.sub(r"[ \t]+", " ", stripped)


# Latin: speaker name immediately followed by uvaca/uvacha, at a line start.
_LATIN_MARKER = re.compile(
    r"(?m)^\s*(?P<name>"
    r"(?:sri|shri|sree)\s*bhagavan(?=\s*u)|bhagavan(?=\s*u)"
    r"|arjuna|sanjaya|samjaya|sanjay"
    r"|dhrtarastra|dhritarashtra|dhrtarashtra|dhritarastra"
    r"|janaka|astavakra|ashtavakra|ashtavakra"
    r")\s*u(?:v|w)ac(?:h)?a\b"
)
_LATIN_NAMES = {
    "bhagavan": "Krishna", "arjuna": "Arjuna", "sanjaya": "Sanjaya",
    "samjaya": "Sanjaya", "sanjay": "Sanjaya", "dhrtarastra": "Dhritarashtra",
    "dhritarashtra": "Dhritarashtra", "dhrtarashtra": "Dhritarashtra",
    "dhritarastra": "Dhritarashtra", "janaka": "Janaka",
    "astavakra": "Ashtavakra", "ashtavakra": "Ashtavakra",
}

# Devanagari: "श्रीभगवानुवाच" (sandhi), "अर्जुन उवाच", "सञ्जय/संजय उवाच", ...
_DEVA_MARKER = re.compile(
    r"(?m)^\s*(?:"
    r"(?P<krishna>(?:श्री\s*)?भगवा(?:न्\s*उ|न\s*उ|नु)वाच)"
    r"|(?P<name>अर्जुन|स(?:ञ्|ं|न्)जय|धृतराष्ट्र|जनक|अष्टावक्र)\s*उवाच"
    r")"
)
_DEVA_NAMES = {
    "अर्जुन": "Arjuna", "धृतराष्ट्र": "Dhritarashtra", "जनक": "Janaka",
    "अष्टावक्र": "Ashtavakra",
}


def normalize_marker(text: str) -> str | None:
    """Return the speaker named by an "uvāca" marker in `text`, or None.

    Only a name directly before "uvāca" at the start of a line counts, so
    narrative uses such as "tam uvāca hṛṣīkeśaḥ" return None.
    """
    if not text:
        return None
    deva = _DEVA_MARKER.search(text)
    if deva:
        if deva.group("krishna"):
            return "Krishna"
        name = deva.group("name")
        return "Sanjaya" if name.startswith("स") else _DEVA_NAMES[name]
    latin = _LATIN_MARKER.search(fold(text))
    if latin:
        key = re.sub(r"^(?:sri|shri|sree)\s*", "", latin.group("name"))
        return _LATIN_NAMES[key.strip()]
    return None


# --------------------------------------------------------------------------
# Translation / address / commentary signals (English, diacritics folded)
# --------------------------------------------------------------------------

_SAID = re.compile(
    r"^\s*(?P<who>the blessed lord|the supreme lord|the supreme person"
    r"(?: \[bhagavan\])?|the lord|sri bhagavan|lord krsna|krsna|krishna"
    r"|arjuna|sanjaya|dhrtarastra|dhritarashtra|janaka|astavakra|ashtavakra)"
    r"(?: then)? (?:said|inquired|asked)\b"
)
_SAID_NAMES = {
    "arjuna": "Arjuna", "sanjaya": "Sanjaya", "dhrtarastra": "Dhritarashtra",
    "dhritarashtra": "Dhritarashtra", "janaka": "Janaka",
    "astavakra": "Ashtavakra", "ashtavakra": "Ashtavakra",
}

# Vocatives -> who is speaking. "Bhārata"/"descendant of Bharata" and
# "mighty-armed" are used by more than one speaker, so they are left out.
_ADDRESS = (
    ("Krishna", re.compile(
        r"\bo (?:my dear )?(?:arjuna|partha|son of kunti|son of prtha"
        r"|dhananjaya|kaunteya|gudakesa|chastiser of the enemy"
        r"|subduer of the enemy|conqueror of wealth|winner of wealth"
        r"|best of the kurus|son of the kurus|kurunandana|parantapa)\b")),
    ("Arjuna", re.compile(
        r"\bo (?:my dear )?(?:lord )?(?:krsna|krishna|kesava|madhava"
        r"|janardana|govinda|madhusudana|acyuta|varsneya|hrsikesa"
        r"|killer of madhu|killer of the demon madhu|supreme personality"
        r"|lord of the universe|master of all senses|infallible one)\b")),
    ("Sanjaya", re.compile(r"\bo (?:great )?king\b")),
    ("Ashtavakra", re.compile(r"\b(?:my son|my child|o child)\b")),
)

_COMMENTARY = re.compile(
    r"\b(?P<who>lord krsna|krsna|the lord|arjuna|sanjaya|dhrtarastra)"
    r" (?:here |now |again |further |then )?"
    r"(?:says|is saying|replies|answers|asks|inquires|is inquiring"
    r"|is asking|questions|instructs)\b"
)
_COMMENTARY_NAMES = {
    "lord krsna": "Krishna", "krsna": "Krishna", "the lord": "Krishna",
    "arjuna": "Arjuna", "sanjaya": "Sanjaya", "dhrtarastra": "Dhritarashtra",
}


def translation_speaker(text: str) -> str | None:
    m = _SAID.match(fold(text))
    if not m:
        return None
    return _SAID_NAMES.get(m.group("who"), "Krishna")


def address_speaker(text: str, book: str | None = None) -> str | None:
    folded = fold(text)
    allowed = ALLOWED.get(book or "", None)
    hits = {name for name, rx in _ADDRESS
            if (allowed is None or name in allowed) and rx.search(folded)}
    return hits.pop() if len(hits) == 1 else None


def commentary_speaker(text: str) -> str | None:
    hits = {_COMMENTARY_NAMES[m.group("who")]
            for m in _COMMENTARY.finditer(fold(text))}
    return hits.pop() if len(hits) == 1 else None


# --------------------------------------------------------------------------
# Structural rules
# --------------------------------------------------------------------------

# Ashtavakra Gita chapter speakers (standard frame; ch. 6 is attributed to
# Ashtavakra in most editions but some read it as Janaka, so it stays open).
AG_CHAPTERS: dict[int, str | None] = {
    1: "Ashtavakra", 2: "Janaka", 3: "Ashtavakra", 4: "Janaka",
    5: "Ashtavakra", 6: None, 7: "Janaka", 8: "Ashtavakra",
    9: "Ashtavakra", 10: "Ashtavakra", 11: "Ashtavakra", 12: "Janaka",
    13: "Janaka", 14: "Janaka", 15: "Ashtavakra", 16: "Ashtavakra",
    17: "Ashtavakra", 18: "Ashtavakra", 19: "Janaka", 20: "Janaka",
}
# Bhagavad Gita default floor holder per chapter (None = no default).
BG_CHAPTER_DEFAULT: dict[int, str | None] = {1: "Sanjaya", 11: None}


def structural_speaker(book: str, chapter: int, first_no: int) -> str | None:
    if book == "bhagavad_gita":
        if chapter == 1 and first_no == 1:
            return "Dhritarashtra"
        return BG_CHAPTER_DEFAULT.get(chapter, "Krishna")
    if chapter == 1 and first_no == 1:
        return "Janaka"
    return AG_CHAPTERS.get(chapter)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


@dataclass
class Row:
    verse_id: str
    book: str
    chapter: int
    verse_no: str
    sanskrit: str
    transliteration: str
    translation: str
    commentary: str
    first_no: int | None = None
    signals: dict[str, str | None] = field(default_factory=dict)
    marker_here: bool = False
    marker_conflict: str | None = None


def first_number(verse_no: str) -> int | None:
    m = re.match(r"\s*(\d+)", verse_no or "")
    return int(m.group(1)) if m else None


def reading_key(row: Row) -> tuple[int, int, int, str]:
    first = row.first_no if row.first_no is not None else 10**6
    return (BOOK_ORDER.get(row.book, 9), row.chapter, first, row.verse_no)


def load_rows(db_path: Path) -> list[Row]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            "SELECT verse_id, book, chapter, verse_no, sanskrit, "
            "transliteration, translation, commentary FROM verses"
        )
        rows = [Row(*(v if v is not None else "" for v in r)) for r in cur]
    finally:
        conn.close()
    for r in rows:
        r.chapter = int(r.chapter)
        r.first_no = first_number(r.verse_no)
    rows.sort(key=reading_key)
    return rows


def load_reference(path: Path) -> dict[str, dict]:
    """verse_id -> {speaker, verified} for each speaker-change point."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {e["verse_id"]: e for e in data.get("entries", [])}


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def collect_signals(rows: list[Row], reference: dict[str, dict]) -> None:
    marker_chain: dict[str, str | None] = {}
    ref_chain: dict[str, str | None] = {}
    ref_verified: dict[str, bool] = {}
    for r in rows:
        if r.first_no is None:
            continue  # malformed row: never part of the chain
        sk = normalize_marker(r.sanskrit)
        tr = normalize_marker(r.transliteration)
        here = sk or tr
        if sk and tr and sk != tr:
            r.marker_conflict = f"sanskrit={sk} transliteration={tr}"
        if here:
            r.marker_here = True
            marker_chain[r.book] = here
        ref = reference.get(r.verse_id)
        if ref:
            ref_chain[r.book] = ref.get("speaker")
            ref_verified[r.book] = bool(ref.get("verified"))
        r.signals = {
            "marker": marker_chain.get(r.book),
            "structure": structural_speaker(r.book, r.chapter, r.first_no),
            "translation": translation_speaker(r.translation),
            "address": address_speaker(r.translation, r.book),
            "commentary": commentary_speaker(r.commentary),
            "reference": ref_chain.get(r.book),
        }
        if ref_verified.get(r.book):
            r.signals["reference_verified"] = r.signals["reference"]


def score(rows: list[Row], reference: dict[str, dict]) -> tuple[dict, dict]:
    """Return (speaker_map, review_info); review_info maps verse_id to
    ({name: [sources]}, reason)."""
    out: dict[str, dict] = {}
    review: dict[str, tuple[dict[str, list[str]], str]] = {}
    prev: dict[tuple[str, int], str | None] = {}
    prev_book_speaker: dict[str, str | None] = {}
    for r in rows:
        if r.first_no is None:
            out[r.verse_id] = {"speaker": None, "status": "unknown",
                               "sources": ["malformed_verse_no"]}
            continue
        s = r.signals
        allowed = ALLOWED.get(r.book, ())
        named = {k: v for k, v in s.items() if v and k != "reference_verified"}
        bad = {k: v for k, v in named.items() if v not in allowed}
        for k in bad:
            named.pop(k)
        speaker = (s["marker"] or s["translation"] or s["structure"]
                   or s.get("reference_verified") or s["reference"])
        if speaker not in allowed:
            speaker = None
        supporting = [k for k in SUPPORT if named.get(k) == speaker]
        if s.get("reference_verified") == speaker and speaker:
            supporting.append("reference")
        values = sorted(set(named.values()))
        candidates: dict[str, list[str]] = {}
        for k, v in named.items():
            candidates.setdefault(v, []).append(k)

        chapter_start = (r.book, r.chapter) not in prev
        changed = (r.book in prev_book_speaker
                   and prev_book_speaker[r.book] != speaker)
        ref_change_here = r.verse_id in reference

        reason = None
        if not named:
            status = "unknown"
        elif len(values) > 1:
            status, reason = "review", "signals disagree"
        elif r.marker_conflict:
            status, reason = "review", f"marker conflict ({r.marker_conflict})"
        elif chapter_start and not r.marker_here:
            status, reason = "review", "chapter boundary without marker"
        elif (changed or ref_change_here) and not r.marker_here:
            status, reason = "review", "speaker change without marker"
        elif len(supporting) < 2:
            status, reason = "review", "single supporting signal"
        else:
            status = "confirmed"
        if bad:
            status = "review"
            reason = f"non-allowlisted name {bad}"

        out[r.verse_id] = {
            "speaker": speaker if status != "unknown" else None,
            "status": status,
            "sources": supporting if status != "unknown" else [],
        }
        if status == "review":
            review[r.verse_id] = (candidates, reason or "")
        prev[(r.book, r.chapter)] = speaker
        prev_book_speaker[r.book] = speaker
    return out, review


def apply_human(speaker_map: dict, decisions: dict[str, str | None]) -> None:
    for vid, name in decisions.items():
        if vid not in speaker_map:
            continue
        speaker_map[vid] = (
            {"speaker": None, "status": "unknown", "sources": ["human"]}
            if name is None
            else {"speaker": name, "status": "confirmed", "sources": ["human"]}
        )


def existing_human(path: Path) -> dict[str, str | None]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {vid: e.get("speaker") for vid, e in data.items()
            if "human" in e.get("sources", [])}


def write_map(path: Path, rows: list[Row], speaker_map: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {r.verse_id: speaker_map[r.verse_id] for r in rows}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


CSV_COLUMNS = ("verse_id", "last_verse_id", "verses", "book",
               "current_speaker", "candidates", "reason",
               "translation_snippet", "decided_speaker")


def _format_candidates(cands: dict[str, list[str]]) -> str:
    return " | ".join(f"{name} [{', '.join(srcs)}]"
                      for name, srcs in sorted(cands.items()))


def write_review(path: Path, rows: list[Row], speaker_map: dict,
                 review: dict[str, tuple[dict[str, list[str]], str]]) -> int:
    groups: list[dict] = []
    merged: list[dict[str, list[str]]] = []
    last_key = None
    for r in rows:
        entry = speaker_map[r.verse_id]
        if entry["status"] != "review":
            last_key = None
            continue
        cands, reason = review[r.verse_id]
        key = (r.book, r.chapter, entry["speaker"], frozenset(cands), reason)
        if key != last_key:
            groups.append({
                "verse_id": r.verse_id, "last_verse_id": r.verse_id,
                "verses": 0, "book": r.book,
                "current_speaker": entry["speaker"] or "",
                "reason": reason,
                "translation_snippet": " ".join(r.translation.split())[:160],
                "decided_speaker": "",
            })
            merged.append({})
        groups[-1]["last_verse_id"] = r.verse_id
        groups[-1]["verses"] += 1
        for name, srcs in cands.items():
            bucket = merged[-1].setdefault(name, [])
            bucket.extend(x for x in srcs if x not in bucket)
        last_key = key
    for g, cands in zip(groups, merged):
        g["candidates"] = _format_candidates(cands)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(groups)
    return len(groups)


def read_decisions(path: Path, rows: list[Row]) -> dict[str, str | None]:
    """Expand filled CSV rows (verse_id..last_verse_id spans) to decisions."""
    order = [r.verse_id for r in rows]
    index = {vid: i for i, vid in enumerate(order)}
    book_of = {r.verse_id: r.book for r in rows}
    decisions: dict[str, str | None] = {}
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for line in csv.DictReader(fh):
            raw = (line.get("decided_speaker") or "").strip()
            if not raw:
                continue
            first = line["verse_id"].strip()
            last = (line.get("last_verse_id") or first).strip() or first
            if first not in index or last not in index:
                errors.append(f"{first}..{last}: unknown verse_id")
                continue
            book = book_of[first]
            if raw.lower() == "unknown":
                name: str | None = None
            else:
                match = [n for n in ALLOWED[book] if n.lower() == raw.lower()]
                if not match:
                    errors.append(f"{first}: '{raw}' not allowed for {book}")
                    continue
                name = match[0]
            for vid in order[index[first]:index[last] + 1]:
                decisions[vid] = name
    if errors:
        raise SystemExit("Bad review decisions:\n  " + "\n  ".join(errors))
    return decisions


def build(db_path: Path = DB_PATH, ref_path: Path = REF_PATH,
          prior: dict[str, str | None] | None = None
          ) -> tuple[list[Row], dict, dict]:
    rows = load_rows(db_path)
    reference = load_reference(ref_path)
    collect_signals(rows, reference)
    speaker_map, review = score(rows, reference)
    if prior:
        apply_human(speaker_map, prior)
    return rows, speaker_map, review


def summarize(rows: list[Row], speaker_map: dict) -> str:
    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        st = speaker_map[r.verse_id]["status"]
        counts.setdefault(r.book, {"confirmed": 0, "review": 0, "unknown": 0})
        counts[r.book][st] += 1
    return "\n".join(f"  {b}: {c}" for b, c in counts.items())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--merge", nargs="?", const=REVIEW_PATH, type=Path,
                    help="merge decided_speaker values from the review CSV")
    args = ap.parse_args(argv)
    if not args.db.exists():
        print(f"missing {args.db}", file=sys.stderr)
        return 1
    prior = existing_human(MAP_PATH)
    if args.merge:
        rows = load_rows(args.db)
        prior.update(read_decisions(args.merge, rows))
    rows, speaker_map, review = build(args.db, REF_PATH, prior)
    write_map(MAP_PATH, rows, speaker_map)
    review = {k: v for k, v in review.items()
              if speaker_map[k]["status"] == "review"}
    n = write_review(REVIEW_PATH, rows, speaker_map, review)
    print(f"wrote {MAP_PATH.relative_to(ROOT)} ({len(speaker_map)} verses)")
    print(f"wrote {REVIEW_PATH.relative_to(ROOT)} ({n} rows, "
          f"{len(review)} verses)")
    print(summarize(rows, speaker_map))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
