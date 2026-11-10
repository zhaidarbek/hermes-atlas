"""Questions to Atlas: the index, the answers, the refusal, the late answer, and that reading
changes nothing but the conversation Note."""
import asyncio
import shutil
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from atlas import ask, channel, converse, rules
from atlas.app import Atlas
from atlas.model import (Agent, ChannelSetting, DayTime, Librarian, Membership, OutsideAction, Owner,
                         OwnerMessage, PeriodTimeQ, ResumePromptQ)
from atlas.record import Record
from harness import seeds, world
from harness.fake_llm import FakeLlm
from snapshots import at, pursuit, snapshot

SUNDAY = date(2026, 11, 22)
WEEK = PeriodTimeQ(date(2026, 11, 16), SUNDAY, "area")


def said(text: str, when: datetime, addressed: bool = False) -> OwnerMessage:
    return OwnerMessage(text, when, "telegram", None, None, None, addressed)


def test_the_index_is_built_from_the_files_alone_and_deleting_it_loses_nothing(tmp_path):
    seeds.thursday(tmp_path)
    snap = Record.open(tmp_path).snapshot()
    gpx = seeds.evidence_id(world.by_title("gpx elevation smoothing"))
    first = [h.evidence for h in snap.find("rolling median spikes")]
    (tmp_path / ".atlas" / "index.sqlite").unlink()
    assert first[0] == gpx
    assert [h.evidence for h in Record.open(tmp_path).snapshot().find("rolling median spikes")] == first
    assert Record.open(tmp_path).reindex() == len(Record.open(tmp_path).snapshot().sessions())


def test_the_index_follows_the_record_when_it_moves(tmp_path):
    seeds.thursday(tmp_path)
    assert Record.open(tmp_path).snapshot().find("mandolin") == ()
    record = Record.open(tmp_path)
    snap = record.snapshot()
    bike = snap.pursuit("Bike route planner")
    record.commit(snap.changes().put_pursuit(replace(bike, name="Bike route planner mandolin")))
    assert {h.evidence for h in Record.open(tmp_path).snapshot().find("mandolin")} == {m.evidence for m in bike.sessions}


def test_the_commit_that_removes_a_session_takes_its_words_out_of_the_index_file(tmp_path):
    seeds.thursday(tmp_path)
    record = Record.open(tmp_path)
    gpx = seeds.evidence_id(world.by_title("gpx elevation smoothing"))
    assert gpx in {h.evidence for h in record.snapshot().find("rolling median spikes")}
    (tmp_path / ".atlas" / "index.sqlite.4242.tmp").write_bytes(b"cut short")
    record.commit(record.snapshot().changes().remove_evidence(gpx))
    index = (tmp_path / ".atlas" / "index.sqlite").read_bytes()
    assert b"rolling median" not in index and gpx.encode() not in index
    assert list((tmp_path / ".atlas").glob("index.sqlite.*")) == []


def test_a_session_folder_removed_by_hand_is_never_found(tmp_path):
    seeds.thursday(tmp_path)
    gpx = seeds.evidence_id(world.by_title("gpx elevation smoothing"))
    assert gpx in {h.evidence for h in Record.open(tmp_path).snapshot().find("rolling median spikes")}
    for folder in tmp_path.rglob(gpx):
        shutil.rmtree(folder)
    assert gpx not in {h.evidence for h in Record.open(tmp_path).snapshot().find("rolling median spikes")}


def test_find_names_the_session_its_dropped_pursuit_and_the_checked_line(tmp_path):
    seeds.thursday(tmp_path)
    gpx = world.by_title("gpx elevation smoothing")
    llm = FakeLlm({"atlas_answer": [{"session": seeds.evidence_id(gpx), "text": "You smoothed it.",
                                      "quote": "a 30-point rolling median"}]})
    reply = Atlas(Record.open(tmp_path), _host(llm)).handle(said("find the chat where gpx smoothing worked",
                                                                 world.FIND_AT))
    assert reply.text.splitlines() == ["Codex, Sun 25 Oct 20:14, ~/code/bike-routes [F]",
                                       "Pursuit: Bike route planner (dropped) [F]", "You smoothed it. [F]"]


