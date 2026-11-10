"""Answers to the questions the owner asks Atlas in a Channel, as the text Atlas replies with.
Pure: a Snapshot and values in, text out. Reading never changes the record: the only write a
question leads to is the conversation Note (rules.converse), and a Note never counts as work on
a Pursuit (W Q14).

  ResumeQ      where the owner left off on one Pursuit, ending with messages.RESUME_HINT (D Q2)
  PeriodTimeQ  Active Time by Area over the Journal's days, today counted live (D Q9)
  FindQ        the Session the model picked from the index's hits, its Pursuit and the grounded line
  OutsideAction  always refused (OUTSIDE_WORLD); the status of the Pursuit it names, to forward
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Mapping, Sequence

from . import messages
from .messages import RESUME_HINT, Bare, Style
from .model import (ONE_OFF, Cite, DayTime, EvidenceId, Fact, Inference, OutsideAction, Pursuit, SessionEvidence,
                    Statement, harness_of)
from .record import Snapshot

CANNOT_SEND = "I can't send anything to other people. I only read, record, and message you."
TO_FORWARD = "Here is the status to forward yourself:"
FOUND_NOTHING = "I found no Session that answers that."
WIDTH = 80                                   # a row of the time table wraps here, under its Pursuits
SAID = 300                                   # the most of the model's sentence a find answer shows


def refusal(snap: Snapshot, action: OutsideAction) -> str:
    """Atlas never acts outside its record. When the words name a Pursuit, the reply adds its
    status for the owner to forward, in the owner's words for it; text meant to leave Atlas, so
    its mark is bare (G1)."""
    p = closest(action.about, snap.pursuits()) if action.about else None
    if p is None or not p.resume.left_off:
        return CANNOT_SEND
    status = " ".join(s.text for s in p.resume.left_off)
    mark = "[F]" if all(isinstance(s, Fact) for s in p.resume.left_off) else "[I]"
    label = action.about[:1].upper() + action.about[1:]
    return "\n".join([CANNOT_SEND, TO_FORWARD, f'"{label}: {status}" {mark}'])


def closest(words: str, pursuits: Sequence[Pursuit]) -> Pursuit | None:
    """The Pursuit the words name: its exact name, else the one Pursuit whose name holds each of
    the words with the fewest words besides ("billing export" names "Billing export service"
    rather than "Billing export: staging deploy"). None when nothing or a tie matches."""
    exact = [p for p in pursuits if p.name.casefold() == words.strip().casefold()]
    if len(exact) == 1:
        return exact[0]
    wanted = set(_words(words))
    found = sorted(((len(set(_words(p.name)) - wanted), p) for p in pursuits if wanted and wanted <= set(_words(p.name))),
                   key=lambda pair: pair[0])
    if not found or (len(found) > 1 and found[0][0] == found[1][0]):
        return None
    return found[0][1]


def resume(snap: Snapshot, p: Pursuit, now: datetime) -> str:
    """Where the owner left off on `p`, from its Resume Note, marked by the G1 rule: Where is a
    record Fact, so bare; the first Fact read from one Session cites it."""
    head = [p.name, p.kind.value, p.status.value]
    if p.last_worked is not None:
        head.append(f"last Session {_day(p.last_worked, now, weekday=True)}")
    parts: list[tuple[str, Statement | Bare]] = [("Where: ", Bare(s)) for s in p.resume.where]
    for label, part in (("Left off: ", p.resume.left_off), ("Decided: ", p.resume.decided),
                        ("Open question: ", p.resume.open_questions)):
        parts += [(label, s) for s in part]
    if p.next_step is not None:
        parts.append(("Next Step: ", Inference(_lowered(p.next_step.text.text), p.next_step.text.basis)))
    marked = messages.item([s for _, s in parts], Style.TELEGRAM, now)
    lines = [" · ".join(head), *(label + line for (label, _), line in zip(parts, marked))]
    if p.resume.reopen:
        lines.append("Sessions to reopen: " + ", ".join(_reopen(snap, id, now) for id in p.resume.reopen))
    return "\n".join([*lines, RESUME_HINT])


def week_time(start: date, end: date, days: Sequence[DayTime], names: Mapping[str, str]) -> str:
    """Active Time by Area from `start` to `end`: each Area with its total and its Pursuits, a
    one-off Session's time last. Areas and Pursuits run from most time to least. Counted Facts,
    so the mark is a bare [F] (D Q9)."""
    areas: dict[str, dict[str, timedelta]] = {}
    for day in days:
        for area, split in day.areas.items():
            for key, t in split.items():
                areas.setdefault(area, {})
                areas[area][key] = areas[area].get(key, timedelta()) + t
    span = f"{start:%a} {start.day}" + ("" if start.month == end.month else f" {start:%b}")
    lines = [f"Active time with AI agents, {span} to {end:%a} {end.day} {end:%b} [F]"]
    totals = {a: sum(split.values(), timedelta()) for a, split in areas.items()}
    width = max(map(len, totals), default=0) + 3
    for area in sorted((a for a, t in totals.items() if t), key=lambda a: (-totals[a], a)):
        split = areas[area]
        keys = sorted((k for k, t in split.items() if t), key=lambda k: (k == ONE_OFF, -split[k], names.get(k, k)))
        pursuits = ", ".join(f"{names.get(k, k)} {duration(split[k])}" for k in keys)
        lead = f"{area:<{width}}{duration(totals[area]):>7}   "
        lines += textwrap.wrap(pursuits, WIDTH, initial_indent=lead, subsequent_indent=" " * len(lead),
                               break_on_hyphens=False)
    if len(lines) == 1:
        lines.append("Nothing counted yet.")
    return "\n".join(lines)


def duration(t: timedelta) -> str:
    """"13 h 40", "4 h 05", "40 min"."""
    minutes = int(t.total_seconds() // 60)
    return f"{minutes // 60} h {minutes % 60:02d}" if minutes >= 60 else f"{minutes} min"


def found(snap: Snapshot, s: SessionEvidence, line: Statement | None, now: datetime) -> str:
    """The Session that answers a FindQ: where and when, the Pursuit that lists it even when
    ended, and the line the model drew from it, cut at a word past SAID characters. Each line
    names its source, so marks are bare."""
    at = (line.source.at if isinstance(line, Fact) and isinstance(line.source, Cite) else s.started).astimezone(now.tzinfo)
    label = s.harness.label + (f" via {s.interface}" if s.interface else "")
    year = "" if at.year == now.year else f" {at.year}"
    where = f", {s.cwd}" if s.cwd else ""
    lines = [f"{label}, {at:%a} {at.day} {at:%b}{year} {at:%H:%M}{where} [F]"]
    p = next((p for p in snap.pursuits() if any(m.evidence == s.id for m in p.sessions)), None)
    if p is not None:
        lines.append(f"Pursuit: {p.name} ({p.status.value}) [F]")
    if line is not None:
        lines.append(messages.statement(replace(line, text=textwrap.shorten(line.text, SAID, placeholder=" …")),
                                        Style.TELEGRAM))
    return "\n".join(lines)


def _reopen(snap: Snapshot, id: EvidenceId, now: datetime) -> str:
    ref = snap.session_ref(id)
    harness = harness_of(id)
    return f'{harness.label if harness else ref.label} "{ref.title}" ({_day(ref.day, now)})'


def _day(day: date, now: datetime, weekday: bool = False) -> str:
    """"Sat 14 Nov" within a week, else "30 Aug" ("Sun 30 Aug" with `weekday`), with the year
    when it is not this one."""
    recent = 0 <= (now.date() - day).days < 7
    text = (f"{day:%a} " if weekday or recent else "") + f"{day.day} {day:%b}"
    return text + ("" if day.year == now.year else f" {day.year}")


def _lowered(step: str) -> str:
    return step[:1].lower() + step[1:] if step[1:2].islower() else step


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", text.casefold()) if w not in ("the", "my", "a", "an")]
