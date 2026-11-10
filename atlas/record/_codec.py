"""PRIVATE to atlas.record. Domain values <-> Markdown with YAML front matter.

Front matter (scalars, ids, dates) + Markdown body (statements, one per line). Statement line
grammar (documented in the record's README.md):
    - <text> [F <label> · <yyyy-mm-dd hh:mm> · <evidence id>]
    - <text> [F counted · <rule> · <n> Sessions]   ids in the file's `counted:` list, in mark order
    - <text> [I · <evidence id>, <evidence id>]
    - <text> [I · pattern: <description>, <first>..<last>]   drawn_from in profile.md's `patterns:`
A mark may hold a mask ("[secret: <kind>]"): render masks every text first (see _scrubbed).
Deterministic: same value -> same bytes (so re-applying an op is a no-op commit). Every
rendered file carries a blank `sealed:` line that _seal.stamp fills.

Time zones: a time in a file body (marks, Notes headings, History, Sessions, Pause and Forgotten
lines) is wall time in settings.timezone, so render and parse take that zone and parse returns
aware datetimes. A front-matter datetime carries its offset. A Step's Done.at is its basis
Fact's time. Skipped.at is its basis time, or 00:00 local on the date shown for a skip by
silence.

A value the documented format has no place for raises Unrepresentable instead of being
dropped.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime, time, timedelta, tzinfo
from enum import Enum
from functools import cache
from types import UnionType
from typing import Any, Callable, Literal, Mapping, NewType, Sequence, Union, get_args, get_origin, get_type_hints

import yaml

from .. import privacy
from ..model import (COMMANDS, ONE_OFF, Actor, Area, BriefStep, ChannelSetting, Cite, Count, DayTime, DecisionAsk, Done,
                     EndingProposal,
                     EvidenceId, Fact, FocusProposal, Followup, Forgotten, HarnessId, HarnessSetting, HistoryId,
                     HistoryLine, Inference, InterfaceSetting, JournalDay, Kind, Membership, Note, NoteOrigin, Pattern,
                     Pause,
                     Profile, ProfileEntry, Proposal, Pursuit, PursuitId, RawPart, ReopenProposal, ResumeNote,
                     SendAttempt, SendStatus, SentBrief, SessionCard, SessionEvidence, Settings, Skipped, Statement,
                     Status, Step, StepId, Turn, Who, cites, sent_sha256)
from ._layout import FileKind


class Unrepresentable(ValueError):
    """The record format has no place for this value."""


@dataclass(frozen=True)
class SessionRef:
    """How a Pursuit file names a Session it lists: Harness label, title and day."""
    label: str
    title: str
    day: date


@dataclass(frozen=True)
class PursuitFile:
    pursuit: Pursuit
    sessions: Mapping[EvidenceId, SessionRef]


@dataclass(frozen=True)
class NoteEntry:
    note: Note
    about_name: str | None = None            # the file names the Pursuit; Snapshot resolves it to an id


@dataclass(frozen=True)
class NotesFile:
    day: date
    via: str
    entries: tuple[NoteEntry, ...]


@dataclass(frozen=True)
class Codec:
    render: Callable[[Any, tzinfo | None], str]
    parse: Callable[[str, tzinfo | None], Any]


def render(kind: FileKind, value: object, tz: tzinfo | None) -> bytes:
    """`tz` is settings.timezone. Only settings.md and README.md render without it. Every text
    in `value` is masked first, so no codec writes a detected secret (D Q4). A surrogate pair is
    written as the character it stands for, and a lone surrogate, which UTF-8 cannot hold, as
    U+FFFD. Bytes Atlas could not read again, or a Profile whose entries would read back as
    others, raise Unrepresentable, so no write leaves a file that stalls whatever reads it."""
    masked = _scrubbed(value, type(value))
    data = render_bare(kind, masked, tz)
    try:
        again = parse(kind, data, tz)
        same = kind != "profile" or _entries(again) == _entries(masked)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise Unrepresentable(f"a {kind} file that does not read back ({type(error).__name__})") from error
    if not same:
        raise Unrepresentable(f"a {kind} file that reads back as something else")
    return data


def _entries(p: Profile) -> list[tuple]:
    return [(type(e.statement), e.statement.text, cites(e.statement), e.added, e.by) for e in p.entries]


def render_bare(kind: FileKind, value: object, tz: tzinfo | None) -> bytes:
    """`value` written with its texts as they are, as an Atlas that did not mask wrote it."""
    return _written(CODECS[kind].render(value, tz)).encode()


def parse(kind: FileKind, data: bytes, tz: tzinfo | None) -> object:
    """Strict. A sealed file always parses; a parse failure means a hand edit or corruption,
    which the seal check catches first."""
    return CODECS[kind].parse(data.decode(), tz)


def local(dt: datetime, tz: tzinfo | None) -> datetime:
    """`dt` as wall time in the record's zone."""
    if dt.tzinfo is None:
        raise ValueError(f"a record time must carry its zone: {dt}")
    if tz is None:
        raise ValueError("a time in the record needs settings.timezone")
    return dt.astimezone(tz)


def _wall(naive: datetime, tz: tzinfo | None) -> datetime:
    if tz is None:
        raise ValueError("a time in the record needs settings.timezone")
    return naive.replace(tzinfo=tz)


# ------------------------------------------------------------------ secrets (D Q4)

_KEPT: dict[type, frozenset[str]] = {
    RawPart: frozenset({"name", "sha256", "fingerprint", "origin"}),
    SessionEvidence: frozenset({"namespace", "native_key"}),
    Turn: frozenset({"part"}),
    SendAttempt: frozenset({"message_ids", "text_sha256"}),
    Note: frozenset({"platform_ref"}),
    Forgotten: frozenset({"skip_keys"}),
    Settings: frozenset(f.name for f in fields(Settings)),
}


def _scrubbed(value: Any, hint: Any) -> Any:
    """`value`, of declared type `hint`, with privacy.scrub run on every text it holds. Ids (a
    NewType but Area, which the owner names), fixed words (a Literal) and _KEPT's fields stay as
    they are: each of those names something outside the file that must match it exactly (a raw/
    part, a Harness's own key, a sent or received message, a skip key), or is the owner's
    settings, whose roots and chats Atlas acts on. Each text is masked on its own, before the
    codec joins it to others, so a mask never lands inside the file's own structure."""
    hint = _narrowed(hint, value)
    if isinstance(value, Enum) or isinstance(hint, NewType) and hint is not Area or get_origin(hint) is Literal:
        return value
    if isinstance(value, str):
        return privacy.scrub(value)
    if is_dataclass(value):
        hints, kept = _hints(type(value)), _KEPT.get(type(value), frozenset())
        return replace(value, **{f.name: _scrubbed(getattr(value, f.name), hints[f.name])
                                 for f in fields(value) if f.name not in kept})
    args = get_args(hint)
    if isinstance(value, tuple):
        items = args[:1] * len(value) if args[1:] == (...,) else args or (Any,) * len(value)
        return tuple(_scrubbed(x, h) for x, h in zip(value, items))
    if isinstance(value, list):
        return [_scrubbed(x, args[0] if args else Any) for x in value]
    if isinstance(value, frozenset):
        return frozenset(_scrubbed(x, args[0] if args else Any) for x in value)
    if isinstance(value, Mapping):
        keys, values = args or (Any, Any)
        return {_scrubbed(k, keys): _scrubbed(v, values) for k, v in value.items()}
    return value


def _narrowed(hint: Any, value: Any) -> Any:
    """The member of a union `hint` that `value` is."""
    if get_origin(hint) not in (Union, UnionType):
        return hint
    for arg in get_args(hint):
        if isinstance(arg, NewType) and isinstance(value, str) or get_origin(arg) is Literal and value in get_args(arg):
            return arg
        origin = get_origin(arg) or arg
        if isinstance(origin, type) and isinstance(value, origin):
            return arg
    return Any


@cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


# ------------------------------------------------------------------ shared pieces

def _document(front: list[str], body: str) -> str:
    return "---\n" + "\n".join(front) + "\nsealed: \n---\n" + body


def _split(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("no front matter")
    end = text.index("\n---\n", 3)
    return yaml.safe_load(text[4:end + 1]) or {}, text[end + 5:]


_UNQUOTABLE = re.compile("[^\t\n\r\x20-\x7e\xa0-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]|[\u2028\u2029]")
_SURROGATE = re.compile("[\ud800-\udfff]")
_PAIR = re.compile("[\ud800-\udbff][\udc00-\udfff]")


def _joined(s: str) -> str:
    """`s` with each surrogate pair written as the character it stands for, so the YAML and the
    JSON reader read it back the same."""
    return _PAIR.sub(lambda m: m[0].encode("utf-16-le", "surrogatepass").decode("utf-16-le"), s)


def _written(s: str) -> str:
    return _SURROGATE.sub("\ufffd", _joined(s))


def _q(s: str) -> str:
    """`s` double-quoted, as YAML and JSON both read it. A character the YAML reader rejects or
    reads as a line break (a C1 control, U+FFFE, U+FFFF, a lone surrogate, NEL, a line or
    paragraph separator) is written as its \\u escape, and a surrogate pair as its character."""
    return _UNQUOTABLE.sub(lambda m: f"\\u{ord(m[0]):04x}", json.dumps(_joined(s), ensure_ascii=False))


def _line(text: str) -> str:
    """`text` on one body line: a line break as a space."""
    return " ".join(text.splitlines())


def _plain(s: str) -> str:
    return s if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._/+-]*", s) and yaml.safe_load(s) == s else _q(s)


