from dataclasses import replace
from datetime import date, timedelta

import pytest

from atlas import converse, neglect, rules
from atlas.model import (Actor, BriefStep, Cite, Continue, DecisionAsk, Done, EndingProposal, Fact, Followup, HistoryLine,
                         HoldDecision, Inference, NeglectedQ, OwnerMessage, Pause, SendAttempt, SendStatus, SentBrief,
                         SentRef, SetStatus, Status, WhichQ)
from snapshots import after, at, pursuit, snapshot

THURSDAY = date(2026, 11, 19)
WEDNESDAY = THURSDAY - timedelta(days=1)
NOW = at(THURSDAY, "07:30")
BACKUPS = pursuit("p-3n0w", "Home server backups", worked=[date(2026, 10, 29)])
BIKE = pursuit("p-3b8e", "Bike route planner", worked=[date(2026, 10, 26)])
STAGING = pursuit("p-9m3c", "Billing export: staging deploy", worked=[date(2026, 11, 12)])
READING = pursuit("p-5g9d", "Reading tracker for the kids", worked=[date(2026, 11, 18)], focus=True)


def sent(day: date, decision: DecisionAsk | None = None, followups=(), steps=()) -> SentBrief:
    return SentBrief(day, (SendAttempt(at(day, "07:30"), SendStatus.SENT, ("88",)),), None, tuple(followups),
                     tuple(steps), decision, False, "")


def said(p, text: str, day: date, hm: str = "08:00"):
    """`p` with an owner History line at that time."""
    line = HistoryLine(f"h-{len(p.history) + 1:04d}", at(day, hm), Actor.OWNER,
                       Fact(text, Cite("nt-0said000", "your Note", at(day, hm))))
    return replace(p, history=(*p.history, line))


def compose(pursuits, briefs=(), now=NOW):
    d = rules.compose_daily(snapshot([READING, *pursuits], briefs=briefs), now)
    return d, after(d)


@pytest.mark.parametrize("pursuits, asked, want, parked", [
    pytest.param([BACKUPS, BIKE], None, DecisionAsk("p-3b8e", 1, 24), None, id="first ask, the quietest"),
    pytest.param([BACKUPS, BIKE], DecisionAsk("p-3n0w", 1, 20), DecisionAsk("p-3n0w", 2, 21), None,
                 id="second ask, though another is quieter"),
    pytest.param([BACKUPS, BIKE], DecisionAsk("p-3n0w", 2, 20), DecisionAsk("p-3b8e", 1, 24), "p-3n0w",
                 id="parked by silence, the slot moves on"),
    pytest.param([BACKUPS, STAGING], DecisionAsk("p-3n0w", 2, 20), None, "p-3n0w",
                 id="parked by silence, no other Neglected"),
    pytest.param([said(BACKUPS, rules.CONTINUE_SAID[0], WEDNESDAY), BIKE], DecisionAsk("p-3n0w", 1, 20),
                 DecisionAsk("p-3b8e", 1, 24), None, id="answered, the slot moves on"),
    pytest.param([replace(BACKUPS, decision_held_until=date(2026, 11, 20)), BIKE], DecisionAsk("p-3n0w", 1, 20),
                 DecisionAsk("p-3b8e", 1, 24), None, id="held, not asked and not counted"),
    pytest.param([replace(BACKUPS, decision_held_until=THURSDAY), STAGING], None, DecisionAsk("p-3n0w", 1, 21), None,
                 id="a hold ending today asks again"),
])
def test_the_decision_slot_counts_its_asks(pursuits, asked, want, parked):
    briefs = [sent(WEDNESDAY, asked)] if asked else []
    d, then = compose(pursuits, briefs)
    assert d.send.decision == want
    for p in pursuits:
        kept = then.pursuit(p.id)
        if p.id == parked:
            assert (kept.status, kept.focus) == (Status.PARKED, False)
            assert (kept.history[-1].actor, kept.history[-1].statement.text) == (Actor.ATLAS, neglect.PARKED_BY_SILENCE)
        else:
            assert kept.status is p.status


def test_continue_undoes_a_park_by_silence():
    _, then = compose([BACKUPS, BIKE], [sent(WEDNESDAY, DecisionAsk("p-3n0w", 2, 20))])
    msg = OwnerMessage("continue the backups", at(THURSDAY, "09:00"), "telegram", None, None, "t:1")
    read = converse.interpret(msg, then, None)
    assert read.commands == (Continue("p-3n0w"),)
    d = rules.converse(then, msg, read.commands, read.asks)
    assert d.outcome.lines == ("Home server backups: active again. Its quiet days count from today.",)
    backups = after(d).pursuit("p-3n0w")
    assert (backups.status, backups.continued) == (Status.ACTIVE, THURSDAY)


