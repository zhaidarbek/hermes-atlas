"""The Profile pass (G3): what a model may add to the Profile, read and placed by fixed rules.

Backfill asks once over the whole history, while the Profile has no entry. Each day the tick
first writes a closed Journal day, it asks once about that day. An answer adds at most
MAX_NEW entries. Each new entry rests on Sessions the model was shown: an Inference on the
ones it names, or a Pattern drawn from every Session in the input. Each goes after the
numbered entry it names. An entry with a text the Profile already holds, or one the owner
corrected away, is left out, and so is one whose text the file could read as its own marks. An
answer that names an entry, a Session or a position the input does not hold is unusable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, tzinfo
from typing import Iterable, Mapping, Sequence

from .model import EvidenceId, Inference, Pattern, Profile, ProfileEntry, Pursuit, SentBrief, SessionEvidence, \
    Statement, Who

MAX_NEW = 3
_MARKS = re.compile(r"[\[\]\\·]|<!--|-->")

INSTRUCTIONS = """You keep the owner's Profile: a few short lines about how they work, there only to make
their Daily Brief better (which Steps to offer, when). Read the input and answer with the entries to add,
none when nothing new is clear. Each entry is one sentence in the second person ("You ..."). Name the
Sessions it rests on by their ids, or give `pattern` a short description of what it was drawn from
("131 days of Sessions") when it rests on the whole input. `after` is the number of the entry it goes
after, 0 for the top, null for the end. Never repeat an entry the Profile holds, and never add again a text
the owner corrected away."""

SCHEMA = {"title": "profile", "type": "object", "required": ["entries"], "properties": {
    "entries": {"type": "array", "items": {"type": "object", "required": ["text", "sessions", "pattern", "after"],
                                           "properties": {
        "text": {"type": "string"},
        "sessions": {"type": "array", "items": {"type": "string"}},
        "pattern": {"anyOf": [{"type": "null"}, {"type": "string"}]},
        "after": {"anyOf": [{"type": "null"}, {"type": "integer"}]}}}}}}


@dataclass(frozen=True)
class NewEntry:
    statement: Statement
    after: int | None


def _session(s: SessionEvidence) -> str:
    label = s.harness.label + (f" via {s.interface}" if s.interface else "")
    return f"Session {s.id} · {label} · {s.started:%Y-%m-%d %H:%M} to {s.last_at:%Y-%m-%d %H:%M}"


def on_day(s: SessionEvidence, day: date, zone: tzinfo) -> bool:
    """Whether `s` began or has a turn on `day` in the owner's zone, as the Journal counts a day."""
    return s.started.astimezone(zone).date() == day or any(t.at and t.at.astimezone(zone).date() == day
                                                           for t in s.turns)


def _entries(profile: Profile, corrected: Iterable[str]) -> list[str]:
    whose = {Who.OWNER: " (the owner's)"}
    return ([f"Profile entry {n}{whose.get(e.by, '')}: {e.statement.text}" for n, e in enumerate(profile.entries, 1)]
            or ["Profile: empty"]) + [f"The owner corrected this away; never add it again: {t}" for t in corrected]


def _key(text: str) -> str:
    """A text as its words alone, so case, spacing and punctuation never tell two texts apart."""
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def history_input(profile: Profile, pursuits: Sequence[Pursuit], sessions: Mapping[EvidenceId, SessionEvidence],
                  budget: int, corrected: Iterable[str] = ()) -> str:
    """The whole history, newest Sessions first once `budget` characters run out: each Pursuit
    with its Sessions, then the Sessions no Pursuit holds."""
    lines = [*_entries(profile, corrected), ""]
    filed: set[EvidenceId] = set()
    for p in pursuits:
        lines.append(f"Pursuit {p.name} · {p.kind.value} · {p.status.value} · {p.area}")
        for m in reversed(p.sessions):
            if m.evidence in sessions:
                lines.append("  " + _session(sessions[m.evidence]))
                filed.add(m.evidence)
    rest = sorted((s for id, s in sessions.items() if id not in filed), key=lambda s: s.started, reverse=True)
    lines += ["One-off Sessions:", *("  " + _session(s) for s in rest)]
    text = ""
    for line in lines:
        if len(text) + len(line) + 1 > budget:
            break
        text += line + "\n"
    return text