def _flow(v: object, tz: tzinfo | None) -> str:
    """One YAML flow value: dates and enums as scalars, datetimes with their offset."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, Enum):
        return _flow(v.value, tz)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, datetime):
        return _offset(v, tz)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return _plain(v)
    if isinstance(v, (tuple, list)):
        return "[" + ", ".join(_flow(x, tz) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_plain(str(k))}: {_flow(x, tz)}" for k, x in v.items()) + "}"
    raise Unrepresentable(f"a {type(v).__name__} in front matter")


def _coerce(hint: Any, v: object, tz: tzinfo | None) -> object:
    """A front-matter value back into the type a dataclass field declares."""
    if v is None:
        return None
    args = [a for a in get_args(hint) if a is not type(None)]
    if get_origin(hint) in (Union, UnionType):
        return _coerce(args[0], v, tz) if len(args) == 1 else v
    if get_origin(hint) is tuple:
        return tuple(_coerce(args[0], x, tz) for x in v)
    if isinstance(hint, type) and issubclass(hint, Enum):
        return hint(v)
    if hint is datetime:
        return local(_as_datetime(v), tz)
    if hint is date:
        return _as_date(v)
    return v


def _opt_date(d: date | None) -> str:
    return d.isoformat() if d else "null"


def _hm(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _read_hm(s: str, tz: tzinfo | None) -> datetime:
    return _wall(datetime.strptime(s, "%Y-%m-%d %H:%M"), tz)


def _hms(dt: datetime) -> str:
    """A Session's last turn, to the second when it falls between whole minutes, so filing can
    tell a Session that grew from the one it filed (an older record holds the minute only)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S" if dt.second else "%Y-%m-%d %H:%M")


def _read_hms(s: str, tz: tzinfo | None) -> datetime:
    return _wall(datetime.strptime(s, "%Y-%m-%d %H:%M:%S" if s.count(":") == 2 else "%Y-%m-%d %H:%M"), tz)


def _short_day(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b} {d.year}"


def _long_day(d: date) -> str:
    return f"{d:%A} {d.day} {d:%B} {d.year}"


def _as_date(v: object) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def _as_datetime(v: object) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def _hhmm(t: time) -> str:
    return t.strftime("%H:%M")


# ------------------------------------------------------------------ statements and marks

_IN_MARK = r"(?:[^\[\]]|" + privacy.MASK.pattern + r")+"
_STATEMENT = re.compile(r"^(?P<text>.*?) ?\[(?P<mark>F " + _IN_MARK + r"|I · " + _IN_MARK + r")\]$")
_UNMARKABLE = re.compile(r"[\\\[\]·\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")
_ESCAPED = re.compile(r"\\u([0-9a-f]{4})")


def _cite_label(label: str) -> str:
    """A Fact's label as its mark holds it: a backslash, a bracket, a middle dot, a control
    character, a line break or a lone surrogate written as its \\u escape."""
    return _UNMARKABLE.sub(lambda m: f"\\u{ord(m[0]):04x}", _joined(label))


def _read_cite_label(text: str) -> str:
    return _ESCAPED.sub(lambda m: chr(int(m[1], 16)), text)


def _span(p: Pattern) -> tuple[str, date, date]:
    return p.description, p.first, p.last


_COUNTED = re.compile(r"^counted · (?P<rule>\w+) · (?P<n>\d+) Sessions$")


class _Marks:
    """Writes and reads one file's marks in body order, with the zone its times are in.
    `counted` is the file's list that holds each counted mark's Sessions, in mark order, and
    `patterns` the list a pattern mark resolves in by description and span. None where the file
    has no such list."""

    def __init__(self, tz: tzinfo | None, counted: list[Count] | None = None,
                 patterns: list[Pattern] | None = None) -> None:
        self.tz = tz
        self.counted = counted
        self.patterns = patterns
        self._counted_read = 0
        self._patterns_read: set[tuple[str, date, date]] = set()

    def render(self, s: Statement) -> str:
        mark = self._mark(s)
        text = _line(s.text)
        return f"{text} {mark}" if text else mark

    def _mark(self, s: Statement) -> str:
        if isinstance(s, Fact):
            if isinstance(s.source, Count):
                if self.counted is None:
                    raise Unrepresentable("a counted Fact where the file has no list for its Sessions")
                self.counted.append(s.source)
                n = len(s.source.basis)
                return f"[F counted · {s.source.rule} · {n} Sessions]"
            return f"[F {_cite_label(s.source.label)} · {_hm(local(s.source.at, self.tz))} · {s.source.evidence}]"
        if isinstance(s.basis, Pattern):
            if self.patterns is None:
                raise Unrepresentable("a Pattern outside profile.md: the file has no place for its drawn_from")
            p = self._entry(s.basis)
            return f"[I · pattern: {p.description}, {p.first.isoformat()}..{p.last.isoformat()}]"
        return f"[I · {', '.join(s.basis)}]"

    def _entry(self, p: Pattern) -> Pattern:
        """`p` as the patterns list holds it. Another Pattern that already has its description
        and span, such as one whose description differed only by a secret masking hid, keeps
        it, and `p` takes the description with " (2)", " (3)", ... after it."""
        q, n = p, 1
        while (same := next((x for x in self.patterns if _span(x) == _span(q)), None)) not in (None, q):
            n += 1
            q = replace(p, description=f"{p.description} ({n})")
        if same is None:
            self.patterns.append(q)
        return q

    def parse(self, line: str) -> Statement:
        m = _STATEMENT.match(line)
        if not m:
            raise ValueError(f"no mark: {line!r}")
        text, mark = m["text"], m["mark"]
        if mark.startswith("F "):
            counted = _COUNTED.match(mark[2:])
            if counted:
                return Fact(text, self._next_count(counted["rule"], int(counted["n"])))
            label, at, evidence = mark[2:].split(" · ")
            return Fact(text, Cite(EvidenceId(evidence), _read_cite_label(label), _read_hm(at, self.tz)))
        rest = mark.removeprefix("I · ")
        if rest.startswith("pattern: "):
            description, _, span = rest.removeprefix("pattern: ").rpartition(", ")
            first, last = span.split("..")
            key = (description, date.fromisoformat(first), date.fromisoformat(last))
            pattern = next((q for q in self.patterns or () if _span(q) == key), None)
            if pattern is None:
                raise ValueError(f"a pattern mark with no entry in the file's patterns list: {line!r}")
            self._patterns_read.add(key)
            return Inference(text, pattern)
        return Inference(text, tuple(EvidenceId(e) for e in rest.split(", ")))

    def _next_count(self, rule: str, n: int) -> Count:
        if self.counted is None or self._counted_read == len(self.counted):
            raise ValueError("a counted mark with no entry in the file's counted list")
        count = self.counted[self._counted_read]
        self._counted_read += 1
        if count.rule != rule or len(count.basis) != n:
            raise ValueError(f"counted mark {rule} · {n} does not match its entry {count}")
        return count

    def finish(self) -> None:
        """Every front-matter entry a mark resolves to was used, so a re-render loses none."""
        if self.counted is not None and self._counted_read != len(self.counted):
            raise ValueError("the counted list names more marks than the body has")
        if self.patterns is not None and len(self._patterns_read) != len(self.patterns):
            raise ValueError("the patterns list names a pattern no mark uses")


def _bullets(lines: list[str]) -> list[str]:
    return [line.removeprefix("- ") for line in lines if line.startswith("- ")]


# ------------------------------------------------------------------ README.md

_WHO_WORDS = {Who.OWNER: "you", Who.AGENT_TOLD: "an agent you told", Who.AGENT: "an agent",
              Who.LIBRARIAN: "Atlas"}