def test_continue_restarts_the_quiet_clock_from_today():
    msg = OwnerMessage("continue the backups", at(THURSDAY, "07:45"), "telegram", None, None, "t:2")
    snap = after(rules.converse(snapshot([BACKUPS]), msg, [Continue("p-3n0w")], []))
    assert rules.quiet_since(snap.pursuit("p-3n0w")) == THURSDAY
    assert rules.neglected(snap, THURSDAY) == []
    assert [p.id for p in rules.neglected(snap, THURSDAY + timedelta(days=14))] == ["p-3n0w"]


def test_neglected_is_empty_when_focus_holds_every_active_pursuit():
    starred = [replace(p, focus=True) for p in (BACKUPS, BIKE, STAGING)]
    assert rules.neglected(snapshot(starred), THURSDAY) == []


PAYMENTS = pursuit("p-2x7b", "Payments CI flakes", worked=[date(2026, 11, 18)], focus=True)
SEEN = Cite("cx-0seen0000", "Codex", at(WEDNESDAY, "16:05"))
ENDING = EndingProposal(Status.DONE, Inference("Its purpose is met.", ("cx-0seen0000",)), WEDNESDAY, False)


def finished(proposal=ENDING):
    step = replace(PAYMENTS.next_step, ended=Done("seen", SEEN.at, Fact("50 of 50 runs pass", SEEN)))
    return replace(PAYMENTS, next_step=None, past_steps=(step,), proposals=(proposal,) if proposal else ())


def followed_up(p, day=WEDNESDAY):
    return sent(day, steps=[BriefStep(1, p.id, p.past_steps[0].id)])


def test_the_follow_up_proposes_the_ending_yesterdays_step_finished_and_marks_it_raised():
    d, then = compose([finished()], [followed_up(finished())])
    assert [(f.pursuit, f.ending_proposed) for f in d.send.followups] == [("p-2x7b", True)]
    assert f"      {rules.messages.FINISHED}" in d.send.text.splitlines()
    assert then.pursuit("p-2x7b").proposals == (replace(ENDING, in_followup=True),)


@pytest.mark.parametrize("proposal", [
    pytest.param(replace(ENDING, in_followup=True), id="already raised"),
    pytest.param(replace(ENDING, since=WEDNESDAY - timedelta(days=3)), id="older than the last brief"),
    pytest.param(replace(ENDING, to=Status.DROPPED), id="a drop waits for the Review"),
    pytest.param(None, id="no proposal"),
])
def test_an_ending_proposal_is_raised_at_most_once_and_only_when_yesterdays_step_finished_it(proposal):
    d, _ = compose([finished(proposal)], [followed_up(finished(proposal))])
    assert not any(f.ending_proposed for f in d.send.followups)
    assert rules.messages.FINISHED not in d.send.text


def test_a_proposal_once_raised_is_not_raised_by_the_next_brief():
    _, then = compose([finished()], [followed_up(finished())])
    friday = rules.compose_daily(then, at(THURSDAY + timedelta(days=1), "07:30"))
    assert rules.messages.FINISHED not in friday.send.text


def test_close_it_ends_the_proposed_pursuit_which_leaves_focus_and_answers_the_proposal():
    thursday = replace(sent(THURSDAY), followups=(Followup("p-2x7b", "s-20261115-1", "done_seen", None, True),))
    snap = snapshot([finished(replace(ENDING, in_followup=True)), BACKUPS], briefs=[thursday])
    msg = OwnerMessage("close it. continue the backups", at(THURSDAY, "07:45"), "telegram", None,
                       SentRef("brief", THURSDAY.isoformat()), "t:3")
    read = converse.interpret(msg, snap, None)
    assert read.commands == (SetStatus("p-2x7b", Status.DONE), Continue("p-3n0w"))
    d = rules.converse(snap, msg, read.commands, read.asks)
    assert d.outcome.lines == ("Payments CI flakes: done. It leaves Focus.",
                               "Home server backups: stays active. Its quiet days count from today.")
    closed = after(d).pursuit("p-2x7b")
    assert (closed.status, closed.focus, closed.proposals) == (Status.DONE, False, ())


