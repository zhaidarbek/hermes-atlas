"""Active Time (W Q13). Pure. Inputs are aware datetimes; every Harness's timestamp format is
parsed by its adapter, never here, and every comparison is between instants, so a turn in the
repeated hour of a DST fallback counts where it happened.

Per-Harness timestamp source (done in sources/_adapters.py):
  Claude Code  owner: top-level user record `timestamp` (human/typed/sdk-under-T3, not meta, not
               tool result); agent: assistant record `timestamp`
  Codex        owner: item_completed UserMessage started_at_ms (top-level threads only);
               agent: AgentMessage completed_at_ms
  Devin        owner: message_nodes role=user, is_user_input, no subagent/handoff; created_at
  Hermes       messages.timestamp of the rows Hermes shows (active=1, or archived by a
               compaction: active=0, compacted=1), minus Atlas turns and send-mirror rows
  ChatGPT      message create_time; null -> `undated` (D Q6)
  claude.ai    chat_messages created_at
Folded parts (subagents, T3 app_owned children) contribute AGENT times only.
Atlas conversation turns (atlas_tool_turn, and all claimed Channel messages, which never reach
state.db) contribute nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from typing import Sequence
from zoneinfo import ZoneInfo

from .model import ONE_OFF, Area, DayTime, EvidenceId

Work = tuple[Area, str]
"""What a Session was worked on: an Area and a PursuitId, or ONE_OFF for a one-off in that Area."""


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class TimedSession:
    id: EvidenceId
    owner_times: tuple[datetime, ...]
    agent_times: tuple[datetime, ...]
    work: tuple[Work, ...]


def stretches(owner: Sequence[datetime], agent: Sequence[datetime], gap: timedelta) -> list[Interval]:
    """Engagement stretches of one Session. A stretch runs while the owner's messages are less
    than `gap` apart. It ends `gap` after the last of them, or sooner at the agent's last reply
    before the owner writes again. A stretch of no length is left out."""
    owner = sorted(_utc(t) for t in owner)
    agent = sorted(_utc(t) for t in agent)
    runs: list[list[datetime]] = []
    for t in owner:
        if runs and t - runs[-1][-1] < gap:
            runs[-1].append(t)
        else:
            runs.append([t])
    out = []
    for n, run in enumerate(runs):
        first, last = run[0], run[-1]
        upto = runs[n + 1][0] if n + 1 < len(runs) else None
        replies = [a for a in agent if last <= a and (upto is None or a < upto)]
        end = min(last + gap, replies[-1]) if replies else last + gap
        if end > first:
            out.append(Interval(first, end))
    return out


def day(sessions: Sequence[TimedSession], on: date, tz: str, gap: timedelta) -> DayTime:
    """Active Time on the local day `on`: the union of every Session's stretches, so parallel
    Sessions count once. Each moment is split evenly between the distinct Pursuits worked at
    that moment (a one-off counts as its Area's own), then rounded once to whole minutes by
    largest remainder, so Pursuits add up to their Area and Areas to the day. Its basis is the
    Sessions that add time to the day."""
    zone = ZoneInfo(tz)
    start = _utc(datetime.combine(on, time(), tzinfo=zone))
    end = _utc(datetime.combine(on + timedelta(days=1), time(), tzinfo=zone))
    spans = [(s, i) for s in sessions if s.work for i in stretches(s.owner_times, s.agent_times, gap)
             if i.start < end and i.end > start]
    cuts = sorted({start, end, *(max(start, min(end, t)) for _, i in spans for t in (i.start, i.end))})
    seconds: dict[Work, Fraction] = {}
    basis: dict[EvidenceId, None] = {}
    total = Fraction(0)
    for a, b in zip(cuts, cuts[1:]):
        live = [s for s, i in spans if i.start <= a and b <= i.end]
        if not live:
            continue
        length = Fraction((b - a) // timedelta(microseconds=1), 1_000_000)
        worked = list(dict.fromkeys(w for s in live for w in s.work))
        total += length
        for w in worked:
            seconds[w] = seconds.get(w, Fraction(0)) + length / len(worked)
        basis.update(dict.fromkeys(s.id for s in live))
    minutes = _largest_remainder({w: s / 60 for w, s in seconds.items()}, int(total / 60 + Fraction(1, 2)))
    areas: dict[Area, dict[str, timedelta]] = {}
    for (area, key), m in sorted(minutes.items(), key=lambda kv: kv[0][1] == ONE_OFF):
        if m:
            areas.setdefault(area, {})[key] = timedelta(minutes=m)
    ordered = {a: areas[a] for a in dict.fromkeys(a for a, _ in seconds) if a in areas}
    return DayTime(timedelta(minutes=sum(minutes.values())), ordered, tuple(basis))


def undated(times: Sequence[datetime | None], created: datetime | None,
            updated: datetime | None) -> list[datetime | None]:
    """Times for the messages of one conversation branch, in order, where some have none (D Q6).
    An undated message is placed linearly between the nearest timed messages on the branch.
    Without a timed message on one side, the conversation's create time bounds it before and its
    update time after. Without that bound either, it gets no time."""
    known = [(n, _utc(t)) for n, t in enumerate(times) if t is not None]
    out: list[datetime | None] = list(times)
    for n, t in enumerate(times):
        if t is not None:
            continue
        before = next(((k, at) for k, at in reversed(known) if k < n), None) or (
            (-1, _utc(created)) if created is not None else None)
        after = next(((k, at) for k, at in known if k > n), None) or (
            (len(times), _utc(updated)) if updated is not None else None)
        if before is None or after is None or after[1] < before[1]:
            continue
        (i, a), (j, b) = before, after
        out[n] = a + (b - a) * (n - i) / (j - i)
    return out


def _largest_remainder(exact: dict[Work, Fraction], whole: int) -> dict[Work, int]:
    """Whole numbers that add up to `whole`, each the floor of its exact value, plus one for
    the largest remainders. A tie goes to the one worked first."""
    floors = {k: int(v) for k, v in exact.items()}
    order = sorted(exact, key=lambda k: -(exact[k] - floors[k]))
    for k in order[:whole - sum(floors.values())]:
        floors[k] += 1
    return floors


def _utc(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("Active Time needs times that carry their zone")
    return at.astimezone(timezone.utc)