def _render_readme(_: None, tz: tzinfo | None) -> str:
    changes = "\n".join(
        f"- {cls.__name__}: "
        + (", ".join(_WHO_WORDS[w] for w in Who if w in cls.allowed) or "nobody; Atlas never acts outside this folder")
        for cls in COMMANDS)
    return _document(["atlas: readme/1"], f"""# Atlas record

Atlas keeps this folder. Read anything; change things by talking to Atlas. A file edited
here by hand is put back, and your text is kept as a Note in notes/; reply "apply" to make
the change through Atlas.

## Marks
Every statement ends with a mark.
- `[F Codex · 2026-11-16 08:05 · cx-acdo6557j7vy6xfl]` is a Fact: read directly from that Evidence at that time.
- `[F counted · active_time · 5 Sessions]` is a Fact counted from Evidence by a fixed rule (Active Time,
  quiet days, numbers of Steps or Sessions). The file lists those Sessions in its front matter: a
  Journal day under `evidence:`, any other file under `counted:`, one entry per mark in body order.
- `[I · cx-acdo6557j7vy6xfl, cc-spjgwqujw7ejpbl7]` is an Inference: Atlas's judgment from those pieces of Evidence.
- Detected secrets are shown as `[secret: <kind>]` everywhere except the verbatim copies in `raw/`.
- `[I · pattern: 131 days of Sessions, 2026-06-02..2026-11-15]` is an Inference from a pattern across many Sessions.

## Evidence ids
`cc` Claude Code, `cx` Codex, `dv` Devin, `hm` Hermes, `gpt` ChatGPT, `cai` claude.ai, `nt` a Note.
The id is also the folder name: evidence/<harness>/<yyyy>/<mm>/<id>/session.md.
A Note's id heads it in notes/: `### 07:58 · nt-1a9s4d2f`.

## Files
- settings.md: times, gap, Channel, Harnesses found and added, exclusions.
- profile.md: your Areas and what Atlas learned about you.
- pauses.md: every Pause, with its end.
- forgotten.md: when something was forgotten, never what.
- pursuits/: one file per Pursuit, any Status.
- journal/: one file per day.
- briefs/ and reviews/: each Daily Brief and Review exactly as sent.
- notes/: your conversations with Atlas, Notes from agents, and hand edits Atlas put back.
- evidence/: each Session as a readable transcript, with its verbatim copy in raw/.
- inbox/: drop ChatGPT or claude.ai export zips here.
- .atlas/: Atlas's machinery. The index and the cache can be deleted at any time.

## Changes and who may make them
{changes}
""")


def _parse_readme(text: str, tz: tzinfo | None) -> None:
    _split(text)


# ------------------------------------------------------------------ settings.md

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _render_settings(s: Settings, tz: tzinfo | None) -> str:
    width = max((len(h.value) for h in s.harnesses), default=0) + 2
    harnesses = [
        f"  {h.value + ':':<{width}}{{enabled: {str(v.enabled).lower()}, mode: {v.mode}, "
        f"found_by: {v.found_by}, roots: [{', '.join(_q(r) for r in v.roots)}]}}"
        for h, v in sorted(s.harnesses.items(), key=lambda kv: list(HarnessId).index(kv[0]))]
    interfaces = ", ".join(f"{k}: {{found_by: {v.found_by}, root: {_q(v.root)}}}" for k, v in s.interfaces.items())
    ch = s.channel
    front = [
        "atlas: settings/1",
        f"timezone: {s.timezone}",
        f'brief_at: "{_hhmm(s.brief_at)}"',
        f'review: {{day: {_DAYS[s.review_day - 1]}, at: "{_hhmm(s.review_at)}"}}',
        f"active_time_gap_minutes: {int(s.gap.total_seconds() // 60)}",
        f"machine: {_plain(s.machine)}",
        f"channel: {{platform: {ch.platform}, atlas_chat: {_q(ch.atlas_chat) if ch.atlas_chat else 'null'}, "
        f"address_word: {ch.address_word}, exchange_window_minutes: {int(ch.exchange_window.total_seconds() // 60)}}}",
        '# atlas_chat: e.g. "telegram:<chat>:<topic>" makes that chat or topic Atlas\'s own '
        "(briefs go there, every message there is Atlas's)",
        "harnesses:" + ("" if harnesses else " {}"),
        *harnesses,
        f"interfaces: {{{interfaces}}}",
        f"exclusions: [{', '.join(_q(e) for e in s.exclusions)}]",
    ]
    where = (f"Atlas's own chat {ch.atlas_chat}" if ch.atlas_chat else "your Hermes home chat")
    body = (f"# Settings\n\nDaily Brief at {_hhmm(s.brief_at)}, Review on {_DAYS[s.review_day - 1].title()} "
            f"at {_hhmm(s.review_at)} ({s.timezone}). Active Time gap: {int(s.gap.total_seconds() // 60)} minutes.\n"
            f"Channel: {ch.platform.title()}, {where}. Change these by telling Atlas or in the Board's Settings tab.\n")
    return _document(front, body)


def _parse_settings(text: str, tz: tzinfo | None) -> Settings:
    f, _ = _split(text)
    ch = f["channel"]
    return Settings(
        timezone=f["timezone"],
        brief_at=time.fromisoformat(f["brief_at"]),
        review_day=_DAYS.index(f["review"]["day"]) + 1,
        review_at=time.fromisoformat(f["review"]["at"]),
        gap=timedelta(minutes=f["active_time_gap_minutes"]),
        machine=str(f["machine"]),
        harnesses={HarnessId(k): HarnessSetting(v["enabled"], v["mode"], tuple(v["roots"]), v["found_by"])
                   for k, v in (f["harnesses"] or {}).items()},
        exclusions=tuple(f["exclusions"]),
        channel=ChannelSetting(ch["platform"], ch["atlas_chat"], ch["address_word"],
                               timedelta(minutes=ch["exchange_window_minutes"])),
        interfaces={k: InterfaceSetting(v["found_by"], v["root"]) for k, v in (f["interfaces"] or {}).items()},
    )


# ------------------------------------------------------------------ profile.md

_ADDED_BY = {Who.LIBRARIAN: "atlas", Who.OWNER: "you"}
_ENTRY = re.compile(r"^- (?P<statement>.*) <!-- added (?P<added>\d{4}-\d{2}-\d{2}) by (?P<by>\w+) -->$")


def _render_profile(p: Profile, tz: tzinfo | None) -> str:
    marks = _Marks(tz, patterns=[])
    lines = []
    for e in p.entries:
        if e.by not in _ADDED_BY:
            raise Unrepresentable(f"a Profile entry added by {e.by.value}")
        lines.append(f"- {marks.render(e.statement)} <!-- added {e.added.isoformat()} by {_ADDED_BY[e.by]} -->")
    body = "# Profile\n" + ("\n" + "\n".join(lines) + "\n" if lines else "")
    front = ["atlas: profile/1", f"areas: [{', '.join(_plain(a) for a in p.areas)}]"]
    if marks.patterns:
        front += ["patterns:", *(f"  - {_flow(_pattern_row(q), tz)}" for q in marks.patterns)]
    return _document(front, body)


def _pattern_row(p: Pattern) -> dict:
    return {"description": p.description, "first": p.first, "last": p.last, "drawn_from": p.drawn_from}


def _parse_profile(text: str, tz: tzinfo | None) -> Profile:
    f, body = _split(text)
    marks = _Marks(tz, patterns=[Pattern(q["description"], _as_date(q["first"]), _as_date(q["last"]),
                                         tuple(q["drawn_from"])) for q in f.get("patterns") or ()])
    by = {v: k for k, v in _ADDED_BY.items()}
    entries = []
    for line in body.splitlines():
        if line.startswith("- "):
            m = _ENTRY.match(line)
            if not m:
                raise ValueError(f"profile entry without its added comment: {line!r}")
            entries.append(ProfileEntry(marks.parse(m["statement"]), date.fromisoformat(m["added"]), by[m["by"]]))
    marks.finish()
    return Profile(tuple(f["areas"]), tuple(entries))


# ------------------------------------------------------------------ pauses.md

def _when(dt: datetime) -> str:
    return f"{dt:%a} {dt.day} {dt:%b %H:%M}"


def _offset(dt: datetime, tz: tzinfo | None) -> str:
    return local(dt, tz).isoformat(timespec="minutes")


def _render_pauses(pauses: tuple[Pause, ...], tz: tzinfo | None) -> str:
    rows = [f"  - {{start: {_offset(p.start, tz)}, end: {_offset(p.end, tz)}, said: {_q(p.said)}}}" for p in pauses]
    front = ["atlas: pauses/1", "pauses:" + ("" if rows else " []"), *rows]
    lines = [f"- {_when(local(p.start, tz))} to {_when(local(p.end, tz))}." + (f" You said: {_q(p.said)}." if p.said else "")
             for p in pauses]
    return _document(front, "# Pauses\n" + ("\n" + "\n".join(lines) + "\n" if lines else ""))


def _parse_pauses(text: str, tz: tzinfo | None) -> tuple[Pause, ...]:
    f, _ = _split(text)
    return tuple(Pause(local(_as_datetime(p["start"]), tz), local(_as_datetime(p["end"]), tz), p["said"])
                 for p in f["pauses"] or ())


# ------------------------------------------------------------------ forgotten.md

_FORGOT = re.compile(r"^- \w{3} (?P<at>\d{1,2} \w{3} \d{4} \d{2}:\d{2}): (?P<what>.+) was forgotten at your request\.$")