def test_close_it_with_no_ending_proposed_is_left_to_hermes():
    snap = snapshot([PAYMENTS], briefs=[sent(THURSDAY)])
    msg = OwnerMessage("close it", at(THURSDAY, "07:45"), "telegram", None, None, "t:4")
    assert converse.interpret(msg, snap, None).unclear == converse.UNREAD


def test_parking_from_focus_says_it_leaves_focus_and_outside_focus_does_not():
    msg = OwnerMessage("park", at(THURSDAY, "08:00"), "telegram", None, None, "t:5")
    d = rules.converse(snapshot([PAYMENTS, BACKUPS]), msg,
                       [SetStatus("p-2x7b", Status.PARKED), SetStatus("p-3n0w", Status.PARKED)], [])
    assert d.outcome.lines == ("Payments CI flakes: parked. It leaves Focus.", "Home server backups: parked.")


def test_what_have_i_neglected_reads_as_a_question():
    msg = OwnerMessage("what have I neglected?", at(THURSDAY, "12:05"), "telegram", None, None, "t:6", addressed=True)
    assert converse.interpret(msg, snapshot([BACKUPS]), None).asks == (NeglectedQ(),)


def test_the_neglected_answer_lists_neglected_pursuits_and_the_weeks_decisions():
    newsletter = said(pursuit("p-7d1c", "Agent-tooling newsletter", status=Status.PARKED, worked=[date(2026, 10, 19)]),
                      "Parked it.", date(2026, 11, 16))
    bike = said(replace(BIKE, status=Status.DROPPED), "Dropped it.", date(2026, 11, 17))
    continued = said(replace(BACKUPS, continued=THURSDAY), rules.CONTINUE_SAID[0], THURSDAY, "07:45")
    old = said(replace(STAGING, status=Status.PARKED), "Parked it.", date(2026, 11, 10))
    noon = at(THURSDAY, "12:05")
    assert neglect.answer(snapshot([newsletter, bike, continued, old]), noon) == "\n".join([
        "Nothing is Neglected today. [F]",
        "You parked Agent-tooling newsletter on Monday and dropped Bike route planner on Tuesday.",
        'Home server backups was Neglected until you said "continue" this morning. [F]'])
    assert neglect.answer(snapshot([BACKUPS, BIKE]), noon) == "\n".join([
        "Bike route planner · project · quiet 24 days [F]", "Home server backups · project · quiet 21 days [F]"])


FRIDAY = THURSDAY + timedelta(days=1)
SATURDAY = FRIDAY + timedelta(days=1)


def asked_twice(**heard):
    return [sent(THURSDAY, DecisionAsk("p-3n0w", 2, 21, **heard))]


def test_a_question_the_owner_wrote_about_unreadably_is_asked_again_as_it_was_and_parks_nothing():
    d, then = compose([BACKUPS, BIKE], asked_twice(heard=True), at(FRIDAY, "07:30"))
    assert d.send.decision == DecisionAsk("p-3n0w", 2, 22)
    assert then.pursuit("p-3n0w").status is Status.ACTIVE


def test_days_inside_a_pause_are_no_silence():
    paused = Pause(at(THURSDAY, "10:00"), at(FRIDAY, "12:00"), "pause")
    snap = snapshot([READING, BACKUPS, BIKE], briefs=asked_twice(), pauses=[paused])
    prev = snap.brief(THURSDAY)
    assert neglect.decision(snap, prev, set(), SATURDAY, snap.pauses()) == (DecisionAsk("p-3n0w", 2, 22), None)
    assert neglect.decision(snap, prev, set(), SATURDAY)[1] == "p-3n0w"
    d, then = compose([BACKUPS, BIKE], asked_twice(), at(SATURDAY, "07:30"))
    assert then.pursuit("p-3n0w").status is Status.PARKED


def reply(text, day=THURSDAY, hm="08:00", to: date | None = THURSDAY):
    to = SentRef("brief", to.isoformat()) if to else None
    return OwnerMessage(text, at(day, hm), "telegram", None, to, f"t:{hm}")


@pytest.mark.parametrize("text, want", [
    ("continue", Continue("p-3n0w")), ("Continue it!", Continue("p-3n0w")),
    ("park that", SetStatus("p-3n0w", Status.PARKED)),
    ("drop", SetStatus("p-3n0w", Status.DROPPED)),
])
def test_a_bare_word_answers_the_question_of_the_brief_replied_to(text, want):
    snap = snapshot([BACKUPS, BIKE], briefs=asked_twice())
    assert converse.interpret(reply(text), snap, None).commands == (want,)
    assert converse.interpret(reply(text, to=None), snap, None).commands == (want,)


