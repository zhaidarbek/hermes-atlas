"""The Board's writes beside the hand-edit sweep (scenes 28, 29) and Forget (scene 30): a seen mark or a
Board click is Atlas's own write, never put back and never offered as a hand edit, and a Pursuit
Forget removes or rewrites leaves the Board readable with no Session id in seen.json."""
import json
import re
from dataclasses import replace
from datetime import timedelta

import pytest

from atlas.model import NoteOrigin
from atlas.record import Record
from harness import scenes, seeds, world
from harness.home import AtlasHome
from harness.model import world_model

ASK = "atlas, forget the Codex Session from Thu 19 Nov 14:02. I pasted a password in it"
CONFIG = seeds.evidence_id(world.by_title("staging deploy config"))


def _cards(home: AtlasHome) -> list[dict]:
    got = home.dashboard("GET", "/board")
    assert got.status_code == 200, got.text
    return [c for cards in got.json()["columns"].values() for c in cards]


def _see_all(home: AtlasHome) -> None:
    for _ in range(3):
        for c in _cards(home):
            assert home.dashboard("POST", "/seen", {"marks": {c["id"]: c["upto"]}}).status_code == 200


def _tick_puts_nothing_back(home: AtlasHome) -> None:
    root = home.record_root
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and not {".atlas", "evidence"} & set(p.relative_to(root).parts)}
    seen = (root / ".atlas" / "seen.json").read_bytes()
    home.set_clock(home.now + timedelta(minutes=3))
    ran = home.cli("atlas", "tick", "--quiet")
    assert ran.code == 0, ran.err
    snap = Record.open(root).snapshot()
    assert [n for n in snap.notes() if n.origin is NoteOrigin.HAND_EDIT] == []
    assert snap.pending_applies() == []
    assert Record.open(root).reconcile() == []
    assert (root / ".atlas" / "seen.json").read_bytes() == seen
    assert {p: p.read_bytes() for p in before} == before
    assert not (root / ".atlas" / "last-written" / ".atlas").exists()


@pytest.fixture
def backfilled(tmp_path):
    with AtlasHome(tmp_path / "scene", world_model()) as home:
        home.set_clock(seeds.BACKFILLED)
        seeds.after_scene_1(home.record_root)
        assert home.cli("atlas", "backfill").code == 0
        yield home


def test_seen_marks_on_a_backfilled_record_are_no_hand_edit(backfilled):
    _see_all(backfilled)
    assert not any(c["new"] or c["changed"] for c in _cards(backfilled))
    _tick_puts_nothing_back(backfilled)
    assert not any(c["new"] or c["changed"] for c in _cards(backfilled))


def test_a_star_and_a_park_from_the_board_are_no_hand_edit(backfilled):
    _see_all(backfilled)
    active = [c for c in backfilled.dashboard("GET", "/board").json()["columns"]["active"]]
    assert len(active) >= 2
    assert backfilled.dashboard("PATCH", f"/pursuits/{active[0]['id']}", {"focus": True}).status_code == 200
    assert backfilled.dashboard("PATCH", f"/pursuits/{active[1]['id']}", {"status": "parked"}).status_code == 200
    _tick_puts_nothing_back(backfilled)
    assert Record.open(backfilled.record_root).reconcile() == []


def _seen_holds_pursuit_and_history_ids_only(home: AtlasHome) -> dict:
    marks = json.loads((home.record_root / ".atlas" / "seen.json").read_text())
    assert all(re.fullmatch(r"p-[a-z0-9]+", k) and re.fullmatch(r"h-\d+", v) for k, v in marks.items()), marks
    assert CONFIG not in json.dumps(marks)
    return marks


@pytest.fixture
def forgettable(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        scenes._forgettable(home)
        yield home


def test_forget_rewriting_a_seen_pursuit_leaves_the_board_readable(forgettable):
    _see_all(forgettable)
    assert forgettable.telegram(ASK)
    assert forgettable.telegram("forget") == "Forgotten."
    assert seeds.STAGING in _seen_holds_pursuit_and_history_ids_only(forgettable)
    names = {c["id"] for c in _cards(forgettable)}
    assert seeds.STAGING in names


def test_forget_removing_a_seen_pursuit_leaves_the_board_readable(forgettable):
    record = Record.open(forgettable.record_root)
    snap = record.snapshot()
    staging = snap.pursuit(seeds.STAGING)
    record.commit(snap.changes().put_pursuit(replace(
        staging, sessions=tuple(m for m in staging.sessions if m.evidence == CONFIG),
        resume=replace(staging.resume, reopen=(CONFIG,)))))
    _see_all(forgettable)
    assert forgettable.telegram(ASK)
    assert forgettable.telegram("forget") == "Forgotten."
    _seen_holds_pursuit_and_history_ids_only(forgettable)
    assert seeds.STAGING not in {c["id"] for c in _cards(forgettable)}
    gone = forgettable.dashboard("POST", "/seen", {"marks": {seeds.STAGING: "h-0001"}})
    assert gone.status_code == 404
    assert forgettable.dashboard("GET", "/board").status_code == 200