def _render_forgotten(f: Forgotten, tz: tzinfo | None) -> str:
    keys = [f'  - {k}        # never re-collect: hash of "<harness>:<native id>", not content' for k in sorted(f.skip_keys)]
    lines = [f"- {at:%a} {at.day} {at:%b %Y %H:%M}: {what} was forgotten at your request."
             for at, what in ((local(at, tz), what) for at, what in f.entries)]
    front = ["atlas: forgotten/1", "skip_keys:" + ("" if keys else " []"), *keys]
    return _document(front, "# Forgotten\n" + ("\n" + "\n".join(lines) + "\n" if lines else ""))


def _parse_forgotten(text: str, tz: tzinfo | None) -> Forgotten:
    f, body = _split(text)
    entries = []
    for line in body.splitlines():
        if line.startswith("- "):
            m = _FORGOT.match(line)
            if not m:
                raise ValueError(f"unreadable forgotten line: {line!r}")
            entries.append((_wall(datetime.strptime(m["at"], "%d %b %Y %H:%M"), tz), m["what"]))
    return Forgotten(tuple(entries), frozenset(f["skip_keys"] or ()))


# ------------------------------------------------------------------ pursuits/<slug>.md

_RESUME_PARTS = (("Where", "where"), ("Left off", "left_off"), ("Decided", "decided"),
                 ("Open questions", "open_questions"))
_ACTORS = {Actor.OWNER: "You", Actor.ATLAS: "Atlas"}
_NEVER_EMPTY = ("Resume Note", "Sessions", "History")
_STEP = re.compile(r"^- \[(?P<state>[^\]]+)\] (?P<id>s-(?P<ymd>\d{8})-\d+) · (?P<statement>.*)$")
_MEMBER = re.compile(r'^- (?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?) · (?P<label>[^·]+) · (?P<title>".*") · (?P<id>\S+)$')
_REOPEN = re.compile(r'^- (?P<label>.+?) (?P<title>".*") · (?P<day>\d{4}-\d{2}-\d{2}) · (?P<id>\S+)$')
_HISTORY = re.compile(r"^- (?P<id>h-\d+) · (?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (?P<actor>[^·]+?) · (?P<statement>.*)$")


def _ended_at(basis: Statement | None, day: date, tz: tzinfo | None) -> datetime:
    """Done.at is its basis Fact's time; Skipped.at is its basis time, or 00:00 local on the
    day shown for a skip by silence."""
    if basis is None:
        return datetime.combine(day, time(), tzinfo=tz)
    if isinstance(basis, Fact) and isinstance(basis.source, Cite):
        return basis.source.at
    raise Unrepresentable("a Step ended on an Inference or a counted Fact: the file shows no time for it")


def _step_lines(s: Step, marks: _Marks) -> list[str]:
    if s.created != datetime.strptime(s.id.split("-")[1], "%Y%m%d").date():
        raise Unrepresentable("a Step's id carries its creation date (s-<yyyymmdd>-<n>)")
    if s.ended is None:
        state = f"open · held until {s.held_until.isoformat()}" if s.held_until else "open"
    else:
        day = local(s.ended.at, marks.tz).date()
        if s.ended.at != _ended_at(s.ended.basis, day, marks.tz):
            raise Unrepresentable("a Step's end time is its basis time, or 00:00 for a skip by silence")
        state = f"{_ENDED[type(s.ended)]} · {s.ended.how} {day.isoformat()}"
    lines = [f"- [{state}] {s.id} · {marks.render(s.text)}"]
    if s.ended is not None and s.ended.basis is not None:
        lines.append(f"  {_ENDED[type(s.ended)]}: {marks.render(s.ended.basis)}")
    return lines


_ENDED = {Done: "done", Skipped: "skipped"}


def _parse_steps(lines: list[str], marks: _Marks) -> list[Step]:
    read: list[list] = []
    for line in lines:
        if line.startswith("  "):
            word, _, rest = line.strip().partition(": ")
            if not read or read[-1][2] is not None or not read[-1][0]["state"].startswith(word + " · "):
                raise ValueError(f"a basis line without its ended Step: {line!r}")
            read[-1][2] = marks.parse(rest)
            continue
        m = _STEP.match(line)
        if not m:
            raise ValueError(f"unreadable Step line: {line!r}")
        read.append([m, marks.parse(m["statement"]), None])
    steps = []
    for m, text, basis in read:
        created = datetime.strptime(m["ymd"], "%Y%m%d").date()
        state = m["state"].split(" · ")
        held = ended = None
        if state[0] == "open":
            held = date.fromisoformat(state[1].removeprefix("held until ")) if len(state) > 1 else None
        else:
            how, on = state[1].split(" ")
            day = date.fromisoformat(on)
            at = _ended_at(basis, day, marks.tz)
            if local(at, marks.tz).date() != day:
                raise ValueError(f"a Step ended on {day} by a basis from another day: {m.string!r}")
            if state[0] == "done":
                if basis is None:
                    raise ValueError(f"a done Step names its basis: {m.string!r}")
                ended = Done(how, at, basis)
            else:
                ended = Skipped(how, at, basis)
        steps.append(Step(StepId(m["id"]), text, created, held, ended))
    return steps


_PROPOSALS = {"ending": EndingProposal, "focus": FocusProposal, "reopen": ReopenProposal}


def _proposal_row(pr: Proposal, marks: _Marks) -> dict:
    kind = next(k for k, cls in _PROPOSALS.items() if isinstance(pr, cls))
    row: dict = {"kind": kind}
    for f in fields(pr):
        v = getattr(pr, f.name)
        row[f.name] = marks.render(v) if isinstance(v, (Fact, Inference)) else v
    return row


def _parse_proposal(row: dict, marks: _Marks) -> Proposal:
    row = dict(row)
    cls = _PROPOSALS[row.pop("kind")]
    if set(row) != {f.name for f in fields(cls)}:
        raise ValueError(f"a {cls.__name__} with fields {sorted(row)}")
    hints = get_type_hints(cls)
    return cls(**{k: marks.parse(v) if hints[k] in (Fact, Inference) else _coerce(hints[k], v, marks.tz)
                  for k, v in row.items()})


def _actor(h: HistoryLine) -> str:
    return h.agent if h.actor is Actor.AGENT else _ACTORS[h.actor]


def _render_pursuit(pf: PursuitFile, tz: tzinfo | None) -> str:
    p = pf.pursuit
    marks = _Marks(tz, counted=[])
    ref = pf.sessions
    summary = f"{p.kind.value} · {_line(p.area)} · {p.status.value}"
    if p.last_worked:
        summary += f" · last worked {_short_day(p.last_worked)}"
    resume = []
    for title, attr in _RESUME_PARTS:
        resume.append("\n".join([title, *(f"- {marks.render(s)}" for s in getattr(p.resume, attr))]))
    resume.append("\n".join(["Sessions to reopen", *(
        f"- {ref[e].label} {_q(ref[e].title)} · {ref[e].day.isoformat()} · {e}" for e in p.resume.reopen)]))
    sections = [
        ("Resume Note", "\n\n".join(resume)),
        ("Next Step", "\n".join(_step_lines(p.next_step, marks)) if p.next_step else ""),
        ("Steps", "\n".join(line for s in p.past_steps for line in _step_lines(s, marks))),
        ("Filing rules", "\n".join(f"- {marks.render(f)}" for f in p.filing_rules)),
        ("Sessions", "\n".join(f"- {_hms(local(m.at, tz))} · {ref[m.evidence].label} · {_q(ref[m.evidence].title)} · {m.evidence}"
                               for m in p.sessions)),
        ("History", "\n".join(f"- {h.id} · {_hm(local(h.at, tz))} · {_actor(h)} · {marks.render(h.statement)}"
                              for h in p.history)),
    ]
    body = f"# {_line(p.name)}\n\n{summary}\n" + "".join(
        f"\n## {title}\n\n{content}\n" if content else f"\n## {title}\n" for title, content in sections)
    in_front = _Marks(tz)
    proposals = [f"  - {_flow(_proposal_row(pr, in_front), tz)}" for pr in p.proposals]
    read_only = [m.evidence for m in p.sessions if not m.advances]
    front = [
        "atlas: pursuit/1", f"id: {p.id}", f"name: {_q(p.name)}", f"kind: {p.kind.value}",
        f"area: {_plain(p.area)}", f"status: {p.status.value}", f"focus: {str(p.focus).lower()}",
        f"created: {p.created.isoformat()}", f"continued: {_opt_date(p.continued)}",
        f"decision_held_until: {_opt_date(p.decision_held_until)}", f"raised_at: {_opt_date(p.raised_at)}",
        "proposals:" + ("" if proposals else " []"), *proposals,
    ]
    if read_only:
        front.append(f"read_only_sessions: {_flow(read_only, tz)}")
    if marks.counted:
        front += ["counted:", *(f"  - {_flow({'rule': c.rule, 'basis': c.basis}, tz)}" for c in marks.counted)]
    return _document(front, body)


