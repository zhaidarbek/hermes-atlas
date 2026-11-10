"""Turn the owner's plain words into Commands and Queries. One interpreter for every place
the owner writes to Atlas: claimed Telegram messages, /atlas, the CLI.

Two tiers:
1. A deterministic grammar for the replies that must never depend on a model:
   "forget" (exact, only with a pending plan), "apply" (latest pending hand edit of
   profile.md -> ApplyNote), "<n> done|skip|prompt", bare "prompt" right after a
   resume answer (D Q2), "close it",
   "continue|park|drop" with a decision pending, "remind me <weekday|date>", "pause [until ...]",
   "I'm back". The brief or Review being replied to gives the numbers their meaning.
2. Otherwise one ctx.llm structured call (task atlas_converse) whose JSON schema is generated
   from the Command and Query dataclasses in atlas.model (one list; no hand-kept schema).
   Output is parsed into domain types at this boundary; Pursuit names are resolved against the
   Snapshot (Ambiguous -> ask which).

Any reply other than "forget" while a ForgetPlan is pending cancels it first (W Q16).

Built so far: "pause [until <end> | for <days>][, <words>]" (PauseCmd, its end read by
atlas.pausing) and "I'm back" (EndPause), each the whole message; the grammar for "<n> prompt", "<n>
done [detail]", "skip <n> [detail]" (and "<n> skip"), "<n> remind me <weekday>", and
"park|drop|continue <words naming one Pursuit>" ("continue" only for an active or parked one), a bare
"park|drop|continue [it|that]" for the brief's decision question or, with none asked, the one park by
silence, "remind me <weekday> [about <words>]" for the decision question, "close it" (the ending the
brief proposed); and the questions: "what have I neglected?" (NeglectedQ), "find the conversation
where <words>" (FindQ),
"send|tell|email|message <someone> <words>" in a message addressed to Atlas by name (OutsideAction,
always refused), "how long did I work
this|last week[, by Area]" (PeriodTimeQ), "where did I leave off on <words naming one Pursuit>"
(ResumeQ), and a bare "prompt" right after a ResumeQ answer. A message is split into clauses at
each sentence end, and at a comma or semicolon followed by a command ("1 done, 2 skip"). A clause that reads as nothing right after "<n>
done" or a skip is that Step's detail ("skip 2, not today", "2 done. tablet app, nothing fancy");
any other clause must read, or the message is UNREAD, and so is a message with no clause at all.
Numbers name the Steps of the brief replied to, else of the latest brief the owner was sent.
"profile: <words>" is one CorrectProfile, the model reading the words against the Profile, and
"apply" names the latest hand edit of the Profile not yet applied. The rest of the model tier reads
only the corrections to filing (MODEL_READS), only those the owner's words ask for (ASKED_BY), and
only words the grammar left UNREAD: words that ask for none make no model call. An UNREAD or
AMBIGUOUS message is not Atlas's yet, and goes to Hermes's agent, which has
Atlas's tools.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, fields, replace
from datetime import date, timedelta
from typing import Callable, Sequence, get_args
from zoneinfo import ZoneInfo

from . import forget, neglect, pausing
from .librarian import CONVERSE, Librarian, Llm, ModelError, ask
from .messages import RESUME_HINT
from .model import (AddArea, ApplyNote, Area, BriefStep, Command, Continue, EndingProposal, EndPause, FindQ,
                    HoldDecision, HoldStep, MarkStep, Merge, NeglectedQ, NoteOrigin, OutsideAction, OwnerMessage,
                    PauseCmd, PeriodTimeQ, Pursuit, Query, ResumePromptQ, ResumeQ, SendStatus, SentBrief, SetArea,
                    SetStatus, Split, Status, WhichQ)
from .record import Snapshot

UNREAD = "UNREAD"
PROFILE_FILE = "profile.md"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Interpretation:
    commands: tuple[Command, ...]
    asks: tuple[Query, ...]
    cancel_pending_forget: bool
    unclear: str | None                      # UNREAD, or "AMBIGUOUS:<name>|<name>..." when words named several Pursuits


class _Unclear(Exception):
    pass


def interpret(msg: OwnerMessage, snap: Snapshot, librarian: Librarian | None) -> Interpretation:
    asked = forget.interpret(msg, snap)
    if asked is not None:
        return Interpretation((asked,), (), False, None)
    return replace(_read(msg, snap, librarian), cancel_pending_forget=snap.pending_forget() is not None)


def _read(msg: OwnerMessage, snap: Snapshot, librarian: Librarian | None) -> Interpretation:
    whole = _paused(msg, snap) or _whole(msg.text, snap, librarian)
    if whole is not None:
        return whole
    said = _grammar(msg, snap)
    kinds = asked(msg.text)
    if said.unclear != UNREAD or librarian is None or not kinds:
        return said
    try:
        return Interpretation(_modelled(msg.text, snap, librarian._llm, kinds), (), False, None)
    except _Unclear as unclear:
        return Interpretation((), (), False, str(unclear))
    except Exception as failed:
        log.warning("Atlas could not ask the model to read a message (%s)", type(failed).__name__)
        return said


def _grammar(msg: OwnerMessage, snap: Snapshot) -> Interpretation:
    read: list[Command | Query] = []
    for clause in _clauses(msg.text):
        try:
            read.append(_clause(clause, msg, snap, bool(read) and isinstance(read[-1], MarkStep)))
        except _Unclear as unclear:
            if str(unclear) == UNREAD and read and isinstance(read[-1], MarkStep) and not _command_shaped(clause):
                read[-1] = replace(read[-1], detail=", ".join(filter(None, (read[-1].detail, clause))))
                continue
            return Interpretation((), (), False, str(unclear))
    if not read:
        return Interpretation((), (), False, UNREAD)
    queries = get_args(Query)
    commands = tuple(r for r in read if not isinstance(r, queries))
    return Interpretation(commands, tuple(r for r in read if isinstance(r, queries)), False, None)


def _whole(text: str, snap: Snapshot, librarian: Librarian | None) -> Interpretation | None:
    """A message read as one command whatever its sentences: "profile: <words>", whose words the
    model reads against the Profile, and "apply", the latest hand edit of the Profile not yet
    applied. None for any other message."""
    if _APPLY.fullmatch(text.strip()):
        edits = [n for n in snap.pending_applies() if n.origin is NoteOrigin.HAND_EDIT and n.via == PROFILE_FILE]
        if not edits or librarian is None:
            return Interpretation((), (), False, UNREAD)
        return Interpretation((ApplyNote(edits[-1].id),), (), False, None)
    m = _PROFILE.fullmatch(text.strip())
    if m is None:
        return None
    if librarian is None:
        return Interpretation((), (), False, UNREAD)
    try:
        return Interpretation((librarian.correct_profile(snap, m["words"]),), (), False, None)
    except ModelError as unusable:
        log.info("Atlas could not read a Profile correction (%s)", type(unusable).__name__)
        return Interpretation((), (), False, UNREAD)


def _paused(msg: OwnerMessage, snap: Snapshot) -> Interpretation | None:
    """"pause", "pause until <end>" or "pause for <days>", then any words of the owner's own
    ("pause until sunday morning, we're at my parents'"), and "I'm back". An end Atlas cannot read
    leaves the message UNREAD, and so do words that ask about the Pause or take it back ("pause -
    what does that do?", "pause. actually no"). None for any other message."""
    text = " ".join(msg.text.split())
    if _BACK.fullmatch(text):
        return Interpretation((EndPause(),), (), False, None)
    m = _PAUSE.fullmatch(text)
    if m is None:
        return None
    if text.rstrip().endswith("?") or _TAKEN_BACK.match((m["tail"] or "").lstrip(" ,;:.!-")):
        return Interpretation((), (), False, UNREAD)
    said = m["said"].strip(" ,;:.!")
    if m["until"] is None and m["for"] is None:
        return Interpretation((PauseCmd(None, said),), (), False, None)
    tz = ZoneInfo(snap.settings().timezone)
    end = pausing.until(m["until"], msg.at, tz) if m["until"] else pausing.lasting(m["for"], msg.at)
    if end is None:
        return Interpretation((), (), False, UNREAD)
    return Interpretation((PauseCmd(end, said),), (), False, None)


_PAUSE = re.compile(r"pause(?:\s+atlas)?(?P<said>(?:\s+(?:until|till|til)\s+(?P<until>[^,;.!]+?)"
                    r"|\s+for\s+(?P<for>[^,;.!]+?))?\s*(?:[,;:.!-]\s*(?P<tail>.*))?)", re.I | re.S)
TAKE_BACK = ("no", "not", "don't", "dont", "do not", "never mind", "nevermind", "actually", "wait", "cancel",
             "nope", "nah", "scratch that", "forget it", "kidding", "jk")
"""Words that, opening what follows "pause", ask Atlas not to pause after all."""
_TAKEN_BACK = re.compile(r"(?:%s)\b" % "|".join(re.escape(w).replace("'", "['’]") for w in TAKE_BACK), re.I)
_BACK = re.compile(r"(?:(?:i'?m|i am|we'?re|we are)\s+back|back|unpause|end\s+(?:the\s+)?pause|stop\s+pausing"
                   r"|come\s+back|resume)[.!]*", re.I)
_APPLY = re.compile(r"apply[.!]?", re.I)
_PROFILE = re.compile(r"profile\s*[:,-]?\s+(?P<words>\S.*)", re.I | re.S)


def _command_shaped(clause: str) -> bool:
    """Whether the words have a command's shape, read or not: such words are never a Step's detail.
    An outside action's shape is not one: "1 done, message queue fixed" is a Step's detail."""
    return any(pattern.fullmatch(clause) for pattern, read in _GRAMMAR if read is not _outside)


def _clauses(text: str) -> list[str]:
    clauses = []
    for sentence in re.split(r"(?<=\.)\s+|\n+", text.strip()):
        parts = re.split(r"([,;]\s*)", sentence.strip().rstrip(".").strip())
        clause = parts[0]
        for mark, part in zip(parts[1::2], parts[2::2]):
            if _command_shaped(part.strip()):
                clauses.append(clause)
                clause = part
            else:
                clause += mark + part
        clauses.append(clause)
    return [c.strip() for c in clauses if c.strip(" .,;")]


def _clause(clause: str, msg: OwnerMessage, snap: Snapshot, after_step: bool) -> Command | Query:
    """An outside action is read only in a message addressed to Atlas by name, and never right
    after a Step's mark, where its words are that Step's detail (scene 24)."""
    for pattern, read in _GRAMMAR:
        if read is _outside and (after_step or not msg.addressed):
            continue
        m = pattern.fullmatch(clause)
        if m:
            return read(m, msg, snap)
    raise _Unclear(UNREAD)


def _prompt(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Query:
    brief, _ = _item(int(m["n"]), msg, snap)
    return ResumePromptQ(brief=brief.day, item=int(m["n"]))


def _mark(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Command:
    _, step = _item(int(m["n"]), msg, snap)
    outcome = "done" if m["word"].lower() == "done" else "skipped"
    detail = " ".join(m["detail"].split()) if m["detail"] else None
    return MarkStep(step.pursuit, step.step, outcome, detail or None)


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _hold(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Command:
    """"3 remind me friday": hold Step 3 until the next Friday after today, in the owner's zone."""
    _, step = _item(int(m["n"]), msg, snap)
    return HoldStep(step.pursuit, _next(m["day"], msg, snap))


def _hold_question(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Command:
    """"remind me friday [about <words naming one Pursuit>]": hold the decision question, the
    brief's own when no words name a Pursuit."""
    if m["words"]:
        return HoldDecision(_resolve(m["words"], snap.pursuits()).id, _next(m["day"], msg, snap))
    brief = _brief(msg, snap)
    if brief is None or brief.decision is None:
        raise _Unclear(UNREAD)
    return HoldDecision(brief.decision.pursuit, _next(m["day"], msg, snap))


def _next(day: str, msg: OwnerMessage, snap: Snapshot) -> date:
    """The next such weekday after today, in the owner's zone."""
    today = msg.at.astimezone(ZoneInfo(snap.settings().timezone)).date()
    return today + timedelta(days=(WEEKDAYS.index(day.lower()) - today.weekday() - 1) % 7 + 1)


def _bare_status(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Command | Query:
    """"continue", "park" or "drop", with "it" or "that" at most: the decision the brief replied to
    asked about, else the latest brief's. With no question asked, "continue" undoes the park by
    silence made before the brief replied to, and asks which when there were several."""
    word = m["word"].lower()
    brief = _brief(msg, snap)
    if brief is not None and brief.decision is not None:
        asked = next((p for p in snap.pursuits() if p.id == brief.decision.pursuit), None)
        if asked is None or _named_another(msg, snap, asked):
            raise _Unclear(UNREAD)
        return _status_of(asked, word)
    if word != "continue":
        raise _Unclear(UNREAD)
    since = min((a.at for a in brief.attempts if a.status is SendStatus.SENT), default=None) \
        if brief is not None and msg.reply_to is not None else None
    parked = [p for p, at in neglect.parked_by_silence(snap) if since is None or at <= since]
    if len(parked) > 1:
        return WhichQ(tuple(sorted(p.name for p in parked)))
    if not parked or _named_another(msg, snap, parked[0]):
        raise _Unclear(UNREAD)
    return Continue(parked[0].id)


def _named_another(msg: OwnerMessage, snap: Snapshot, p: Pursuit) -> bool:
    """A message that replies to nothing follows the owner's last exchange with Atlas, and when
    Atlas's answer there named another Pursuit, "drop it" may mean that one."""
    if msg.reply_to is not None:
        return False
    said = [n for n in snap.notes() if n.origin is NoteOrigin.OWNER and n.atlas_reply is not None and n.at <= msg.at]
    last = said[-1].atlas_reply if said else ""
    return any(q.name in last for q in snap.pursuits() if q.id != p.id)


def _status(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Command:
    return _status_of(_resolve(m["words"], snap.pursuits()), m["word"].lower())


def _status_of(p: Pursuit, word: str) -> Command:
    if word == "continue":
        if p.status.is_ending:
            raise _Unclear(UNREAD)
        return Continue(p.id)
    return SetStatus(p.id, Status.PARKED if word == "park" else Status.DROPPED)


def _close(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Command:
    """"close it" marks done the Pursuit whose ending the brief replied to proposed, or the latest
    brief sent when the message replies to none."""
    proposed = [id for id in neglect.proposed_in(_brief(msg, snap))
                if any(p.id == id and not p.status.is_ending
                       and any(isinstance(x, EndingProposal) and x.to is Status.DONE for x in p.proposals)
                       for p in snap.pursuits())]
    if len(proposed) != 1:
        raise _Unclear(UNREAD)
    return SetStatus(proposed[0], Status.DONE)


def _neglected(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Query:
    return NeglectedQ()


def _find(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Query:
    return FindQ(" ".join(m["text"].split()))


def _outside(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Command:
    about = re.sub(r"^(?:the|my|our)\s+|\s+(?:status|update|progress|news)$", "", m["what"].strip(), flags=re.I)
    return OutsideAction(m["verb"].lower(), m["to"], about or None)


def _period_time(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Query:
    today = msg.at.astimezone(ZoneInfo(snap.settings().timezone)).date()
    start = today - timedelta(days=today.weekday() + (7 if m["when"].lower() == "last" else 0))
    return PeriodTimeQ(start, start + timedelta(days=6), "area")


def _resume(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Query:
    return ResumeQ(_resolve(m["words"], snap.pursuits()).id)


def _bare_prompt(m: re.Match, msg: OwnerMessage, snap: Snapshot) -> Query:
    """A bare "prompt" names the Pursuit of the owner's last exchange with Atlas when that was a
    ResumeQ answer (D Q2)."""
    said = [n for n in snap.notes() if n.origin is NoteOrigin.OWNER and n.atlas_reply is not None]
    last = said[-1] if said else None
    if last is None or last.about is None or not last.atlas_reply.rstrip().endswith(RESUME_HINT):
        raise _Unclear(UNREAD)
    return ResumePromptQ(pursuit=last.about)


_DETAIL = r"(?:[,:;]?\s+(?P<detail>.+))?"
_GRAMMAR: tuple[tuple[re.Pattern, Callable[[re.Match, OwnerMessage, Snapshot], Command | Query]], ...] = (
    (re.compile(r"(?P<n>\d+)\s+prompt", re.I), _prompt),
    (re.compile(rf"(?P<n>\d+)\s+(?P<word>done|skip){_DETAIL}", re.I | re.S), _mark),
    (re.compile(rf"(?P<word>skip)\s+(?P<n>\d+){_DETAIL}", re.I | re.S), _mark),
    (re.compile(rf"(?P<n>\d+)\s+remind\s+me\s+(?:on\s+)?(?P<day>{'|'.join(WEEKDAYS)})", re.I), _hold),
    (re.compile(rf"remind\s+me\s+(?:on\s+)?(?P<day>{'|'.join(WEEKDAYS)})(?:\s+about\s+(?P<words>.+))?", re.I | re.S),
     _hold_question),
    (re.compile(r"(?P<word>park|drop|continue)(?:\s+(?:it|that))?[.!]*", re.I), _bare_status),
    (re.compile(r"(?P<word>park|drop|continue)\s+(?P<words>.+)", re.I | re.S), _status),
    (re.compile(r"prompt", re.I), _bare_prompt),
    (re.compile(r"close\s+it", re.I), _close),
    (re.compile(r"what\s+(?:have|did)\s+i\s+neglect(?:ed)?(?:\s+lately)?\??", re.I), _neglected),
    (re.compile(r"find\s+(?:me\s+)?(?:the\s+|a\s+)?(?:conversation|session|chat)s?\s+(?:where|in which|when|about|on)"
                r"\s+(?P<text>.+?)\??", re.I | re.S), _find),
    (re.compile(r"(?P<verb>send|tell|email|e-mail|message|text|forward|ping)\s+(?P<to>(?!me\b|myself\b)[^\s,]+),?"
                r"\s+(?P<what>.+?)\??", re.I | re.S), _outside),
    (re.compile(r"how\s+(?:long|much)\s+did\s+i\s+work\s+(?P<when>this|last)\s+week(?:\s*,?\s*by\s+areas?)?\??",
                re.I), _period_time),
    (re.compile(r"where\s+did\s+i\s+leave\s+off\s+(?:on|with|in)\s+(?P<words>.+?)\??", re.I | re.S), _resume),
)


def _item(n: int, msg: OwnerMessage, snap: Snapshot) -> tuple[SentBrief, BriefStep]:
    """Step n of the brief the message replies to or, when it replies to nothing, of the latest
    brief with a SENT attempt: a brief still being sent is not one the owner has seen."""
    brief = _brief(msg, snap)
    step = next((s for s in brief.steps if s.n == n), None) if brief else None
    if step is None:
        raise _Unclear(UNREAD)
    return brief, step


def _brief(msg: OwnerMessage, snap: Snapshot) -> SentBrief | None:
    if msg.reply_to is None:
        delivered = [b for b in snap.briefs() if any(a.status is SendStatus.SENT for a in b.attempts)]
        return delivered[-1] if delivered else None
    if msg.reply_to.kind != "brief":
        return None
    return snap.brief(date.fromisoformat(msg.reply_to.key))


_FILLER = frozenset({"the", "my", "a", "an", "one", "project", "pursuit", "it", "its", "this", "that", "these",
                     "those", "them"})
"""Words that name no Pursuit, so "drop it" names none."""


def _resolve(words: str, pursuits: Sequence[Pursuit]) -> Pursuit:
    """The one Pursuit the owner's words name: its exact name, or the only Pursuit whose name holds
    each of their other words ("the bike planner" -> "Bike route planner")."""
    exact = [p for p in pursuits if p.name.casefold() == words.strip().casefold()]
    if len(exact) == 1:
        return exact[0]
    wanted = {w for w in _words(words) if w not in _FILLER}
    found = [p for p in pursuits if wanted and wanted <= set(_words(p.name))]
    if len(found) > 1:
        raise _Unclear("AMBIGUOUS:" + "|".join(sorted(p.name for p in found)))
    if not found:
        raise _Unclear(UNREAD)
    return found[0]


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


MODEL_READS = (Merge, Split, AddArea, SetArea)
"""The Commands the model tier reads, the corrections to filing of scene 5."""
ASKED_BY = (
    (re.compile(r"\bmerge\b"), (Merge,)),
    (re.compile(r"\bsplit\b.*\bout of\b"), (Split,)),
    (re.compile(r"\bnew area\b"), (AddArea, SetArea)),
)
"""The words of scene 5 that ask for each kind of correction, matched on the casefolded message. The
model may answer only with the kinds the owner's words ask for, so a merge, which has no one-step
undo, needs the owner's own word "merge"."""
ONCE = (Merge, Split)
"""Corrections the model may answer at most once per message."""


def asked(text: str) -> frozenset[type]:
    """The kinds of correction the owner's words ask for."""
    said = text.casefold()
    return frozenset(cls for words, kinds in ASKED_BY if words.search(said) for cls in kinds)
_NAMING = ("a", "b", "pursuit")
"""Fields that name a Pursuit."""

FILING = """The owner wrote to Atlas, which keeps their Pursuits: things they started and might return
to, each in one Area of their life. Read the message as corrections to how the Pursuits are filed,
with only these commands:
Merge: Pursuits a and b are one Pursuit. into_name is the one Pursuit's name: short, and what the
two have in common. rule is the owner's reason, in their words, or "" when they gave none.
Split: some of the work in pursuit is a Pursuit of its own. new_name is its name, starting with
what it was split from, and criterion the owner's words for what goes.
AddArea: area is a new Area of the owner's life, in their words.
SetArea: pursuit belongs in the Area area.
Name each Pursuit exactly as it is listed. Answer with no commands when the message asks for
anything else as well, or when you are unsure what it asks."""


def filing_schema() -> dict:
    """Generated from the Command dataclasses the model tier reads: each command names its type
    and fills its own fields."""
    names = dict.fromkeys(f.name for cls in MODEL_READS for f in fields(cls))
    return {"title": "converse", "type": "object", "required": ["commands"], "properties": {
        "commands": {"type": "array", "items": {"type": "object", "required": ["command"], "properties": {
            "command": {"enum": [cls.__name__ for cls in MODEL_READS]},
            **{name: {"type": ["string", "null"]} for name in names}}}}}}


def _modelled(text: str, snap: Snapshot, llm: Llm, kinds: frozenset[type]) -> tuple[Command, ...]:
    """The model's reading of words the grammar cannot read, as commands of the `kinds` the words
    ask for only, and a Merge or Split once. Every Pursuit a command names must be one the record
    holds, and every Area one the owner has or adds in the same message."""
    pursuits, areas = snap.pursuits(), list(snap.profile().areas)
    listed = "\n".join(f"- {p.name} · {p.kind.value} · {p.area} · {p.status.value}" for p in pursuits)
    out = ask(llm, CONVERSE, FILING, f"Areas: {', '.join(areas)}\nPursuits:\n{listed}\n\nMessage: {text}",
              filing_schema())
    named = {cls.__name__: cls for cls in MODEL_READS}
    read: list[Command] = []
    for row in out.get("commands") or ():
        cls = named.get(row.get("command"))
        values = {f.name: row.get(f.name) for f in fields(cls)} if cls else {}
        values |= {"rule": values["rule"] or ""} if "rule" in values else {}
        if cls not in kinds or not all(isinstance(v, str) for v in values.values()):
            raise _Unclear(UNREAD)
        values = {k: " ".join(v.split()) for k, v in values.items()}
        if any(not v for k, v in values.items() if k != "rule"):
            raise _Unclear(UNREAD)
        for name in _NAMING:
            if name in values:
                values[name] = _resolve(values[name], pursuits).id
        if cls is AddArea and not _area(values["area"], areas):
            areas.append(Area(values["area"]))
        if cls is SetArea:
            area = _area(values["area"], areas)
            if area is None:
                raise _Unclear(UNREAD)
            values["area"] = area
        if cls is Merge and values["a"] == values["b"]:
            raise _Unclear(UNREAD)
        read.append(cls(**values))
    if not read or any(sum(isinstance(c, cls) for c in read) > 1 for cls in ONCE):
        raise _Unclear(UNREAD)
    return tuple(read)


def _area(words: str, areas: Sequence[str]) -> Area | None:
    return next((Area(a) for a in areas if a.casefold() == words.casefold()), None)
