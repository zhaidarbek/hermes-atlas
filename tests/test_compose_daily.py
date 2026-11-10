from dataclasses import replace
from datetime import date

import pytest

from atlas import rules
from atlas.model import DecisionAsk, Kind, SendAttempt, SendStatus, Status
from snapshots import at, pursuit, snapshot

MONDAY = date(2026, 11, 16)
NOW = at(MONDAY, "07:30")


def focus(id, name, worked, **kw):
    return pursuit(id, name, worked=[worked], focus=True, **kw)


PAYMENTS = focus("p-2x7b", "Payments CI flakes", date(2026, 11, 14))
BILLING = focus("p-4k7q", "Billing export service", date(2026, 11, 13))
GREENHOUSE = focus("p-6r2t", "Greenhouse logger", date(2026, 11, 11))
READING = focus("p-5g9d", "Reading tracker for the kids", date(2026, 11, 6))
NEWSLETTER = pursuit("p-7d1c", "Agent-tooling newsletter", worked=[date(2026, 10, 19)], kind=Kind.IDEA)
BIKE = pursuit("p-3b8e", "Bike route planner", worked=[date(2026, 10, 26)], kind=Kind.EXPERIMENT)
BACKUPS = pursuit("p-3n0w", "Home server backups", worked=[date(2026, 10, 29)])
STAGING = pursuit("p-9m3c", "Billing export: staging deploy", worked=[date(2026, 11, 12)])
KEYBOARD = pursuit("p-1k2f", "Keyboard firmware", worked=[date(2026, 8, 30)], status=Status.PARKED)
WEEK = [PAYMENTS, BILLING, GREENHOUSE, READING, NEWSLETTER, BIKE, BACKUPS, STAGING, KEYBOARD]


def offered(decision) -> list[str]:
    names = {p.id: p.name for p in WEEK}
    return [names[s.pursuit] for s in decision.send.steps]


def test_steps_come_from_focus_most_recent_work_first_at_most_three():
    d = rules.compose_daily(snapshot(WEEK), NOW)
    assert offered(d) == ["Payments CI flakes", "Billing export service", "Greenhouse logger"]
    assert [s.n for s in d.send.steps] == [1, 2, 3]
    assert '    Reply "1 prompt", "2 prompt" or "3 prompt" for a paste-in prompt.' in d.send.text.splitlines()


def test_a_held_step_comes_first_on_its_day_and_not_before():
    due_today = replace(READING, next_step=replace(READING.next_step, held_until=MONDAY))
    later = replace(PAYMENTS, next_step=replace(PAYMENTS.next_step, held_until=date(2026, 11, 20)))
    d = rules.compose_daily(snapshot([due_today, later, BILLING, GREENHOUSE]), NOW)
    assert offered(d) == ["Reading tracker for the kids", "Billing export service", "Greenhouse logger"]


def test_steps_are_never_padded_from_outside_focus_or_from_pursuits_without_an_open_step():
    no_step = replace(GREENHOUSE, next_step=None)
    d = rules.compose_daily(snapshot([PAYMENTS, no_step, STAGING, NEWSLETTER]), NOW)
    assert offered(d) == ["Payments CI flakes"]
    assert '    Reply "1 prompt" for a paste-in prompt.' in d.send.text.splitlines()


def test_the_quietest_neglected_pursuit_fills_the_decision_slot():
    d = rules.compose_daily(snapshot(WEEK), NOW)
    assert d.send.decision == DecisionAsk("p-7d1c", 1, 28)
    held = replace(NEWSLETTER, decision_held_until=date(2026, 11, 20))
    assert rules.compose_daily(snapshot([held, BIKE, PAYMENTS]), NOW).send.decision == DecisionAsk("p-3b8e", 1, 21)


def test_the_decision_slot_stays_empty_when_none_qualifies():
    d = rules.compose_daily(snapshot([PAYMENTS, STAGING, KEYBOARD]), NOW)
    assert d.send.decision is None
    assert d.send.text.endswith("NEEDS A DECISION\n    Nothing today.")


def test_before_any_focus_steps_come_from_the_most_recent_active_work():
    unstarred = [replace(p, focus=False, history=p.history[:1]) for p in (PAYMENTS, BILLING, GREENHOUSE, READING)]
    d = rules.compose_daily(snapshot([*unstarred, STAGING, KEYBOARD, NEWSLETTER]), NOW)
    assert d.send.no_focus_yet
    assert offered(d) == ["Payments CI flakes", "Billing export service", "Billing export: staging deploy"]
    assert "STEPS (no Focus picked yet)" in d.send.text.splitlines()


def test_once_focus_was_picked_the_stand_in_never_returns():
    unstarred_later = replace(PAYMENTS, focus=False)
    d = rules.compose_daily(snapshot([unstarred_later, STAGING]), NOW)
    assert not d.send.no_focus_yet and d.send.steps == ()


def test_the_brief_is_planned_as_sending_and_planning_is_pure():
    snap = snapshot(WEEK)
    d = rules.compose_daily(snap, NOW)
    assert d.send.attempts == (SendAttempt(NOW, SendStatus.SENDING, ()),)
    assert not d.changes.is_empty()
    assert rules.compose_daily(snap, NOW).send == d.send


@pytest.mark.parametrize("found, again", [
    ((SendStatus.ARRIVAL_UNKNOWN,), True),
    ((SendStatus.SENDING,), True),
    ((SendStatus.SENT,), False),
    ((SendStatus.ARRIVAL_UNKNOWN, SendStatus.ARRIVAL_UNKNOWN), False),
    ((SendStatus.ARRIVAL_UNKNOWN, SendStatus.SENT), False),
])
def test_todays_file_is_sent_again_only_once_with_the_same_text(found, again):
    first = rules.compose_daily(snapshot(WEEK), NOW).send
    attempts = tuple(SendAttempt(at(MONDAY, f"07:{30 + 10 * n}"), s, ()) for n, s in enumerate(found))
    later = at(MONDAY, "09:00")
    d = rules.compose_daily(snapshot(WEEK, briefs=[replace(first, attempts=attempts)]), later)
    if not again:
        assert d.send is None and d.changes.is_empty()
        return
    assert d.send.text == first.text
    assert [a.status for a in d.send.attempts] == [SendStatus.ARRIVAL_UNKNOWN, SendStatus.SENDING]
    assert d.send.attempts[1].at == later
