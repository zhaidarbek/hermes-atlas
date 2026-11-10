"""The Board: rules.marks and rules.board (pure), SetFocus and SetStatus from the Board
(rules.from_board), the seen mark in the record, and the dashboard routes."""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas import board_api, rules
from atlas.model import (Actor, Cite, Fact, FocusProposal, HistoryLine, Inference, MarkSeen, Owner, SetFocus,
                         SetStatus, Status)
from atlas.record import Record
from harness import seeds
from snapshots import after, at, pursuit, snapshot

NOW = at(date(2026, 11, 18), "21:00")
CLICK = Owner("board", "nt-0click00")
CREATED = HistoryLine("h-0001", at(date(2026, 11, 15), "20:10"), Actor.ATLAS, Inference("Created it.", ("cc-1",)))


def line(n: int, actor: Actor) -> HistoryLine:
    statement = Fact("Parked it.", Cite("nt-1", "your Note", NOW)) if actor is Actor.OWNER else Inference("Filed.", ("cc-1",))
    return HistoryLine(f"h-{n:04d}", NOW, actor, statement, agent="Codex" if actor is Actor.AGENT else None)


@pytest.mark.parametrize("history, seen, marks", [
    ((CREATED,), None, (True, False)),
    ((CREATED,), "h-0001", (False, False)),
    ((CREATED, line(2, Actor.OWNER)), "h-0001", (False, False)),
    ((CREATED, line(2, Actor.ATLAS)), "h-0001", (False, True)),
    ((CREATED, line(2, Actor.AGENT)), None, (True, True)),
    ((CREATED, line(2, Actor.ATLAS), line(3, Actor.OWNER)), "h-0002", (False, False)),
    ((line(1, Actor.OWNER),), None, (False, False)),
])
def test_only_atlas_and_agent_lines_after_the_seen_mark_raise_a_mark(history, seen, marks):
    assert rules.marks(history, seen) == marks


def test_the_board_holds_a_column_per_status_with_focus_first():
    snap = snapshot([pursuit("p-1", "Quiet", worked=[date(2026, 11, 2)]),
                     pursuit("p-2", "Recent", worked=[date(2026, 11, 14)]),
                     pursuit("p-3", "Starred", worked=[date(2026, 11, 1)], focus=True),
                     pursuit("p-4", "Shelved", worked=[date(2026, 8, 30)], status=Status.PARKED)])
    view = rules.board(snap).data
    assert [c.name for c in view.columns[Status.ACTIVE]] == ["Starred", "Recent", "Quiet"]
    assert [c.name for c in view.columns[Status.PARKED]] == ["Shelved"]
    assert view.columns[Status.DONE] == view.columns[Status.DROPPED] == ()
    assert view.in_focus == 1
    card = view.columns[Status.ACTIVE][0]
    assert (card.new, card.changed, card.upto) == (True, False, "h-0002")
    assert card.resume_prompt.startswith('Resuming "Starred".')


def test_a_star_puts_an_active_pursuit_in_focus_with_a_history_line_and_a_note():
    snap = snapshot([pursuit("p-1", "Billing", worked=[date(2026, 11, 13)])])
    d = rules.from_board(snap, SetFocus("p-1", True), CLICK, NOW)
    p = after(d).pursuit("p-1")
    assert p.focus and p.history[-1].actor is Actor.OWNER
    assert p.history[-1].statement.text == f"{rules.FOCUS_ADDED} on the Board."
    (note,) = after(d).notes()
    assert (note.id, note.via, note.about, note.text) == ("nt-0click00", "board", "p-1", "Added Billing to Focus on the Board.")


@pytest.mark.parametrize("status", [Status.PARKED, Status.DONE, Status.DROPPED])
def test_only_an_active_pursuit_takes_a_star(status):
    snap = snapshot([pursuit("p-1", "Shelved", worked=[date(2026, 8, 30)], status=status)])
    d = rules.from_board(snap, SetFocus("p-1", True), CLICK, NOW)
    assert (d.outcome.ok, d.outcome.refused, d.changes.is_empty()) == (False, "NOT_ACTIVE", True)


def test_a_sixth_star_is_kept_past_the_soft_cap():
    five = [pursuit(f"p-{n}", f"Starred {n}", worked=[date(2026, 11, 10)], focus=True) for n in range(5)]
    snap = snapshot([*five, pursuit("p-9", "Sixth", worked=[date(2026, 11, 12)])])
    view = rules.board(after(rules.from_board(snap, SetFocus("p-9", True), CLICK, NOW))).data
    assert view.in_focus == 6 > rules.FOCUS_SOFT_CAP


@pytest.mark.parametrize("status", [Status.PARKED, Status.DONE, Status.DROPPED])
def test_a_pursuit_parked_or_ended_leaves_focus(status):
    snap = snapshot([pursuit("p-1", "Staging deploy", worked=[date(2026, 11, 15)], focus=True)])
    d = rules.from_board(snap, SetStatus("p-1", status), CLICK, NOW)
    p = after(d).pursuit("p-1")
    assert (p.status, p.focus) == (status, False)
    assert after(d).notes()[0].text == f"Moved Staging deploy to {status.value.title()} on the Board."


def test_a_star_answers_a_focus_proposal():
    proposal = FocusProposal(True, Inference("Worked on all month.", ("gpt-1",)), date(2026, 11, 18))
    knee = replace(pursuit("p-8", "Knee rehab plan", worked=[date(2026, 11, 16)]), proposals=(proposal,))
    assert rules.board(snapshot([knee])).data.columns[Status.ACTIVE][0].proposals == (proposal,)
    p = after(rules.from_board(snapshot([knee]), SetFocus("p-8", True), CLICK, NOW)).pursuit("p-8")
    assert p.focus and p.proposals == ()


