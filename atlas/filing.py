"""Filing on the tick, as pure functions: which Sessions wait to be filed, and what one Filing the
librarian made changes in the record. No I/O, no model, no clock reads.

A Session waits when backfill has built the Pursuits and the Session is neither listed by a
Pursuit nor a one-off, or when a Pursuit lists it and it has grown since. A grown Session stays
where it is filed: the model's answer only updates the Pursuits that list it. A Session the model
gave no usable filing for waits out a back-off (RETRY) the record keeps, unless it grows first.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from . import forget, rules
from .librarian import ALSO, DECISIONS, Filing
from .model import (Actor, Cite, Done, EndingProposal, EvidenceId, Fact, FocusProposal, HistoryId, HistoryLine,
                    Inference, Membership, Outcome, Pursuit, ReopenProposal, SessionEvidence, Statement, Status, Step,
                    StepId)
from .record import FilingRetry, Snapshot
from .rules import Decision

BUDGET = 20
"""Sessions one tick files at most; the rest wait for the next tick, so a tick stays short."""
RETRY = (timedelta(hours=1), timedelta(days=1))
NEW = timedelta(days=7)
WORTH = 3
"""A Pursuit Atlas created in the last NEW that WORTH Sessions of the last month worked on is new
and worth pushing: Atlas proposes adding it to Focus (W Q10)."""
MONTH = timedelta(days=30)
"""The wait after a Session's first unusable answer, doubled after each one more, up to the second."""


def queue(snap: Snapshot, now: datetime) -> list[EvidenceId]:
    """The Sessions waiting to be filed, by their last turn, those of a web-chat export after the
    others, so a large export never holds back today's Sessions: none until backfill has built
    the Pursuits, and none whose back-off runs at `now`."""
    if not snap.holds_pursuits():
        return []
    retries = snap.filing_retries()
    listed: dict[EvidenceId, datetime] = {}
    for p in snap.pursuits():
        for m in p.sessions:
            listed[m.evidence] = min(listed.get(m.evidence, m.at), m.at, key=_instant)
    held = {h.id for h in snap.held().sessions()}
    waiting = []
    for s in snap.sessions():
        if s.id not in held:
            continue
        r = retries.get(s.id)
        if r is not None and _instant(r.last_at) == _instant(s.last_at) and _instant(now) < _instant(r.retry_at):
            continue
        if s.id in listed:
            if _instant(s.last_at).replace(microsecond=0) > _instant(listed[s.id]):
                waiting.append(s)
            continue
        card = _card(snap, s.id)
        if card is None or card.one_off_area is None:
            waiting.append(s)
    late = snap.settings().late_harnesses
    return [s.id for s in sorted(waiting, key=lambda s: (s.harness in late, _instant(s.last_at), s.id))]


def apply(snap: Snapshot, f: Filing, now: datetime) -> Decision:
    """The Filing on this Snapshot, under the librarian's authority: the card, the membership, and
    for a Session that advanced its Pursuit the Left off, the open Step seen done, the next Step, a
    Kind change, an ending proposal for an active Pursuit, a parked Pursuit active again (a done or
    dropped one gets a reopen proposal), each change Atlas makes with a History line. A Session first
    filed into one Pursuit is also listed by the first ALSO other Pursuits it worked on that are
    active, so its Active Time is split between them; one that is not active is left as it is. A Session that changed since the model read it is left to the next tick. A change of membership cancels a Forget that waits for "forget"."""
    cs = snap.changes()
    s = snap.session(f.evidence)
    if _instant(s.last_at) != _instant(f.last_at):
        return Decision(cs, Outcome(True, ()))
    today = now.astimezone(ZoneInfo(snap.settings().timezone)).date()
    cs.require_sessions([s.id])
    holders = [p for p in snap.pursuits() if any(m.evidence == s.id for m in p.sessions)]
    old = _card(snap, s.id)
    card = replace(f.card, one_off_area=old.one_off_area, one_off_day=old.one_off_day) if old else f.card
    moved = not holders
    if holders:
        for p in holders:
            cs.put_pursuit(_offered(_filed(p, f, s, now, today), now, today) if p.id == f.pursuit else _grown(p, s))
    elif f.pursuit is not None:
        cs.put_pursuit(_offered(_filed(snap.pursuit(f.pursuit), f, s, now, today), now, today))
        for other in [o for o in map(snap.pursuit, f.also) if o.status is Status.ACTIVE][:ALSO]:
            cs.put_pursuit(_grown(other, s, f.advances))
    elif f.new_pursuit is not None:
        cs.put_pursuit(_offered(_created(snap, f, now, today), now, today))
    else:
        card = replace(card, one_off_area=f.one_off_area, one_off_day=today)
        moved = False
    cs.put_card(card)
    plan = snap.pending_forget()
    if moved and plan is not None and f.pursuit is not None and any(
            m.evidence in plan.evidence for m in snap.pursuit(f.pursuit).sessions):
        forget.cancel(cs, snap)
    retries = snap.filing_retries()
    if s.id in retries:
        cs.set_filing_retries({id: r for id, r in retries.items() if id != s.id})
    return Decision(cs, Outcome(True, ()))


