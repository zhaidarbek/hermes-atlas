"""The brief's rules as pure functions: Snapshot + input + now -> Decision (a ChangeSet plus
what to tell the owner). No I/O, no LLM, no clock reads. Every state transition in Atlas
is decided here and nowhere else, so the rules hold for every writer (ADR 0002).

Idempotency: each function is a function of the Snapshot. Running it twice on the same
Snapshot yields the same ChangeSet. Running it again after its own commit yields an empty
ChangeSet: transitions check the current state, e.g. a Step already skipped is not
skipped again.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from . import activetime, ask, messages, neglect, pausing
from .activetime import TimedSession, Work
from .librarian import Grouping, JournalLines, PursuitDraft
from .model import (ONE_OFF, Actor, AddArea, Answer, ApplyNote, Area, Authority, BoardView, BriefStep, Card, Cite,
                    Command, Continue, CorrectProfile, Coverage, DayTime, Done, EditResumeNote, EndingProposal, EndPause, EvidenceId,
                    Fact, FocusProposal, Followup, HistoryId, HistoryLine, HoldDecision, HoldStep, Inference,
                    JournalDay, Librarian, MarkSeen, MarkStep, Merge, Note, NoteOrigin, OutsideAction, Outcome, Owner,
                    OwnerMessage, Pause, PauseCmd, Profile, ProfileEntry, Pursuit, PursuitId, Query, ResumePromptQ,
                    ResumeQ, SendAttempt, SendStatus, SentBrief, SentRef, SentReview, SetArea, SetFocus, SetStatus,
                    Settings, Skipped, Split, Statement, Status, Step, StepId, Who, HarnessId, cites, harness_of, resend)
from .record import ChangeSet, ForgetPlan, Snapshot

log = logging.getLogger(__name__)
OUTSIDE_WORLD = "OUTSIDE_WORLD"


@dataclass(frozen=True)
class Decision:
    changes: ChangeSet
    outcome: Outcome
    send: SentBrief | SentReview | None = None   # status SENDING inside `changes`; app sends after commit


# ------------------------------------------------------------------ authority (the one gate)

def authorize(cmd: Command, who: Authority) -> str | None:
    """Return a refusal code or None. The allowed set is declared on the Command class
    (`type(cmd).allowed`, model.command); this function maps the Authority to a Who and adds
    the few value-dependent narrowings:
    - OutsideAction (allowed = {})        -> "OUTSIDE_WORLD" (v1 never acts outside)
    - Agent with no Instruction           -> Who.AGENT (Notes only) -> "NOT_TOLD" for any Pursuit change
    - Agent with an Instruction           -> Who.AGENT_TOLD; the Instruction was found in an
                                             owner-typed message of the bound Session BEFORE this call
                                             (sources.find_instruction), so nothing is applied unverified
    - SetStatus to DONE/DROPPED by LIBRARIAN -> "OWNER_ONLY" (Atlas proposes endings)
    - SetStatus to DONE/DROPPED by an agent -> allowed only if instruction.names_ending equals
                                             that Status (D Q11: the owner ending it, carried by
                                             the agent); History quotes the words, the card is
                                             marked changed, "reopen" undoes it
    - any other Who outside the allowed set -> "OWNER_ONLY"
    - an agent change with no Instruction  -> not refused silently: app turns it into a
                                             REFUSED_UPDATE Note quoting the owner's words (D Q12)
    - ConfirmForget without a pending plan younger than 1 h -> "NOTHING_TO_FORGET", checked by
                                             forget.converse, where the plan is read
    """
    allowed = type(cmd).allowed
    if not allowed:
        return OUTSIDE_WORLD
    w = _who(who)
    if isinstance(cmd, SetStatus) and cmd.status.is_ending and w is not Who.OWNER:
        if w is not Who.AGENT_TOLD or who.instruction.names_ending is not cmd.status:
            return "NOT_TOLD" if w is Who.AGENT else "OWNER_ONLY"
    if w in allowed:
        return None
    return "NOT_TOLD" if w is Who.AGENT else "OWNER_ONLY"


def _who(a: Authority) -> Who:
    if isinstance(a, Owner):
        return Who.OWNER
    if isinstance(a, Librarian):
        return Who.LIBRARIAN
    return Who.AGENT_TOLD if a.instruction is not None else Who.AGENT


def decide(snap: Snapshot, cmd: Command, who: Authority, now: datetime, prework: object | None = None) -> Decision:
    """Apply one Command. `prework` carries LLM results computed outside the lock
    (Split partition, ApplyNote translation of a hand edit). Writes a HistoryLine for every Pursuit
    change, with actor and (for agents) the quoted Instruction.
    Librarian changes to a field the owner set after `who.basis` are dropped (stale model
    output never overrides a correction).
    Side rules applied here: ending or parking clears focus; Continue sets `continued`;
    parked + Librarian "advances" -> ACTIVE outside Focus; done/dropped + advances ->
    ReopenProposal; a Focus add on a non-active Pursuit is refused.
    Built so far: the owner's MarkStep, SetStatus, SetFocus and Continue (CHANGES)."""
    refused = authorize(cmd, who)
    if refused:
        return Decision(snap.changes(), Outcome(False, (), refused))
    before = snap.pursuit(cmd.pursuit)
    p, line = _change(before, cmd, who, now)
    cs = snap.changes()
    return Decision(cs.put_pursuit(p) if p != before else cs, Outcome(True, (line,)))


def _change(p: Pursuit, cmd: Command, who: Authority, now: datetime) -> tuple[Pursuit, str]:
    if not isinstance(who, Owner):
        raise NotImplementedError("a change by an agent (#14) or by Atlas itself (#10)")
    change = CHANGES.get(type(cmd))
    if change is None:
        raise NotImplementedError(f"{type(cmd).__name__}: its ticket under #1")
    return change(p, cmd, Cite(who.note, "your Note", now.replace(second=0, microsecond=0)))


FOCUS_ADDED = "Added it to Focus"            # how the owner's SetFocus History line begins (D Q10 reads it)
STATUS_SAID = {Status.PARKED: ("Parked it.", "parked."),
               Status.DROPPED: ("Dropped it.", "dropped. I won't push it again. You can still find it."),
               Status.ACTIVE: ("Made it active.", "active."),
               Status.DONE: ("Marked it done.", "done.")}
"""SetStatus by the owner's word: the History line, and what Atlas answers after the Pursuit's name."""
LEAVES_FOCUS = "It leaves Focus."
STEP_SAID = {"done": ("Marked the Step done.", "Step done."), "skipped": ("Skipped the Step.", "Step skipped.")}
CONTINUE_SAID = ('Said "continue".', "stays active. Its quiet days count from today.")
ACTIVE_AGAIN = "active again. Its quiet days count from today."
"""What Atlas answers "continue" with for a parked Pursuit, such as one parked after two unanswered asks."""


