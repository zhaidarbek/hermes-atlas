"""activetime as table tests (W Q13, D Q6). Pure: no record, no clock."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from atlas.activetime import Interval, TimedSession, day, stretches, undated
from atlas.model import ONE_OFF

BERLIN = ZoneInfo("Europe/Berlin")
MONDAY = date(2026, 11, 16)
GAP = timedelta(minutes=15)


def at(hm: str, on: date = MONDAY) -> datetime:
    return datetime.combine(on, time.fromisoformat(hm), tzinfo=BERLIN)


def ats(*hms: str) -> tuple[datetime, ...]:
    return tuple(at(hm) for hm in hms)


def span(start: str, end: str) -> Interval:
    return Interval(at(start).astimezone(timezone.utc), at(end).astimezone(timezone.utc))


@pytest.mark.parametrize("owner, agent, gap, want", [
    pytest.param(("09:00", "09:14"), ("09:16",), GAP, [("09:00", "09:16")],
                 id="messages less than the gap apart are one stretch"),
    pytest.param(("09:00", "09:15"), ("09:01", "09:16"), GAP, [("09:00", "09:01"), ("09:15", "09:16")],
                 id="messages the gap apart are two stretches"),
    pytest.param(("09:00",), (), GAP, [("09:00", "09:15")],
                 id="with no reply the stretch ends the gap after the owner's last message"),
    pytest.param(("09:00", "09:05"), ("09:02", "09:09"), GAP, [("09:00", "09:09")],
                 id="a stretch ends sooner at the agent's last reply"),
    pytest.param(("09:00",), ("09:02", "09:10", "09:40", "10:30"), GAP, [("09:00", "09:15")],
                 id="an agent working alone does not count"),
    pytest.param(("09:00", "09:30"), ("09:03", "09:33"), GAP, [("09:00", "09:03"), ("09:30", "09:33")],
                 id="the replies before the next message end a stretch"),
    pytest.param(("09:00", "09:20"), ("09:25",), timedelta(minutes=30), [("09:00", "09:25")],
                 id="a longer gap joins messages"),
    pytest.param(("09:00", "09:20"), ("09:25",), timedelta(minutes=10), [("09:00", "09:10"), ("09:20", "09:25")],
                 id="a shorter gap splits them"),
    pytest.param(("09:00",), ("09:00",), GAP, [],
                 id="a reply in the same moment as the message adds no time"),
    pytest.param((), ("09:00", "09:30"), GAP, [],
                 id="the agent alone, with no message from the owner"),
])
def test_stretches(owner, agent, gap, want):
    assert stretches(ats(*owner), ats(*agent), gap) == [span(*w) for w in want]


def session(id, owner, agent=(), work=(("work", "p-a"),)) -> TimedSession:
    return TimedSession(id, ats(*owner), ats(*agent), tuple(work))


def minutes(t: timedelta) -> int:
    return int(t.total_seconds() // 60)


def table(dt) -> tuple:
    return (minutes(dt.total), {a: {k: minutes(v) for k, v in split.items()} for a, split in dt.areas.items()},
            dt.basis)


WORK, B, HEALTH = (("work", "p-a"),), (("work", "p-b"),), (("health", "p-k"),)
ALONE_WORK, ALONE_FAMILY = (("work", ONE_OFF),), (("family", ONE_OFF),)


@pytest.mark.parametrize("sessions, want", [
    pytest.param([session("cc-1", ("09:00", "09:10"), ("09:20",))],
                 (20, {"work": {"p-a": 20}}, ("cc-1",)),
                 id="one Session, one Pursuit"),
    pytest.param([session("cc-1", ("09:00", "09:10"), ("09:20",)), session("cx-2", ("09:10",), ("09:25",))],
                 (25, {"work": {"p-a": 25}}, ("cc-1", "cx-2")),
                 id="parallel Sessions on one Pursuit count once, as a union"),
    pytest.param([session("cc-1", ("09:00",), ("09:10",)), session("cc-2", ("09:00",), ("09:10",)),
                  session("cx-3", ("09:00",), ("09:10",), work=B)],
                 (10, {"work": {"p-a": 5, "p-b": 5}}, ("cc-1", "cc-2", "cx-3")),
                 id="two Sessions on A and one on B at once give A and B half each"),
    pytest.param([session("cx-1", ("12:30", "12:40"), ("12:50",), work=WORK + B)],
                 (20, {"work": {"p-a": 10, "p-b": 10}}, ("cx-1",)),
                 id="one Session on two Pursuits splits its time evenly"),
    pytest.param([session("cc-1", ("10:00",), ("10:10",), work=ALONE_WORK),
                  session("cx-2", ("11:00", "11:10"), ("11:20",)),
                  session("gpt-3", ("19:10", "19:20", "19:30", "19:40"), ("19:50",), work=HEALTH)],
                 (70, {"work": {"p-a": 20, ONE_OFF: 10}, "health": {"p-k": 40}}, ("cc-1", "cx-2", "gpt-3")),
                 id="a one-off is its Area's own, shown after the Area's Pursuits"),
    pytest.param([session("cc-1", ("10:00",), ("10:10",), work=ALONE_WORK),
                  session("cc-2", ("11:00",), ("11:05",), work=ALONE_FAMILY)],
                 (15, {"work": {ONE_OFF: 10}, "family": {ONE_OFF: 5}}, ("cc-1", "cc-2")),
                 id="one-offs in two Areas stay apart"),
    pytest.param([session("cc-1", ("09:00",), ("09:10",), work=WORK + B + (("home", "p-c"),))],
                 (10, {"work": {"p-a": 4, "p-b": 3}, "home": {"p-c": 3}}, ("cc-1",)),
                 id="largest remainder rounding adds up to the day"),
    pytest.param([session("cc-1", ("09:00",), ("09:10",), work=())], (0, {}, ()),
                 id="a Session filed nowhere adds nothing"),
    pytest.param([session("cc-1", (), ("09:00", "09:40"))], (0, {}, ()),
                 id="an agent alone adds nothing and is not the day's Evidence"),
])
def test_day(sessions, want):
    assert table(day(sessions, MONDAY, "Europe/Berlin", GAP)) == want


def test_a_changed_gap_setting_changes_the_day():
    s = [session("cc-1", ("09:00", "09:20"), ("09:25",))]
    assert minutes(day(s, MONDAY, "Europe/Berlin", timedelta(minutes=30)).total) == 25
    assert minutes(day(s, MONDAY, "Europe/Berlin", timedelta(minutes=10)).total) == 15


def test_a_stretch_across_midnight_is_cut_at_the_day():
    late = TimedSession("cc-1", (at("23:50"),), (at("00:05", MONDAY + timedelta(days=1)),), (("work", "p-a"),))
    assert table(day([late], MONDAY, "Europe/Berlin", GAP)) == (10, {"work": {"p-a": 10}}, ("cc-1",))
    assert table(day([late], MONDAY + timedelta(days=1), "Europe/Berlin", GAP)) == (5, {"work": {"p-a": 5}}, ("cc-1",))


def test_the_repeated_hour_of_a_dst_fallback_counts_as_it_happened():
    first = datetime(2026, 10, 25, 2, 52, tzinfo=BERLIN)
    second = datetime(2026, 10, 25, 2, 5, fold=1, tzinfo=BERLIN)
    reply = (second.astimezone(timezone.utc) + timedelta(minutes=5)).astimezone(BERLIN)
    s = TimedSession("cc-1", (first, second), (reply,), (("work", "p-a"),))
    assert table(day([s], date(2026, 10, 25), "Europe/Berlin", GAP)) == (18, {"work": {"p-a": 18}}, ("cc-1",))


def test_seconds_count_toward_the_gap():
    s = TimedSession("cc-1", (at("09:00"), at("09:14") + timedelta(seconds=59)), (at("09:20"),), (("work", "p-a"),))
    assert minutes(day([s], MONDAY, "Europe/Berlin", GAP).total) == 20


T = datetime(2026, 11, 16, 19, 0, tzinfo=BERLIN)


def m(n: int) -> datetime:
    return T + timedelta(minutes=n)


@pytest.mark.parametrize("times, created, updated, want", [
    pytest.param([m(0), None, m(10)], None, None, [m(0), m(5), m(10)],
                 id="between the nearest timed messages on the branch"),
    pytest.param([m(0), None, None, m(30)], None, None, [m(0), m(10), m(20), m(30)],
                 id="several between the same timed messages"),
    pytest.param([None, m(10)], m(0), None, [m(5), m(10)],
                 id="no timed message before it: the conversation's create time bounds it"),
    pytest.param([m(0), None], None, m(20), [m(0), m(10)],
                 id="no timed message after it: the conversation's update time bounds it"),
    pytest.param([None, None, None], m(0), m(40), [m(10), m(20), m(30)],
                 id="no timed message at all: create and update times bound every message"),
    pytest.param([None, m(10)], None, None, [None, m(10)],
                 id="no bound before it: no time"),
    pytest.param([m(0), None], None, None, [m(0), None],
                 id="no bound after it: no time"),
    pytest.param([None], None, m(10), [None],
                 id="an update time alone: no time"),
])
def test_undated_messages(times, created, updated, want):
    assert undated(times, created, updated) == want
