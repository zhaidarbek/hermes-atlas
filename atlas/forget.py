"""Forget (W Q16, D Q3, D Q14): the owner's word removes a Session and everything Atlas derived
from it, after one confirmation. Pure: Snapshot + the owner's message -> Decision.

"atlas, forget the Codex Session from Thu 19 Nov 14:02" plans it: Atlas keeps the Sessions as
the pending plan and answers with what goes, what it cannot remove, and how to go ahead. Only
the exact word "forget", within an hour, goes ahead. Any other message cancels the plan. What
goes is worked out again from the record when the owner confirms, so a line written since the
ask goes too: the Session's evidence (raw copies, transcript, card, cached model outputs), every
Pursuit statement, Step, History line and proposal that cites it, its memberships, a Pursuit
made from it alone, every Journal line that cites it with each day's Active Time counted again
without it, every Profile entry drawn from it, every hand-edit Note whose copied text cites it,
the edit typed there with it, and every Note an agent left from it. forgotten.md keeps when, never what, and the
skip key of the Session and of each Session folded into it, so collection never reads them again.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import PurePosixPath
from typing import Sequence
from zoneinfo import ZoneInfo

from . import activetime, rules
from .model import (Command, ConfirmForget, EvidenceId, Fact, Forgotten, HarnessId, Note, NoteOrigin, OwnerMessage,
                    Outcome, Owner, Proposal, Pursuit, RequestForget, SessionEvidence, Statement, cites, skip_key)
from .record import ChangeSet, ForgetPlan, Snapshot, sessions_cited
from .rules import Decision

WORD = "forget"
WAITS = timedelta(hours=1)
FORGOTTEN = "Forgotten."
NOT_FOUND = "I found no {harness} Session from {at}."
SECRETS = ("password", "passphrase", "token", "api key", "key", "secret", "credentials")

_ASKED = re.compile(r"forget\s+(?:the\s+|my\s+)?(?P<harness>.+?)\s+session\s+(?:from|of|on|at)\s+"
                    r"(?:(?:mon|tue|wed|thu|fri|sat|sun)\w*\s+)?(?P<day>\d{1,2})\s+(?P<month>[a-z]{3,9})"
                    r"(?:\s+(?P<year>\d{4}))?,?\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})"
                    r"(?:\s*[.!,;:]\s*(?P<why>.*))?", re.I | re.S)


def interpret(msg: OwnerMessage, snap: Snapshot) -> Command | None:
    """RequestForget for the ask, ConfirmForget for the exact word while a plan waits, else None."""
    if msg.text.strip().casefold() == WORD:
        plan = snap.pending_forget()
        return ConfirmForget() if plan is not None and msg.at - plan.asked_at < WAITS else None
    m = _ASKED.fullmatch(msg.text.strip())
    if m is None:
        return None
    harness = next((h for h in HarnessId if h.label.casefold() == m["harness"].casefold()), None)
    if harness is None:
        return None
    zone = ZoneInfo(snap.settings().timezone)
    said = msg.at.astimezone(zone)
    try:
        month = datetime.strptime(m["month"][:3].title(), "%b").month
        at = datetime(int(m["year"] or said.year), month, int(m["day"]), int(m["hour"]), int(m["minute"]), tzinfo=zone)
    except ValueError:
        return None
    if m["year"] is None and at > said:
        at = at.replace(year=at.year - 1)
    return RequestForget(f"{harness.value} {at.isoformat()}")


def converse(snap: Snapshot, msg: OwnerMessage, cmd: Command) -> Decision:
    """The owner's Forget words, answered in one commit with the Note that keeps them."""
    if refused := rules.authorize(cmd, Owner(msg.via, rules.note_id(msg))):
        return Decision(snap.changes(), Outcome(False, (), refused))
    if isinstance(cmd, RequestForget):
        return _ask(snap, msg, cmd.selector)
    plan = snap.pending_forget()
    if plan is None or msg.at - plan.asked_at >= WAITS:
        return Decision(snap.changes(), Outcome(False, (), "NOTHING_TO_FORGET"))
    cs = execute(snap, plan.evidence, msg.at)
    return _answered(cs, msg, FORGOTTEN)