def test_a_model_sentence_over_its_maximum_is_refused_never_cut_into_the_record(tmp_path):
    seeds.thursday(tmp_path)
    gpx = world.by_title("gpx elevation smoothing")
    llm = FakeLlm({"atlas_answer": [{"session": seeds.evidence_id(gpx), "text": "You smoothed it again. " * 2000,
                                      "quote": "a 30-point rolling median"}]})
    reply = Atlas(Record.open(tmp_path), _host(llm)).handle(said("find the chat where gpx smoothing worked",
                                                                 world.FIND_AT))
    assert reply.text.startswith("I couldn't answer that") and "smoothed" not in reply.text


def test_a_quote_the_session_does_not_hold_is_an_inference(tmp_path):
    seeds.thursday(tmp_path)
    gpx = world.by_title("gpx elevation smoothing")
    llm = FakeLlm({"atlas_answer": [{"session": seeds.evidence_id(gpx), "text": "You smoothed it.",
                                      "quote": "words nobody ever typed there"}]})
    reply = Atlas(Record.open(tmp_path), _host(llm)).handle(said("find the chat where gpx smoothing worked", world.FIND_AT))
    assert reply.text.splitlines()[0] == "Codex, Sun 25 Oct 19:52, ~/code/bike-routes [F]"
    assert reply.text.splitlines()[-1] == "You smoothed it. [I]"


def test_a_find_the_model_cannot_answer_is_told_and_kept_in_the_note(tmp_path):
    seeds.thursday(tmp_path)
    reply = Atlas(Record.open(tmp_path), _host(FakeLlm())).handle(said("find the chat where gpx smoothing worked",
                                                                       world.FIND_AT))
    assert reply.text == "I couldn't answer that: UnscriptedCall."
    assert Record.open(tmp_path).snapshot().notes()[-1].atlas_reply == reply.text


@pytest.mark.parametrize("who", [Owner("telegram", "nt-0000001"), Librarian(("cc-1",), 1),
                                 Agent("codex", None, None)])
def test_an_outside_action_is_always_refused(who):
    assert rules.authorize(OutsideAction("send", "Dana", "billing export"), who) == "OUTSIDE_WORLD"


def test_the_refusal_changes_nothing_and_still_reads_the_rest_of_the_message():
    payments = pursuit("p-2x7b", "Payments CI flakes", worked=[date(2026, 11, 14)])
    snap = snapshot([payments])
    msg = said("send Dana the payments status. park payments", at(date(2026, 11, 19), "12:00"), addressed=True)
    reading = converse.interpret(msg, snap, None)
    assert reading.commands[0] == OutsideAction("send", "Dana", "payments")
    decision = rules.converse(snap, msg, reading.commands, reading.asks)
    assert decision.outcome.lines == (ask.CANNOT_SEND + "\n" + ask.TO_FORWARD + '\n"Payments: Left it here." [F]',
                                      "Payments CI flakes: parked.")


def test_a_refusal_naming_no_pursuit_says_only_that_atlas_cannot_send():
    snap = snapshot([pursuit("p-2x7b", "Payments CI flakes", worked=[date(2026, 11, 14)])])
    assert ask.refusal(snap, OutsideAction("email", "Sam", "the quarterly numbers")) == ask.CANNOT_SEND


def test_the_closest_name_wins_and_a_tie_names_none():
    service = pursuit("p-4k7q", "Billing export service")
    staging = pursuit("p-9m3c", "Billing export: staging deploy")
    assert ask.closest("billing export", [service, staging]) is service
    assert ask.closest("export", [pursuit("p-1", "Export one"), pursuit("p-2", "Export two")]) is None


def test_week_time_sums_the_days_by_area_most_time_first():
    days = [DayTime(timedelta(minutes=70), {"work": {"p-a": timedelta(minutes=50), "one-off": timedelta(minutes=10)},
                                            "home": {"p-b": timedelta(minutes=10)}}, ()),
            DayTime(timedelta(minutes=30), {"home": {"p-b": timedelta(minutes=30)}}, ())]
    text = ask.week_time(date(2026, 11, 30), date(2026, 12, 6), days, {"p-a": "Alpha", "p-b": "Beta"})
    assert text.splitlines() == ["Active time with AI agents, Mon 30 Nov to Sun 6 Dec [F]",
                                 "work    1 h 00   Alpha 50 min, one-off 10 min",
                                 "home    40 min   Beta 40 min"]


