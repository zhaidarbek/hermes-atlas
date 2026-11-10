"""A Pause (W Q17, ADR 0006): the owner's words for its end, the Pauses after "pause" or "I'm
back", and what Atlas answers. Pure: no record, no clock but the ones passed in.

Ends the owner can give after "until": a weekday ("sunday", "next sunday"), "tomorrow", a date
("30 nov", "nov 30"), each with an optional part of the day ("morning" 07:00, "noon", "afternoon",
"evening", "night") or a time ("9:00", "9am"), or a time alone (the next one to come). After
"for": a number of days or weeks. A day with no time ends at its start, so that day's brief comes.
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from .model import Pause, PauseCmd, Settings

log = logging.getLogger(__name__)

DEFAULT = timedelta(days=7)
"""A Pause given no end lasts a week, and the reply says so (W Q17)."""
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
PARTS = {"morning": time(7), "noon": time(12), "afternoon": time(13), "evening": time(18), "night": time(21)}
COUNTS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10,
          "fourteen": 14}

_CLOCK = r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?"
_MONTH = r"(?:%s)[a-z]*\.?" % "|".join(MONTHS)
_DAY = re.compile(
    rf"(?:(?P<next>next\s+)?(?P<weekday>{'|'.join(WEEKDAYS)})|(?P<tomorrow>tomorrow)"
    rf"|(?P<d>\d{{1,2}})\s+(?P<month>{_MONTH})|(?P<month2>{_MONTH})\s+(?P<d2>\d{{1,2}}))"
    rf"(?:\s+(?:in\s+the\s+|at\s+)?(?:(?P<part>{'|'.join(PARTS)})|{_CLOCK}))?", re.I)
_TIME = re.compile(rf"(?:at\s+)?{_CLOCK}", re.I)
_FOR = re.compile(r"(?P<n>\d+|%s)\s+(?P<unit>days?|weeks?)" % "|".join(COUNTS), re.I)


def until(words: str, now: datetime, tz: ZoneInfo) -> datetime | None:
    """The end the words give after "until", in `tz`, when it is after `now`; else None. A weekday
    is the next one whose time is still to come; "next" skips today's."""
    words = " ".join(words.split())
    local = now.astimezone(tz)
    if m := _TIME.fullmatch(words):
        at = _clock(m["h"], m["m"], m["ampm"])
        if at is None:
            return None
        end = datetime.combine(local.date(), at, tz)
        if end <= now and m["m"] is None and m["ampm"] is None and 1 <= at.hour < 12:
            evening = datetime.combine(local.date(), at.replace(hour=at.hour + 12), tz)
            if evening > now:
                return evening
        return end if end > now else datetime.combine(local.date() + timedelta(days=1), at, tz)
    m = _DAY.fullmatch(words)
    if m is None:
        return None
    at = PARTS[m["part"].lower()] if m["part"] else _clock(m["h"], m["m"], m["ampm"]) if m["h"] else time()
    if at is None:
        return None
    if m["weekday"]:
        day = local.date() + timedelta(days=(WEEKDAYS.index(m["weekday"].lower()) - local.weekday()) % 7)
        if datetime.combine(day, at, tz) <= now or (m["next"] and day == local.date()):
            day += timedelta(days=7)
    elif m["tomorrow"]:
        day = local.date() + timedelta(days=1)
    else:
        day = _date(m["d"] or m["d2"], m["month"] or m["month2"], local.date())
        if day is None:
            return None
    end = datetime.combine(day, at, tz)
    return end if end > now else None


def lasting(words: str, now: datetime) -> datetime | None:
    """The end the words give after "for": "3 days", "a week", "two weeks"."""
    m = _FOR.fullmatch(" ".join(words.split()))
    if m is None:
        return None
    n = int(m["n"]) if m["n"].isdigit() else COUNTS[m["n"].lower()]
    days = n * (7 if m["unit"].lower().startswith("week") else 1)
    return now + timedelta(days=days) if timedelta() < timedelta(days=days) <= LONGEST else None


def _clock(h: str, m: str | None, ampm: str | None) -> time | None:
    hour, minute = int(h), int(m or 0)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
    return time(hour, minute) if hour < 24 and minute < 60 else None


def _date(d: str, month: str, today: date) -> date | None:
    """The day this year, or next year's when this year's has gone and next year's is near ("3 jan"
    said in December). A day long gone is likely a slip, so it gives no end."""
    for year in (today.year, today.year + 1):
        try:
            day = date(year, MONTHS.index(month[:3].lower()) + 1, int(d))
        except ValueError:
            return None
        if today <= day <= today + NEAR:
            return day
    return None


NEAR = timedelta(days=92)
LONGEST = timedelta(days=366)
"""The longest Pause the words can give ("for 366 days"); a dated end is at most NEAR away. A
Pause in pauses.md that lasts longer was not written by Atlas and is ignored."""


def kept(read) -> tuple[Pause, ...]:
    """The Pauses `read()` gives (Snapshot.pauses), or none when pauses.md cannot be read, without
    those lasting longer than LONGEST. Nothing in pauses.md may stop the tick or silence Atlas for
    good: either case logs one line naming the error type."""
    try:
        pauses = tuple(read())
    except Exception as failed:
        log.warning("Atlas could not read pauses.md (%s); it reads as no Pause", type(failed).__name__)
        return ()
    if any(p.end - p.start > LONGEST for p in pauses):
        log.warning("Atlas ignores a Pause in pauses.md longer than %d days", LONGEST.days)
    return tuple(p for p in pauses if p.end - p.start <= LONGEST)