@pytest.mark.parametrize("text", ["continue the conversation later", "can you continue?", "close the door",
                                  "don't close it yet"])
def test_ordinary_words_stay_unread(text):
    snap = snapshot([BACKUPS, BIKE], briefs=asked_twice())
    assert converse.interpret(reply(text), snap, None).unclear == converse.UNREAD


def test_a_bare_word_with_no_question_asked_stays_unread():
    snap = snapshot([BACKUPS, BIKE], briefs=[sent(THURSDAY)])
    assert converse.interpret(reply("continue"), snap, None).unclear == converse.UNREAD


def silenced(p, day=FRIDAY):
    return neglect.parked(p, at(day, "07:30"))


def test_continue_after_a_park_by_silence_undoes_the_one_park():
    snap = snapshot([silenced(BACKUPS), BIKE], briefs=[sent(SATURDAY)])
    assert converse.interpret(reply("continue it", SATURDAY, to=SATURDAY), snap, None).commands == (Continue("p-3n0w"),)
    assert converse.interpret(reply("continue", SATURDAY, to=None), snap, None).commands == (Continue("p-3n0w"),)
    assert converse.interpret(reply("park", SATURDAY, to=SATURDAY), snap, None).unclear == converse.UNREAD


def test_continue_after_a_park_by_silence_names_them_when_there_are_several():
    snap = snapshot([silenced(BACKUPS), silenced(BIKE, THURSDAY)], briefs=[sent(SATURDAY)])
    read = converse.interpret(reply("continue", SATURDAY, to=SATURDAY), snap, None)
    assert read.asks == (WhichQ(("Bike route planner", "Home server backups")),)
    assert neglect.which(read.asks[0].names) == \
        'Which one: Bike route planner or Home server backups? Reply "continue" with its name.'


def test_continue_to_a_brief_sent_before_the_park_does_not_undo_it():
    snap = snapshot([silenced(BACKUPS, SATURDAY)], briefs=[sent(FRIDAY)])
    assert converse.interpret(reply("continue", SATURDAY, to=FRIDAY), snap, None).unclear == converse.UNREAD


@pytest.mark.parametrize("text, to", [("remind me friday", THURSDAY), ("remind me on Friday about the backups", None)])
def test_remind_me_holds_the_decision_question(text, to):
    snap = snapshot([BACKUPS, STAGING], briefs=asked_twice())
    msg = reply(text, to=to)
    read = converse.interpret(msg, snap, None)
    assert read.commands == (HoldDecision("p-3n0w", FRIDAY),)
    d = rules.converse(snap, msg, read.commands, read.asks)
    assert d.outcome.lines == ("Home server backups: I'll ask about this again on Friday.",)
    held = after(d)
    assert held.pursuit("p-3n0w").decision_held_until == FRIDAY
    assert neglect.decision(held, held.brief(THURSDAY), set(), THURSDAY) == (None, None)
    assert neglect.decision(held, held.brief(THURSDAY), set(), FRIDAY) == (DecisionAsk("p-3n0w", 1, 22), None)


def test_remind_me_with_no_question_asked_stays_unread():
    snap = snapshot([BACKUPS], briefs=[sent(THURSDAY)])
    assert converse.interpret(reply("remind me friday"), snap, None).unclear == converse.UNREAD


def test_close_it_needs_the_ending_proposal_still_on_the_pursuit():
    thursday = replace(sent(THURSDAY), followups=(Followup("p-2x7b", "s-20261115-1", "done_seen", None, True),))
    snap = snapshot([finished(replace(ENDING, in_followup=True))], briefs=[thursday])
    assert converse.interpret(reply("close it"), snap, None).commands == (SetStatus("p-2x7b", Status.DONE),)
    answered = snapshot([replace(finished(), proposals=())], briefs=[thursday])
    assert converse.interpret(reply("close it"), answered, None).unclear == converse.UNREAD


@pytest.mark.parametrize("text, to, marked", [
    ("keep the backups", THURSDAY, True), ("the backup can wait", None, True), ("thanks", THURSDAY, True),
    ("thanks", None, False), ("", THURSDAY, False),
])
def test_words_about_the_open_question_mark_it_heard(text, to, marked):
    snap = snapshot([BACKUPS], briefs=asked_twice())
    b = neglect.heard(snap, reply(text, to=to))
    assert (b is not None and b.decision.heard) is marked
