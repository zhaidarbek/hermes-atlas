"""The Journal: backfill and the tick write each day, a rebuild reads only what its file does not
cite yet, and nothing of a removed Session stays in it. Driven on the fixture world's Monday."""
import logging
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from atlas.app import Atlas
from atlas.librarian import HostLlm, Librarian
from atlas.model import (LOCAL, Area, EvidenceId, HarnessId, Membership, Note, NoteOrigin, SessionEvidence, Turn,
                         evidence_id)
from atlas.record import Record
from harness import scenes, seeds, world
from harness.fake_llm import FakeLlm
from harness.home import AtlasHome
from harness.model import filing_model, world_model

MONDAY = seeds.MONDAY
TUESDAY_TICK = scenes.AFTER_MONDAY


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene", filing_model()) as home:
        seeds.after_scene_1(home.record_root)
        home.set_clock(TUESDAY_TICK)
        home.cli("atlas", "tick", "--quiet")
        seeds.monday_filed(home.record_root)
        yield home


def READ_ONLY(call: dict) -> dict:
    """Files each Session under the Knee rehab plan as one that only read about it, so it adds no
    Active Time."""
    return {"intent": "Ask one thing.", "outcome": "Answered.", "pursuit": "p-8h1n", "new_pursuit": None,
            "one_off_area": None, "rule": None, "advances": False, "left_off": [], "step_done": None,
            "next_step": None, "kind": None}


def snap(home):
    return Record.open(home.record_root).snapshot()


def journal_calls(home) -> list[dict]:
    return [c for c in home.ctx.fake_llm.calls if c["task"] == "atlas_journal"]


