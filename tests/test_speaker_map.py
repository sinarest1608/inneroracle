"""Speaker map: marker normalizer (offline) and anchors against verses.db."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "verses.db"
MAP_PATH = ROOT / "app" / "resources" / "speaker_map.json"
REF_PATH = ROOT / "app" / "resources" / "speaker_changes_reference.json"

_spec = importlib.util.spec_from_file_location(
    "build_speaker_map", ROOT / "scripts" / "build_speaker_map.py")
bsm = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bsm  # dataclasses need the module registered
_spec.loader.exec_module(bsm)

needs_db = pytest.mark.skipif(not DB_PATH.exists(),
                              reason="data/verses.db not present")


# ---- normalizer (no DB) ---------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("śrī-bhagavān uvāca\nkutas tvā kaśmalam idaṁ", "Krishna"),
    ("śrī bhagavān uvāca", "Krishna"),
    ("Sri Bhagavan uvacha", "Krishna"),
    ("श्रीभगवानुवाच ।\nकुतस्त्वा", "Krishna"),
    ("arjuna uvāca\nprakṛtiṁ puruṣaṁ", "Arjuna"),
    ("अर्जुन उवाच ।", "Arjuna"),
    ("sañjaya uvāca", "Sanjaya"),
    ("सञ्जय उवाच", "Sanjaya"),
    ("संजय उवाच", "Sanjaya"),
    ("dhṛtarāṣṭra uvāca\ndharma-kṣetre", "Dhritarashtra"),
    ("Dhritarashtra uvacha", "Dhritarashtra"),
    ("janaka uvāca", "Janaka"),
    ("aṣṭāvakra uvāca", "Ashtavakra"),
    ("अष्टावक्र उवाच", "Ashtavakra"),
])
def test_normalize_marker(text, expected):
    assert bsm.normalize_marker(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "tam uvāca hṛṣīkeśaḥ\nprahasann iva bhārata",           # BG-2.10
    "sarveṣāṁ ca mahīkṣitām\nuvāca pārtha paśyaitān",       # BG-1.25
    "तमुवाच हृषीकेशः प्रहसन्निव भारत",
    "jātasya hi dhruvo mṛtyur dhruvaṁ janma mṛtasya ca",
])
def test_normalize_marker_ignores_narrative(text):
    assert bsm.normalize_marker(text) is None


def test_english_signals():
    assert bsm.translation_speaker("The Blessed Lord said: O Arjuna") == "Krishna"
    assert bsm.translation_speaker("Sañjaya said: Seeing Arjuna") == "Sanjaya"
    assert bsm.translation_speaker("Arjuna inquired: O my Lord") == "Arjuna"
    assert bsm.translation_speaker("Arjuna saw the form") is None
    assert bsm.address_speaker("Fight, O son of Kuntī.") == "Krishna"
    assert bsm.address_speaker("O Keśava, why do You urge me") == "Arjuna"
    assert bsm.address_speaker("If you seek liberation, my son", "ashtavakra_gita") == "Ashtavakra"
    assert bsm.address_speaker("my son", "bhagavad_gita") is None


def test_reference_entries_all_unverified_and_allowlisted():
    data = json.loads(REF_PATH.read_text(encoding="utf-8"))
    for e in data["entries"]:
        assert e["verified"] is False
        book = "bhagavad_gita" if e["verse_id"].startswith("BG-") else "ashtavakra_gita"
        assert e["speaker"] in bsm.ALLOWED[book]


# ---- against verses.db -----------------------------------------------------

@pytest.fixture(scope="module")
def built():
    rows, speaker_map, _ = bsm.build(DB_PATH, REF_PATH)
    return rows, speaker_map


@needs_db
@pytest.mark.parametrize("verse_id, speaker", [
    ("BG-1.1", "Dhritarashtra"),
    ("BG-1.2", "Sanjaya"),
    ("BG-2.4", "Arjuna"),
    ("BG-2.47", "Krishna"),
    ("AG-1.1", "Janaka"),
    ("AG-1.2", "Ashtavakra"),
])
def test_anchors(built, verse_id, speaker):
    _, speaker_map = built
    assert speaker_map[verse_id]["speaker"] == speaker


@needs_db
def test_committed_map_matches_anchors_and_covers_db():
    speaker_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT verse_id, book FROM verses").fetchall()
    finally:
        conn.close()
    assert {vid for vid, _ in rows} <= set(speaker_map)
    for vid, book in rows:
        entry = speaker_map[vid]
        assert entry["status"] in {"confirmed", "review", "unknown"}
        assert entry["speaker"] is None or entry["speaker"] in bsm.ALLOWED[book]
        if entry["status"] == "unknown":
            assert entry["speaker"] is None
    assert speaker_map["BG-2.47"]["speaker"] == "Krishna"
    assert speaker_map["BG-1.1"]["speaker"] == "Dhritarashtra"


@needs_db
def test_reading_order_is_numeric(built):
    rows, _ = built
    ids = [r.verse_id for r in rows]
    assert ids.index("BG-2.9") < ids.index("BG-2.10") < ids.index("BG-10.1")
    assert ids.index("BG-1.37-38") < ids.index("BG-1.39")


@needs_db
def test_merge_human_decisions(built, tmp_path):
    rows, _ = built
    csv_path = tmp_path / "review.csv"
    csv_path.write_text(
        "verse_id,last_verse_id,decided_speaker\n"
        "BG-4.4,BG-4.4,Arjuna\n"
        "AG-2.1,AG-2.3,janaka\n"
        "AG-6.1,,unknown\n",
        encoding="utf-8",
    )
    decisions = bsm.read_decisions(csv_path, rows)
    assert decisions == {"BG-4.4": "Arjuna", "AG-2.1": "Janaka",
                         "AG-2.2": "Janaka", "AG-2.3": "Janaka",
                         "AG-6.1": None}
    _, merged, _ = bsm.build(DB_PATH, REF_PATH, decisions)
    assert merged["BG-4.4"] == {"speaker": "Arjuna", "status": "confirmed",
                                "sources": ["human"]}
    assert merged["AG-2.2"]["status"] == "confirmed"
    assert merged["AG-6.1"] == {"speaker": None, "status": "unknown",
                                "sources": ["human"]}


@needs_db
def test_merge_rejects_wrong_book_name(built, tmp_path):
    rows, _ = built
    csv_path = tmp_path / "review.csv"
    csv_path.write_text("verse_id,last_verse_id,decided_speaker\n"
                        "AG-1.1,AG-1.1,Krishna\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        bsm.read_decisions(csv_path, rows)
