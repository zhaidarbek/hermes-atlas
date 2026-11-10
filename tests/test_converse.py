"""converse.interpret's grammar: the owner's plain words as Commands and Queries, no model."""
from datetime import date

import pytest

from atlas import converse
from atlas.model import (BriefStep, Continue, FindQ, MarkStep, OutsideAction, OwnerMessage, ResumePromptQ,
                         SendAttempt, SendStatus, SentBrief, SentRef, SetStatus, Status)
from snapshots import at, pursuit, snapshot

MONDAY, TUESDAY, WEDNESDAY = date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18)
PAYMENTS = pursuit("p-2x7b", "Payments CI flakes", worked=[date(2026, 11, 14)], focus=True)
BILLING = pursuit("p-4k7q", "Billing export service", worked=[date(2026, 11, 13)], focus=True)
STAGING = pursuit("p-9m3c", "Billing export: staging deploy", worked=[date(2026, 11, 12)])
NEWSLETTER = pursuit("p-7d1c", "Agent-tooling newsletter", worked=[date(2026, 10, 19)])
BIKE = pursuit("p-3b8e", "Bike route planner", worked=[date(2026, 10, 26)])
KEYBOARD = pursuit("p-1k2f", "Keyboard firmware", worked=[date(2026, 8, 30)], status=Status.PARKED)


def brief(day, *pursuits):
    return SentBrief(day, (SendAttempt(at(day, "07:30"), SendStatus.SENT, (f"88{day.day}",)),), None, (),
                     tuple(BriefStep(n, p.id, p.next_step.id) for n, p in enumerate(pursuits, 1)), None, False, "")


SNAP = snapshot([PAYMENTS, BILLING, STAGING, NEWSLETTER, BIKE, KEYBOARD],
                briefs=[brief(MONDAY, PAYMENTS, BILLING), brief(TUESDAY, BILLING)])
ON_MONDAY = SentRef("brief", MONDAY.isoformat(), ("8816",))


def read(text, reply_to=ON_MONDAY, addressed=False):
    return converse.interpret(OwnerMessage(text, at(TUESDAY, "09:00"), "telegram", None, reply_to, None, addressed),
                              SNAP, None)


STEP_1 = MarkStep("p-2x7b", "s-20261115-1", "done", None)


@pytest.mark.parametrize("text, commands, asks", [
    ("1 prompt", (), (ResumePromptQ(brief=MONDAY, item=1),)),
    ("1 done, 2 skip", (STEP_1, MarkStep("p-4k7q", "s-20261115-1", "skipped", None)), ()),
    ("1 done; park the newsletter", (STEP_1, SetStatus("p-7d1c", Status.PARKED)), ()),
    ("1 done. 10/10 pass", (MarkStep("p-2x7b", "s-20261115-1", "done", "10/10 pass"),), ()),
    ("1 done", (STEP_1,), ()),
    ("1 Done.", (STEP_1,), ()),
    ("1 done, 10/10 pass with one worker", (MarkStep("p-2x7b", "s-20261115-1", "done", "10/10 pass with one worker"),), ()),
    ("1 done. tablet app, nothing fancy", (MarkStep("p-2x7b", "s-20261115-1", "done", "tablet app, nothing fancy"),), ()),
    ("skip 2, not today", (MarkStep("p-4k7q", "s-20261115-1", "skipped", "not today"),), ()),
    ("2 skip", (MarkStep("p-4k7q", "s-20261115-1", "skipped", None),), ()),
    ("1 done, 10/10 pass with one worker. park the newsletter",
     (MarkStep("p-2x7b", "s-20261115-1", "done", "10/10 pass with one worker"), SetStatus("p-7d1c", Status.PARKED)), ()),
    ("drop the bike planner", (SetStatus("p-3b8e", Status.DROPPED),), ()),
    ("park Billing export: staging deploy", (SetStatus("p-9m3c", Status.PARKED),), ()),
    ("continue the bike", (Continue("p-3b8e"),), ()),
    ("continue the keyboard", (Continue("p-1k2f"),), ()),
])
def test_the_grammar_reads(text, commands, asks):
    assert read(text) == converse.Interpretation(commands, asks, False, None)