def _mark_step(p: Pursuit, cmd: MarkStep, said: Cite) -> tuple[Pursuit, str]:
    """The owner's word settles a Step at once (W Q7). Done a second time, or skipped a second
    time, changes nothing. A Step skipped by word that leaves no next Step comes back as a new
    Step in the same words, so the next brief offers it again (scene 34)."""
    history, reply = STEP_SAID[cmd.outcome]
    basis = Fact(cmd.detail or "", said)
    end = Done("word", said.at, basis) if cmd.outcome == "done" else Skipped("word", said.at, basis)
    steps = [s for s in (p.next_step, *p.past_steps) if s is not None and s.id == cmd.step]
    if not steps or type(steps[0].ended) is type(end):
        return p, f"{p.name}: {reply}"
    ended = replace(steps[0], ended=end)
    if p.next_step is not None and p.next_step.id == cmd.step:
        p = replace(p, next_step=None, past_steps=(*p.past_steps, ended))
        if cmd.outcome == "skipped":
            day = said.at.date()
            p = replace(p, next_step=Step(step_id(p, day), steps[0].text, day))
    else:
        p = replace(p, past_steps=tuple(ended if s.id == cmd.step else s for s in p.past_steps))
    picked = chosen(steps[0].text.text, cmd.detail) if cmd.outcome == "done" else None
    if picked:
        p = replace(p, resume=replace(p.resume, decided=(*p.resume.decided, Fact(picked, said))))
    return _noted(p, history, said), f"{p.name}: {reply}"


def step_id(p: Pursuit, day: date) -> StepId:
    """The first s-<day>-<n> no Step of the Pursuit holds."""
    taken = {st.id for st in (p.next_step, *p.past_steps) if st is not None}
    return next(StepId(f"s-{day:%Y%m%d}-{n}") for n in range(1, len(taken) + 2) if f"s-{day:%Y%m%d}-{n}" not in taken)


def chosen(step: str, detail: str | None) -> str | None:
    """The option the owner picked, when the Step asked for a choice ("paper chart or tablet app")
    and the detail's first words name one of its options ("tablet app, nothing fancy")."""
    if not detail or not re.search(r"\bor\b", step, re.I):
        return None
    first = re.split(r"[,;]", detail, maxsplit=1)[0].strip().rstrip(".")
    return first if first and re.search(rf"\b{re.escape(first)}\b", step, re.I) else None


def _hold_step(p: Pursuit, cmd: HoldStep, said: Cite) -> tuple[Pursuit, str]:
    """"remind me friday": the Step stays open and is not offered before that day (W Q8)."""
    step = p.next_step
    if step is not None and step.ended is None and step.held_until != cmd.until:
        p = _noted(replace(p, next_step=replace(step, held_until=cmd.until)),
                   f"Held the Step until {cmd.until:%a} {cmd.until.day} {cmd.until:%b}.", said)
    return p, f"{p.name}: I'll suggest this Step again on {cmd.until:%A}."


def _hold_decision(p: Pursuit, cmd: HoldDecision, said: Cite) -> tuple[Pursuit, str]:
    """"remind me friday" on the decision question: not asked, nor counted, before that day."""
    if p.decision_held_until != cmd.until:
        p = _noted(replace(p, decision_held_until=cmd.until),
                   f"Held the question until {cmd.until:%a} {cmd.until.day} {cmd.until:%b}.", said)
    return p, f"{p.name}: I'll ask about this again on {cmd.until:%A}."


def _set_status(p: Pursuit, cmd: SetStatus, said: Cite) -> tuple[Pursuit, str]:
    """A Pursuit that is no longer active leaves Focus, and the reply says so. An ending answers
    Atlas's proposals to end it."""
    history, reply = STATUS_SAID[cmd.status]
    leaves = p.focus and cmd.status is not Status.ACTIVE
    if p.status is not cmd.status:
        kept = tuple(x for x in p.proposals if not (cmd.status.is_ending and isinstance(x, EndingProposal)))
        p = _noted(replace(p, status=cmd.status, focus=False, proposals=kept), history, said)
    return p, f"{p.name}: {reply}" + (f" {LEAVES_FOCUS}" if leaves else "")


FOCUS_SAID = {True: (f"{FOCUS_ADDED} on the Board.", "in Focus."),
              False: ("Took it out of Focus on the Board.", "out of Focus.")}


def _set_focus(p: Pursuit, cmd: SetFocus, said: Cite) -> tuple[Pursuit, str]:
    """A star answers a Focus proposal the same way: the proposal goes."""
    history, reply = FOCUS_SAID[cmd.on]
    if p.focus is not cmd.on:
        kept = tuple(x for x in p.proposals if not (isinstance(x, FocusProposal) and x.add is cmd.on))
        p = _noted(replace(p, focus=cmd.on, proposals=kept), history, said)
    return p, f"{p.name}: {reply}"


def _continue(p: Pursuit, cmd: Continue, said: Cite) -> tuple[Pursuit, str]:
    """A parked Pursuit is active again."""
    history, reply = CONTINUE_SAID
    today = said.at.date()
    parked = p.status is Status.PARKED
    if p.continued != today or parked:
        p = _noted(replace(p, continued=today, status=Status.ACTIVE), history, said)
    return p, f"{p.name}: {ACTIVE_AGAIN if parked else reply}"


RESUME_EDITED = "Changed the Resume Note."
_WORD = re.compile(r"[^\W_]+")


def words(text: str) -> list[str]:
    """A text's words as an agent's change is checked against the owner's: casefolded runs of
    letters and digits, so "5-minute" is "5" and "minute"."""
    return _WORD.findall(text.casefold())