def test_a_seen_mark_clears_the_new_mark_and_writes_no_history_or_note():
    snap = snapshot([pursuit("p-1", "Billing", worked=[date(2026, 11, 13)])])
    assert rules.board(snap).data.columns[Status.ACTIVE][0].new
    d = rules.from_board(snap, MarkSeen({"p-1": "h-0001"}), CLICK, NOW)
    seen = after(d)
    assert seen.seen() == {"p-1": "h-0001"} and seen.notes() == []
    assert seen.pursuit("p-1") == snap.pursuit("p-1")
    assert not rules.board(seen).data.columns[Status.ACTIVE][0].new


def test_seen_marks_are_kept_by_the_record_and_a_broken_file_reads_as_none(tmp_path):
    record = Record.open(tmp_path / "atlas")
    seeds.after_scene_6(record.root)
    record.commit(record.snapshot().changes().mark_seen({"p-4k7q": "h-0002"}))
    record.commit(record.snapshot().changes().mark_seen({"p-2x7b": "h-0001"}))
    assert record.snapshot().seen() == {"p-2x7b": "h-0001", "p-4k7q": "h-0002"}
    (record.root / ".atlas" / "seen.json").write_text("{not json")
    assert record.snapshot().seen() == {}


def test_a_seen_mark_never_moves_back_even_from_a_stale_snapshot(tmp_path):
    record = Record.open(tmp_path / "atlas")
    seeds.after_scene_6(record.root)
    stale = record.snapshot()
    record.commit(record.snapshot().changes().mark_seen({"p-4k7q": "h-0002"}))
    record.commit(stale.changes().mark_seen({"p-4k7q": "h-0001", "p-2x7b": "h-0001"}))
    assert record.snapshot().seen() == {"p-2x7b": "h-0001", "p-4k7q": "h-0002"}


@pytest.mark.parametrize("bad", [7, "garbage", "h-x", None, ["h-0001"]])
def test_a_value_in_seen_json_that_is_not_a_history_id_reads_as_no_mark_for_that_pursuit(tmp_path, bad):
    record = Record.open(tmp_path / "atlas")
    seeds.after_scene_6(record.root)
    (record.root / ".atlas" / "seen.json").write_text(json.dumps({"p-4k7q": bad, "p-2x7b": "h-0001"}))
    assert record.snapshot().seen() == {"p-2x7b": "h-0001"}


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    root = tmp_path / "atlas"
    seeds.after_scene_6(root)
    monkeypatch.setenv("ATLAS_RECORD", str(root))
    monkeypatch.setenv("ATLAS_NOW", NOW.isoformat())
    app = FastAPI()
    app.include_router(board_api.router, prefix="/api/plugins/atlas")
    return TestClient(app)


def test_a_route_refuses_a_star_on_a_parked_pursuit_and_an_unknown_one(dashboard):
    assert dashboard.patch("/api/plugins/atlas/pursuits/p-1k2f", json={"focus": True}).status_code == 409
    assert dashboard.patch("/api/plugins/atlas/pursuits/p-none", json={"focus": True}).status_code == 404
    assert dashboard.patch("/api/plugins/atlas/pursuits/p-4k7q", json={"focus": True, "status": "parked"}).status_code == 422


@pytest.mark.parametrize("bad", [7, "garbage", "h-x"])
def test_the_board_reads_a_bad_seen_value_as_no_mark(dashboard, tmp_path, bad):
    (tmp_path / "atlas" / ".atlas" / "seen.json").write_text(json.dumps({"p-4k7q": bad}))
    board = dashboard.get("/api/plugins/atlas/board")
    assert board.status_code == 200
    assert all(c["new"] for cards in board.json()["columns"].values() for c in cards
               if c["name"] != "Billing export: staging deploy")


def test_a_seen_post_refuses_an_upto_that_is_not_a_history_id_and_an_unknown_pursuit(dashboard):
    assert dashboard.post("/api/plugins/atlas/seen", json={"marks": {"p-4k7q": "garbage"}}).status_code == 422
    assert dashboard.post("/api/plugins/atlas/seen", json={"marks": {"p-4k7q": "h-0099"}}).status_code == 422
    assert dashboard.post("/api/plugins/atlas/seen", json={"marks": {"p-none": "h-0001"}}).status_code == 404


def test_concurrent_seen_posts_for_every_card_all_land(dashboard):
    cards = [c for cards in dashboard.get("/api/plugins/atlas/board").json()["columns"].values() for c in cards]
    for _ in range(3):
        with ThreadPoolExecutor(len(cards)) as pool:
            codes = list(pool.map(lambda c: dashboard.post("/api/plugins/atlas/seen",
                                                           json={"marks": {c["id"]: c["upto"]}}).status_code, cards))
        assert codes == [200] * len(cards)
    board = dashboard.get("/api/plugins/atlas/board").json()
    assert not any(c["new"] or c["changed"] for cards in board["columns"].values() for c in cards)


def test_the_record_folder_is_read_from_the_plugin_setting_in_hermes_config(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    (hermes / "config.yaml").write_text(f"plugins:\n  entries:\n    atlas:\n      settings:\n"
                                        f"        record_dir: {tmp_path / 'elsewhere'}\n")
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    assert board_api._record_dir() == tmp_path / "elsewhere"
    (hermes / "config.yaml").write_text("plugins: {enabled: [atlas]}\n")
    assert board_api._record_dir() is None
