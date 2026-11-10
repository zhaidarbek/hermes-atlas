"""Corrections to Journal days already written, as pure functions: a Session that reaches Atlas
late (a web-chat export, or one filed after its day closed) adds to a day the Journal has, and
the day says so in a line derived from what its Evidence gained:

    Corrected Wed 18 Nov: added ChatGPT 19:10 (40 min) when your ChatGPT export arrived.

The date is the day of the correction; each Session added names its Harness, its first time that
day and the Active Time it adds to the day.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from . import activetime
from .model import EvidenceId, JournalDay
from .record import Snapshot


def late_days(snap: Snapshot, filed: Iterable[EvidenceId], journaled: set[date], before: date) -> set[date]:
    """The days before `before` that the Journal has written and a Session in `filed` has a turn on."""
    zone = ZoneInfo(snap.settings().timezone)
    days: set[date] = set()
    for id in filed:
        try:
            s = snap.session(id)
        except Exception:
            continue
        days |= {t.at.astimezone(zone).date() for t in s.turns if t.at is not None}
    return {d for d in days if d in journaled and d < before}


def line(snap: Snapshot, current: JournalDay | None, sessions: Sequence[activetime.TimedSession], day: date,
         now: datetime) -> str | None:
    """The correction for `day` as `sessions` count it now, against the file the Journal has: None
    when there is no file yet or it already counts every Session that adds time to the day."""
    if current is None:
        return None
    settings = snap.settings()
    zone = ZoneInfo(settings.timezone)
    time = activetime.day(sessions, day, settings.timezone, settings.gap)
    added = [id for id in time.basis if id not in current.evidence]
    if not added:
        return None
    clauses = []
    for id in added:
        s = snap.session(id)
        first = min((t.at.astimezone(zone) for t in s.turns if t.at and t.at.astimezone(zone).date() == day),
                    default=s.started.astimezone(zone))
        alone = time.total - activetime.day([t for t in sessions if t.id != id], day, settings.timezone,
                                            settings.gap).total
        label = s.harness.label + (f" via {s.interface}" if s.interface else "")
        late = f" when your {s.harness.label} export arrived" if s.harness in settings.late_harnesses else ""
        clauses.append(f"added {label} {first:%H:%M} ({duration(alone)}){late}")
    today = now.astimezone(zone).date()
    return f"{today:%a} {today.day} {today:%b}: {'; '.join(clauses)}."


def duration(t: timedelta) -> str:
    """"40 min", "1 h 10 min", "2 h", as the Journal writes a day's time."""
    h, m = divmod(int(t.total_seconds() // 60), 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"