def minutes(t: timedelta) -> int:
    return int(t.total_seconds() // 60)


def tick(home) -> None:
    ran = home.cli("atlas", "tick", "--quiet")
    assert (ran.code, ran.out) == (0, "")


def test_the_tick_writes_the_day_that_closed_and_not_today(home):
    tick(home)
    assert snap(home).journal_days() == [MONDAY]
    day = snap(home).journal(MONDAY)
    assert minutes(day.time.total) == 235
    assert {a: minutes(t) for a, t in day.time.by_area.items()} == {"work": 195, "health": 40}


def test_a_second_tick_with_nothing_new_asks_and_writes_nothing(home):
    tick(home)
    tick(home)
    asked, before = len(home.ctx.fake_llm.calls), home.files()
    home.set_clock(TUESDAY_TICK + timedelta(minutes=10))
    tick(home)
    assert len(home.ctx.fake_llm.calls) == asked
    assert {p for p in before.keys() | home.files().keys() if p.startswith("atlas/journal/")
            and before.get(p) != home.files().get(p)} == set()


def late_web_session(home) -> SessionEvidence:
    """A second ChatGPT Session on Monday that arrives after the day was written, filed under
    the Knee rehab plan."""
    at = datetime(2026, 11, 16, 20, 30, tzinfo=world.TZ)
    s = SessionEvidence(evidence_id(HarnessId.CHATGPT, LOCAL, "late-knee"), HarnessId.CHATGPT, None, LOCAL,
                        "late-knee", "knee week 3", at, at + timedelta(minutes=12), "ubuntu-pc", "~", None, (),
                        (Turn("owner", at, "what about the third week of physio", "main"),
                         Turn("agent", at + timedelta(minutes=12), "Add single-leg squats in week 3.", "main")))
    record = Record.open(home.record_root)
    record.commit(record.snapshot().changes().put_session(s, {}))
    current = record.snapshot()
    knee = current.pursuit("p-8h1n")
    record.commit(current.changes().put_pursuit(replace(
        knee, sessions=(*knee.sessions, Membership(s.id, s.last_at, True)))))
    return s


def test_rebuilding_a_day_sends_only_the_evidence_its_file_does_not_cite_yet(home):
    tick(home)
    before = snap(home).journal(MONDAY)
    late = late_web_session(home)
    home.ctx.fake_llm = FakeLlm({"atlas_journal": [{"worked_on": [], "finished": [], "decided": [
        {"pursuit": "Knee rehab plan", "text": "add squats in week 3.", "quote": None}]}]})
    home.set_clock(TUESDAY_TICK + timedelta(minutes=10))
    tick(home)
    (call,) = journal_calls(home)
    assert late.id in call["input"] and not any(id in call["input"] for id in before.evidence)
    after = snap(home).journal(MONDAY)
    assert (after.worked_on, after.decided[:-1]) == (before.worked_on, before.decided)
    assert after.decided[-1].text == "Knee rehab plan: add squats in week 3."
    assert late.id in after.evidence and minutes(after.time.by_area["health"]) == 52


def test_atlas_conversations_never_add_active_time(home):
    tick(home)
    before = snap(home).journal(MONDAY)
    record = Record.open(home.record_root)
    at = datetime(2026, 11, 16, 15, 0, tzinfo=world.TZ)
    cs = record.snapshot().changes()
    for n in range(6):
        cs.append_note(Note(EvidenceId(f"nt-atlas{n}"), at + timedelta(minutes=5 * n), NoteOrigin.OWNER, "telegram",
                            "what did I do on the billing export?", atlas_reply="You compared the totals."))
    record.commit(cs)
    s = record.snapshot().session(seeds.evidence_id(world.by_title("invoice ids in payments")))
    record.commit(record.snapshot().changes().put_session(replace(s, parts=(), turns=tuple(
        Turn("owner", at + timedelta(minutes=5 * n), "atlas, note this", "main", atlas_tool_turn=True)
        for n in range(6))), {}))
    home.set_clock(TUESDAY_TICK + timedelta(minutes=10))
    tick(home)
    after = snap(home).journal(MONDAY)
    assert after.time == before.time
    assert not [id for id in after.evidence if id.startswith("nt-")]


def test_with_no_model_the_tick_logs_the_kind_of_failure_writes_no_day_and_the_next_tick_does(home, caplog):
    home.ctx.fake_llm = FakeLlm()
    with caplog.at_level(logging.WARNING, logger="atlas"):
        tick(home)
    assert snap(home).journal_days() == []
    assert "UnscriptedCall" in caplog.text and "test_retry" not in caplog.text and "Payments" not in caplog.text
    home.ctx.fake_llm = world_model()
    home.set_clock(TUESDAY_TICK + timedelta(minutes=10))
    tick(home)
    assert snap(home).journal_days() == [MONDAY]


@pytest.mark.parametrize("when", ["before the model reads it", "before the day commits"])
def test_a_session_removed_while_the_day_is_written_leaves_no_word_of_it(home, monkeypatch, when):
    gone = seeds.evidence_id(world.by_title("per-invoice vat rounding"))
    record = Record.open(home.record_root)
    real_lines, real_commit = Librarian.journal_lines, Record.commit
    removed = []

    def remove():
        if not removed:
            removed.append(gone)
            real_commit(record, record.snapshot().changes().remove_evidence(gone))

    def lines_after_removal(self, snap, day, new):
        if when == "before the model reads it":
            remove()
        return real_lines(self, snap, day, new)

    def commit_after_removal(self, cs):
        if when == "before the day commits" and any(op.path.startswith("journal/") for op in cs._ops):
            remove()
        return real_commit(self, cs)

    monkeypatch.setattr(Librarian, "journal_lines", lines_after_removal)
    monkeypatch.setattr(Record, "commit", commit_after_removal)
    tick(home)
    day = snap(home).journal(MONDAY)
    assert removed and gone not in day.evidence
    assert not any("Billing export service:" in s.text for s in day.lines)
    text = (home.record_root / "journal" / "2026-11-16.md").read_text()
    assert gone not in text and "per-invoice" not in text
    assert not any(gone in p.read_text() for p in (home.record_root / ".atlas" / "cache").glob("*.json"))


def test_backfill_with_no_model_writes_no_journal_and_prints_no_journal_line(tmp_path, caplog):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(seeds.BACKFILLED)
        seeds.after_scene_1(home.record_root)
        report = Atlas.open(home.record_root, None).backfill(seeds.BACKFILLED)
        assert report.journal is None and "Journal" not in report.render()
        assert snap(home).journal_days() == []


def test_a_journal_day_atlas_cannot_read_is_logged_and_never_written_over(home, caplog):
    path = home.record_root / "journal" / "2026-11-16.md"
    path.write_text("---\natlas: journal/1\n---\nnot a day\n")
    with caplog.at_level(logging.WARNING, logger="atlas"):
        tick(home)
    assert path.read_text() == "---\natlas: journal/1\n---\nnot a day\n"
    assert "could not read it (" in caplog.text


def sunday_session(home, length: timedelta) -> SessionEvidence:
    """A ChatGPT Session on Sunday at 10:00, `length` long, filed under the Knee rehab plan."""
    sunday = datetime(2026, 11, 15, 10, 0, tzinfo=world.TZ)
    s = SessionEvidence(evidence_id(HarnessId.CHATGPT, LOCAL, "sunday-knee"), HarnessId.CHATGPT, None, LOCAL,
                        "sunday-knee", "knee check", sunday, sunday + length, "ubuntu-pc", "~", None, (),
                        (Turn("owner", sunday, "is week 1 enough", "main"),
                         Turn("agent", sunday + length, "Yes.", "main")))
    record = Record.open(home.record_root)
    record.commit(record.snapshot().changes().put_session(s, {}))
    current = record.snapshot()
    knee = current.pursuit("p-8h1n")
    record.commit(current.changes().put_pursuit(replace(
        knee, sessions=(*knee.sessions, Membership(s.id, s.last_at, True)))))
    return s


def test_a_day_under_half_a_minute_is_not_written_and_never_stops_the_other_days(home, caplog):
    """A day whose only Active Time rounds to 0 minutes has no Journal file, the model is not
    asked about it, and the tick and backfill still write Monday."""
    s = sunday_session(home, timedelta(seconds=20))
    home.ctx.fake_llm = FakeLlm({
        "atlas_backfill": lambda call: {"intent": "Check the knee plan.", "outcome": "Answered.", "unfinished": None},
        "atlas_journal": lambda call: {"worked_on": [], "decided": [], "finished": []},
        "atlas_file": READ_ONLY})
    with caplog.at_level(logging.WARNING, logger="atlas"):
        tick(home)
        report = Atlas.open(home.record_root, HostLlm(home.ctx)).backfill(TUESDAY_TICK)
    assert caplog.text == ""
    assert snap(home).journal_days() == [MONDAY]
    assert minutes(snap(home).journal(MONDAY).time.total) == 235
    assert not any(s.id in c["input"] for c in journal_calls(home))
    assert report.journal is not None and "Journal" in report.render()


def test_a_day_that_cannot_be_written_is_logged_and_the_days_after_it_are_written(home, monkeypatch, caplog):
    sunday_session(home, timedelta(minutes=10))
    home.ctx.fake_llm = FakeLlm({"atlas_journal": lambda call: {"worked_on": [], "decided": [], "finished": []}})
    real_commit = Record.commit
    failed = []

    def commit(self, cs):
        if not failed and any(op.path.startswith("journal/") for op in cs._ops):
            failed.append(next(op.path for op in cs._ops if op.path.startswith("journal/")))
            raise ValueError("invented")
        return real_commit(self, cs)

    monkeypatch.setattr(Record, "commit", commit)
    with caplog.at_level(logging.WARNING, logger="atlas"):
        tick(home)
    assert failed == ["journal/2026-11-15.md"]
    assert snap(home).journal_days() == [MONDAY]
    assert "Atlas left the Journal of 2026-11-15 to the next run (ValueError)" in caplog.text


SECRET = "ghp_" + "Inv3ntedT0kenAbCdEfGhIjKlMnOpQrStUvWx"


def test_a_secret_in_a_pursuit_or_area_name_is_masked_in_the_journal_and_what_backfill_prints(home):
    record = Record.open(home.record_root)
    current = record.snapshot()
    knee = current.pursuit("p-8h1n")
    record.commit(current.changes().put_pursuit(replace(knee, name=f"Knee {SECRET}", area=Area(f"health {SECRET}"))))
    home.set_clock(TUESDAY_TICK)
    ran = home.cli("atlas", "backfill")
    assert ran.code == 0 and "Journal" in ran.out and SECRET not in ran.out
    text = (home.record_root / "journal" / "2026-11-16.md").read_text()
    assert SECRET not in text and "[secret: github token]" in text
