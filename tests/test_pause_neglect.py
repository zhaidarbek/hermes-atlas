"""Where the Pause meets the Neglected question (#133): the Pause is written through the owner's
own "atlas, pause ..." and the brief's decision slot reads it."""
from datetime import date, datetime, timedelta

import pytest

from atlas import converse, neglect, pausing, rules
from atlas.model import (Continue, DecisionAsk, EndPause, HoldDecision, MarkStep, Owner, OwnerMessage, PauseCmd, SentRef,
                         SetStatus, Status)
from snapshots import after, at, snapshot
from test_neglect import BACKUPS, BIKE, READING, THURSDAY, WEDNESDAY, sent
from test_pause import _Unreadable
from test_skip_by_word import KNEE, _brief

FRIDAY = THURSDAY + timedelta(days=1)


def _pause(snap, text: str, now: datetime):
    msg = OwnerMessage(text, now, "telegram", "telegram:5550001", None, f"5550001:{now:%d%H%M}", addressed=True)
    read = converse.interpret(msg, snap, None)
    assert isinstance(read.commands[0], PauseCmd)
    return after(rules.converse(snap, msg, read.commands, read.asks))


def _second_ask_then_a_week_away():
    """Wed asked about the backups once, Thu a second time; the owner paused for a week after Thu's brief."""
    briefs = [sent(WEDNESDAY, DecisionAsk(BACKUPS.id, 1, 20)), sent(THURSDAY, DecisionAsk(BACKUPS.id, 2, 21))]
    return _pause(snapshot([READING, BACKUPS, BIKE], briefs=briefs), "pause for a week", at(THURSDAY, "08:00"))


def test_a_week_long_pause_after_the_second_ask_asks_again_on_the_first_day_back():
    snap = _second_ask_then_a_week_away()
    back = pausing.brief_from(THURSDAY + timedelta(days=7), snap.pauses(), snap.settings())
    d = rules.compose_daily(snap, back)
    assert d.send.decision is not None and (d.send.decision.pursuit, d.send.decision.ask) == (BACKUPS.id, 2)
    assert after(d).pursuit(BACKUPS.id).status is Status.ACTIVE


@pytest.mark.parametrize("day", [FRIDAY, FRIDAY + timedelta(days=3)])
def test_while_paused_nothing_is_asked_and_nothing_is_parked(day):
    snap = _second_ask_then_a_week_away()
    d = rules.compose_daily(snap, at(day, "07:30"))
    assert d.send is None and after(d).brief(day) is None
    assert after(d).pursuit(BACKUPS.id).status is Status.ACTIVE


def test_the_first_brief_after_a_pause_carries_the_question_it_would_have_carried():
    unpaused = rules.compose_daily(snapshot([READING, BACKUPS, BIKE]), at(THURSDAY, "07:30")).send.decision
    snap = _pause(snapshot([READING, BACKUPS, BIKE]), "pause until sunday", at(WEDNESDAY, "09:00"))
    assert rules.compose_daily(snap, at(THURSDAY, "07:30")).send is None
    sunday = rules.compose_daily(snap, at(date(2026, 11, 22), "07:30")).send.decision
    assert (sunday.pursuit, sunday.ask) == (unpaused.pursuit, unpaused.ask) == (BIKE.id, 1)
    assert sunday.quiet_days == unpaused.quiet_days


def test_an_unreadable_pauses_md_stops_neither_the_decision_slot_nor_the_neglected_answer():
    snap = _Unreadable(snapshot([READING, BACKUPS, BIKE], briefs=[sent(WEDNESDAY, DecisionAsk(BACKUPS.id, 1, 20))]),
                       ValueError("bad"))
    assert rules.compose_daily(snap, at(THURSDAY, "07:30")).send.decision == DecisionAsk(BACKUPS.id, 2, 21)
    assert neglect.answer(snap, at(THURSDAY, "09:00")).startswith("Bike route planner")


ASKING, SILENT = DecisionAsk(BACKUPS.id, 1, 20), None


@pytest.mark.parametrize("decision", [ASKING, SILENT], ids=["asks", "asks-none"])
@pytest.mark.parametrize("text, asking, silent", [
    ("remind me friday", (HoldDecision(BACKUPS.id, FRIDAY),), None),
    ("pause", (PauseCmd(None, ""),), (PauseCmd(None, ""),)),
    ("resume", (EndPause(),), (EndPause(),)),
    ("back", (EndPause(),), (EndPause(),)),
    ("continue", (Continue(BACKUPS.id),), None),
    ("park", (SetStatus(BACKUPS.id, Status.PARKED),), None),
    ("drop", (SetStatus(BACKUPS.id, Status.DROPPED),), None),
])
def test_each_reply_to_a_brief_goes_to_exactly_one_reader(decision, text, asking, silent):
    snap = snapshot([READING, BACKUPS, BIKE], briefs=[sent(WEDNESDAY, decision)])
    msg = OwnerMessage(text, at(THURSDAY, "09:00"), "telegram", None, SentRef("brief", WEDNESDAY.isoformat()), "t:9")
    read = converse.interpret(msg, snap, None)
    want = asking if decision else silent
    if want is None:
        assert (read.commands, read.asks, read.unclear) == ((), (), converse.UNREAD)
    else:
        assert (read.commands, read.asks, read.unclear) == (want, (), None)


def test_a_step_skipped_by_word_comes_back_counting_its_own_offers_and_the_decision_slot_still_asks():
    friday = after(rules.decide(snapshot([KNEE], briefs=[_brief(FRIDAY, "s-20261115-1")]),
                                MarkStep(KNEE.id, "s-20261115-1", "skipped", "not today"),
                                Owner("telegram", "nt-0said000"), at(FRIDAY, "09:45")))
    knee = friday.pursuit(KNEE.id)
    briefs = [_brief(FRIDAY, "s-20261115-1"), _brief(date(2026, 11, 21), knee.next_step.id)]
    snap = snapshot([knee, READING, BACKUPS], briefs=briefs)
    assert rules.offers(snap, KNEE.id, knee.next_step) == 1
    sunday = rules.compose_daily(snap, at(date(2026, 11, 22), "07:30"))
    assert after(sunday).pursuit(KNEE.id).next_step == knee.next_step
    assert sunday.send.decision is not None and sunday.send.decision.pursuit == BACKUPS.id