def day_input(profile: Profile, day: date, brief: SentBrief | None, pursuits: Mapping[str, Pursuit],
              journal: Sequence[str], sessions: Mapping[EvidenceId, SessionEvidence],
              corrected: Iterable[str] = (), *, zone: tzinfo) -> str:
    """One closed day: the Steps its brief offered and how each ended, its Journal lines, and the
    Sessions of the day and of each Pursuit the brief named."""
    lines = [*_entries(profile, corrected), "", f"Day: {day:%A %d %B %Y}"]
    for item in brief.steps if brief else ():
        p = pursuits.get(item.pursuit)
        step = next((s for s in (p.next_step, *p.past_steps) if s and s.id == item.step), None) if p else None
        if step is None:
            continue
        state = "open" if step.ended is None else f"{type(step.ended).__name__.lower()} ({step.ended.how})"
        lines.append(f"Step {item.n} · {p.name} · {step.text.text} · {state}")
        lines += ["  " + _session(sessions[m.evidence]) for m in p.sessions if m.evidence in sessions]
    lines += ["Journal:", *("  " + line for line in journal)]
    lines += ["Sessions that day:", *("  " + _session(s) for s in sessions.values() if on_day(s, day, zone))]
    return "\n".join(lines) + "\n"


def read(out: dict, profile: Profile, shown: Mapping[EvidenceId, SessionEvidence],
         corrected: Iterable[str] = ()) -> tuple[NewEntry, ...]:
    """The answer's new entries, grounded on the Sessions the input showed. Raises ValueError
    for an answer Atlas cannot use."""
    rows = out["entries"]
    if not isinstance(rows, list) or len(rows) > MAX_NEW:
        raise ValueError("more new Profile entries than one pass may add")
    left_out = {_key(e.statement.text) for e in profile.entries} | {_key(t) for t in corrected}
    new: list[NewEntry] = []
    for row in rows:
        text, after, pattern, sessions = row["text"], row["after"], row["pattern"], row["sessions"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("a Profile entry with no text")
        if after is not None and (not isinstance(after, int) or isinstance(after, bool)
                                  or not 0 <= after <= len(profile.entries)):
            raise ValueError("a Profile entry placed after an entry the Profile does not hold")
        if not isinstance(sessions, list) or not all(isinstance(i, str) for i in sessions) or not (
                pattern is None or isinstance(pattern, str)):
            raise ValueError("a Profile entry whose Sessions or pattern are not texts")
        if _MARKS.search(text) or (pattern and _MARKS.search(pattern)) or _key(text) in left_out:
            continue
        ids = tuple(dict.fromkeys(EvidenceId(i) for i in sessions))
        if any(i not in shown for i in ids):
            raise ValueError("a Profile entry resting on a Session the input did not show")
        if pattern:
            days = [s.started.date() for s in shown.values()]
            statement = Inference(text, Pattern(pattern, min(days), max(days), tuple(shown)))
        elif ids:
            statement = Inference(text, ids)
        else:
            raise ValueError("a Profile entry resting on nothing")
        left_out.add(_key(text))
        new.append(NewEntry(statement, after))
    return tuple(new)


def added(profile: Profile, new: Iterable[NewEntry], today: date) -> Profile:
    """`profile` with each new entry after the one it names, as the Librarian's, added today. A
    text the Profile already holds is not added twice."""
    held = {e.statement.text for e in profile.entries}
    slots: list[list[ProfileEntry]] = [[] for _ in range(len(profile.entries) + 1)]
    for entry in new:
        if entry.statement.text in held:
            continue
        held.add(entry.statement.text)
        at = len(profile.entries) if entry.after is None else min(entry.after, len(profile.entries))
        slots[at].append(ProfileEntry(entry.statement, today, Who.LIBRARIAN))
    entries = [*slots[0]]
    for n, e in enumerate(profile.entries, 1):
        entries += [e, *slots[n]]
    return replace(profile, entries=tuple(entries))
