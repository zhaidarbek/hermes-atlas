from dataclasses import replace
from datetime import date, datetime

import pytest

from atlas.model import (Actor, BriefStep, Cite, Fact, HistoryLine, Inference, Kind, Membership, Note, NoteOrigin, Pursuit,
                         ResumeNote, SendAttempt, SendStatus, SentBrief, Status, Step)
from atlas.record import Ambiguous, Record
from atlas.record._codec import Unrepresentable
from test_commit import BERLIN, PROFILE, SETTINGS


@pytest.fixture
def record(tmp_path) -> Record:
    rec = Record.open(tmp_path / "atlas")
    rec.create(SETTINGS, PROFILE)
    return rec


def pursuit(id: str, name: str) -> Pursuit:
    told = Fact("Named it.", Cite("nt-1", "your Note", datetime(2026, 11, 15, 20, 41, tzinfo=BERLIN)))
    return Pursuit(
        id=id, name=name, kind=Kind.PROJECT, area="home", status=Status.ACTIVE, focus=True,
        created=date(2026, 11, 15), continued=None,
        resume=ResumeNote(where=(), left_off=(told,), decided=(), open_questions=(), reopen=()),
        next_step=Step("s-20261115-1", Inference("Read the last 20 log lines.", ("nt-1",)), date(2026, 11, 15)),
        past_steps=(), filing_rules=(), sessions=(), proposals=(),
        history=(HistoryLine("h-0001", datetime(2026, 11, 15, 20, 41, tzinfo=BERLIN), Actor.OWNER, told),))


def test_a_put_pursuit_reads_back_and_renaming_moves_its_file(record):
    p = pursuit("p-6r2t", "Greenhouse logger")
    record.commit(record.snapshot().changes().put_pursuit(p))
    assert (record.root / "pursuits/greenhouse-logger.md").exists()
    assert record.snapshot().pursuit("greenhouse") == p

    record.commit(record.snapshot().changes().put_pursuit(replace(p, name="Greenhouse logger firmware")))
    assert not (record.root / "pursuits/greenhouse-logger.md").exists()
    assert not (record.root / ".atlas/last-written/pursuits/greenhouse-logger.md").exists()
    assert record.snapshot().pursuit("p-6r2t").name == "Greenhouse logger firmware"


def test_a_fuzzy_name_matching_two_pursuits_is_ambiguous(record):
    cs = record.snapshot().changes().put_pursuit(pursuit("p-1", "Greenhouse logger")).put_pursuit(pursuit("p-2", "Greenhouse dashboard"))
    record.commit(cs)
    with pytest.raises(Ambiguous) as e:
        record.snapshot().pursuit("greenhouse")
    assert {p.id for p in e.value.candidates} == {"p-1", "p-2"}


def test_notes_append_per_day_and_origin_and_resolve_what_they_are_about(record):
    record.commit(record.snapshot().changes().put_pursuit(pursuit("p-6r2t", "Greenhouse logger")))
    first = Note("nt-0p4m7c2a", datetime(2026, 11, 15, 20, 39, tzinfo=BERLIN), NoteOrigin.OWNER, "telegram", "merge them",
                 atlas_reply="Merged.")
    second = Note("nt-7v1x0b9e", datetime(2026, 11, 15, 21, 20, tzinfo=BERLIN), NoteOrigin.AGENT, "claude_code",
                  "Log every 5 minutes.", about="p-6r2t", cwd="~/home/greenhouse")
    third = Note("nt-3k8d1p0x", datetime(2026, 11, 15, 20, 41, tzinfo=BERLIN), NoteOrigin.OWNER, "telegram", "split it")
    record.commit(record.snapshot().changes().append_note(first).append_note(second))
    record.commit(record.snapshot().changes().append_note(third).append_note(first))

    assert record.snapshot().notes() == [first, third, second]
    assert sorted(p.name for p in (record.root / "notes/2026/11").iterdir()) == [
        "2026-11-15-claude-code.md", "2026-11-15-telegram.md"]


