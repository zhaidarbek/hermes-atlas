"""Atlas.backfill and the tick's collect phase against the fixture world's stores."""
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import time, timedelta

import pytest

from atlas import app
from atlas.app import Atlas, BackfillReport
from atlas.model import Forgotten, HarnessId, SendAttempt, SendStatus, SentBrief, skip_key
from atlas.record import Record
from harness import seeds, world
from harness.home import AtlasHome

NOW = seeds.BACKFILLED


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(NOW)
        seeds.after_scene_1(home.record_root)
        yield home


def record_files(home) -> dict:
    return {k: v for k, v in home.files().items() if k.startswith("atlas/") and "/.atlas/" not in f"/{k}"}


def held(home) -> set:
    return {s.id for s in Record.open(home.record_root).snapshot().sessions()}


def owner_ids(now) -> set:
    return {seeds.evidence_id(s) for s in world.visible(now) if s.is_owner_session}


def test_backfill_reports_what_it_read():
    report = BackfillReport(977, 186, {"T3 Code": {HarnessId.CODEX: 120, HarnessId.CLAUDE_CODE: 181}})
    assert report.render() == ("Read 977 Sessions. 186 subagent conversations were folded into the Sessions that "
                               "started them.\n301 Sessions came through T3 Code. Each counts once, under Claude Code "
                               "or Codex.")
    assert BackfillReport(1, 1, {"T3 Code": {HarnessId.DEVIN: 1}}).render() == (
        "Read 1 Session. 1 subagent conversation was folded into the Session that started it.\n"
        "1 Session came through T3 Code. It counts once, under Devin.")


def test_a_backfill_interrupted_mid_batch_resumes(home, monkeypatch):
    monkeypatch.setattr(app, "BATCH", 5)
    commits = []
    real = Record.commit

    def crash_on_the_third(record, cs):
        commits.append(cs)
        if len(commits) == 3:
            raise KeyboardInterrupt
        return real(record, cs)

    monkeypatch.setattr(Record, "commit", crash_on_the_third)
    with pytest.raises(KeyboardInterrupt):
        Atlas.open(home.record_root).backfill(NOW)
    assert len(held(home)) == 10
    monkeypatch.setattr(Record, "commit", real)
    report = Atlas.open(home.record_root).backfill(NOW)
    assert held(home) == owner_ids(NOW) and report.sessions == len(owner_ids(NOW))


def test_re_running_backfill_changes_nothing(home):
    Atlas.open(home.record_root).backfill(NOW)
    before = record_files(home)
    Atlas.open(home.record_root).backfill(NOW)
    assert record_files(home) == before


def test_a_forgotten_session_is_not_collected_again(home):
    forgotten = world.by_title("pi-hole dns loop")
    home.seed(lambda snap: snap.changes().put_forgotten(
        Forgotten(((NOW, "a Session"),), frozenset({skip_key(forgotten.harness, forgotten.native_key)}))))
    Atlas.open(home.record_root).backfill(NOW)
    assert held(home) == owner_ids(NOW) - {seeds.evidence_id(forgotten)}


def test_hermes_rows_matching_a_sent_brief_are_dropped(home):
    brief = SentBrief(NOW.date(), (SendAttempt(NOW, SendStatus.SENT, ("41",)),), None, (), (), None, False,
                      "Atlas · Daily Brief\n\n1. Payments CI flakes")
    home.seed(lambda snap: snap.changes().put_brief(brief))
    shelf = world.by_title("shelf bracket sizes")
    with closing(sqlite3.connect(home.home / ".hermes/state.db")) as con:
        con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                    (shelf.native, brief.text, NOW.timestamp()))
        con.commit()
    Atlas.open(home.record_root).backfill(NOW)
    turns = Record.open(home.record_root).snapshot().session(seeds.evidence_id(shelf)).turns
    assert brief.text not in {t.text for t in turns} and len(turns) == len(shelf.turns())


def test_the_tick_collects_what_is_new_since_the_last_one(tmp_path):
    with AtlasHome(tmp_path / "late-brief") as home:
        home.set_clock(NOW)
        Record.open(home.record_root).create(replace(seeds.AFTER_SETUP, brief_at=time(23, 59)), seeds.PROFILE)
        collects_what_is_new(home)


def collects_what_is_new(home):
    atlas = Atlas.open(home.record_root)
    assert atlas.tick(NOW).collected == len(owner_ids(NOW))
    assert atlas.tick(NOW + timedelta(minutes=10)).collected == 0
    sunday = {k: v[0] for k, v in record_files(home).items() if "/raw/" in k}
    monday = world.FIRST_BRIEF_AT + timedelta(hours=1)
    home.set_clock(monday)
    atlas.tick(monday)
    assert held(home) == owner_ids(monday)
    assert {k: v[0] for k, v in record_files(home).items() if k in sunday} == sunday


def test_the_tick_skips_collecting_while_backfill_holds_the_lease(home):
    from atlas import schedule
    with schedule.TickLease(home.record_root):
        report = Atlas.open(home.record_root).tick(NOW)
    assert report.skipped_busy and report.collected == 0 and held(home) == set()


def test_a_tick_on_unreadable_stores_logs_and_still_sends(home, caplog):
    (home.home / ".hermes/state.db").write_bytes(b"not a database")
    for transcript in (home.home / ".claude/projects").glob("*/*.jsonl"):
        transcript.write_text("{not json\n")
    report = Atlas.open(home.record_root).tick(NOW)
    assert report.collected == len([s for s in world.visible(NOW) if s.is_owner_session
                                    and s.harness in (HarnessId.CODEX, HarnessId.DEVIN)])
    assert "could not be read" in caplog.text


def test_a_tick_that_cannot_collect_at_all_does_not_raise(home, monkeypatch, caplog):
    def broken(*args):
        raise RuntimeError("store format unknown")

    monkeypatch.setattr(app.sources, "collect", broken)
    report = Atlas.open(home.record_root).tick(NOW)
    assert report.collected == 0 and report.sent == ("brief 2026-11-15",)
    assert "skipped collecting" in caplog.text


def test_a_tick_before_any_store_exists_collects_nothing(tmp_path):
    with AtlasHome(tmp_path / "empty") as home:
        home.now = NOW
        seeds.after_scene_1(home.record_root)
        assert Atlas.open(home.record_root).tick(NOW).collected == 0


def test_backfill_with_no_model_says_how_to_set_one():
    report = BackfillReport(1, 0, unanswered=True)
    assert report.render() == (
        "Read 1 Session. 0 subagent conversations were folded into the Sessions that started them.\n"
        "No model answered, so no Pursuits yet. Set auxiliary.atlas_backfill in Hermes's config and run backfill again.")
    assert "No model answered" not in BackfillReport(1, 0).render()
