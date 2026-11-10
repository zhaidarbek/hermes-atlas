from datetime import date

import pytest

from atlas import rules
from atlas.model import Status
from snapshots import SETTINGS, pursuit, snapshot

MONDAY = date(2026, 11, 16)


@pytest.mark.parametrize("since, days", [
    (date(2026, 10, 19), 28),
    (date(2026, 11, 2), 14),
    (date(2026, 11, 15), 1),
    (MONDAY, 0),
    (date(2025, 11, 16), 365),
])
def test_with_no_pause_every_day_after_the_start_counts(since, days):
    assert rules.clock_days(since, MONDAY, (), SETTINGS) == days


def test_neglected_is_active_outside_focus_and_quiet_14_days_quietest_first():
    snap = snapshot([
        pursuit("p-0new", "Agent-tooling newsletter", worked=[date(2026, 10, 19)]),
        pursuit("p-0bik", "Bike route planner", worked=[date(2026, 10, 25), date(2026, 10, 26)]),
        pursuit("p-0bak", "Home server backups", worked=[date(2026, 10, 29)]),
        pursuit("p-0two", "Exactly two weeks", worked=[date(2026, 11, 2)]),
        pursuit("p-0thr", "Thirteen days", worked=[date(2026, 11, 3)]),
        pursuit("p-0foc", "In Focus", worked=[date(2026, 10, 1)], focus=True),
        pursuit("p-0par", "Parked long ago", worked=[date(2026, 8, 30)], status=Status.PARKED),
        pursuit("p-0con", "Continued lately", worked=[date(2026, 10, 1)], continued=date(2026, 11, 10)),
        pursuit("p-0nam", "Named in words only"),
    ])
    assert [p.name for p in rules.neglected(snap, MONDAY)] == [
        "Agent-tooling newsletter", "Bike route planner", "Home server backups", "Exactly two weeks"]


def test_a_pursuit_named_in_words_only_is_quiet_from_its_creation():
    named = pursuit("p-0nam", "Named in words only")
    assert rules.quiet_since(named) == date(2026, 11, 15)
    assert [p.name for p in rules.neglected(snapshot([named]), date(2026, 11, 29))] == ["Named in words only"]