def test_two_pursuits_with_one_slug_in_one_commit_both_keep_a_file(record):
    record.commit(record.snapshot().changes()
                  .put_pursuit(pursuit("p-1aaa", "Foo Bar")).put_pursuit(pursuit("p-2bbb", "Foo-Bar")))
    snap = record.snapshot()
    assert {p.id for p in snap.pursuits()} == {"p-1aaa", "p-2bbb"}


def test_putting_a_pursuit_twice_in_one_commit_keeps_one_file(record):
    p = pursuit("p-1aaa", "Foo Bar")
    record.commit(record.snapshot().changes().put_pursuit(p).put_pursuit(p))
    assert sorted(f.name for f in (record.root / "pursuits").iterdir()) == ["foo-bar.md"]


def test_filing_a_session_no_pursuit_lists_yet_asks_the_session_evidence(record, monkeypatch):
    asked = []

    def session(self, id):
        asked.append(id)
        raise LookupError(id)

    monkeypatch.setattr(type(record.snapshot()), "session", session)
    p = replace(pursuit("p-1aaa", "Foo Bar"),
                sessions=(Membership("cc-2", datetime(2026, 11, 15, 9, 0, tzinfo=BERLIN), True),))
    with pytest.raises(LookupError):
        record.snapshot().changes().put_pursuit(p)
    assert asked == ["cc-2"]


def test_a_new_timezone_is_refused_until_body_times_can_move_with_it(record):
    with pytest.raises(Unrepresentable):
        record.snapshot().changes().put_settings(replace(SETTINGS, timezone="Asia/Tokyo"))
    record.snapshot().changes().put_settings(SETTINGS)


def test_renaming_a_pursuit_keeps_the_notes_about_it(record):
    p = pursuit("p-6r2t", "Greenhouse logger firmware")
    note = Note("nt-7v1x0b9e", datetime(2026, 11, 15, 21, 20, tzinfo=BERLIN), NoteOrigin.AGENT, "claude_code",
                "Log every 5 minutes.", about="p-6r2t", cwd="~/home/greenhouse")
    record.commit(record.snapshot().changes().put_pursuit(p))
    record.commit(record.snapshot().changes().append_note(note))
    record.commit(record.snapshot().changes().put_pursuit(replace(p, name="Greenhouse logger")))
    assert [n.about for n in record.snapshot().notes()] == ["p-6r2t"]
    assert "about: Greenhouse logger ·" in (record.root / "notes/2026/11/2026-11-15-claude-code.md").read_text()


def test_a_pursuit_merged_away_leaves_its_notes_and_briefs_to_the_one_it_joined(record):
    kept, gone = pursuit("p-6r2t", "Greenhouse logger"), pursuit("p-6r2u", "Greenhouse dashboard")
    note = Note("nt-7v1x0b9e", datetime(2026, 11, 15, 21, 20, tzinfo=BERLIN), NoteOrigin.AGENT, "claude_code",
                "Chart the temperature too.", about="p-6r2u", cwd="~/home/greenhouse")
    sent = SendAttempt(datetime(2026, 11, 16, 7, 30, tzinfo=BERLIN), SendStatus.SENT, ("8812",))
    brief = SentBrief(date(2026, 11, 16), (sent,), None, (), (BriefStep(1, "p-6r2u", "s-20261115-1"),), None, False,
                      "Atlas · Daily Brief\n")
    record.commit(record.snapshot().changes().put_pursuit(kept).put_pursuit(gone))
    record.commit(record.snapshot().changes().append_note(note).put_brief(brief))
    record.commit(record.snapshot().changes().remove_pursuit("p-6r2u", "p-6r2t"))
    snap = record.snapshot()
    assert [p.id for p in snap.pursuits()] == ["p-6r2t"]
    assert not (record.root / "pursuits/greenhouse-dashboard.md").exists()
    assert [n.about for n in snap.notes()] == ["p-6r2t"]
    assert snap.brief(date(2026, 11, 16)).steps == (BriefStep(1, "p-6r2t", "s-20261115-1"),)
    assert snap.brief(date(2026, 11, 16)).text == brief.text