def active(pauses: Sequence[Pause], at: datetime) -> Pause | None:
    return next((p for p in pauses if p.start <= at < p.end), None)


def brief_from(day: date, pauses: Sequence[Pause], s: Settings) -> datetime | None:
    """The first moment of `day` the Daily Brief may go out: its brief time, or the end of the
    Pause running then when that end falls on the same day. None when Pauses cover the rest of the
    day. schedule.due sends by it, and the Pause's reply names it."""
    tz = ZoneInfo(s.timezone)
    at = datetime.combine(day, s.brief_at, tz)
    while (running := active(pauses, at)) is not None:
        at = running.end.astimezone(tz)
        if at.date() != day:
            return None
    return at


def pause(pauses: Sequence[Pause], cmd: PauseCmd, now: datetime, s: Settings) -> tuple[tuple[Pause, ...], str]:
    """A new Pause from now. One already running ends now, so the new end replaces its end and the
    record keeps both sets of words."""
    end = cmd.until or now + DEFAULT
    kept = tuple(replace(p, end=now) if p.start <= now < p.end else p for p in pauses)
    new = Pause(now, end, cmd.said)
    return (*kept, new), said_paused((*kept, new), now, s, cmd.until is None)


def end_pause(pauses: Sequence[Pause], now: datetime, s: Settings, briefed: bool) -> tuple[tuple[Pause, ...], str]:
    """The running Pause ends now. With none running nothing changes. `briefed`: today's brief
    file exists."""
    running = active(pauses, now)
    if running is None:
        return tuple(pauses), "Atlas is not paused."
    kept = tuple(replace(p, end=now) if p is running else p for p in pauses)
    return kept, f"Welcome back. The Pause has ended. {_next_brief(now, s, briefed)}"


def said_paused(pauses: Sequence[Pause], now: datetime, s: Settings, default: bool) -> str:
    """"Paused until Sun 22 Nov 07:00. No Daily Brief on Saturday, and no Pursuit becomes Neglected
    while Atlas is paused. Write to me to come back early." With no end given it says the week. The
    last of `pauses` is the new one. When it ends after a day's brief time, that day's brief comes
    when it ends, and the reply names the moment."""
    p, tz = pauses[-1], ZoneInfo(s.timezone)
    end = p.end.astimezone(tz)
    head = (f"Paused for one week, until {_when(end)}, since you gave no end." if default
            else f"Paused until {_when(end)}.")
    first = _first_brief(pauses, now, s)
    late = first is not None and first.time() != s.brief_at
    briefs = [d for d in _covered(p, now, s.brief_at, tz, lambda d: True) if first is None or d < first.date()]
    reviews = _covered(p, now, s.review_at, tz, lambda d: d.isoweekday() == s.review_day)
    silent = f"No Daily Brief {_days(briefs)}" if briefs else "" if late else "No Daily Brief falls in it"
    if reviews:
        silent += f" and no Review {_days(reviews)}" if silent else f"No Review {_days(reviews)}"
    neglect = "no Pursuit becomes Neglected while Atlas is paused."
    said = f"{head} {silent}, and {neglect}" if silent else f"{head} {neglect[0].upper()}{neglect[1:]}"
    if late:
        said += f" The next Daily Brief comes on {first:%a} {first.day} {first:%b} at {first:%H:%M}, when the Pause ends."
    return f"{said} Write to me to come back early."


def _first_brief(pauses: Sequence[Pause], now: datetime, s: Settings) -> datetime | None:
    tz = ZoneInfo(s.timezone)
    start, last = now.astimezone(tz).date(), pauses[-1].end.astimezone(tz).date()
    for n in range((last - start).days + 2):
        at = brief_from(start + timedelta(days=n), pauses, s)
        if at is not None and at >= now:
            return at
    return None


def _covered(p: Pause, now: datetime, at: time, tz: ZoneInfo, wanted) -> list[date]:
    first, last = max(p.start, now).astimezone(tz).date(), p.end.astimezone(tz).date()
    days = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return [d for d in days if wanted(d) and max(p.start, now) <= datetime.combine(d, at, tz) < p.end]


def _days(days: list[date]) -> str:
    named = len(days) <= 7
    name = (lambda d: f"{d:%A}") if named else (lambda d: f"{d:%a} {d.day} {d:%b}")
    if len(days) == 1:
        return f"on {name(days[0])}"
    if len(days) == 2:
        return f"on {name(days[0])} or {name(days[1])}"
    return f"from {name(days[0])} to {name(days[-1])}"


def _next_brief(now: datetime, s: Settings, briefed: bool) -> str:
    local = now.astimezone(ZoneInfo(s.timezone))
    if local.time() < s.brief_at:
        return f"Your Daily Brief comes today at {s.brief_at:%H:%M}."
    if not briefed:
        return "Today's Daily Brief comes within 10 minutes."
    return f"Your next Daily Brief comes tomorrow at {s.brief_at:%H:%M}."


def _when(at: datetime) -> str:
    return f"{at:%a} {at.day} {at:%b} {at:%H:%M}"
