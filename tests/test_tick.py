"""Atlas.tick's send phase against a seeded record and the harness's fake `hermes`."""
import json
import logging
import subprocess
import sys
from dataclasses import replace
from datetime import date, timedelta

import pytest

from atlas import rules, schedule
from atlas.app import Atlas
from atlas.model import Note, NoteOrigin, SendStatus
from atlas.record import Conflict, Record
from harness import seeds, world
from harness.home import AtlasHome

MONDAY = world.FIRST_BRIEF_AT.date()


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(world.FIRST_BRIEF_AT)
        seeds.after_scene_6(home.record_root)
        yield home


def tick(home, minutes_later: int = 0):
    return Atlas.open(home.record_root).tick(world.FIRST_BRIEF_AT + timedelta(minutes=minutes_later))


def attempts(home) -> list[SendStatus]:
    return [a.status for a in Record.open(home.record_root).snapshot().brief(MONDAY).attempts]


def sends(home) -> int:
    return sum(1 for call in home.hermes_calls() if call[:1] == ["send"] and "-l" not in call)


def test_a_busy_collect_does_not_hold_back_the_brief(home):
    with schedule.TickLease(home.record_root) as collecting:
        assert collecting.held
        report = tick(home)
    assert report.skipped_busy and report.sent == ("brief 2026-11-16",)
    assert attempts(home) == [SendStatus.SENT]


def test_a_tick_that_finds_another_sending_sends_nothing(home):
    with schedule.SendLease(home.record_root):
        assert tick(home).sent == ()
    assert home.sent() == []
    assert tick(home, 10).sent == ("brief 2026-11-16",)


def test_arrival_unknown_is_sent_once_more_and_never_a_third_time(home):
    home.script_hermes("send", exit=1, stdout=json.dumps({"error": "Telegram send timed out"}, indent=2))
    assert tick(home).sent == ()
    assert attempts(home) == [SendStatus.ARRIVAL_UNKNOWN]
    assert tick(home, 10).sent == ()
    assert attempts(home) == [SendStatus.ARRIVAL_UNKNOWN, SendStatus.ARRIVAL_UNKNOWN]
    for later in (20, 30, 16 * 60):
        tick(home, later)
    assert sends(home) == 2
    texts = {call[-1] for call in home.hermes_calls() if call[:1] == ["send"]}
    assert len(texts) == 1, "the retry sends the same text"


def test_a_sending_attempt_left_by_a_crash_counts_as_arrival_unknown(home):
    record = Record.open(home.record_root)
    record.commit(rules.compose_daily(record.snapshot(), world.FIRST_BRIEF_AT).changes)
    assert attempts(home) == [SendStatus.SENDING]
    tick(home, 10)
    assert attempts(home) == [SendStatus.ARRIVAL_UNKNOWN, SendStatus.SENT]
    kept = record.snapshot().brief(MONDAY)
    assert kept.attempts[-1].message_ids == (home.sent()[0]["message_id"],)
    tick(home, 20)
    assert sends(home) == 1


def test_a_usage_error_leaves_the_attempt_sending_and_the_next_tick_tries_once_more(home):
    home.script_hermes("send", exit=2, stderr="hermes send: no message provided.\n")
    tick(home)
    assert attempts(home) == [SendStatus.SENDING]
    home.script_hermes("send", exit=1)
    tick(home, 10)
    assert attempts(home) == [SendStatus.ARRIVAL_UNKNOWN, SendStatus.ARRIVAL_UNKNOWN]


def test_two_plans_from_one_snapshot_commit_once(home):
    record = Record.open(home.record_root)
    snap = record.snapshot()
    first, second = (rules.compose_daily(snap, world.FIRST_BRIEF_AT) for _ in range(2))
    record.commit(first.changes)
    with pytest.raises(Conflict):
        record.commit(second.changes)