def test_week_time_counts_today_live_with_the_journal_days(tmp_path):
    seeds.sunday_evening(tmp_path)
    record = Record.open(tmp_path)
    debounce = world.by_title("keyboard debounce 10 ms")
    snap = record.snapshot()
    cs = snap.changes().put_session(replace(seeds._evidence(debounce), turns=seeds._turns(debounce)), {})
    record.commit(cs)
    snap = record.snapshot()
    keyboard = snap.pursuit("Keyboard firmware")
    member = Membership(seeds.evidence_id(debounce), debounce.end, True)
    record.commit(snap.changes().put_pursuit(replace(keyboard, sessions=(*keyboard.sessions, member))))
    text = Atlas(Record.open(tmp_path), None).read(WEEK, world.WEEK_TIME_AT).data
    home_row = next(line for line in text.splitlines() if line.startswith("home"))
    assert home_row.split() == ["home", "2", "h", "28", "Greenhouse", "logger", "2", "h", "10,", "Keyboard", "firmware",
                                "18", "min"]


def test_a_bare_prompt_resolves_to_the_last_resume_answer(tmp_path):
    seeds.keyboard_parked(tmp_path)
    atlas = Atlas(Record.open(tmp_path), None)
    atlas.handle(said("where did I leave off on the keyboard firmware", world.RESUME_AT))
    snap = Record.open(tmp_path).snapshot()
    reading = converse.interpret(said("prompt", world.RESUME_AT + timedelta(minutes=1)), snap, None)
    assert reading.asks == (ResumePromptQ(pursuit="p-1k2f"),)


def test_a_bare_prompt_after_anything_else_is_not_read(tmp_path):
    seeds.keyboard_parked(tmp_path)
    atlas = Atlas(Record.open(tmp_path), None)
    atlas.handle(said("where did I leave off on the keyboard firmware", world.RESUME_AT))
    atlas.handle(said("how long did I work this week", world.RESUME_AT + timedelta(minutes=1)))
    assert atlas.handle(said("prompt", world.RESUME_AT + timedelta(minutes=2))) is None


def test_reading_leaves_every_quiet_clock_and_writes_only_the_note(tmp_path):
    seeds.keyboard_parked(tmp_path)
    before = Record.open(tmp_path).snapshot()
    atlas = Atlas(Record.open(tmp_path), None)
    for text in ("where did I leave off on the keyboard firmware?", "prompt", "how long did I work this week, by Area?"):
        assert atlas.handle(said(text, world.RESUME_AT)) is not None
    after = Record.open(tmp_path).snapshot()
    assert list(after.pursuits()) == list(before.pursuits())
    assert [rules.quiet_since(p) for p in after.pursuits()] == [rules.quiet_since(p) for p in before.pursuits()]
    assert len(after.notes()) == len(before.notes()) + 3


class _SlowAtlas:
    """An Atlas whose answer waits for the test to release it."""

    def __init__(self, reply):
        self.release = threading.Event()
        self.reply = reply

    def handle(self, msg):
        self.release.wait(5)
        return self.reply

    def channel(self):
        return ChannelSetting("telegram")


@pytest.mark.parametrize("reply, late", [(type("R", (), {"text": "Codex, Sun 25 Oct 20:14"})(), "Codex, Sun 25 Oct 20:14"),
                                         (None, channel.LATE_FAILURE)])
def test_over_the_budget_the_holding_reply_comes_first_and_the_answer_follows(monkeypatch, reply, late):
    sent = []
    done = threading.Event()
    monkeypatch.setattr(channel, "send", lambda setting, text: (sent.append((setting.target, text)), done.set()))
    slow = _SlowAtlas(reply)
    listener = channel.Listener(lambda: slow, lambda: world.FIND_AT, budget=0.05)
    msg = said("find it", world.FIND_AT)
    assert asyncio.run(listener._within_budget(slow, msg)) == channel.HOLDING
    assert sent == []
    slow.release.set()
    assert done.wait(5)
    assert sent == [("telegram", late)]


def test_within_the_budget_the_answer_is_the_reply(monkeypatch):
    monkeypatch.setattr(channel, "send", lambda *a: pytest.fail("nothing is sent late"))
    quick = _SlowAtlas(type("R", (), {"text": "Here."})())
    quick.release.set()
    listener = channel.Listener(lambda: quick, lambda: world.FIND_AT, budget=5)
    assert asyncio.run(listener._within_budget(quick, said("find it", world.FIND_AT))) == "Here."


def _host(llm: FakeLlm):
    from atlas.librarian import HostLlm
    return HostLlm(type("Ctx", (), {"llm": llm})())
