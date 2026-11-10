"""What an agent in a Harness reads from Atlas and writes to it, as pure functions: a Snapshot and
the call in, a Decision or text out. agentapi reads the call and the calling process; app commits.

An agent may read everything and add Notes unasked (R4 Q4). It changes a Pursuit only on the
owner's words found in its own Session, and only the Pursuit that lists that Session; anything
else it asks to change is kept as a refused-update Note the owner can "apply" (D Q12). Its text
is untrusted: masked before it is stored, never longer than a model's answer may be, and kept as
the agent's, never as Atlas's own words.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import privacy, rules, told, untrusted
from .librarian import FIELD_MAX, TEXT_MAX
from .messages import Style, statement
from .model import (Actor, AddNote, Agent, Cite, EditResumeNote, EvidenceId, Fact, HarnessId, HistoryLine,
                    JournalDay, Note, NoteOrigin, Outcome, Pursuit, PursuitId, Statement, cites, harness_of)
from .record import Snapshot
from .rules import Decision
from .sources import _caller

WORDS_MAX = _caller.INSTRUCTION_MAX          # the owner's whole message an update quotes
PENDING_MAX = 3                              # refused updates one Harness may have waiting for "apply"
NAME_MAX = FIELD_MAX["name"]
REFUSED = ("Atlas could not confirm your instruction in this Session; it kept it as a Note and will offer "
           "to apply it.")
REFUSED_REPLY = 'Not applied: I could not confirm these words in that Session. Reply "apply" to make the change.'
NOT_KEPT = ("Atlas keeps {n} changes from {harness} that the owner has not applied yet. It kept those and did "
            "not keep this one.")
USED = "Atlas already made the change these words asked for. One message from the owner makes one change."
UNTOLD = ('owner_words must be the whole message the owner typed to you, as they typed it, and it must name Atlas, '
          'as in "tell atlas ...".')
MACHINE_ONLY = "Covers AI Sessions on {machine} only. Work on other machines is not collected. [F]"


class Unusable(ValueError):
    """A call Atlas cannot act on; nothing is written, and the message is the tool's answer."""


def text(value: object, most: int, what: str) -> str:
    """An agent's text as Atlas keeps it and shows it (untrusted.one_line), refused when Atlas does
    not take it, never cut."""
    try:
        return untrusted.one_line(value, most, what)
    except untrusted.Refused as refused:
        raise Unusable(str(refused)) from None


def pursuit(snap: Snapshot, words: object) -> Pursuit:
    """The one Pursuit the words name, by its id or its name."""
    words = text(words, NAME_MAX, "pursuit")
    named = [p for p in snap.pursuits() if p.id == words or p.name.casefold() == words.casefold()]
    named = named or [p for p in snap.pursuits() if words.casefold() in p.name.casefold()]
    if len(named) != 1:
        raise Unusable("Name one Pursuit as atlas_read shows it." if named else "Atlas has no Pursuit by that name.")
    return named[0]


def asked(p: Pursuit, change: object, owner_words: str) -> told.Asked:
    """What an update asks for: `remove` (at most one text, the agent's words for the Open question,
    used only in its answer) and `add_decided` (true: add the owner's message as a decision). What
    it removes and adds comes from the owner's message (told). Anything else refuses the call."""
    if not isinstance(change, dict) or set(change) - {"remove", "add_decided"}:
        raise Unusable('change takes "remove", a list of at most one text, and "add_decided", true or false.')
    remove, decide = change.get("remove") or [], change.get("add_decided", False)
    if not isinstance(remove, list) or len(remove) > 1:
        raise Unusable("An update removes at most one Open question: give at most one text in remove.")
    if not isinstance(decide, bool):
        raise Unusable('"add_decided" is true or false: Atlas adds the owner\'s own message as the decision.')
    if not remove and not decide:
        raise Unusable("change asks for nothing.")
    if not _caller.names_atlas(owner_words):
        raise Unusable(UNTOLD)
    return told.Asked(p.id, text(remove[0], TEXT_MAX, "a line to remove") if remove else None, decide)


def decide(snap: Snapshot, cmd: AddNote | told.Asked, who: Agent, now: datetime, words: str | None) -> Decision:
    """An agent's Note, or its change to a Resume Note on the owner's `words`."""
    if isinstance(cmd, AddNote):
        return note(snap, who, cmd.text, snap.pursuit(cmd.about) if cmd.about else None, now)
    return update(snap, who, cmd, words, now)