def unusable(snap: Snapshot, id: EvidenceId, now: datetime) -> Decision:
    """The model gave no usable filing for the Session as it is now: it waits out its back-off.
    Entries of Sessions the record no longer holds are dropped."""
    s = snap.session(id)
    retries = {k: r for k, r in snap.filing_retries().items() if k in {h.id for h in snap.held().sessions()}}
    was = retries.get(id)
    failures = was.failures + 1 if was is not None and _instant(was.last_at) == _instant(s.last_at) else 1
    wait = min(RETRY[0] * 2 ** (failures - 1), RETRY[1])
    retries[id] = FilingRetry(s.last_at, failures, now + wait)
    return Decision(snap.changes().set_filing_retries(retries), Outcome(True, ()))


def _filed(p: Pursuit, f: Filing, s: SessionEvidence, now: datetime, today) -> Pursuit:
    label = _label(s)
    added = all(m.evidence != s.id for m in p.sessions)
    worked_after = _instant(s.last_at) > max((_instant(h.at) for h in p.history), default=_instant(s.last_at))
    q = _grown(p, s, f.advances)
    lines: list[Statement] = []
    if added and f.rule is not None:
        lines.append(Inference(f"Filed a {label} Session here by your Filing rule: {f.rule.text}", (s.id,)))
    if f.advances:
        resume = q.resume
        if f.left_off:
            resume = replace(resume, left_off=f.left_off)
        decided = {d.text: d for d in resume.decided}
        for d in f.decided[:DECISIONS]:
            decided.setdefault(d.text, d)
        resume = replace(resume, decided=tuple(decided.values()))
        q = replace(q, resume=replace(resume, reopen=(s.id, *(r for r in resume.reopen if r != s.id))[:3]))
    seen = f.step_outcome
    if seen is not None and q.next_step is not None and q.next_step.id == seen.step and q.next_step.ended is None:
        done = replace(q.next_step, ended=Done("seen", seen.basis.source.at, seen.basis))
        q = replace(q, next_step=None, past_steps=(*q.past_steps, done))
        lines.append(Fact("Marked the Step done from your Session.", seen.basis.source))
    if f.advances and f.next_step is not None and (q.next_step is None or q.next_step.ended is not None):
        q = replace(q, next_step=Step(rules.step_id(q, today), f.next_step, today))
    if f.kind_change is not None and f.kind_change[0] is not q.kind:
        kind, reason = f.kind_change
        lines.append(Inference(f"{rules.KIND_CHANGED} {q.kind.value} to {kind.value}: {reason.text}", reason.basis))
        q = replace(q, kind=kind)
    if f.ending is not None and q.status is Status.ACTIVE and not any(
            isinstance(x, EndingProposal) for x in q.proposals):
        to, reason = f.ending
        q = replace(q, proposals=(*q.proposals, EndingProposal(to, reason, today, False)))
        lines.append(Inference(f"Proposed marking it {to.value}: {reason.text}", reason.basis))
    if f.advances and worked_after and q.status is Status.PARKED:
        q = replace(q, status=Status.ACTIVE)
        lines.append(Fact(f"Active again: your {label} Session worked on it.", Cite(s.id, label, s.last_at)))
    elif f.advances and worked_after and q.status.is_ending and not any(
            isinstance(x, ReopenProposal) and x.basis.source.evidence == s.id for x in q.proposals):
        basis = Fact(f"Your {label} Session worked on it.", Cite(s.id, label, s.last_at))
        q = replace(q, proposals=(*q.proposals, ReopenProposal(basis, today)))
        lines.append(Inference("Proposed reopening it: a Session worked on it after it ended.", (s.id,)))
    return _history(q, lines, now)