def _stem(word: str) -> str:
    for ending in ("ing", "ed", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[:-len(ending)]
    return word


def named(text: str, lines: Sequence[Statement]) -> list[Statement]:
    """The lines `text` names: every word of it is a word of the line, each taken without an
    ending "ing", "ed" or "s", so "the card filling up" names "does the 32 GB card fill up before
    spring?"."""
    said = {_stem(w) for w in words(text)}
    return [s for s in lines if said and said <= {_stem(w) for w in words(s.text)}]


def edit_resume(p: Pursuit, cmd: EditResumeNote, said: Cite) -> tuple[Pursuit, list[Statement] | None]:
    """The Resume Note without the Open question each `remove` text is, and with `add_decided` as
    Decided lines resting on `said`. Also the Open questions removed, or None, with the Pursuit
    unchanged, when one of them is no longer there."""
    r = p.resume
    gone = [[s for s in r.open_questions if s.text == text] for text in cmd.remove]
    if any(len(g) != 1 for g in gone):
        return p, None
    removed = [g[0] for g in gone]
    edited = replace(r, decided=(*r.decided, *(Fact(text, said) for text in cmd.add_decided)),
                     open_questions=tuple(s for s in r.open_questions if s not in removed))
    return replace(p, resume=edited), removed


def _edit_resume_note(p: Pursuit, cmd: EditResumeNote, said: Cite) -> tuple[Pursuit, str]:
    """"apply" on a refused agent update: the change it kept, made by the owner's word, when each
    Open question it removes is still there."""
    edited, removed = edit_resume(p, cmd, said)
    if removed is None:
        return p, f"{p.name}: not changed. The Open question that change removes is no longer in its Resume Note."
    shown = [f'removed the open question "{s.text}"' for s in removed]
    shown += [f'added the decision "{d}"' for d in cmd.add_decided]
    told = " and ".join(shown)
    return _noted(edited, RESUME_EDITED, said), f"{p.name}: Resume Note changed. {told[:1].upper()}{told[1:]}"


CHANGES: dict[type, Callable[[Pursuit, Command, Cite], tuple[Pursuit, str]]] = {
    MarkStep: _mark_step, HoldStep: _hold_step, HoldDecision: _hold_decision, SetStatus: _set_status,
    Continue: _continue, SetFocus: _set_focus, EditResumeNote: _edit_resume_note}
"""How the owner's word changes one Pursuit, by Command type: the new Pursuit and the line Atlas answers."""


def _noted(p: Pursuit, text: str, said: Cite) -> Pursuit:
    return replace(p, history=(*p.history, HistoryLine(_next_history_id(p), said.at, Actor.OWNER, Fact(text, said))))


def skipped_in_a_row(p: Pursuit) -> int:
    """Ended Steps skipped since the last one done, by word or silence alike: three on a Focus
    Pursuit lead Atlas to propose removing it from Focus (W Q10)."""
    n = 0
    for s in reversed(p.past_steps):
        if not isinstance(s.ended, Skipped):
            break
        n += 1
    return n


# ------------------------------------------------------------------ the owner's words

def note_id(msg: OwnerMessage) -> EvidenceId:
    """One id per message: from the platform's message id when there is one, so a redelivery is
    the same Note."""
    key = msg.platform_ref or f"{msg.via}:{msg.at.isoformat()}:{msg.text}"
    digest = base64.b32encode(hashlib.sha256(key.encode()).digest()).decode().lower()
    return EvidenceId(f"nt-{digest[:8]}")


def redelivered(snap: Snapshot, platform_ref: str | None) -> Note | None:
    """The Note an earlier delivery of this platform message left, if any."""
    if platform_ref is None:
        return None
    return next((n for n in snap.notes() if n.platform_ref == platform_ref), None)


def noted(snap: Snapshot, msg: OwnerMessage) -> Note | None:
    """The Note this message left, if any, found by its id: so a message with no platform id is
    found too, as when a commit that failed partway was rolled forward."""
    id = note_id(msg)
    return next((n for n in snap.notes() if n.id == id), None)


def replied_to(snap: Snapshot, chat: str, message_id: str) -> SentRef | None:
    """The brief a reply in `chat` replies to: one sent there as `message_id`, either copy of a
    brief sent twice (D Q13). A brief attempt with no chat recorded was sent before Atlas kept one,
    to a home channel that may have moved since, so its ids name it in no chat."""
    for b in snap.briefs():
        if any(message_id in a.message_ids and a.chat == chat for a in b.attempts):
            return SentRef("brief", b.day.isoformat(), tuple(i for a in b.attempts for i in a.message_ids))
    return None


SENDING_FOR = timedelta(minutes=2)
"""How long a send attempt can be in flight: `hermes send` gives up after 60 s."""


def in_flight(snap: Snapshot, now: datetime) -> bool:
    """A brief is being sent: its last attempt is SENDING and began less than SENDING_FOR ago. One
    older than that was left by a crash."""
    return any(b.attempts[-1].status is SendStatus.SENDING and now - b.attempts[-1].at < SENDING_FOR
               for b in snap.briefs())


def converse(snap: Snapshot, msg: OwnerMessage, commands: Sequence[Command], asks: Sequence[Query],
             applies: Mapping[EvidenceId, Sequence[Command]] | None = None,
             prework: Mapping[Command, object] | None = None, answers: Mapping[Query, str] = {}) -> Decision:
    """The owner wrote to Atlas: one ChangeSet holds the Note (W Q14), with Atlas's reply, and
    every change its Commands make under the owner's authority naming that Note. ApplyNote makes
    the changes `applies` read from that Note, naming it, and marks it applied. A redelivered
    message changes nothing and gets the same reply. Answers follow the change lines: a
    ResumePromptQ's from this Snapshot, every other question's from `answers`, written before the
    commit (ask). An OutsideAction is refused with its own line, and the rest of the message
    still stands. A ResumeQ answer makes the Note about its Pursuit, so a bare "prompt" next
    finds it; the Note changes no Pursuit. The reply ends with one "apply" offer, for the latest
    file Atlas put back or agent change it refused, when any has not been offered yet (W Q15,
    D Q12); the owner's Note records the one it named, which "apply" then makes, and every Note
    not yet offered is marked offered in one write per Notes file. A change "apply" makes is
    stamped with the time of "apply". `prework` holds, for each Split, the new Pursuit's draft
    (librarian.partition)."""
    earlier = redelivered(snap, msg.platform_ref)
    if earlier is not None:
        return Decision(snap.changes(), Outcome(True, (earlier.atlas_reply,)))
    note = Note(note_id(msg), msg.at.replace(second=0, microsecond=0), NoteOrigin.OWNER, msg.via, msg.text,
                platform_ref=msg.platform_ref)
    who = Owner(msg.via, note.id)
    refused = next((r for r in map(lambda c: authorize(c, who), commands) if r and r != OUTSIDE_WORLD), None)
    if refused:
        return Decision(snap.changes(), Outcome(False, (), refused))
    desk = _Desk(snap)
    profile = snap.profile() if any(isinstance(c, (CorrectProfile, ApplyNote)) for c in commands) else None
    pauses = None
    kept: dict[EvidenceId, Note] = {}
    lines = []
    for cmd in commands:
        if isinstance(cmd, OutsideAction):
            lines.append(ask.refusal(snap, cmd))
            continue
        if isinstance(cmd, ApplyNote):
            applied = next(n for n in snap.pending_applies() if n.id == cmd.note)
            kept[applied.id] = replace(applied, offered=True, applied=True)
            said, changes = (Owner(who.via, applied.id), applied.at), (applies or {})[applied.id]
        else:
            said, changes = (who, note.at), (cmd,)
        for change in changes:
            if isinstance(change, (PauseCmd, EndPause)):
                pauses, line = _pausing(snap, pauses, change, note.at, msg.at)
            elif isinstance(change, CorrectProfile):
                profile, line = _correct_profile(profile, change, Cite(said[0].note, "your Note", said[1]), note.at)
                if line is None:
                    return Decision(snap.changes(), Outcome(False, (), "NO_SUCH_ENTRY"))
            elif type(change) in FILING:
                try:
                    line = FILING[type(change)](desk, change, Cite(note.id, "your Note", note.at),
                                                (prework or {}).get(change))
                except (AttributeError, KeyError, ValueError) as stale:
                    return Decision(snap.changes(), Outcome(False, (), f"FILING: {type(stale).__name__}"))
            else:
                p = desk.pursuit(change.pursuit)
                desk.changed[p.id], line = _change(p, change, said[0], note.at)
            lines += [line] if line else []
    lines += [resume_prompt(snap, q).data if isinstance(q, ResumePromptQ) else answers[q] for q in asks]
    about = next((q.pursuit for q in asks if isinstance(q, ResumeQ)), None)
    unoffered = [n for n in snap.pending_applies() if not n.offered and n.id not in kept]
    offer = unoffered[-1] if unoffered else None
    if offer is not None:
        lines.append(messages.apply_offer(offer.via) if offer.origin is NoteOrigin.HAND_EDIT else
                     messages.update_offer(HarnessId(offer.via).label, desk.pursuit(offer.pending.pursuit).name,
                                           offer.pending.remove, offer.pending.add_decided))
    kept.update((n.id, replace(n, offered=True)) for n in unoffered)
    cs = snap.changes()
    held = {p.id: p for p in snap.pursuits()}
    for p in desk.changed.values():
        if p != held.get(p.id):
            cs.put_pursuit(p)
    for gone, into in desk.merged.items():
        cs.remove_pursuit(gone, into)
    if desk.areas is not None:
        profile = replace(profile or snap.profile(), areas=desk.areas)
    if profile is not None and profile != snap.profile():
        cs.put_profile(profile)
        texts = {e.statement.text for e in profile.entries}
        gone = {e.statement.text: cites(e.statement) for e in snap.profile().entries if e.statement.text not in texts}
        if gone:
            cs.set_profile_corrections({**snap.profile_corrections(), **gone})
    if pauses is not None and pauses != pausing.kept(snap.pauses):
        cs.put_pauses(pauses)
    cs.put_notes(kept.values())
    cs.append_note(replace(note, atlas_reply="\n".join(lines), about=about, offer=offer.id if offer else None))
    return Decision(cs, Outcome(True, tuple(lines)))


def _pausing(snap: Snapshot, pauses: tuple[Pause, ...] | None, cmd: PauseCmd | EndPause, start: datetime,
             now: datetime) -> tuple[tuple[Pause, ...], str]:
    """A Pause begins at the minute of the owner's Note, and "I'm back" ends it there. The Hermes
    cron job is never touched (ADR 0006): the tick reads pauses.md."""
    s, held = snap.settings(), pausing.kept(snap.pauses) if pauses is None else pauses
    if isinstance(cmd, PauseCmd):
        return pausing.pause(held, cmd, start, s)
    briefed = snap.brief(now.astimezone(ZoneInfo(s.timezone)).date()) is not None
    return pausing.end_pause(held, start, s, briefed)


def _correct_profile(profile: Profile, cmd: CorrectProfile, said: Cite, now: datetime) -> tuple[Profile, str | None]:
    """The owner's correction replaces the entry it names, as an entry of the owner's resting on
    their Note. None when the Profile holds no such entry."""
    at = next((n for n, e in enumerate(profile.entries) if e.statement.text == cmd.entry), None)
    if at is None or cmd.replacement is None:
        return profile, None
    entry = ProfileEntry(Fact(cmd.replacement, said), said.at.date(), Who.OWNER)
    return (replace(profile, entries=(*profile.entries[:at], entry, *profile.entries[at + 1:])),
            messages.profile_change(entry.statement, now))


# ------------------------------------------------------------------ filing corrections (scene 5)

@dataclass
class _Desk:
    """What one message's Commands have changed so far, in order: later Commands see earlier ones."""
    snap: Snapshot
    areas: tuple[Area, ...] | None = None
    changed: dict[PursuitId, Pursuit] = field(default_factory=dict)
    merged: dict[PursuitId, PursuitId] = field(default_factory=dict)     # merged away -> merged into
    added: set[Area] = field(default_factory=set)

    def pursuit(self, ref: str) -> Pursuit:
        if ref in self.changed:
            return self.changed[ref]
        p = self.snap.pursuit(self.merged.get(ref, ref))
        return self.changed.get(p.id, p)

    def owner_areas(self) -> tuple[Area, ...]:
        if self.areas is None:
            self.areas = self.snap.profile().areas
        return self.areas


def _sessions(n: int) -> str:
    return f"{n} Session{'' if n == 1 else 's'}"


def _merge(desk: _Desk, cmd: Merge, said: Cite, _: object) -> str:
    """One Pursuit from two (W Q2): the Kind, Area, Resume Note and Next Step of the one worked on
    last, every Session, both Histories, and the owner's reason as a Filing rule. The other's open
    Step ends as replaced, and a Step of its whose id the kept one uses takes the next free id.
    It is active and in Focus when either was."""
    keep, gone = sorted((desk.pursuit(cmd.a), desk.pursuit(cmd.b)),
                        key=lambda p: (p.last_worked or p.created, p.id), reverse=True)
    status = Status.ACTIVE if Status.ACTIVE in (keep.status, gone.status) else keep.status
    ended = (replace(gone.next_step, ended=Skipped("replaced", said.at, Fact("Merged into one Pursuit.", said))),
             ) if gone.next_step else ()
    taken = {st.id for st in (keep.next_step, *keep.past_steps) if st}
    carried_steps = []
    for st in gone.past_steps + ended:
        n = 1
        while st.id in taken:
            st = replace(st, id=StepId(f"s-{st.created:%Y%m%d}-{n}"))
            n += 1
        taken.add(st.id)
        carried_steps.append(st)
    n = max((int(h.id.split("-")[1]) for h in keep.history), default=0)
    carried = tuple(replace(h, id=HistoryId(f"h-{n + i:04d}")) for i, h in enumerate(gone.history, 1))
    rule = f'Work filed as "{gone.name}" belongs here' + (f": {cmd.rule}." if cmd.rule else ".")
    merged = replace(
        keep, name=cmd.into_name or keep.name, status=status,
        focus=status is Status.ACTIVE and (keep.focus or gone.focus), created=min(keep.created, gone.created),
        continued=max((d for d in (keep.continued, gone.continued) if d), default=None),
        past_steps=keep.past_steps + tuple(carried_steps),
        filing_rules=keep.filing_rules + gone.filing_rules + (Fact(rule, said),),
        sessions=tuple(sorted(keep.sessions + gone.sessions, key=lambda m: (m.at, m.evidence))),
        proposals=keep.proposals + gone.proposals, history=keep.history + carried)
    merged = _noted(merged, f'Merged "{keep.name}" and "{gone.name}" into one Pursuit.', said)
    desk.changed[keep.id] = merged
    desk.changed.pop(gone.id, None)
    desk.merged[gone.id] = keep.id
    return (f'Merged into "{merged.name}" ({merged.kind.value}). It keeps both histories, '
            f"{_sessions(len(merged.sessions))}.")


def _split(desk: _Desk, cmd: Split, said: Cite, draft: PursuitDraft) -> str:
    """A new Pursuit from the Sessions of one that the owner's criterion names, as
    librarian.partition drafted it. Both keep the criterion as a Filing rule, so later Sessions
    are filed by it (scene 15)."""
    p = desk.pursuit(cmd.pursuit)
    moved = {m.evidence for m in draft.sessions}
    goes = tuple(m for m in p.sessions if m.evidence in moved)
    stays = tuple(m for m in p.sessions if m.evidence not in moved)
    if not goes or not stays:
        raise ValueError("a split that leaves one side empty")
    today = said.at.date()
    taken = {q.id for q in desk.snap.pursuits()} | set(desk.changed)
    criterion = cmd.criterion[:1].upper() + cmd.criterion[1:]
    new = Pursuit(
        id=_pursuit_id(replace(draft, sessions=goes), taken), name=cmd.new_name, kind=draft.kind, area=p.area,
        status=starting_status(max(m.at for m in goes).date(), today), focus=False, created=today, continued=None,
        resume=draft.resume,
        next_step=Step(StepId(f"s-{today:%Y%m%d}-1"), draft.next_step, today) if draft.next_step else None,
        past_steps=(), filing_rules=(Fact(f'{criterion} belong here, not in "{p.name}".', said),), sessions=goes,
        proposals=(), history=(HistoryLine(HistoryId("h-0001"), said.at, Actor.OWNER,
                                           Fact(f'Split out of "{p.name}" with {_sessions(len(goes))}.', said)),))
    left = {m.evidence for m in stays}
    reopen = tuple(e for e in p.resume.reopen if e in left) or (stays[-1].evidence,)
    p = replace(p, sessions=stays, resume=replace(p.resume, reopen=reopen),
                filing_rules=p.filing_rules + (Fact(f'{criterion} belong in "{new.name}", not here.', said),))
    desk.changed[p.id] = _noted(p, f'Split "{new.name}" out of it with {_sessions(len(goes))}.', said)
    desk.changed[new.id] = new
    step = f"\nNext Step: {_sentence_part(new.next_step.text.text)} [I]" if new.next_step else ""
    return (f'Split. "{new.name}" ({new.kind.value}) has {_sessions(len(goes))}.{step}\n'
            f'"{p.name}" keeps {_sessions(len(stays))}.')


def _sentence_part(text: str) -> str:
    """A sentence as part of another: no full stop, and a lower-case first letter unless its
    first word is an acronym."""
    text = text.rstrip(".")
    first = text.split(" ", 1)[0]
    return text if first.isupper() and len(first) > 1 else text[:1].lower() + text[1:]


def _add_area(desk: _Desk, cmd: AddArea, said: Cite, _: object) -> str:
    known = next((a for a in desk.owner_areas() if a.casefold() == cmd.area.casefold()), None)
    if known is None:
        desk.areas += (cmd.area,)
        desk.added.add(cmd.area)
    listed = ", ".join(desk.areas)
    if known is not None:
        return f'You already have the Area "{known}". Your Areas: {listed}.'
    return f'Added the Area "{cmd.area}". Your Areas: {listed}.'


def _set_area(desk: _Desk, cmd: SetArea, said: Cite, _: object) -> str | None:
    """Moves a Pursuit to an Area. Said with the Area's adding, the adding's line answers both."""
    p = desk.pursuit(cmd.pursuit)
    if p.area != cmd.area:
        desk.changed[p.id] = _noted(replace(p, area=cmd.area), f'Moved it to the Area "{cmd.area}".', said)
    return None if cmd.area in desk.added else f'{p.name}: now in the Area "{cmd.area}".'


FILING: dict[type, Callable[[_Desk, Command, Cite, object], str | None]] = {
    Merge: _merge, Split: _split, AddArea: _add_area, SetArea: _set_area}
"""The owner's corrections to filing, by Command type: they change several Pursuits or the Profile."""


def resume_prompt(snap: Snapshot, q: ResumePromptQ) -> Answer:
    """The paste-in prompt for a brief's Step n, or for a named Pursuit (D Q2)."""
    if q.pursuit is not None:
        p = snap.pursuit(q.pursuit)
    else:
        step = next(s for s in snap.brief(q.brief).steps if s.n == q.item)
        p = snap.pursuit(step.pursuit)
    return Answer(None, (), Coverage(), data=messages.resume_prompt(p, snap))


# ------------------------------------------------------------------ clocks (Pause stops all of them)

NEGLECTED_AFTER = 14                         # quiet clock days outside Focus


def paused(pauses: Sequence[Pause], at: datetime) -> bool:
    return any(p.start <= at < p.end for p in pauses)


def clock_days(since: date, today: date, pauses: Sequence[Pause], s: Settings) -> int:
    """Days in (since, today] that count on a clock. A day does not count if a Pause covered
    that day's Daily Brief time. Used by Neglected (14), parked raise (90) and nothing else:
    Step offers count briefs, and no brief is sent while paused."""
    tz = ZoneInfo(s.timezone)
    days = (since + timedelta(days=n) for n in range(1, (today - since).days + 1))
    return sum(1 for d in days if not paused(pauses, datetime.combine(d, s.brief_at, tz)))


def quiet_since(p: Pursuit) -> date:
    """Later of the last advancing Session and the owner's last "continue". `created` counts
    only for a Pursuit with no Session at all (one the owner named in words), so backfill does
    not reset historical quiet: on day one the newsletter is already 28 days quiet (brief l.107)."""
    marks = [d for d in (p.last_worked, p.continued) if d is not None]
    return max(marks) if marks else p.created


def neglected(snap: Snapshot, today: date) -> list[Pursuit]:
    """Active, not in Focus, clock_days(quiet_since) >= 14, quietest first. Derived, never stored."""
    s, pauses = snap.settings(), pausing.kept(snap.pauses)
    quiet = {p.id: clock_days(quiet_since(p), today, pauses, s)
             for p in snap.pursuits() if p.status is Status.ACTIVE and not p.focus}
    return sorted((p for p in snap.pursuits() if quiet.get(p.id, 0) >= NEGLECTED_AFTER),
                  key=lambda p: (-quiet[p.id], p.name))


def parked_to_raise(snap: Snapshot, today: date) -> Pursuit | None:
    """The parked Pursuit quiet longest, counted from max(last_worked, raised_at), if >= 90 clock days."""
    raise NotImplementedError


def time_proposal(snap: Snapshot, today: date) -> time | None:
    """G4. Over the last 14 days with Active Time and no Pause: if on >= 10 of them the first
    Active Time began >= 60 min before or after settings.brief_at, return the median of those
    first starts rounded down to the quarter hour; else None. None also while a proposal was
    declined or ignored in a Review of the last 4 weeks (SentReview.time_proposed). Counted
    Fact [F]. Daily Brief only; the Review time is never proposed in v1."""
    raise NotImplementedError


def offers(snap: Snapshot, pursuit: PursuitId, step: Step) -> int:
    """Number of SENT briefs that offered this Step in its current offer cycle (briefs/*.md is
    the single source). A hold starts a new cycle: only briefs on or after `held_until` count,
    so the day the hold ends is the Step's first offer (W Q8). ARRIVAL_UNKNOWN briefs never
    count."""
    since = step.held_until or date.min
    return sum(1 for b in snap.briefs() if b.day >= since and any(a.status is SendStatus.SENT for a in b.attempts)
               and any(s.pursuit == pursuit and s.step == step.id for s in b.steps))


# ------------------------------------------------------------------ messages Atlas starts

MAX_STEPS = 3


def compose_daily(snap: Snapshot, now: datetime) -> Decision:
    """The Daily Brief for today in the owner's zone, committed as SENDING before it is sent.
    Focus never picked (no Pursuit in Focus and no owner SetFocus line in any History, D Q10): Steps
    come from the most recently active Pursuits instead, at most three, one per Pursuit, under the
    heading "no Focus picked yet"; SentBrief.no_focus_yet. This stops for good after the first pick.
    It is a stand-in, not a Focus proposal.
    Nothing while paused: the Pause is checked again here, when the send is planned.
    Today's file already there: a second attempt with the same text when its delivery allows one
    (model.RESEND, D Q13), guarded by that file's seal; otherwise nothing. A new file's base
    'absent' is the guard against two ticks racing. The brief is planned from every Pursuit, so
    its commit conflicts with a change since the Snapshot to any file it read (guard_reads): an
    owner's park between composing and committing makes the tick compose again, and a Note does not.
    A Step offered twice and still open ends as skipped by silence (W Q7). The follow-up covers
    each Step of the last brief sent before today. When the owner acted on none of them, the Steps
    with no sign rest a day. The brief never pads to three Steps.
    The follow-up proposes an ending only where yesterday's Step finished the Pursuit, and the
    decision slot counts its asks (neglect).
    # TODO pseudocode (#10):
    #   3rd skip in a row on a Focus Pursuit -> FocusProposal(remove)
    """
    s, pauses = snap.settings(), pausing.kept(snap.pauses)
    if paused(pauses, now):
        return Decision(snap.changes(), Outcome(True, ()))
    today = now.astimezone(ZoneInfo(s.timezone)).date()
    existing = snap.brief(today)
    if existing is not None:
        if not resend(existing.attempts):
            return Decision(snap.changes(), Outcome(True, ()))
        earlier = tuple(replace(a, status=SendStatus.ARRIVAL_UNKNOWN) for a in existing.attempts)
        again = replace(existing, attempts=(*earlier, SendAttempt(now, SendStatus.SENDING, ())))
        return Decision(snap.changes().guard_reads().put_brief(again), Outcome(True, ()), send=again)
    cs = snap.changes().guard_reads()
    pursuits = []
    for p in snap.pursuits():
        if p.next_step is not None and p.next_step.ended is None and offers(snap, p.id, p.next_step) >= OFFERS:
            p = _skip_by_silence(p, datetime.combine(today, time(), tzinfo=ZoneInfo(s.timezone)), now)
            cs.put_pursuit(p)
        pursuits.append(p)
    prev = _previous(snap, today)
    current = {p.id: p for p in pursuits}
    followups = _followups(prev, current, today, s) if prev else ()
    if prev:
        followups, raised = neglect.endings(followups, current, prev)
        for p in raised:
            current[p.id] = p
            cs.put_pursuit(p)
    acted = any(f.state in ACTED for f in followups)
    resting = set() if acted else {(f.pursuit, f.step) for f in followups if f.state == "no_sign"}
    picked = any(p.focus or any(_adds_focus(h) for h in p.history) for p in pursuits)
    pool = [p for p in pursuits if (p.focus if picked else p.status is Status.ACTIVE)
            and _offerable(p.next_step, today) and (p.id, p.next_step.id) not in resting]
    offered = sorted(pool, key=lambda p: _step_order(snap, p, today))[:MAX_STEPS]
    ask, silenced = neglect.decision(snap, prev, {o.id for o in offered}, today, pauses)
    if silenced is not None:
        cs.put_pursuit(neglect.parked(current[silenced], now))
    draft = SentBrief(
        day=today, attempts=(SendAttempt(now, SendStatus.SENDING, ()),), follows_up=prev.day if prev else None,
        followups=followups, steps=tuple(BriefStep(n, p.id, p.next_step.id) for n, p in enumerate(offered, 1)),
        decision=ask,
        no_focus_yet=not picked, text="")
    b = replace(draft, text=messages.brief(draft, snap, messages.Style.TELEGRAM))
    return Decision(cs.put_brief(b), Outcome(True, ()), send=b)


def settle_send(snap: Snapshot, day: date, outcome: SendAttempt) -> Decision:
    """The outcome of the brief's last attempt (SENT with the message ids, or ARRIVAL_UNKNOWN)
    replaces its SENDING mark."""
    b = snap.brief(day)
    settled = replace(b, attempts=(*b.attempts[:-1], outcome))
    return Decision(snap.changes().put_brief(settled), Outcome(True, ()))


def _adds_focus(h: HistoryLine) -> bool:
    return h.actor is Actor.OWNER and h.statement.text.startswith(FOCUS_ADDED)


def _offerable(step: Step | None, today: date) -> bool:
    return step is not None and step.ended is None and (step.held_until is None or step.held_until <= today)


def _step_order(snap: Snapshot, p: Pursuit, today: date) -> tuple:
    """A held Step on its first offer after the hold first (W Q8), then the oldest Step, then the
    most recent work."""
    held_due = p.next_step.held_until is not None and offers(snap, p.id, p.next_step) == 0
    return (not held_due, p.next_step.created, -(p.last_worked or date.min).toordinal(), p.name)


OFFERS = 2
"""A Step offered this many times and still open ends as skipped by silence (W Q7)."""
ACTED = frozenset({"done_word", "done_seen", "skipped_word"})
"""Follow-up states that show the owner acted on yesterday's brief. When none does, the Steps with
no sign rest a day: the brief says it will offer each once more, and offers the rest."""


def _skip_by_silence(p: Pursuit, midnight: datetime, now: datetime) -> Pursuit:
    """Ended at 00:00 local of the day it is skipped, as the record writes a skip by silence."""
    step = p.next_step
    ended = replace(step, ended=Skipped("silence", midnight, None))
    line = HistoryLine(_next_history_id(p), now, Actor.ATLAS,
                       Inference(f"Skipped the Step after {OFFERS} offers with no sign of it.", step.text.basis))
    return replace(p, next_step=None, past_steps=(*p.past_steps, ended), history=(*p.history, line))


def _next_history_id(p: Pursuit) -> HistoryId:
    n = max((int(h.id.split("-")[1]) for h in p.history), default=0) + 1
    return HistoryId(f"h-{n:04d}")


def _previous(snap: Snapshot, today: date) -> SentBrief | None:
    """The last brief before today the owner was sent: Friday's, after a paused Saturday."""
    sent = [b for b in snap.briefs() if b.day < today and any(a.status is SendStatus.SENT for a in b.attempts)]
    return max(sent, key=lambda b: b.day, default=None)


def _followups(prev: SentBrief, pursuits: dict[PursuitId, Pursuit], today: date, s: Settings) -> tuple[Followup, ...]:
    out = []
    for offered in prev.steps:
        p = pursuits.get(offered.pursuit)
        steps = (p.next_step, *p.past_steps) if p else ()
        step = next((st for st in steps if st is not None and st.id == offered.step), None)
        if step is not None:
            out.append(Followup(p.id, step.id, _followup_state(p, step, today, s), None))
    return tuple(out)


def _followup_state(p: Pursuit, step: Step, today: date, s: Settings) -> str:
    if isinstance(step.ended, Done):
        return f"done_{step.ended.how}"
    if isinstance(step.ended, Skipped):
        return "skipped_word" if step.ended.how == "word" else "skipped_silence"
    if step.held_until is not None and step.held_until > today:
        return "held"
    return "not_visible_yet" if _worked_late(p, s) else "no_sign"


def _worked_late(p: Pursuit, s: Settings) -> bool:
    """The Pursuit's latest Session is from a Harness whose Sessions reach Atlas late (W Q7)."""
    return bool(p.sessions) and harness_of(max(p.sessions, key=lambda m: m.at).evidence) in s.late_harnesses


def compose_review(snap: Snapshot, now: datetime) -> Decision:
    """Weekly Review for the ISO week ending now. Covers everything since the previous SENT
    Review (so the first Review after a Pause covers the whole gap). Sections from History
    lines and Journal 'Decided' lines (already marked), open proposals not raised in a
    follow-up, parked_to_raise (records raised_at), pending hand edits, reverted agent
    changes, and a brief-time proposal when the Profile's work pattern disagrees."""
    raise NotImplementedError


# ------------------------------------------------------------------ librarian results -> state

ACTIVE_DAYS = 30


def starting_status(last_worked: date, today: date) -> Status:
    """A Pursuit the backfill builds starts active when it was worked on in the last 30 days,
    and parked otherwise. It never starts done or dropped: only the owner ends a Pursuit."""
    return Status.ACTIVE if (today - last_worked).days <= ACTIVE_DAYS else Status.PARKED


def start_state(snap: Snapshot, grouping: Grouping, now: datetime) -> Decision:
    """Backfill: each draft becomes a Pursuit, active or parked by `starting_status` on its
    Sessions' last turn, outside Focus, with the History line "Created from N Sessions". Each
    one-off is marked with its Area. The model wrote the grouping after reading every Session in
    `grouping.read`, so it is used whole or not at all: Conflict, at once or at the commit, when
    one of them is gone or changed, and the caller groups again without it."""
    zone = ZoneInfo(snap.settings().timezone)
    today = now.astimezone(zone).date()
    taken = {p.id for p in snap.pursuits()}
    cs = snap.changes().require_sessions(grouping.read)
    for draft in grouping.drafts:
        id = _pursuit_id(draft, taken)
        taken.add(id)
        ids = tuple(m.evidence for m in draft.sessions)
        created = HistoryLine(HistoryId("h-0001"), now, Actor.ATLAS,
                              Inference(f"Created from {len(ids)} Session{'' if len(ids) == 1 else 's'}", ids))
        next_step = Step(StepId(f"s-{today:%Y%m%d}-1"), draft.next_step, today) if draft.next_step else None
        last = max(m.at.astimezone(timezone.utc) for m in draft.sessions).astimezone(zone).date()
        cs.put_pursuit(Pursuit(
            id=id, name=draft.name, kind=draft.kind, area=draft.area, status=starting_status(last, today),
            focus=False, created=today, continued=None, resume=draft.resume, next_step=next_step, past_steps=(),
            filing_rules=(), sessions=draft.sessions, proposals=(), history=(created,)))
    for id, area in grouping.one_offs.items():
        cs.mark_one_off(id, area, today)
    return Decision(cs, Outcome(True, ()))


def _pursuit_id(d: PursuitDraft, taken: set[PursuitId]) -> PursuitId:
    """p- and base32 of its Sessions' ids, so planning the same build again names it the same."""
    digest = base64.b32encode(hashlib.sha256(" ".join(m.evidence for m in d.sessions).encode()).digest()).decode()
    digest = digest.lower()
    return next(PursuitId(f"p-{digest[:n]}") for n in range(4, len(digest)) if f"p-{digest[:n]}" not in taken)


# ------------------------------------------------------------------ the Journal

def timed(snap: Snapshot, one_offs: Mapping[EvidenceId, Area], since: datetime | None = None) -> list[TimedSession]:
    """Every Session a Pursuit lists as worked on, or that is a one-off in `one_offs`, with a
    turn at or after `since`, as Active Time reads it: the owner's messages in its own
    conversation, every other turn as the agent's, and nothing of an exchange with Atlas."""
    work: dict[EvidenceId, list[Work]] = {id: [(area, ONE_OFF)] for id, area in one_offs.items()}
    for p in snap.pursuits():
        for m in p.sessions:
            if m.advances:
                work.setdefault(m.evidence, []).append((p.area, p.id))
    out = []
    for s in snap.sessions(since=since):
        if s.id in work:
            turns = [t for t in s.turns if t.at and not t.atlas_tool_turn]
            out.append(TimedSession(s.id, tuple(t.at for t in turns if t.role == "owner" and t.own),
                                    tuple(t.at for t in turns if not (t.role == "owner" and t.own)),
                                    tuple(dict.fromkeys(work[s.id]))))
    return out


def workdays(timed: Sequence[TimedSession], settings: Settings, since: date | None,
             until: date) -> dict[date, list[TimedSession]]:
    """The local days from `since` to `until` that Active Time touches, oldest first, each with
    the Sessions that touch it."""
    zone = ZoneInfo(settings.timezone)
    days: dict[date, list[TimedSession]] = {}
    for s in timed:
        touched = set()
        for i in activetime.stretches(s.owner_times, s.agent_times, settings.gap):
            day, last = i.start.astimezone(zone).date(), (i.end - timedelta(microseconds=1)).astimezone(zone).date()
            while day <= last:
                touched.add(day)
                day += timedelta(days=1)
        for day in touched:
            if (since is None or since <= day) and day <= until:
                days.setdefault(day, []).append(s)
    return dict(sorted(days.items()))


def uncited(snap: Snapshot, current: JournalDay | None, time: DayTime) -> list[EvidenceId]:
    """The day's Sessions its file does not cite yet, which the model reads for it, in the order
    the day's Active Time lines show their Pursuits."""
    cited = {*current.evidence, *(id for s in current.lines for id in cites(s))} if current else set()
    shown = [key for split in time.areas.values() for key in split]
    place: dict[EvidenceId, int] = {}
    for p in snap.pursuits():
        for m in p.sessions:
            if m.advances and p.id in shown:
                place[m.evidence] = min(place.get(m.evidence, len(shown)), shown.index(p.id))
    return sorted((id for id in time.basis if id not in cited), key=lambda id: place.get(id, len(shown)))


def journal_due(current: JournalDay | None, time: DayTime, held: set[EvidenceId]) -> bool:
    """A day with at least a minute of Active Time is written when its file is missing, shows
    another Active Time, or cites a Session the record no longer holds."""
    if not time.total:
        return False
    return current is None or current.time != time or not _still_held(current.lines, held)


def journal_day(snap: Snapshot, day: date, time: DayTime, lines: JournalLines, corrected: str | None = None
                ) -> Decision:
    """The day's file: its Active Time, the lines it had whose Sessions are all still held, then
    `lines`, which the model drafted from the Sessions the file did not cite yet, and its
    corrections, `corrected` (corrections.line) added after them."""
    current = snap.journal(day)
    held = {h.id for h in snap.held().sessions()}

    def section(attr: str) -> tuple:
        kept = tuple(s for s in (getattr(current, attr) if current else ()) if _still_held((s,), held))
        return kept + tuple(s for s in getattr(lines, attr) if s not in kept)
    written = JournalDay(day, time, section("worked_on"), section("decided"), section("finished"),
                         (*(current.corrections if current else ()),
                          *((corrected,) if corrected and (not current or corrected not in current.corrections) else ())))
    return Decision(snap.changes().put_journal(written), Outcome(True, ()))


def _still_held(lines: Sequence, held: set[EvidenceId]) -> bool:
    return all(id in held for s in lines for id in cites(s) if harness_of(id))


# ------------------------------------------------------------------ the Board

FOCUS_SOFT_CAP = 5
"""A star past it is kept; the Board then shows how many are in Focus."""


def from_board(snap: Snapshot, cmd: SetFocus | SetStatus | MarkSeen, who: Owner, now: datetime) -> Decision:
    """A Board click: the change, its History line, and a one-line Note naming it (who.note). Only
    an active Pursuit takes a star. A seen mark is machinery: no Note, no History."""
    if isinstance(cmd, MarkSeen):
        known = all(upto in {h.id for h in snap.pursuit(id).history} for id, upto in cmd.marks.items())
        return Decision(snap.changes().mark_seen(cmd.marks) if known else snap.changes(),
                        Outcome(known, (), None if known else "NOT_A_HISTORY_ID"))
    p = snap.pursuit(cmd.pursuit)
    if isinstance(cmd, SetFocus) and cmd.on and p.status is not Status.ACTIVE:
        return Decision(snap.changes(), Outcome(False, (), "NOT_ACTIVE"))
    decision = decide(snap, cmd, who, now)
    if not decision.outcome.ok or decision.changes.is_empty():
        return decision
    at = now.replace(second=0, microsecond=0)
    note = Note(who.note, at, NoteOrigin.OWNER, "board", _clicked(p, cmd), about=p.id,
                atlas_reply="\n".join(decision.outcome.lines))
    return Decision(decision.changes.append_note(note), decision.outcome)


def _clicked(p: Pursuit, cmd: SetFocus | SetStatus) -> str:
    if isinstance(cmd, SetFocus):
        return f"{'Added' if cmd.on else 'Took'} {p.name} {'to' if cmd.on else 'out of'} Focus on the Board."
    return f"Moved {p.name} to {cmd.status.value.title()} on the Board."


def marks(history: Sequence[HistoryLine], seen: HistoryId | None) -> tuple[bool, bool]:
    """(new, changed) of a card. Only Atlas's and agents' lines after the seen mark raise one; the
    owner's never do. New: Atlas created it and the owner has not seen it since. Changed: any other
    such line."""
    raised = [h for h in history if h.actor is not Actor.OWNER and (seen is None or _n(h.id) > _n(seen))]
    created = bool(history) and history[0].actor is Actor.ATLAS and history[0] in raised
    return created, any(h is not history[0] for h in raised) if created else bool(raised)


KIND_CHANGED = "Changed Kind from"
"""How Atlas's History line for a Kind change begins: "Changed Kind from idea to project: ..."."""


def kind_line(p: Pursuit, zone: str) -> str:
    """The card's Kind, with the Kind it had until Atlas last changed it: "project (an idea until
    Wed 18 Nov)"."""
    changed = [h for h in p.history if h.actor is Actor.ATLAS and h.statement.text.startswith(KIND_CHANGED)]
    if not changed:
        return p.kind.value
    was = changed[-1].statement.text.removeprefix(KIND_CHANGED).split()[0]
    day = changed[-1].at.astimezone(ZoneInfo(zone))
    return f"{p.kind.value} ({'an' if was[0] in 'aeiou' else 'a'} {was} until {day:%a} {day.day} {day:%b})"


def _n(id: HistoryId) -> int:
    return int(id.split("-")[1])


def board(snap: Snapshot) -> Answer:
    """BoardView: columns by Status, Focus first, then the latest worked; new/changed from History
    against the seen marks; proposals."""
    seen = snap.seen()
    columns: dict[Status, list[Card]] = {s: [] for s in Status}
    for p in snap.pursuits():
        new, changed = marks(p.history, seen.get(p.id))
        columns[p.status].append(Card(
            pursuit=p.id, name=p.name, kind_line=kind_line(p, snap.settings().timezone), last_worked=p.last_worked,
            left_off=p.resume.left_off[0] if p.resume.left_off else None,
            next_step=p.next_step.text if p.next_step and p.next_step.ended is None else None,
            focus=p.focus, new=new, changed=changed, proposals=p.proposals,
            resume_prompt=messages.resume_prompt(p, snap), upto=max((h.id for h in p.history), key=_n, default=None)))
    order = lambda c: (not c.focus, -(c.last_worked or date.min).toordinal(), c.name)
    return Answer(None, (), Coverage(), data=BoardView({s: tuple(sorted(cs, key=order)) for s, cs in columns.items()}))