def note(snap: Snapshot, who: Agent, said: str, about: Pursuit | None, now: datetime) -> Decision:
    """A Note from an agent, citing the agent's Session when Atlas could bind it, so Forget of that
    Session removes it too."""
    refused = rules.authorize(AddNote(said, about.id if about else None), who)
    if refused:
        return Decision(snap.changes(), Outcome(False, (), refused))
    n = _note(who, NoteOrigin.AGENT, said, about.id if about else None, now)
    shown = f'Added a Note to {about.name}: "{said}"' if about else f'Added a Note: "{said}"'
    return Decision(snap.changes().append_note(n), Outcome(True, (shown,)))


def update(snap: Snapshot, who: Agent, asked: told.Asked, words: str, now: datetime) -> Decision:
    """The change the owner's own message makes (told.owners), with a History line quoting it, when
    find_instruction found that message in the calling Session and it made no change before;
    otherwise a refused-update Note keeping the change told.offered derives from the message, for
    "apply" (D Q12), unless its Harness already has PENDING_MAX of those waiting. Nothing is applied
    and later reverted."""
    p = snap.pursuit(asked.pursuit)
    session = _caller.session_id(who.session) if who.session else None
    if session is not None and _used(snap, session, words):
        return Decision(snap.changes(), Outcome(False, (USED,), "USED"))
    verified = _caller.sealed(who.instruction) and who.instruction.session == who.session
    message = text(who.instruction.text, WORDS_MAX, "the owner's message") if verified else words
    if verified:
        listing = [q for q in snap.pursuits() if any(m.evidence == session for m in q.sessions)]
        cmd = told.owners(p, listing, message, asked)
        if isinstance(cmd, EditResumeNote) and rules.authorize(cmd, who) is None:
            return _applied(snap, who, p, asked, cmd, message, now)
    cmd = told.offered(p, message, asked)
    if cmd is None:
        raise Unusable("Atlas cannot make that change: an update removes the one Open question it names and adds "
                       "the owner's message as a decision.")
    waiting = sum(1 for n in snap.pending_applies() if n.origin is NoteOrigin.REFUSED_UPDATE and n.via == who.harness)
    if waiting >= PENDING_MAX:
        shown = NOT_KEPT.format(n=waiting, harness=HarnessId(who.harness).label)
        return Decision(snap.changes(), Outcome(False, (shown,), "NOT_KEPT"))
    n = replace(_note(who, NoteOrigin.REFUSED_UPDATE, message, p.id, now), pending=cmd, atlas_reply=REFUSED_REPLY)
    return Decision(snap.changes().append_note(n), Outcome(False, (REFUSED,), "NOT_TOLD"))


def _applied(snap: Snapshot, who: Agent, p: Pursuit, asked: told.Asked, cmd: EditResumeNote, message: str,
             now: datetime) -> Decision:
    said = Cite(_caller.session_id(who.session), who.session.harness.label, who.instruction.at)
    edited, removed = rules.edit_resume(p, cmd, said)
    if removed is None:
        return Decision(snap.changes(), Outcome(False, ("The Open question to remove is no longer there.",), "STALE"))
    history = HistoryLine(rules._next_history_id(p), now.replace(second=0, microsecond=0), Actor.AGENT,
                          Fact(_on_your_words(message), said), agent=who.session.harness.label)
    edited = replace(edited, history=(*edited.history, history))
    return Decision(snap.changes().put_pursuit(edited), Outcome(True, (told.answer(p.name, asked, cmd),)))


def _on_your_words(words: str) -> str:
    return f'Changed the Resume Note on your words: "{words}"'


def _used(snap: Snapshot, session: EvidenceId, words: str) -> bool:
    """Whether an update already made a change on this message of this Session: its History line
    quotes the message and cites the Session."""
    quoted = " ".join(_on_your_words(words).casefold().split())
    return any(h.actor is Actor.AGENT and " ".join(h.statement.text.casefold().split()) == quoted and session in cites(h.statement)
               for p in snap.pursuits() for h in p.history)


def _note(who: Agent, origin: NoteOrigin, said: str, about: PursuitId | None, now: datetime) -> Note:
    via = HarnessId(who.harness)
    at = now.replace(second=0, microsecond=0)
    key = f"{via.value}:{origin.value}:{at.isoformat()}:{said}"
    digest = base64.b32encode(hashlib.sha256(key.encode()).digest()).decode().lower()
    derived = (_caller.session_id(who.session),) if who.session else ()
    return Note(EvidenceId(f"nt-{digest[:8]}"), at, origin, via.value, said, about=about, derived_from=derived,
                cwd=who.cwd)


# ------------------------------------------------------------------ reading, as an agent reads it

