"""Neglected Pursuits and ending proposals in the Daily Brief, as pure functions: which Pursuit the
decision slot asks about and how many times, which Pursuit two unanswered asks park, which ending
a follow-up proposes, and the answer to "what have I neglected?" (W Q11, W Q12). No I/O, no model,
no clock reads.

The decision slot asks the quietest Neglected Pursuit (rules.neglected) whose Step today's brief
does not offer and whose question the owner has not held. A question the owner left unanswered
(no line of theirs in its History since the brief that asked it) is asked once more, as the
second ask. After a second silence the brief parks it under Atlas's own authority and the slot
moves to the next quietest.

The owner answers by word, by hold, or by writing about the question in words Atlas cannot read:
that last leaves the question open, asked again as it was. Days inside a Pause are no silence.

An ending proposal reaches a follow-up only when yesterday's Step finished the Pursuit: the
Filing that saw the Step done proposed the ending since the last brief. It is raised once; every
other ending waits for the Review.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Mapping, Sequence

from . import rules
from .model import (Actor, Count, DecisionAsk, EndingProposal, Fact, Followup, HistoryLine, OwnerMessage, Pause,
                    Pursuit, PursuitId, SendStatus, SentBrief, Status)
from .record import Snapshot

ASKS = 2
"""How many briefs ask one Neglected question before silence parks the Pursuit."""
PARKED_BY_SILENCE = "Parked after two unanswered asks; reply continue to undo"
RECENT = timedelta(days=7)
"""How far back "what have I neglected?" lists the owner's park, drop and continue decisions."""


def decision(snap: Snapshot, prev: SentBrief | None, offered: set[PursuitId], today: date,
             pauses: Sequence[Pause] = ()) -> tuple[DecisionAsk | None, PursuitId | None]:
    """Today's decision ask, and the Pursuit to park because two briefs asked about it and the
    owner said nothing. The owner answered when they wrote about it, even in words Atlas could not
    read; a day inside one of `pauses` is no silence, so the question is asked again as it was."""
    quiet = [p for p in rules.neglected(snap, today) if p.id not in offered]
    asked = prev.decision if prev is not None else None
    silenced = None
    if asked is not None and _unanswered(snap, asked.pursuit, prev, today):
        p = next((q for q in quiet if q.id == asked.pursuit), None)
        if p is not None and (asked.heard or _paused_since(prev.day, today, pauses, snap)):
            return _ask(snap, p, asked.ask, today), None
        if p is not None and asked.ask < ASKS:
            return _ask(snap, p, asked.ask + 1, today), None
        if p is not None:
            silenced = p.id
            quiet.remove(p)
    nxt = next((p for p in quiet if not _held(p, today)), None)
    return (_ask(snap, nxt, 1, today) if nxt else None), silenced


def _unanswered(snap: Snapshot, pursuit: PursuitId, prev: SentBrief, today: date) -> bool:
    p = next((q for q in snap.pursuits() if q.id == pursuit), None)
    if p is None or _held(p, today):
        return False
    sent = min(a.at for a in prev.attempts if a.status is SendStatus.SENT)
    return not any(h.actor is Actor.OWNER and h.at >= sent for h in p.history)


def _paused_since(asked: date, today: date, pauses: Sequence[Pause], snap: Snapshot) -> bool:
    """A Pause covered a brief's time after the day the question was asked."""
    return rules.clock_days(asked, today, pauses, snap.settings()) < (today - asked).days


def heard(snap: Snapshot, msg: OwnerMessage) -> SentBrief | None:
    """The latest brief sent, its question marked heard, when the owner's message, which Atlas
    could not read, replies to a brief that asked about the same Pursuit or names that Pursuit.
    None when the message is not about the open question, or holds no words."""
    if not re.search(r"\w", msg.text):
        return None
    sent = [b for b in snap.briefs() if any(a.status is SendStatus.SENT and a.at <= msg.at for a in b.attempts)]
    last = max(sent, key=lambda b: b.day, default=None)
    if last is None or last.decision is None or last.decision.heard:
        return None
    p = next((q for q in snap.pursuits() if q.id == last.decision.pursuit), None)
    to = msg.reply_to
    replied = snap.brief(date.fromisoformat(to.key)) if to is not None and to.kind == "brief" else None
    about = replied is not None and replied.decision is not None and replied.decision.pursuit == last.decision.pursuit
    if not about and (p is None or not _names(p.name, msg.text)):
        return None
    return replace(last, decision=replace(last.decision, heard=True))


def _names(name: str, text: str) -> bool:
    """The text holds a word of the name of four letters or more, singular or plural alike."""
    said = {w.rstrip("s") for w in re.findall(r"\w+", text.casefold())}
    return any(w.rstrip("s") in said for w in re.findall(r"\w+", name.casefold()) if len(w) >= 4)