def _sections(body: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:]
            out[current] = []
        elif current is not None and line:
            out[current].append(line)
    return out


def _parse_pursuit(text: str, tz: tzinfo | None) -> PursuitFile:
    f, body = _split(text)
    marks = _Marks(tz, counted=[Count(c["rule"], tuple(c["basis"])) for c in f.get("counted") or ()])
    sec = _sections(body)
    if missing := [title for title in _NEVER_EMPTY if title not in sec]:
        raise ValueError(f"a Pursuit body cut short, without its {missing[0]} section")
    refs: dict[EvidenceId, SessionRef] = {}
    parts: dict[str, list[str]] = {}
    current = None
    for line in sec.get("Resume Note", []):
        if not line.startswith("- "):
            current = line
            parts[current] = []
        else:
            parts[current].append(line)
    resume_statements = {attr: tuple(marks.parse(s) for s in _bullets(parts.get(title, [])))
                         for title, attr in _RESUME_PARTS}
    reopen = []
    for line in parts.get("Sessions to reopen", []):
        m = _REOPEN.match(line)
        if not m:
            raise ValueError(f"unreadable Session to reopen: {line!r}")
        refs[EvidenceId(m["id"])] = SessionRef(m["label"], json.loads(m["title"]), date.fromisoformat(m["day"]))
        reopen.append(EvidenceId(m["id"]))
    next_steps = _parse_steps(sec.get("Next Step", []), marks)
    past_steps = _parse_steps(sec.get("Steps", []), marks)
    filing_rules = tuple(marks.parse(s) for s in _bullets(sec.get("Filing rules", [])))
    read_only = set(f.get("read_only_sessions") or ())
    sessions = []
    for line in sec.get("Sessions", []):
        m = _MEMBER.match(line)
        if not m:
            raise ValueError(f"unreadable Session line: {line!r}")
        at = _read_hms(m["at"], tz)
        refs[EvidenceId(m["id"])] = SessionRef(m["label"].strip(), json.loads(m["title"]), at.date())
        sessions.append(Membership(EvidenceId(m["id"]), at, m["id"] not in read_only))
    if not read_only <= {m.evidence for m in sessions}:
        raise ValueError(f"read_only_sessions not in the Sessions list: {sorted(read_only)}")
    actors = {v: k for k, v in _ACTORS.items()}
    history = []
    for line in sec.get("History", []):
        m = _HISTORY.match(line)
        if not m:
            raise ValueError(f"unreadable History line: {line!r}")
        actor = actors.get(m["actor"], Actor.AGENT)
        history.append(HistoryLine(HistoryId(m["id"]), _read_hm(m["at"], tz), actor, marks.parse(m["statement"]),
                                   agent=m["actor"] if actor is Actor.AGENT else None))
    marks.finish()
    in_front = _Marks(tz)
    pursuit = Pursuit(
        id=PursuitId(f["id"]), name=f["name"], kind=Kind(f["kind"]), area=f["area"], status=Status(f["status"]),
        focus=f["focus"], created=_as_date(f["created"]), continued=_as_date(f["continued"]),
        resume=ResumeNote(**resume_statements, reopen=tuple(reopen)),
        next_step=next_steps[0] if next_steps else None, past_steps=tuple(past_steps), filing_rules=filing_rules,
        sessions=tuple(sessions), proposals=tuple(_parse_proposal(r, in_front) for r in f["proposals"] or ()),
        history=tuple(history), decision_held_until=_as_date(f["decision_held_until"]),
        raised_at=_as_date(f["raised_at"]))
    return PursuitFile(pursuit, refs)


# ------------------------------------------------------------------ notes/<yyyy>/<mm>/<date>-<via>.md

HAND_EDITS = "hand-edits"
_NOTE_FILE_TITLES = {"telegram": "Telegram", "board": "Board", "cli": "command line", "slash": "/atlas",
                     HAND_EDITS: "hand edits"}
_NOTE_HEAD = re.compile(r"^### (?P<hm>\d{2}:\d{2}) · (?P<id>nt-\S+)(?P<extras>(?: · .*)?)$")
_FENCE = re.compile(r"^(`{3,})[a-z]*$")
_HAND_EDIT_SAYS = 'Someone edited {path} outside Atlas. Atlas put it back. Reply "apply" to make this change through Atlas.'
_AS_EDITED = "The file as it was edited:"
_ENTRY_FIELDS = ("platform_ref", "offered", "applied", "derived_from", "pending", "offer")


def _file_title(via: str) -> str:
    if via in _NOTE_FILE_TITLES:
        return _NOTE_FILE_TITLES[via]
    return f"from {HarnessId(via).label}"


ON_YOUR_WORDS = ", on your words"
"""How a refused update's Note names who asked: "Devin, on your words: <the owner's words>"."""


def _speaker(n: Note) -> str:
    if n.origin is NoteOrigin.OWNER:
        return "You"
    return HarnessId(n.via).label + (ON_YOUR_WORDS if n.origin is NoteOrigin.REFUSED_UPDATE else "")


def _said(who: str, text: str) -> list[str]:
    first, *rest = text.split("\n")
    return [f"{who}: {first}", *(f"  {line}" for line in rest)]


def _fenced(lang: str, text: str) -> list[str]:
    """A fenced block whose content reads back as `text`, which ends with a newline or is empty."""
    if text and not text.endswith("\n"):
        raise Unrepresentable("a fenced block cannot show a text without its final newline")
    fence = "`" * max([3, *(len(run) + 1 for run in re.findall(r"^(`+)", text, re.M))])
    return [fence + lang, *text.split("\n")[:-1], fence]


def _note_entry(n: Note, tz: tzinfo | None) -> dict:
    """The Note's machinery fields that differ from their defaults, for the file's entries map."""
    out: dict = {}
    if n.platform_ref is not None:
        out["platform_ref"] = n.platform_ref
    if n.offered:
        out["offered"] = True
    if n.applied:
        out["applied"] = True
    if n.derived_from:
        out["derived_from"] = n.derived_from
    if n.offer is not None:
        out["offer"] = n.offer
    if n.pending is not None:
        out["pending"] = {"command": type(n.pending).__name__,
                          **{f.name: getattr(n.pending, f.name) for f in fields(n.pending)}}
    return out


def _parse_note_entry(row: dict, tz: tzinfo | None) -> dict:
    if not set(row) <= set(_ENTRY_FIELDS):
        raise ValueError(f"unknown Note fields: {sorted(set(row) - set(_ENTRY_FIELDS))}")
    out = dict(row)
    if "derived_from" in row:
        out["derived_from"] = tuple(EvidenceId(e) for e in row["derived_from"])
    if "offer" in row:
        out["offer"] = EvidenceId(row["offer"])
    if "pending" in row:
        args = dict(row["pending"])
        name = args.pop("command")
        cls = next(c for c in COMMANDS if c.__name__ == name)
        hints = get_type_hints(cls)
        out["pending"] = cls(**{k: _coerce(hints[k], v, tz) for k, v in args.items()})
    return out


def _said_block(e: NoteEntry, at: datetime) -> list[str]:
    n = e.note
    if n.origin not in (NoteOrigin.OWNER, NoteOrigin.AGENT, NoteOrigin.REFUSED_UPDATE):
        raise Unrepresentable(f"a {n.origin.value} Note: the record format has no body for it")
    if n.origin is NoteOrigin.REFUSED_UPDATE and n.pending is None:
        raise Unrepresentable("a refused update's Note keeps the change it would make")
    if n.diff is not None:
        raise Unrepresentable("a diff on a Note that is not a hand edit")
    head = f"### {at:%H:%M} · {n.id}"
    if e.about_name:
        head += f" · about: {e.about_name}"
    if n.cwd:
        head += f" · cwd: {n.cwd}"
    lines = [head, *_said(_speaker(n), n.text)]
    if n.atlas_reply is not None:
        lines += _said("Atlas", n.atlas_reply)
    return lines


def _hand_edit_block(e: NoteEntry, at: datetime) -> list[str]:
    n = e.note
    if n.origin is not NoteOrigin.HAND_EDIT or n.diff is None:
        raise Unrepresentable("the hand-edits file holds hand-edit Notes, each with its diff")
    if e.about_name or n.cwd or n.atlas_reply is not None:
        raise Unrepresentable("a hand-edit Note shows no Pursuit, folder or reply")
    return [f"### {at:%H:%M} · {n.id} · {n.via}", _HAND_EDIT_SAYS.format(path=n.via), "",
            *_fenced("diff", n.diff), "", _AS_EDITED, "", *_fenced("markdown", n.text)]