def resume(snap: Snapshot, p: Pursuit, now: datetime) -> tuple[str, list[dict]]:
    """The Resume Note as scene 12 shows it to an agent: marks bare, their sources in the citations."""
    r = p.resume
    parts: list[tuple[str, Statement]] = [("Left off: ", s) for s in r.left_off]
    parts += [("Decided: ", s) for s in r.decided] + [("Open question: ", s) for s in r.open_questions]
    if p.next_step is not None:
        step = p.next_step.text.text
        parts.append(("Next Step: ", replace(p.next_step.text, text=step[:1].lower() + step[1:]
                                             if step[1:2].islower() else step)))
    lines = [f'Resume Note for "{p.name}":', *(f"Where: {s.text}" for s in r.where),
             *(label + statement(s, Style.TELEGRAM) for label, s in parts)]
    reopen = [_reopened(snap, id, now) for id in r.reopen]
    if any(reopen):
        lines.append("Sessions to reopen: " + ", ".join(x for x in reopen if x))
    return "\n".join(lines), citations(snap, (*r.where, *(s for _, s in parts)))


def day(snap: Snapshot, d: date, j: JournalDay | None) -> tuple[str, list[dict]]:
    """A Journal day as scene 23 shows it to an agent: each line with its Pursuit's Area and the
    Session it rests on, the day's Active Time, and what the Journal does not cover (ADR 0003)."""
    head = f"Journal for {d:%A} {d.day} {d:%B %Y}:"
    machine = MACHINE_ONLY.format(machine=snap.settings().machine)
    if j is None:
        return "\n".join([f"Atlas has no Journal for {d:%A} {d.day} {d:%B %Y}.", machine]), []
    areas = {p.name: p.area for p in snap.pursuits()}
    noon = datetime.combine(d, time(12), ZoneInfo(snap.settings().timezone))
    lines = [head]
    for s in j.lines:
        name, sep, rest = s.text.partition(": ")
        shown = replace(s, text=f"{name} ({areas[name]}): {rest}") if sep and name in areas else s
        lines.append("- " + statement(shown, Style.TELEGRAM, cite=noon, one_day=True))
    once = one_offs(snap, d)
    lines += ["- " + statement(s, Style.TELEGRAM, cite=noon, one_day=True) for s in once]
    lines += [f"Active time: {_duration(j.time.total)}. [F]", machine]
    return "\n".join(lines), citations(snap, (*j.lines, *once))


def one_offs(snap: Snapshot, d: date) -> list[Fact]:
    """A line for each one-off Session of the day, as scene 23 shows it, from its card: "One-off
    question, no Pursuit (family): a birthday party plan for a seven-year-old.", cited by its
    Harness and start time. The Journal itself gives a one-off no line (record-layout)."""
    tz = ZoneInfo(snap.settings().timezone)
    out = []
    for s in snap.sessions(day=d):
        card = snap.card(s.id)
        if card is None or card.one_off_area is None or s.started.astimezone(tz).date() != d:
            continue
        intent = privacy.scrub(" ".join(card.intent.text.split()))[:TEXT_MAX].rstrip(".")
        first = intent.split(" ", 1)[0]
        intent = intent[:1].lower() + intent[1:] if first[1:].islower() or first in ("A", "An") else intent
        out.append(Fact(f"One-off question, no Pursuit ({card.one_off_area}): {intent}.",
                        Cite(s.id, s.harness.label, s.started.astimezone(tz))))
    return out


def citations(snap: Snapshot, statements) -> list[dict]:
    """Each statement's mark and the Evidence it rests on, so an agent can follow it."""
    out = []
    for s in statements:
        ids = sorted(cites(s))
        at = s.source.at.isoformat() if isinstance(s, Fact) and isinstance(s.source, Cite) else None
        out.append({"text": s.text, "mark": "fact" if isinstance(s, Fact) else "inference", "evidence": [
            {"id": id, "harness": h.label if (h := harness_of(id)) else "Note", **({"at": at} if at else {})}
            for id in ids]})
    return out


def rendered(text: str, cited: list[dict]) -> tuple[str, str]:
    """What an agent receives, masked once more as it leaves Atlas."""
    return privacy.scrub(text), privacy.scrub(json.dumps(cited, ensure_ascii=False))


def _duration(t: timedelta) -> str:
    """"3 h 10 min", "3 h", "40 min"."""
    hours, minutes = divmod(int(t.total_seconds() // 60), 60)
    return " ".join([f"{hours} h"] * bool(hours) + [f"{minutes} min"] * bool(minutes or not hours))


def _reopened(snap: Snapshot, id: EvidenceId, now: datetime) -> str | None:
    try:
        ref = snap.session_ref(id)
    except Exception:
        return None
    when = f"{ref.day:%a} {ref.day.day} {ref.day:%b}" + ("" if ref.day.year == now.year else f" {ref.day.year}")
    harness = harness_of(id)
    return f'{harness.label if harness else ref.label} "{ref.title}" ({when})'