def test_two_ticks_racing_send_one_brief(home, monkeypatch):
    monkeypatch.setenv("FAKE_HERMES_SEND_SECONDS", "1")
    racer = ("import sys; from datetime import datetime; from pathlib import Path; from atlas.app import Atlas; "
             "Atlas.open(Path(sys.argv[1])).tick(datetime.fromisoformat(sys.argv[2]))")
    ticks = [subprocess.Popen([sys.executable, "-c", racer, str(home.record_root), world.FIRST_BRIEF_AT.isoformat()],
                              stderr=subprocess.PIPE, text=True) for _ in range(2)]
    for t in ticks:
        assert t.wait(timeout=30) == 0, t.stderr.read()
    assert len(home.sent()) == 1
    assert sorted(p.name for p in (home.record_root / "briefs").rglob("*.md")) == ["2026-11-16.md"]
    assert attempts(home) == [SendStatus.SENT]


def test_the_day_after_a_sent_brief_the_tick_sends_one_that_follows_it_up(home):
    assert tick(home).sent == ("brief 2026-11-16",)
    assert tick(home, 24 * 60).sent == ("brief 2026-11-17",)
    tuesday = Record.open(home.record_root).snapshot().brief(MONDAY + timedelta(days=1))
    assert tuesday.follows_up == MONDAY
    assert [f.state for f in tuesday.followups] == ["no_sign"] * 3
    assert home.sent()[-1]["text"] == tuesday.text


def test_a_pursuit_file_atlas_cannot_read_is_left_out_and_the_brief_goes_out(home, caplog):
    path = sorted((home.record_root / "pursuits").glob("*.md"))[0]
    (home.record_root / ".atlas/last-written" / path.relative_to(home.record_root)).unlink()
    path.write_text(path.read_text().replace("name: ", "name: \x9f", 1))
    assert tick(home).sent == ("brief 2026-11-16",)
    assert "Pursuit file it could not read" in caplog.text


def test_a_pursuit_name_with_a_lone_surrogate_does_not_hold_back_the_brief(home):
    path = next(p for p in sorted((home.record_root / "pursuits").glob("*.md")) if 'name: "' in p.read_text())
    (home.record_root / ".atlas/last-written" / path.relative_to(home.record_root)).unlink()
    path.write_text(path.read_text().replace('name: "', 'name: "Invented \\ud83d ', 1))
    assert tick(home).sent == ("brief 2026-11-16",)
    assert attempts(home) == [SendStatus.SENT]
    assert "Invented \ufffd " in home.sent()[0]["text"]


def test_a_brief_goes_out_as_its_file_holds_it_with_secrets_masked(home, monkeypatch):
    compose = rules.compose_daily
    secret = "ghp_" + "Inv3ntedT0kenAbCdEfGhIjKlMnOpQrStUvWx"

    def with_a_secret(snap, now):
        planned = compose(snap, now)
        b = replace(planned.send, text=f"{planned.send.text}\nInvented {secret}")
        return rules.Decision(snap.changes().put_brief(b), planned.outcome, send=b)

    monkeypatch.setattr(rules, "compose_daily", with_a_secret)
    assert tick(home).sent == ("brief 2026-11-16",)
    [sent] = home.sent()
    assert sent["text"] == Record.open(home.record_root).snapshot().brief(MONDAY).text
    assert sent["text"].endswith("Invented [secret: github token]")


def test_a_brief_the_record_keeps_changing_under_is_logged_and_left_to_the_next_tick(home, monkeypatch, caplog):
    def changed(record, changes):
        raise Conflict("briefs/2026/11/2026-11-16.md")

    with monkeypatch.context() as m, schedule.TickLease(home.record_root):
        m.setattr(Record, "commit", changed)
        assert tick(home).sent == ()
    assert home.sent() == []
    assert "kept changing" in caplog.text and "(Conflict)" in caplog.text
    assert tick(home, 10).sent == ("brief 2026-11-16",)