def _render_notes(nf: NotesFile, tz: tzinfo | None) -> str:
    blocks, machinery = [], []
    for e in nf.entries:
        at = local(e.note.at, tz)
        if at.date() != nf.day:
            raise ValueError(f"a Note from {at.date()} in the Notes file of {nf.day}")
        blocks.append("\n".join(_hand_edit_block(e, at) if nf.via == HAND_EDITS else _said_block(e, at)))
        if row := _note_entry(e.note, tz):
            machinery.append(f"  {e.note.id}: {_flow(row, tz)}")
    front = ["atlas: notes/1", f"day: {nf.day.isoformat()}", f"via: {nf.via}"]
    if machinery:
        front += ["entries:", *machinery]
    body = f"# Notes · {_long_day(nf.day)} · {_file_title(nf.via)}\n" + "".join(f"\n{b}\n" for b in blocks)
    return _document(front, body)


def _note_blocks(body: str) -> list[list[str]]:
    """The body split at each Note heading, never inside a fenced block."""
    blocks: list[list[str]] = []
    fence = None
    for line in body.split("\n"):
        if fence is None and line.startswith("### "):
            blocks.append([line])
            continue
        if not blocks:
            continue
        blocks[-1].append(line)
        if fence is not None:
            fence = None if line == fence else fence
        elif m := _FENCE.match(line):
            fence = m[1]
    for block in blocks:
        while block[-1] == "":
            block.pop()
    return blocks


def _read_fenced(lines: list[str], lang: str) -> tuple[str, list[str]]:
    m = _FENCE.match(lines[0]) if lines else None
    if not m or lines[0] != m[1] + lang or m[1] not in lines[1:]:
        raise ValueError(f"expected a {lang} block: {lines[:1]!r}")
    end = lines.index(m[1], 1)
    content = lines[1:end]
    return "".join(line + "\n" for line in content), lines[end + 1:]


def _parse_hand_edit(m: re.Match, lines: list[str]) -> dict:
    path = m["extras"].removeprefix(" · ")
    if lines[:2] != [_HAND_EDIT_SAYS.format(path=path), ""]:
        raise ValueError(f"unreadable hand-edit Note: {lines[:2]!r}")
    diff, rest = _read_fenced(lines[2:], "diff")
    if rest[:3] != ["", _AS_EDITED, ""]:
        raise ValueError(f"a hand-edit Note without the edited file: {rest[:3]!r}")
    text, rest = _read_fenced(rest[3:], "markdown")
    if rest:
        raise ValueError(f"text after a hand-edit Note's file: {rest[:1]!r}")
    return {"origin": NoteOrigin.HAND_EDIT, "via": path, "text": text, "diff": diff}


def _parse_said(m: re.Match, lines: list[str], via: str) -> tuple[dict, str | None]:
    extras = dict(x.split(": ", 1) for x in m["extras"].split(" · ")[1:])
    said: list[list[str]] = []
    for line in lines:
        if line.startswith("  ") and said:
            said[-1].append(line[2:])
        else:
            said.append([line])
    speaker, _, first = said[0][0].partition(": ")
    reply = None
    if len(said) > 1:
        reply = "\n".join([said[1][0].removeprefix("Atlas: "), *said[1][1:]])
    origin = (NoteOrigin.OWNER if speaker == "You" else NoteOrigin.REFUSED_UPDATE if speaker.endswith(ON_YOUR_WORDS)
              else NoteOrigin.AGENT)
    return ({"origin": origin, "via": via,
             "text": "\n".join([first, *said[0][1:]]), "atlas_reply": reply, "cwd": extras.get("cwd")},
            extras.get("about"))


def _parse_notes(text: str, tz: tzinfo | None) -> NotesFile:
    f, body = _split(text)
    day, via = _as_date(f["day"]), f["via"]
    if via not in _NOTE_FILE_TITLES and via not in {h.value for h in HarnessId}:
        raise ValueError(f"a Notes file from an unknown origin: {via}")
    machinery = f.get("entries") or {}
    entries = []
    for head, *lines in _note_blocks(body):
        m = _NOTE_HEAD.match(head)
        if not m:
            raise ValueError(f"unreadable Note heading: {head!r}")
        if via == HAND_EDITS:
            values, about = _parse_hand_edit(m, lines), None
        else:
            values, about = _parse_said(m, lines, via)
        note = Note(id=EvidenceId(m["id"]), at=datetime.combine(day, time.fromisoformat(m["hm"]), tzinfo=tz),
                    **values, **_parse_note_entry(machinery.pop(m["id"], {}), tz))
        entries.append(NoteEntry(note, about))
    if machinery:
        raise ValueError(f"entries for Notes the file does not hold: {sorted(machinery)}")
    return NotesFile(day, via, tuple(entries))


# ------------------------------------------------------------------ briefs/<yyyy>/<mm>/<date>.md

def _attempt_row(a: SendAttempt, text: str, tz: tzinfo | None) -> str:
    hashed = f", text_sha256: {_q(a.text_sha256)}" if _hashes(a, text) else ""
    chat = f", chat: {_q(a.chat)}" if a.chat else ""
    return (f"{{at: {local(a.at, tz).isoformat(timespec='seconds')}, status: {a.status.value}, "
            f"message_ids: [{', '.join(_q(m) for m in a.message_ids)}]{hashed}{chat}}}")


def _hashes(a: SendAttempt, text: str) -> bool:
    """Whether the attempt's hash is the hash of the text the file holds. Any other hash is left
    out, so a file never holds the hash of a text it does not show."""
    return a.text_sha256 is not None and a.text_sha256 == sent_sha256(_written(text))


def _followup_row(f: Followup) -> dict:
    if f.statement is not None:
        raise Unrepresentable("a follow-up's line: the brief file keeps it only in the text as sent")
    row: dict = {"pursuit": f.pursuit, "step": f.step, "state": f.state}
    if f.ending_proposed:
        row["ending_proposed"] = True
    return row


def _rows(name: str, rows: list[str]) -> list[str]:
    return [f"{name}:" + ("" if rows else " []"), *(f"  - {r}" for r in rows)]


def _render_brief(b: SentBrief, tz: tzinfo | None) -> str:
    if len(b.attempts) not in (1, 2):
        raise Unrepresentable(f"{len(b.attempts)} send attempts: a message is sent at most twice (D Q13)")
    last = b.attempts[-1]
    front = ["atlas: brief/1", f"day: {b.day.isoformat()}", f"status: {last.status.value}",
             f"sent_at: {local(last.at, tz).isoformat(timespec='seconds')}",
             f"message_ids: [{', '.join(_q(m) for m in last.message_ids)}]"]
    if _hashes(last, b.text):
        front.append(f"text_sha256: {_q(last.text_sha256)}")
    if last.chat:
        front.append(f"chat: {_q(last.chat)}")
    if len(b.attempts) == 2:
        front.append(f"first_attempt: {_attempt_row(b.attempts[0], b.text, tz)}")
    front += [f"follows_up: {_opt_date(b.follows_up)}",
              *_rows("followups", [_flow(_followup_row(f), tz) for f in b.followups]),
              *_rows("steps", [_flow({"n": s.n, "pursuit": s.pursuit, "step": s.step}, tz) for s in b.steps]),
              "decision: " + (_flow(_decision_row(b.decision), tz) if b.decision else "null")]
    if b.no_focus_yet:
        front.append("no_focus_yet: true")
    return _document(front, b.text + "\n")


def _decision_row(d: DecisionAsk) -> dict:
    row = {"pursuit": d.pursuit, "ask": d.ask, "quiet_days": d.quiet_days}
    return row | {"heard": True} if d.heard else row


def _parse_attempt(row: dict, tz: tzinfo | None) -> SendAttempt:
    return SendAttempt(local(_as_datetime(row["at"]), tz), SendStatus(row["status"]),
                       tuple(str(m) for m in row["message_ids"]), row.get("text_sha256"), row.get("chat"))


def _parse_brief(text: str, tz: tzinfo | None) -> SentBrief:
    f, body = _split(text)
    if not body.endswith("\n"):
        raise ValueError("a brief file's text ends with a newline")
    last = _parse_attempt({"at": f["sent_at"], "status": f["status"], "message_ids": f["message_ids"],
                           "text_sha256": f.get("text_sha256"), "chat": f.get("chat")}, tz)
    first = (_parse_attempt(f["first_attempt"], tz),) if "first_attempt" in f else ()
    d = f["decision"]
    return SentBrief(
        day=_as_date(f["day"]), attempts=(*first, last), follows_up=_as_date(f["follows_up"]),
        followups=tuple(Followup(PursuitId(r["pursuit"]), StepId(r["step"]), r["state"], None,
                                 r.get("ending_proposed", False)) for r in f["followups"] or ()),
        steps=tuple(BriefStep(r["n"], PursuitId(r["pursuit"]), StepId(r["step"])) for r in f["steps"] or ()),
        decision=DecisionAsk(PursuitId(d["pursuit"]), d["ask"], d["quiet_days"], d.get("heard", False)) if d else None,
        no_focus_yet=f.get("no_focus_yet", False), text=body[:-1])