def cancel(cs: ChangeSet, snap: Snapshot) -> ChangeSet:
    """Any message but "forget" cancels the plan waiting for it."""
    return cs.set_pending_forget(None) if snap.pending_forget() is not None else cs


def _ask(snap: Snapshot, msg: OwnerMessage, selector: str) -> Decision:
    harness, at = selector.split(" ", 1)
    harness, at = HarnessId(harness), datetime.fromisoformat(at)
    found = sessions_at(snap, harness, at)
    if not found:
        return _answered(cancel(snap.changes(), snap), msg,
                         NOT_FOUND.format(harness=harness.label, at=f"{at:%a} {at.day} {at:%b %H:%M}"))
    ids = tuple(s.id for s in found)
    cs = snap.changes().set_pending_forget(ForgetPlan(ids, msg.at))
    return _answered(cs, msg, preview(snap, found, msg))


def _answered(cs: ChangeSet, msg: OwnerMessage, reply: str) -> Decision:
    note = Note(rules.note_id(msg), msg.at.replace(second=0, microsecond=0), NoteOrigin.OWNER, msg.via, msg.text,
                atlas_reply=reply, platform_ref=msg.platform_ref)
    return Decision(cs.append_note(note), Outcome(True, (reply,)))


def sessions_at(snap: Snapshot, harness: HarnessId, at: datetime) -> list[SessionEvidence]:
    """The Sessions of this Harness that started in the minute `at` names."""
    minute = at.replace(second=0, microsecond=0)
    return [s for s in snap.sessions(since=minute)
            if s.harness is harness and s.started.astimezone(at.tzinfo).replace(second=0, microsecond=0) == minute]


# ------------------------------------------------------------------ what goes

@dataclass(frozen=True)
class _Gone:
    """What the Forget of `ids` removes beyond the evidence itself, and the record without it."""
    pursuits: tuple[tuple[Pursuit, Pursuit | None, dict[str, int]], ...]   # before, after (None: removed), counts
    days: tuple[tuple[date, int], ...]                                     # day, lines removed
    profile: int
    notes: tuple[Note, ...]


def _closure(snap: Snapshot, ids: frozenset[EvidenceId]) -> _Gone:
    pursuits = []
    for p in snap.pursuits():
        after, counts = _without(p, ids)
        if after != p:
            pursuits.append((p, after, counts))
    days = []
    for day in snap.journal_days():
        j = snap.journal(day)
        lines = sum(1 for s in j.lines if cites(s) & ids)
        if lines or set(j.evidence) & ids:
            days.append((day, lines))
    profile = sum(1 for e in snap.profile().entries if cites(e.statement) & ids)
    notes = tuple(n for n in snap.notes() if (n.origin is NoteOrigin.HAND_EDIT
                  and {*n.derived_from, *sessions_cited(n.text, n.diff or "")} & ids)
                  or (n.origin in (NoteOrigin.AGENT, NoteOrigin.REFUSED_UPDATE) and set(n.derived_from) & ids))
    return _Gone(tuple(pursuits), tuple(days), profile, notes)