def test_a_sent_brief_whose_outcome_keeps_conflicting_is_logged_and_the_next_tick_sends_it_once_more(
        home, monkeypatch, caplog):
    commit = Record.commit

    def an_editor_saves_the_brief_first(record, changes):
        for brief in (record.root / "briefs").rglob("*.md"):
            (record.root / ".atlas/last-written" / brief.relative_to(record.root)).unlink(missing_ok=True)
            brief.write_bytes(brief.read_bytes().replace(b"atlas: brief/1\n", b"atlas: brief/1\n\n", 1))
        return commit(record, changes)

    with monkeypatch.context() as m, schedule.TickLease(home.record_root):
        m.setattr(Record, "commit", an_editor_saves_the_brief_first)
        assert tick(home).sent == ("brief 2026-11-16",)
    assert len(home.sent()) == 1
    assert attempts(home) == [SendStatus.SENDING]
    assert "kept changing" in caplog.text and "(Conflict)" in caplog.text
    assert tick(home, 10).sent == ("brief 2026-11-16",)
    assert attempts(home) == [SendStatus.ARRIVAL_UNKNOWN, SendStatus.SENT]
    tick(home, 20)
    assert len(home.sent()) == 2
def test_a_park_committed_while_the_brief_is_composed_makes_the_tick_compose_again(home, monkeypatch):
    commit, parked = Record.commit, []

    def owner_parks_first(record, cs):
        if not parked and any(op.path.startswith("briefs/") for op in cs._ops):
            parked.append(home.telegram("atlas, park Payments CI flakes"))
        return commit(record, cs)

    monkeypatch.setattr(Record, "commit", owner_parks_first)
    assert tick(home).sent == ("brief 2026-11-16",)
    assert parked == ["Payments CI flakes: parked. It leaves Focus."]
    brief = Record.open(home.record_root).snapshot().brief(MONDAY)
    assert "p-2x7b" not in [s.pursuit for s in brief.steps]
    assert home.sent()[0]["text"] == brief.text and "Payments CI flakes" not in brief.text


def test_a_brief_records_the_chat_it_was_sent_to(home):
    tick(home)
    assert Record.open(home.record_root).snapshot().brief(MONDAY).attempts[-1].chat == seeds.HOME_CHAT


def brief_commit(cs) -> bool:
    return any(op.path.startswith("briefs/") for op in cs._ops)


def test_a_note_committed_while_the_brief_is_composed_does_not_hold_it_back(home, monkeypatch):
    commit, notes = Record.commit, []

    def an_agent_note_lands_first(record, cs):
        if len(notes) < 5 and brief_commit(cs):
            notes.append(Note(f"nt-race{len(notes):04d}", world.FIRST_BRIEF_AT, NoteOrigin.AGENT, "claude_code",
                              "Invented progress note."))
            commit(record, record.snapshot().changes().append_note(notes[-1]))
        return commit(record, cs)

    monkeypatch.setattr(Record, "commit", an_agent_note_lands_first)
    assert tick(home).sent == ("brief 2026-11-16",)
    assert len(home.sent()) == 1


def test_a_brief_whose_pursuits_change_under_every_plan_waits_for_the_next_tick(home, monkeypatch, caplog):
    commit, changed = Record.commit, []

    def the_newsletter_changes_first(record, cs):
        if brief_commit(cs):
            snap = record.snapshot()
            changed.append(date(2026, 11, 1) + timedelta(days=len(changed)))
            commit(record, snap.changes().put_pursuit(replace(snap.pursuit("p-7d1c"), continued=changed[-1])))
        return commit(record, cs)

    monkeypatch.setattr(Record, "commit", the_newsletter_changes_first)
    caplog.set_level(logging.WARNING, logger="atlas")
    assert tick(home).sent == ()
    assert home.sent() == [] and Record.open(home.record_root).snapshot().brief(MONDAY) is None
    assert [r.getMessage() for r in caplog.records if r.name == "atlas"] == [
        "Atlas left the brief to the next tick: the record kept changing while it wrote it (Conflict)"]
    monkeypatch.setattr(Record, "commit", commit)
    assert tick(home, 10).sent == ("brief 2026-11-16",)
