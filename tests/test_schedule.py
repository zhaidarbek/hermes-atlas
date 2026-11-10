from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from atlas import schedule
from atlas.model import Pause, SendAttempt, SendStatus, SentBrief
from snapshots import at, snapshot

MONDAY, TUESDAY = date(2026, 11, 16), date(2026, 11, 17)
SENDING, SENT, UNKNOWN = SendStatus.SENDING, SendStatus.SENT, SendStatus.ARRIVAL_UNKNOWN


def brief(day: date, *statuses: SendStatus) -> SentBrief:
    attempts = tuple(SendAttempt(at(day, f"07:{30 + 10 * n}"), s, ()) for n, s in enumerate(statuses))
    return SentBrief(day, attempts, None, (), (), None, False, "Atlas · Daily Brief")


@pytest.mark.parametrize("now, briefs, expected", [
    (at(MONDAY, "07:29"), [], []),
    (at(MONDAY, "07:30"), [], [MONDAY]),
    (datetime(2026, 11, 16, 6, 30, tzinfo=timezone.utc), [], [MONDAY]),
    (at(MONDAY, "23:40"), [], [MONDAY]),
    (at(TUESDAY, "00:10"), [], []),
    (at(TUESDAY, "07:30"), [], [TUESDAY]),
    (at(MONDAY, "09:00"), [brief(MONDAY, SENT)], []),
    (at(MONDAY, "09:00"), [brief(MONDAY, UNKNOWN)], [MONDAY]),
    (at(MONDAY, "09:00"), [brief(MONDAY, SENDING)], [MONDAY]),
    (at(MONDAY, "09:00"), [brief(MONDAY, UNKNOWN, SENT)], []),
    (at(MONDAY, "09:00"), [brief(MONDAY, UNKNOWN, UNKNOWN)], []),
    (at(MONDAY, "09:00"), [brief(MONDAY, UNKNOWN, SENDING)], []),
    (at(TUESDAY, "00:10"), [brief(MONDAY, UNKNOWN)], []),
], ids=["before-the-time", "at-the-time", "at-the-time-given-in-utc", "23:40-after-downtime",
        "after-midnight-yesterday-never-sent", "next-morning", "todays-file-sent", "arrival-unknown-once",
        "sending-left-by-a-crash", "retry-sent", "arrival-unknown-twice", "crash-during-the-retry",
        "after-midnight-no-retry-of-yesterday"])
def test_the_brief_is_due_once_a_day_from_its_time_and_retried_once(now, briefs, expected):
    assert schedule.due(snapshot(briefs=briefs), now) == [schedule.BriefDue(d) for d in expected]


def test_an_arrival_unknown_brief_is_retried_exactly_once():
    once = brief(MONDAY, UNKNOWN)
    assert schedule.due(snapshot(briefs=[once]), at(MONDAY, "07:40")) == [schedule.BriefDue(MONDAY)]
    twice = replace(once, attempts=(*once.attempts, SendAttempt(at(MONDAY, "07:40"), UNKNOWN, ())))
    for later in ("07:50", "12:00", "23:50"):
        assert schedule.due(snapshot(briefs=[twice]), at(MONDAY, later)) == []


def test_nothing_is_due_while_paused():
    away = Pause(at(MONDAY, "00:00"), at(TUESDAY, "07:00"), "away")
    assert schedule.due(snapshot(pauses=[away]), at(MONDAY, "07:30")) == []
    assert schedule.due(snapshot(pauses=[away]), at(TUESDAY, "07:30")) == [schedule.BriefDue(TUESDAY)]


@pytest.mark.parametrize("lease", [schedule.TickLease, schedule.SendLease])
def test_a_lease_never_waits_for_its_holder(tmp_path, lease):
    with lease(tmp_path) as first:
        assert first.held
        with lease(tmp_path) as second:
            assert not second.held
    with lease(tmp_path) as again:
        assert again.held


def test_the_tick_and_send_leases_are_separate_locks(tmp_path):
    with schedule.TickLease(tmp_path) as tick, schedule.SendLease(tmp_path) as send:
        assert tick.held and send.held