# ------------------------------------------------------------------ journal/<date>.md

@dataclass(frozen=True)
class JournalFile:
    """A Journal day as its file shows it: the names of the Pursuits its Active Time lines show,
    the sentences naming each Session that belongs to more than one of them, and the machine
    the day covers."""
    day: JournalDay
    names: Mapping[PursuitId, str]
    shared: str                              # "Codex 12:30 belongs to both A and B.", or ""
    machine: str


_JOURNAL_SECTIONS = (("Worked on", "worked_on"), ("Decided", "decided"), ("Finished", "finished"))
_ACTIVE = re.compile(r"^Active time (?P<span>.+) \[F counted · active_time · (?P<n>\d+) Sessions\]$")
_EVIDENCE = re.compile(r"^Evidence: (?P<n>\d+) Sessions?\.(?P<shared>.*) \[F counted · sessions · (?P<m>\d+) Sessions\]$")
_COVERS = re.compile(r"^Covers AI Sessions on (?P<machine>.+) only\.$")
_NAME_ESCAPES = re.compile(r"[\\,]")
_FRONT_INDENT = " " * len("active_time: {")


def _minutes(t: timedelta) -> int:
    if t % timedelta(minutes=1):
        raise Unrepresentable("Active Time is kept in whole minutes")
    return t // timedelta(minutes=1)