def _without(p: Pursuit, ids: frozenset[EvidenceId]) -> tuple[Pursuit | None, dict[str, int]]:
    keep = lambda s: not cites(s) & ids
    r = p.resume
    resume = replace(r, where=tuple(filter(keep, r.where)), left_off=tuple(filter(keep, r.left_off)),
                     decided=tuple(filter(keep, r.decided)), open_questions=tuple(filter(keep, r.open_questions)),
                     reopen=tuple(e for e in r.reopen if e not in ids))
    steps = tuple(s for s in p.past_steps if keep(s.text) and (s.ended is None or s.ended.basis is None
                                                                   or keep(s.ended.basis)))
    next_step = p.next_step if p.next_step is None or keep(p.next_step.text) else None
    history = tuple(h for h in p.history if keep(h.statement))
    proposals = tuple(x for x in p.proposals if keep(_basis(x)))
    sessions = tuple(m for m in p.sessions if m.evidence not in ids)
    removed = [s for s in (*r.where, *r.left_off, *r.decided, *r.open_questions) if not keep(s)]
    counts = {"resume": len(removed), "facts": sum(1 for s in removed if isinstance(s, Fact)),
              "steps": len(p.past_steps) - len(steps) + (next_step is None and p.next_step is not None),
              "history": len(p.history) - len(history), "proposals": len(p.proposals) - len(proposals)}
    if p.sessions and not sessions:
        return None, counts
    return replace(p, resume=resume, past_steps=steps, next_step=next_step, history=history, proposals=proposals,
                   sessions=sessions), counts


def _basis(proposal: Proposal) -> Statement:
    return getattr(proposal, "basis", None) or proposal.reason


# ------------------------------------------------------------------ the preview

def preview(snap: Snapshot, found: Sequence[SessionEvidence], msg: OwnerMessage) -> str:
    gone = _closure(snap, frozenset(s.id for s in found))
    today = msg.at.astimezone(ZoneInfo(snap.settings().timezone)).date()
    lines = ["Forgetting removes these for good:",
             "- the Session itself" if len(found) == 1 else f"- the {len(found)} Sessions themselves"]
    for before, after, counts in gone.pursuits:
        if after is None:
            lines.append(f"- the Pursuit {before.name}, made from it alone")
            continue
        if counts["resume"]:
            n = counts["resume"]
            what = ("Fact" if counts["facts"] == n else "Inference" if not counts["facts"] else "line") + _s(n)
            lines.append(f"- {n} {what} in the Resume Note of {before.name}")
        for key, what in (("steps", "Step"), ("history", "History line"), ("proposals", "proposal")):
            if counts[key]:
                lines.append(f"- {counts[key]} {what}{_s(counts[key])} of {before.name}")
    for day, n in gone.days:
        if n:
            lines.append(f"- {n} line{_s(n)} in {_journal_of(day, today)}")
    if gone.profile:
        lines.append(f"- {gone.profile} entr{'y' if gone.profile == 1 else 'ies'} in your Profile")
    edits = sum(1 for note in gone.notes if note.origin is NoteOrigin.HAND_EDIT)
    if edits:
        lines.append(f"- {edits} hand-edit Note{_s(edits)} that copied text from it, with the edit typed there")
    if len(gone.notes) > edits:
        n = len(gone.notes) - edits
        lines.append(f"- {n} Note{_s(n)} an agent left from it")
    copies = " and ".join(dict.fromkeys(_outside(s) for s in found))
    secret = next((w for w in SECRETS if re.search(rf"\b{w}\b", msg.text, re.I)), None)
    lines += [f"Not removed: {copies}.",
              f"Delete it there if the {secret} must go." if secret else "Delete it there if it must go.",
              f'Reply "{WORD}" to go ahead.']
    return "\n".join(lines)


def _journal_of(day: date, today: date) -> str:
    if day == today:
        return "today's Journal"
    if 0 < (today - day).days < 7:
        return f"{day:%A}'s Journal"
    return f"the Journal of {day:%a} {day.day} {day:%b}"


def _outside(s: SessionEvidence) -> str:
    """The Harness's own copy, which Atlas never touches: its folder, or its database."""
    main = next((p.source for p in s.parts if p.name == "main.jsonl.zst"), s.parts[0].source if s.parts else "")
    if " sessions/" in main:
        where = main.rsplit(" sessions/", 1)[0]
    else:
        where = f"{PurePosixPath(main).parent}/…" if main else "its store"
    return f"the Session in {s.harness.label} itself ({where})"


def _s(n: int) -> str:
    return "" if n == 1 else "s"