def parked_by_silence(snap: Snapshot) -> list[tuple[Pursuit, datetime]]:
    """The Pursuits Atlas parked after two unanswered asks that the owner has said nothing about
    since, with when each was parked."""
    out = []
    for p in snap.pursuits():
        at = next((h.at for h in reversed(p.history)
                   if h.actor is Actor.ATLAS and h.statement.text == PARKED_BY_SILENCE), None)
        if p.status is Status.PARKED and at is not None and not any(
                h.actor is Actor.OWNER and h.at >= at for h in p.history):
            out.append((p, at))
    return out


def which(names: Sequence[str]) -> str:
    """What Atlas asks when "continue" could undo several parks by silence."""
    listed = ", ".join(names[:-1]) + " or " + names[-1]
    return f'Which one: {listed}? Reply "continue" with its name.'


def _held(p: Pursuit, today: date) -> bool:
    return p.decision_held_until is not None and p.decision_held_until > today


def _ask(snap: Snapshot, p: Pursuit, n: int, today: date) -> DecisionAsk:
    return DecisionAsk(p.id, n, rules.clock_days(rules.quiet_since(p), today, snap.pauses(), snap.settings()))


def parked(p: Pursuit, now: datetime) -> Pursuit:
    """`p` parked by Atlas after two unanswered asks, with the History line that says so."""
    basis = Fact(PARKED_BY_SILENCE, Count("quiet_days", tuple(m.evidence for m in p.sessions)))
    line = HistoryLine(rules._next_history_id(p), now, Actor.ATLAS, basis)
    return replace(p, status=Status.PARKED, focus=False, history=(*p.history, line))


def endings(followups: Sequence[Followup], pursuits: Mapping[PursuitId, Pursuit], prev: SentBrief
            ) -> tuple[tuple[Followup, ...], tuple[Pursuit, ...]]:
    """The follow-ups with the ending proposed where yesterday's Step finished the Pursuit, and
    those Pursuits with their proposal marked as raised, so no later brief raises it again."""
    out, raised = [], []
    for f in followups:
        p = pursuits[f.pursuit]
        proposal = next((x for x in p.proposals if isinstance(x, EndingProposal) and x.to is Status.DONE
                         and not x.in_followup and x.since >= prev.day), None)
        if f.state.startswith("done_") and proposal is not None and not p.status.is_ending \
                and f.pursuit not in {r.id for r in raised}:
            out.append(replace(f, ending_proposed=True))
            raised.append(replace(p, proposals=tuple(replace(x, in_followup=True) if x is proposal else x
                                                     for x in p.proposals)))
        else:
            out.append(f)
    return tuple(out), tuple(raised)


def proposed_in(brief: SentBrief | None) -> tuple[PursuitId, ...]:
    """The Pursuits whose ending `brief`'s follow-up proposed: what "close it" answers."""
    return tuple(f.pursuit for f in brief.followups if f.ending_proposed) if brief else ()


# ------------------------------------------------------------------ "what have I neglected?"

def answer(snap: Snapshot, now: datetime) -> str:
    """The Pursuits Neglected today, then the owner's park, drop and continue decisions of the last
    seven days, and Atlas's parks by silence."""
    today = now.date()
    s, pauses = snap.settings(), snap.pauses()
    quiet = rules.neglected(snap, today)
    lines = [f"{p.name} · {p.kind.value} · quiet {rules.clock_days(rules.quiet_since(p), today, pauses, s)} days [F]"
             for p in quiet] if quiet else ["Nothing is Neglected today. [F]"]
    decided = {rules.STATUS_SAID[Status.PARKED][0]: "parked", rules.STATUS_SAID[Status.DROPPED][0]: "dropped"}
    said, kept, silent = [], [], []
    for p, h in sorted(((p, h) for p in snap.pursuits() for h in p.history if now - h.at <= RECENT),
                       key=lambda ph: ph[1].at):
        when = _when(h.at.astimezone(now.tzinfo), now)
        text = h.statement.text
        if h.actor is Actor.OWNER and text in decided:
            said.append(f"{decided[text]} {p.name} {when}")
        elif h.actor is Actor.OWNER and text == rules.CONTINUE_SAID[0]:
            if _was_neglected(snap, p, h.at.astimezone(now.tzinfo).date()):
                kept.append(f'{p.name} was Neglected until you said "continue" {when}. [F]')
            else:
                said.append(f"kept {p.name} active {when}")
        elif h.actor is Actor.ATLAS and text == PARKED_BY_SILENCE:
            silent.append(f"I parked {p.name} {when}, after two unanswered asks. [F]")
    if said:
        lines.append("You " + (", ".join(said[:-1]) + " and " + said[-1] if len(said) > 1 else said[0]) + ".")
    return "\n".join([*lines, *silent, *kept])


def _when(at: datetime, now: datetime) -> str:
    if at.date() == now.date():
        return "this morning" if at.hour < 12 else "today"
    return f"on {at:%A}"


def _was_neglected(snap: Snapshot, p: Pursuit, day: date) -> bool:
    before = replace(p, continued=None)
    return not p.focus and rules.clock_days(rules.quiet_since(before), day, snap.pauses(),
                                            snap.settings()) >= rules.NEGLECTED_AFTER