def _duration(t: timedelta) -> str:
    """A day's or an Area's time: "3 h 55 min", "40 min", "2 h"."""
    h, m = divmod(_minutes(t), 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"


def _pursuit_duration(t: timedelta) -> str:
    """A Pursuit's time in its Area's line: "1 h 10", "1 h 05", "2 h", "40 min"."""
    h, m = divmod(_minutes(t), 60)
    return f"{h} h {m:02d}" if h and m else f"{h} h" if h else f"{m} min"


def _clock_duration(t: timedelta) -> str:
    h, m = divmod(_minutes(t), 60)
    return f"{h}:{m:02d}"


def _read_clock_duration(text: str) -> timedelta:
    h, m = str(text).split(":")
    return timedelta(hours=int(h), minutes=int(m))


def _day_name(name: str) -> str:
    """A Pursuit's name in an Area's line: a backslash and a comma as their \\u escapes, and the
    hyphen of a name that starts with "one-off" too, so only a one-off's entry starts so."""
    escaped = _NAME_ESCAPES.sub(lambda m: f"\\u{ord(m[0]):04x}", _line(name))
    return escaped.replace("-", "\\u002d", 1) if escaped.startswith(ONE_OFF) else escaped


def _area_lines(t: DayTime, names: Mapping[str, str]) -> list[str]:
    spans = {a: _duration(v) for a, v in t.by_area.items()}
    area_width = max(len(_written(_line(a))) for a in t.areas)
    span_width = max(len(v) for v in spans.values())
    lines = []
    for area, split in t.areas.items():
        entries = [f"{'one-off' if key == ONE_OFF else _day_name(names[key])} {_pursuit_duration(v)}"
                   for key, v in split.items()]
        lines.append(f"  {_written(_line(area)).ljust(area_width)}  {spans[area].center(span_width)}  {', '.join(entries)}")
    return lines


def _render_journal(jf: JournalFile, tz: tzinfo | None) -> str:
    j, t = jf.day, jf.day.time
    if not t.areas:
        raise Unrepresentable("a Journal day without Active Time")
    n = len(j.evidence)
    by_area = ", ".join(f"{_plain(a)}: {_q(_clock_duration(v))}" for a, v in t.by_area.items())
    by_pursuit = ", ".join(f"{_plain(k)}: {_q(_clock_duration(v))}" for k, v in t.by_pursuit.items())
    front = ["atlas: journal/1", f"day: {j.day.isoformat()}",
             f'active_time: {{total: {_q(_clock_duration(t.total))}, by_area: {{{by_area}}},',
             f"{_FRONT_INDENT}by_pursuit: {{{by_pursuit}}}}}",
             f"evidence: {_flow(list(j.evidence), tz)}"]
    marks = _Marks(tz)
    body = [f"# {_long_day(j.day)}", "", f"Active time {_duration(t.total)} [F counted · active_time · {n} Sessions]",
            *_area_lines(t, jf.names)]
    for title, attr in _JOURNAL_SECTIONS:
        if lines := getattr(j, attr):
            body += ["", title, *(f"- {marks.render(s)}" for s in lines)]
    shared = _line(jf.shared)
    body += ["", f"Evidence: {n} Session{'' if n == 1 else 's'}.{' ' + shared if shared else ''} "
                 f"[F counted · sessions · {n} Sessions]",
             *(f"Corrected {_line(c)}" for c in j.corrections),
             f"Covers AI Sessions on {_line(jf.machine)} only."]
    return _document(front, "\n".join(body) + "\n")


def _read_areas(lines: list[str], front: dict) -> tuple[dict, dict[str, str]]:
    """Each Area's time by Pursuit and the Pursuits' names, read from the Area lines in the
    order the front matter lists the Areas and Pursuits."""
    by_area = {str(a): _read_clock_duration(v) for a, v in front["by_area"].items()}
    by_pursuit = {str(k): _read_clock_duration(v) for k, v in front["by_pursuit"].items()}
    if len(lines) != len(by_area):
        raise ValueError(f"{len(lines)} Area lines for {len(by_area)} Areas")
    named = [(k, v) for k, v in by_pursuit.items() if k != ONE_OFF]
    spans = {a: _duration(v) for a, v in by_area.items()}
    area_width, span_width = max(len(_written(_line(a))) for a in by_area), max(len(v) for v in spans.values())
    areas: dict[str, dict[str, timedelta]] = {}
    names: dict[str, str] = {}
    for line, (area, total) in zip(lines, by_area.items()):
        head = f"  {_written(_line(area)).ljust(area_width)}  {spans[area].center(span_width)}  "
        if not line.startswith(head):
            raise ValueError(f"unreadable Area line: {line!r}")
        entries = line[len(head):].split(", ")
        count = len(entries) - entries[-1].startswith(ONE_OFF + " ")
        split: dict[str, timedelta] = {}
        for entry, (key, v) in zip(entries[:count], named[:count]):
            name, sep, rest = entry.rpartition(" " + _pursuit_duration(v))
            if not sep or rest:
                raise ValueError(f"unreadable Pursuit time: {entry!r}")
            split[key], names[key] = v, _read_cite_label(name)
        named = named[count:]
        if count < len(entries):
            alone = total - sum(split.values(), timedelta())
            if entries[-1] != f"one-off {_pursuit_duration(alone)}":
                raise ValueError(f"unreadable one-off time: {entries[-1]!r}")
            split[ONE_OFF] = alone
        areas[area] = split
    if named or sum((s.get(ONE_OFF, timedelta()) for s in areas.values()), timedelta()) != by_pursuit.get(
            ONE_OFF, timedelta()):
        raise ValueError("the Area lines do not show the front matter's Pursuits")
    return areas, names


def _parse_journal(text: str, tz: tzinfo | None) -> JournalFile:
    f, body = _split(text)
    evidence = tuple(EvidenceId(e) for e in f["evidence"])
    lines = body.split("\n")
    if lines[:2] != [f"# {_long_day(_as_date(f['day']))}", ""] or lines[-1] != "":
        raise ValueError("a Journal day starts with its day and ends with a newline")
    blocks = "\n".join(lines[2:-1]).split("\n\n")
    active, *area_lines = blocks[0].split("\n")
    m = _ACTIVE.match(active)
    if not m or int(m["n"]) != len(evidence):
        raise ValueError(f"unreadable Active time line: {active!r}")
    areas, names = _read_areas(area_lines, f["active_time"])
    active_time = DayTime(_read_clock_duration(f["active_time"]["total"]), areas, evidence)
    if m["span"] != _duration(active_time.total):
        raise ValueError(f"the Active time line shows another total: {active!r}")
    marks, sections = _Marks(tz), {}
    titles = dict(_JOURNAL_SECTIONS)
    for block in blocks[1:-1]:
        title, *items = block.split("\n")
        if title not in titles or titles[title] in sections or not items or not all(i.startswith("- ") for i in items):
            raise ValueError(f"unreadable Journal section: {title!r}")
        sections[titles[title]] = tuple(marks.parse(i.removeprefix("- ")) for i in items)
    evidence_line, *rest = blocks[-1].split("\n")
    e = _EVIDENCE.match(evidence_line)
    covers = _COVERS.match(rest[-1]) if rest else None
    if (not e or int(e["n"]) != len(evidence) or int(e["m"]) != len(evidence) or not covers
            or not all(line.startswith("Corrected ") for line in rest[:-1])):
        raise ValueError(f"unreadable Evidence lines: {blocks[-1]!r}")
    corrections = tuple(line.removeprefix("Corrected ") for line in rest[:-1])
    day = JournalDay(_as_date(f["day"]), active_time, sections.get("worked_on", ()), sections.get("decided", ()),
                     sections.get("finished", ()), corrections)
    return JournalFile(day, names, e["shared"].removeprefix(" "), covers["machine"])


# ------------------------------------------------------------------ evidence/<harness>/<yyyy>/<mm>/<id>/session.md
# Not sealed: regenerated from raw/. A turn's heading is
# "**<You | Harness> · <hh:mm>**", with the day before the time when it differs from the turn
# before, then " · <part>" for a folded part, " · Atlas tools" and " · time interpolated".
# A text line that starts with "**" or "\" is written with a "\" before it. A turn's time keeps
# its seconds when they are not zero, and its offset when the wall time is ambiguous (the
# repeated hour of a DST fallback), so it reads back as the same instant.

_TURN = re.compile(r"^\*\*(?P<who>[^·*]+?) · (?:(?P<day>\w{3} \d{1,2} \w{3} \d{4}) )?"
                   r"(?:(?P<hm>\d{2}:\d{2}(?::\d{2})?)(?P<offset>[+-]\d{2}:\d{2})?|undated)"
                   r"(?P<extra>(?: · [^*]+?)*)\*\*$")
_FLAGS = {"Atlas tools": "atlas_tool_turn", "time interpolated": "at_interpolated"}


def _scalar(s: str | None) -> str:
    if s is None:
        return "null"
    return s if re.fullmatch(r"[~A-Za-z0-9][A-Za-z0-9 ._/+~@-]*", s) and yaml.safe_load(s) == s else _q(s)


def _label(e: SessionEvidence) -> str:
    return e.harness.label + (f" via {e.interface}" if e.interface else "")


def _turn_heading(e: SessionEvidence, t: Turn, day: date | None) -> str:
    who = "You" if t.role == "owner" else e.harness.label
    when = "undated" if t.at is None else (_clock(t.at) if t.at.date() == day
                                          else f"{_short_day(t.at.date())} {_clock(t.at)}")
    extra = ([t.part] if t.part != "main" else []) + [flag for flag, field in _FLAGS.items() if getattr(t, field)]
    return "**" + " · ".join([who, when, *extra]) + "**"


def _clock(at: datetime) -> str:
    """A turn's wall time: hh:mm, then :ss unless zero, then the offset when the wall time is
    ambiguous."""
    text = at.strftime("%H:%M:%S" if at.second else "%H:%M")
    if at.replace(fold=1 - at.fold).utcoffset() != at.utcoffset():
        text += at.isoformat()[-6:]
    return text


def _read_clock(day: date, m: re.Match, tz: tzinfo | None) -> datetime | None:
    if m["hm"] is None:
        return None
    clock = time.fromisoformat(m["hm"])
    if m["offset"]:
        return local(datetime.fromisoformat(f"{day.isoformat()}T{clock.isoformat()}{m['offset']}"), tz)
    return _wall(datetime.combine(day, clock), tz)


def _render_session(e: SessionEvidence, tz: tzinfo | None) -> str:
    start = local(e.started, tz)
    parts = [f"  - {{name: {_plain(p.name)}, sha256: {_q(p.sha256)}, source: {_q(p.source)}, "
             f"fingerprint: {_q(p.fingerprint)}, origin: {_q(p.origin)}}}" for p in e.parts]
    front = ["atlas: session/1", f"id: {e.id}", f"harness: {e.harness.value}", f"namespace: {_scalar(e.namespace)}",
             f"interface: {_scalar(e.interface)}", f"native_key: {_scalar(e.native_key)}",
             f"title: {_scalar(e.title)}", f"machine: {_scalar(e.machine)}",
             f"cwd: {_scalar(e.cwd)}", f"git_branch: {_scalar(e.git_branch)}",
             f"started: {start.isoformat(timespec='seconds')}",
             f"last_at: {local(e.last_at, tz).isoformat(timespec='seconds')}", "parts:" + ("" if parts else " []"),
             *parts, "regenerated_from_raw: true"]
    head = f"# {_label(e)} · {_short_day(start.date())} {start:%H:%M}" + (f" · {e.cwd}" if e.cwd else "")
    blocks, day = [], start.date()
    for t in e.turns:
        at = replace(t, at=local(t.at, tz).replace(microsecond=0) if t.at else None)
        text = "\n".join("\\" + line if line.startswith(("**", "\\")) else line
                         for line in t.text.split("\n"))
        blocks.append(_turn_heading(e, at, day) + "\n" + text + "\n")
        day = at.at.date() if at.at else day
    return "---\n" + "\n".join(front) + "\n---\n" + head + "\n" + "".join("\n" + b for b in blocks)


def shown(e: SessionEvidence, turns: Sequence[Turn], tz: tzinfo | None) -> tuple[Turn, ...]:
    """`turns` as `e`'s session.md shows them: at the minute, masked."""
    return _parse_session(render("session", replace(e, turns=tuple(turns)), tz).decode(), tz).turns


def _parse_session(text: str, tz: tzinfo | None) -> SessionEvidence:
    f, body = _split(text)
    start = local(_as_datetime(f["started"]), tz)
    lines = body.split("\n")[2:]
    turns, day = [], start.date()
    heads = [n for n, line in enumerate(lines) if _TURN.match(line)]
    for n, end in zip(heads, heads[1:] + [len(lines)]):
        m = _TURN.match(lines[n])
        if m["day"]:
            day = datetime.strptime(m["day"], "%a %d %b %Y").date()
        at = _read_clock(day, m, tz)
        extra = [x for x in m["extra"].split(" · ") if x]
        content = lines[n + 1:end - (1 if end < len(lines) else 0)]
        if end == len(lines) and content and content[-1] == "":
            content = content[:-1]
        turns.append(Turn("owner" if m["who"] == "You" else "agent", at,
                          "\n".join(line[1:] if line.startswith("\\") else line for line in content),
                          next((x for x in extra if x not in _FLAGS), "main"),
                          **{field: flag in extra for flag, field in _FLAGS.items()}))
    return SessionEvidence(
        id=EvidenceId(f["id"]), harness=HarnessId(f["harness"]), interface=f["interface"], namespace=f["namespace"],
        native_key=str(f["native_key"]), title=f["title"], started=start, last_at=local(_as_datetime(f["last_at"]), tz),
        machine=f["machine"], cwd=f["cwd"], git_branch=f["git_branch"],
        parts=tuple(RawPart(str(p["name"]), p["sha256"], p["source"], p["fingerprint"], str(p.get("origin") or ""))
                    for p in f["parts"] or ()),
        turns=tuple(turns))


# ------------------------------------------------------------------ evidence/<id>/card.md

_CARD_LINES = (("Intent", "intent"), ("Outcome", "outcome"), ("Unfinished", "unfinished"), ("Where", "where"))


def _render_card(c: SessionCard, tz: tzinfo | None) -> str:
    marks = _Marks(tz)
    lines = [f"- {title}: {marks.render(s)}" for title, attr in _CARD_LINES if (s := getattr(c, attr)) is not None]
    front = ["atlas: card/1", f"evidence: {c.evidence}",
             f"one_off_area: {_plain(c.one_off_area) if c.one_off_area else 'null'}",
             f"one_off_day: {_opt_date(c.one_off_day)}"]
    return _document(front, "\n".join(lines) + "\n")


def _parse_card(text: str, tz: tzinfo | None) -> SessionCard:
    f, body = _split(text)
    marks = _Marks(tz)
    titles = dict(_CARD_LINES)
    read: dict[str, Statement] = {}
    for line in body.splitlines():
        title, sep, rest = line.removeprefix("- ").partition(": ")
        if not line.startswith("- ") or not sep or title not in titles or titles[title] in read:
            raise ValueError(f"unreadable card line: {line!r}")
        read[titles[title]] = marks.parse(rest)
    return SessionCard(EvidenceId(f["evidence"]), read["intent"], read["outcome"], read.get("unfinished"),
                       read.get("where"), f["one_off_area"], _as_date(f.get("one_off_day")))


def session_front(data: bytes) -> dict:
    """A session.md's front matter, for the parts the record holds."""
    return _split(data.decode())[0]


CODECS: dict[FileKind, Codec] = {
    "readme": Codec(_render_readme, _parse_readme),
    "settings": Codec(_render_settings, _parse_settings),
    "profile": Codec(_render_profile, _parse_profile),
    "pauses": Codec(_render_pauses, _parse_pauses),
    "forgotten": Codec(_render_forgotten, _parse_forgotten),
    "pursuit": Codec(_render_pursuit, _parse_pursuit),
    "notes": Codec(_render_notes, _parse_notes),
    "brief": Codec(_render_brief, _parse_brief),
    "journal": Codec(_render_journal, _parse_journal),
    "session": Codec(_render_session, _parse_session),
    "card": Codec(_render_card, _parse_card),
}