@pytest.mark.parametrize("addressed", [False, True])
@pytest.mark.parametrize("text, step", [
    ("1 done, message queue fixed", MarkStep("p-2x7b", "s-20261115-1", "done", "message queue fixed")),
    ("1 done. tell Dana tomorrow", MarkStep("p-2x7b", "s-20261115-1", "done", "tell Dana tomorrow")),
    ("skip 2, text Sam first", MarkStep("p-4k7q", "s-20261115-1", "skipped", "text Sam first")),
    ("2 done. send logs later", MarkStep("p-4k7q", "s-20261115-1", "done", "send logs later")),
])
def test_words_after_a_steps_mark_are_its_detail_even_when_they_read_as_an_outside_action(text, step, addressed):
    assert read(text, addressed=addressed) == converse.Interpretation((step,), (), False, None)


@pytest.mark.parametrize("text", [
    "tell the agent to rerun the tests",
    "forward port 8080 to the container",
    "ping 10.0.0.1 and report the latency",
    "send Dana the billing export status",
])
def test_an_outside_action_not_addressed_to_atlas_is_left_to_hermes(text):
    assert read(text) == converse.Interpretation((), (), False, converse.UNREAD)


@pytest.mark.parametrize("text, commands", [
    ("send Dana the billing export status", (OutsideAction("send", "Dana", "billing export"),)),
    ("continue the billing export service. send Dana the billing export status",
     (Continue("p-4k7q"), OutsideAction("send", "Dana", "billing export"))),
])
def test_an_outside_action_addressed_to_atlas_is_read(text, commands):
    assert read(text, addressed=True) == converse.Interpretation(commands, (), False, None)


@pytest.mark.parametrize("text", [
    "merge the two greenhouse ones",
    "1 prompt please now",
    "4 done",
    "1 done. park the zeppelin",
    "1 done, park the zeppelin",
    "park",
    "",
    ".",
    " , ",
    "drop it",
    "park this one",
])
def test_words_the_grammar_cannot_read_are_unread_and_change_nothing(text):
    assert read(text) == converse.Interpretation((), (), False, converse.UNREAD)


def test_words_that_name_several_pursuits_are_ambiguous():
    assert read("park billing").unclear == "AMBIGUOUS:Billing export service|Billing export: staging deploy"
    assert read("park billing").commands == ()


def test_an_exact_name_wins_over_a_longer_one_that_contains_its_words():
    logger = pursuit("p-6r2t", "Greenhouse logger", worked=[date(2026, 11, 11)])
    both = snapshot([logger, pursuit("p-6r2u", "Greenhouse logger dashboard", worked=[date(2026, 11, 9)])])
    msg = OwnerMessage("park greenhouse logger", at(TUESDAY, "09:00"), "telegram", None, None)
    assert converse.interpret(msg, both, None).commands == (SetStatus("p-6r2t", Status.PARKED),)


def test_numbers_name_the_steps_of_the_brief_replied_to_else_of_the_latest_brief():
    assert read("1 done", reply_to=None).commands == (MarkStep("p-4k7q", "s-20261115-1", "done", None),)
    assert read("1 prompt", reply_to=None).asks == (ResumePromptQ(brief=TUESDAY, item=1),)
    assert read("1 done", reply_to=SentRef("reply", "nt-1", ())).unclear == converse.UNREAD


def test_a_pronoun_names_no_pursuit_even_when_a_name_has_a_word_it_begins():
    trip = pursuit("p-9t1p", "Japan trip itinerary", worked=[date(2026, 11, 10)])
    msg = OwnerMessage("drop it", at(TUESDAY, "09:00"), "telegram", None, None)
    assert converse.interpret(msg, snapshot([PAYMENTS, trip]), None).unclear == converse.UNREAD


def test_numbers_never_name_a_brief_still_being_sent():
    sending = SentBrief(WEDNESDAY, (SendAttempt(at(WEDNESDAY, "07:30"), SendStatus.SENDING, ()),), None, (),
                        (BriefStep(1, BIKE.id, BIKE.next_step.id),), None, False, "")
    snap = snapshot([PAYMENTS, BILLING, BIKE], briefs=[brief(TUESDAY, BILLING), sending])
    msg = OwnerMessage("1 done", at(WEDNESDAY, "07:30"), "telegram", None, None)
    assert converse.interpret(msg, snap, None).commands == (MarkStep("p-4k7q", "s-20261115-1", "done", None),)


@pytest.mark.parametrize("addressed", [False, True])
@pytest.mark.parametrize("words", ["I decided to merge the two exporters", "I made a new area for the bike planner"])
def test_a_find_naming_a_merge_or_a_new_area_is_still_a_find(words, addressed):
    assert read(f"find the conversation where {words}", reply_to=None, addressed=addressed) == \
        converse.Interpretation((), (FindQ(words),), False, None)