def _offered(p: Pursuit, now: datetime, today) -> Pursuit:
    """The Pursuit with a proposal to add it to Focus when it is new and worth pushing: Atlas
    created it in the last NEW, the owner has not touched it, it is active and outside Focus, and
    WORTH Sessions of the last month worked on it. Proposed once; a star answers it."""
    if p.focus or p.status is not Status.ACTIVE or today - p.created > NEW or not p.history \
            or p.history[0].actor is not Actor.ATLAS or any(h.actor is Actor.OWNER for h in p.history) \
            or any(isinstance(x, FocusProposal) and x.add for x in p.proposals):
        return p
    recent = tuple(m.evidence for m in p.sessions if m.advances and _instant(now) - _instant(m.at) <= MONTH)
    if len(recent) < WORTH:
        return p
    reason = Inference(f"{len(recent)} Sessions of the last month worked on it, and it is not in Focus.", recent)
    return replace(p, proposals=(*p.proposals, FocusProposal(True, reason, today)))


def _grown(p: Pursuit, s: SessionEvidence, advances: bool = False) -> Pursuit:
    """The Pursuit listing the Session up to its last turn; once advancing, it stays so."""
    members = {m.evidence: m for m in p.sessions}
    was = members.get(s.id)
    members[s.id] = Membership(s.id, s.last_at, advances or (was is not None and was.advances))
    return replace(p, sessions=tuple(sorted(members.values(), key=lambda m: (_instant(m.at), m.evidence))))


def _created(snap: Snapshot, f: Filing, now: datetime, today) -> Pursuit:
    """A Pursuit a Session starts, active or parked by backfill's own rule, so one an imported
    years-old chat starts is parked and no Neglected question asks about it."""
    d = f.new_pursuit
    id = rules._pursuit_id(d, {p.id for p in snap.pursuits()})
    step = Step(StepId(f"s-{today:%Y%m%d}-1"), d.next_step, today) if isinstance(d.next_step, Inference) else None
    line = HistoryLine(HistoryId("h-0001"), now, Actor.ATLAS, Inference("Created from 1 Session", (f.evidence,)))
    tz = ZoneInfo(snap.settings().timezone)
    last = max((m.at.astimezone(tz).date() for m in d.sessions), default=today)
    status = rules.starting_status(last, today)
    return Pursuit(id=id, name=d.name, kind=d.kind, area=d.area, status=status, focus=False, created=today,
                   continued=None, resume=d.resume, next_step=step, past_steps=(), filing_rules=(), sessions=d.sessions,
                   proposals=(), history=(line,))


def _history(p: Pursuit, lines: Sequence[Statement], now: datetime) -> Pursuit:
    for statement in lines:
        p = replace(p, history=(*p.history, HistoryLine(rules._next_history_id(p), now, Actor.ATLAS, statement)))
    return p


def _card(snap: Snapshot, id: EvidenceId):
    try:
        return snap.card(id)
    except Exception:
        return None


def _label(s: SessionEvidence) -> str:
    return s.harness.label + (f" via {s.interface}" if s.interface else "")


def _instant(at: datetime) -> datetime:
    return at.astimezone(timezone.utc)