# ------------------------------------------------------------------ going ahead

def execute(snap: Snapshot, evidence: Sequence[EvidenceId], now: datetime) -> ChangeSet:
    """One ChangeSet: the evidence removed, every file that cites it rewritten without it, the
    Journal's Active Time counted again, forgotten.md told, its filing back-off dropped, and the plan
    cleared."""
    held = {h.id: h for h in snap.held().sessions()}
    ids = frozenset(evidence)
    keys = set()
    for id in ids & held.keys():
        keys.add(held[id].skip_key)
        keys |= folded_keys(snap.session(id))
    gone = _closure(snap, ids)
    cs = snap.changes()
    for before, after, _ in gone.pursuits:
        if after is None:
            cs.remove_pursuit(before.id, None)
        else:
            cs.put_pursuit(after)
    _rewrite_days(snap, cs, [day for day, _ in gone.days], ids)
    if gone.profile:
        profile = snap.profile()
        cs.put_profile(replace(profile, entries=tuple(e for e in profile.entries if not cites(e.statement) & ids)))
    for note in gone.notes:
        cs.remove_note(note)
    for id in sorted(ids & held.keys()):
        cs.remove_evidence(id)
    if keys:
        f = snap.forgotten()
        at = now.replace(second=0, microsecond=0)
        cs.put_forgotten(Forgotten((*f.entries, *((at, "a Session") for _ in ids & held.keys())), f.skip_keys | keys))
    retries = snap.filing_retries()
    if ids & retries.keys():
        cs.set_filing_retries({id: r for id, r in retries.items() if id not in ids})
    return cs.set_pending_forget(None)


def _rewrite_days(snap: Snapshot, cs: ChangeSet, days: Sequence[date], ids: frozenset[EvidenceId]) -> None:
    """Each day as it reads without these Sessions: its lines that cite them gone, its Active
    Time counted again from the rest, and the day removed when no Active Time is left."""
    if not days:
        return
    settings = snap.settings()
    zone = ZoneInfo(settings.timezone)
    one_offs = {}
    for h in snap.held().sessions():
        try:
            card = snap.card(h.id)
        except Exception:
            continue
        if card is not None and card.one_off_area and h.id not in ids:
            one_offs[h.id] = card.one_off_area
    start = datetime.combine(min(days), time(), zone) - settings.gap
    kept = [t for t in rules.timed(snap, one_offs, start) if t.id not in ids]
    touched = rules.workdays(kept, settings, min(days), max(days))
    for day in days:
        j = snap.journal(day)
        active = activetime.day(touched.get(day, []), day, settings.timezone, settings.gap)
        if not active.total:
            cs.remove_journal(day)
            continue
        keep = lambda lines: tuple(s for s in lines if not cites(s) & ids)
        cs.put_journal(replace(j, time=active, worked_on=keep(j.worked_on), decided=keep(j.decided),
                               finished=keep(j.finished)))


def folded_keys(s: SessionEvidence) -> set[str]:
    """The skip key of each Session folded into this one, found by its part's origin: the first
    16 hex digits of sha256("<harness>:<native key>"), the child's native key read from its part."""
    own = hashlib.sha256(f"{s.harness.value}:{s.native_key}".encode()).hexdigest()[:16]
    prefixes = {p.origin.split(":", 1)[0] for p in s.parts if p.origin and not p.origin.startswith(own + ":")}
    if not prefixes:
        return set()
    natives = {t.part.split("/", 1)[0].split(":", 1)[1] for t in s.turns if ":" in t.part.split("/", 1)[0]}
    natives |= {re.sub(r"^(?:t3-child|subagent)-", "", p.name.removesuffix(".jsonl.zst")) for p in s.parts}
    keys = set()
    for native in natives:
        for h in HarnessId:
            if hashlib.sha256(f"{h.value}:{native}".encode()).hexdigest()[:16] in prefixes:
                keys.add(skip_key(h, native))
    return keys
