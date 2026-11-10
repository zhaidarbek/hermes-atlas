"""The weekly Review (scene 36, W Q5), as pure functions: what it covers, each section from the
record, the text sent, which replies answer it, and what Forget leaves of it. No I/O, no model, no
clock reads.

A Review covers the days since the last Review sent, or the ISO week when none was, so the first
Review after a Pause covers the weeks it missed. Each section is one function of plain record data,
left out when it has nothing to say:

    FINISHED             the owner's endings, and the Steps done, by word and seen in Sessions
    DECIDED              Resume Note Decided Facts read in the period, but those that closed an Open
                         question, one per Pursuit per day
    CHANGED              the owner's parks, drops, continues and Focus changes
    WHAT I DID ON MY OWN Atlas's own History lines, whatever the seen marks say (D Q17), but the
                         Step marks FINISHED counts and the proposals the sections below ask; then
                         the Profile entries Atlas added and the owner has not corrected (G3)
    FOCUS                open Focus proposals, with three skipped Steps in a row proposing removal and
                         a slot freed in the period proposing one Pursuit (rules.free_slot)
    ENDINGS, REOPENING   proposals no follow-up raised
    PARKED               rules.parked_to_raise, at most one, quiet by calendar days since its last Session
    WAITING FOR "apply"  hand edits and refused agent updates not yet applied
    IMPORTS              export files kept in the inbox after a failure
    TIME PROPOSAL        rules.time_proposal (G4)

A reply answers only what the Review it replies to asked, or the latest Review when it replies to
nothing and Atlas sent nothing after it. A bare word that fits two of its questions asks which and
changes nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

from . import messages, neglect, rules
from .model import (Actor, AnswerProposal, ChangeSettings, Cite, Command, Continue, Done, EndingProposal, EvidenceId, Fact,
                    FocusProposal, HarnessId, HistoryLine, Inference, IsoWeek, Note, NoteOrigin, OwnerMessage, Outcome, Profile, Pursuit,
                    PursuitId, Query, ReopenProposal, SendAttempt, SendStatus, SentReview, SetStatus, Statement,
                    Status, Who, WhichQ, cites, harness_of, resend)
from .record import ImportLog, Snapshot

QUIET = "A quiet week: nothing to report."
CORRECT = "Reply to correct any of these."
KEEP_PARKED = 'Said "keep parked".'
LEFT_AS_IS = "Left it as it is."
SKIPS = 3
"""Skipped Steps in a row, by word or silence, after which Atlas proposes taking a Pursuit out of Focus (W Q10)."""
BOOKKEEPING = ("Marked the Step done", "Skipped the Step", "Proposed ")
"""Atlas's History lines that WHAT I DID ON MY OWN leaves to other sections: FINISHED counts the
Steps, and the proposal sections ask the proposals."""


@dataclass(frozen=True)
class Item:
    lines: tuple[str, ...]                   # the first is the item's line; the rest are indented under it
    cites: frozenset[EvidenceId] = frozenset()


@dataclass(frozen=True)
class Section:
    title: str
    items: tuple[Item, ...]
    footer: str | None = None


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    now: datetime                            # in the owner's zone: citations read their time from it

    def holds(self, at: datetime) -> bool:
        return self.start <= at.astimezone(self.now.tzinfo).date() <= self.end


# ------------------------------------------------------------------ what a Review covers

def iso_week(day: date) -> IsoWeek:
    year, week, _ = day.isocalendar()
    return IsoWeek(f"{year}-W{week:02d}")


def sent_at(r: SentReview) -> datetime | None:
    return min((a.at for a in r.attempts if a.status is SendStatus.SENT), default=None)


def covers(reviews: Sequence[SentReview], today: date) -> tuple[date, date]:
    """From the day after the last Review sent before today, or this ISO week's Monday when none was,
    to today."""
    before = [r for r in reviews if sent_at(r) is not None and r.covers[1] < today]
    last = max(before, key=lambda r: r.covers[1], default=None)
    start = last.covers[1] + timedelta(days=1) if last else today - timedelta(days=today.weekday())
    return start, today


def heading(start: date, end: date) -> str:
    first = f"{start.day}" if (start.year, start.month) == (end.year, end.month) else f"{start.day} {start:%B}"
    span = f"{first} to {end.day} {end:%B}"
    return f"Atlas · Review · {'week of ' if (end - start).days == 6 else ''}{span}"


# ------------------------------------------------------------------ sections

def finished(pursuits: Sequence[Pursuit], period: Period) -> Section:
    ended = sorted(((h.at, p.name) for p in pursuits for h in p.history if h.actor is not Actor.ATLAS
                    and h.statement.text == rules.STATUS_SAID[Status.DONE][0] and period.holds(h.at)))
    items = [Item((f"{name}: you marked it done {_on(at, period)}. [F]",)) for at, name in ended]
    done = [s.ended.how for p in pursuits for s in (*p.past_steps, p.next_step)
            if s is not None and isinstance(s.ended, Done) and period.holds(s.ended.at)]
    if done:
        parts = [f"{n} {said}" for n, said in ((done.count("word"), "you told me about"),
                                               (done.count("seen"), "I saw in your Sessions")) if n]
        items.append(Item((f"{len(done)} Step{_s(len(done))} done: {', '.join(parts)}. [F]",)))
    return Section("FINISHED", tuple(items))


def decided(pursuits: Sequence[Pursuit], replies: frozenset[EvidenceId], period: Period) -> Section:
    """Resume Note Decided Facts read in the period, by Pursuit, with their source: a Session, an
    agent's Note, or the owner's reply in the chat ("your reply"). A Decided Fact that rests on the
    same source as a QUESTION_CLOSED History line came with an update that removed an Open question:
    it belongs to the question it closed and is not listed. Of the rest, one line per Pursuit per
    day, the earliest."""
    found = {}
    for p in pursuits:
        closed = {h.statement.source for h in p.history if isinstance(h.statement, Fact)
                  and h.statement.text.startswith(rules.QUESTION_CLOSED.split('"')[0])}
        for s in p.resume.decided:
            if not (isinstance(s, Fact) and isinstance(s.source, Cite) and period.holds(s.source.at)) \
                    or s.source in closed:
                continue
            key = (p.name, s.source.at.astimezone(period.now.tzinfo).date())
            if key in found and found[key][0] <= s.source.at:
                continue
            source = s.source
            if source.evidence in replies and source.label == "your Note":
                source = replace(source, label="your reply")
            line = Fact(f"{p.name}: {s.text.rstrip('.')}.", source)
            found[key] = (s.source.at, Item((messages.statement(line, messages.Style.TELEGRAM, cite=period.now),),
                                            _sessions(s)))
    ordered = sorted(found.items(), key=lambda kv: (kv[0][0], kv[1][0]))
    return Section("DECIDED", tuple(item for _, (_, item) in ordered))


def changed(pursuits: Sequence[Pursuit], period: Period) -> Section:
    lines = sorted(((h, p) for p in pursuits for h in p.history if h.actor is Actor.OWNER and period.holds(h.at)),
                   key=lambda hp: hp[0].at)
    said = lambda text: [p.name for h, p in lines if h.statement.text == text]
    parked = said(rules.STATUS_SAID[Status.PARKED][0])
    clauses = [f"{verb} {_and(names)}{after}" for verb, names, after in (
        ("dropped", said(rules.STATUS_SAID[Status.DROPPED][0]), ""),
        ("continued", said(rules.CONTINUE_SAID[0]), ""),
        ("made", said(rules.STATUS_SAID[Status.ACTIVE][0]), " active")) if names]
    items = [Item((f"Parked {_and(parked)}. [F]",))] if parked else []
    if clauses:
        joined = " and ".join(clauses)
        items.append(Item((f"{joined[0].upper()}{joined[1:]}. [F]",)))
    for h, p in lines:
        if h.statement.text.startswith(rules.FOCUS_ADDED):
            items.append(Item((f"{p.name} added to Focus {_on(h.at, period)}. [F]",)))
        elif h.statement.text.startswith(rules.FOCUS_SAID[False][0].split(" on ")[0]):
            items.append(Item((f"{p.name} taken out of Focus {_on(h.at, period)}. [F]",)))
    return Section("CHANGED", tuple(items))


def own(pursuits: Sequence[Pursuit], profile: Profile, period: Period) -> Section:
    """Atlas's own History lines in the period, by kind: created, Kind changed, active again,
    parked after two unanswered asks, filed by a Filing rule (one line per Pursuit and rule), any
    other; then the Profile entries Atlas added and the owner has not corrected."""
    kinds: dict[str, list[tuple[datetime, Item]]] = {k: [] for k in ("created", "kind", "active", "parked", "other")}
    filed: dict[tuple[PursuitId, str], list[HistoryLine]] = {}
    names = {p.id: p for p in pursuits}
    for p in pursuits:
        for h in p.history:
            text = h.statement.text
            if h.actor is not Actor.ATLAS or not period.holds(h.at) or text.startswith(BOOKKEEPING):
                continue
            if m := _FILED.fullmatch(text):
                filed.setdefault((p.id, m["rule"]), []).append(h)
                continue
            kind, line = _own_line(p, h)
            kinds[kind].append((h.at, Item((line,), _sessions(h.statement))))
    for (id, rule), hs in filed.items():
        p = names[id]
        kinds["other"].append((hs[0].at, Item((f"Filed {len(hs)} Session{_s(len(hs))} under {p.name}, "
                                                f"{_by_rule(p, rule, period)}. [F]",),
                                               frozenset().union(*(_sessions(h.statement) for h in hs)))))
    items = [item for k in kinds for _, item in sorted(kinds[k], key=lambda x: x[0])]
    items += [Item((f'Added to your Profile: "{e.statement.text}" {_mark(e.statement)}',), _sessions(e.statement))
              for e in profile.entries if e.by is Who.LIBRARIAN and period.start <= e.added <= period.end]
    return Section("WHAT I DID ON MY OWN", tuple(items), CORRECT if items else None)


_FILED = re.compile(r"Filed an? .+ Session here by your Filing rule: (?P<rule>.+)")


def _own_line(p: Pursuit, h: HistoryLine) -> tuple[str, str]:
    text = h.statement.text.rstrip(".")
    if text.startswith("Created"):
        rest = text.removeprefix("Created").removeprefix(" it")
        return "created", f"Created {p.name} ({p.area}){rest}. [F]"
    if text.startswith(rules.KIND_CHANGED):
        return "kind", f"Changed {p.name}{text.removeprefix('Changed Kind')}. {_mark(h.statement)}"
    if text.startswith("Active again"):
        return "active", f"Made {p.name} active again{text.removeprefix('Active again')}. [F]"
    if text == neglect.PARKED_BY_SILENCE.rstrip("."):
        return "parked", f'Parked {p.name} after two unanswered asks; reply "continue" to undo it. [F]'
    return "other", f"{p.name}: {text}. {_mark(h.statement)}"


def _by_rule(p: Pursuit, rule: str, period: Period) -> str:
    """"by Sunday's split": the day the owner's correction made the rule, and which correction."""
    made = next((r for r in p.filing_rules if r.text == rule and isinstance(r.source, Cite)), None)
    if made is None:
        return "by your Filing rule"
    day = made.source.at.astimezone(period.now.tzinfo)
    when = f"{day:%A}'s" if 0 <= (period.now.date() - day.date()).days <= 7 else f"the {day.day} {day:%b}"
    return f"by {when} {'merge' if rule.startswith('Work filed as') else 'split'}"


def focus(pursuits: Sequence[Pursuit]) -> Section:
    items = []
    for p in sorted(pursuits, key=lambda p: p.name):
        for x in p.proposals:
            if isinstance(x, FocusProposal) and (p.status is Status.ACTIVE) and x.add is not p.focus:
                word = f'"{"add" if x.add else "remove"} {reply_word(p, pursuits)}"'
                items.append(Item((f'{x.reason.text} [I] Reply {word} or "leave it".',), _sessions(x.reason)))
    return Section("FOCUS", tuple(items))


def endings(pursuits: Sequence[Pursuit]) -> Section:
    items = []
    for p in sorted(pursuits, key=lambda p: p.name):
        for x in p.proposals:
            if isinstance(x, EndingProposal) and not x.in_followup and not p.status.is_ending:
                word = "close it" if x.to is Status.DONE else "drop it"
                items.append(Item((f"{p.name}: I propose marking it {x.to.value}. {x.basis.text} [I]",
                                   f'Reply "{word}" or "leave it".'), _sessions(x.basis)))
    return Section("ENDINGS", tuple(items))


def reopening(pursuits: Sequence[Pursuit], period: Period) -> Section:
    items = []
    for p in sorted(pursuits, key=lambda p: p.name):
        for x in p.proposals:
            if isinstance(x, ReopenProposal) and p.status.is_ending:
                said = messages.statement(Fact(f"{p.name} is {p.status.value}. {x.basis.text}", x.basis.source),
                                          messages.Style.TELEGRAM, cite=period.now)
                items.append(Item((said, 'Reply "reopen it" or "leave it".'), _sessions(x.basis)))
    return Section("REOPENING", tuple(items))


def parked(p: Pursuit | None, quiet_days: int, period: Period) -> Section:
    if p is None:
        return Section("PARKED", ())
    lines = [f"{p.name} · {p.kind.value} · quiet {quiet_days} days [F]"]
    if p.resume.left_off:
        lines.append("Left off: " + messages.statement(p.resume.left_off[0], messages.Style.TELEGRAM, cite=period.now))
    lines.append("Continue, keep parked, or drop?")
    return Section("PARKED", (Item(tuple(lines), _sessions(*p.resume.left_off[:1])),))


def waiting(pending: Sequence[Note], pursuits: Sequence[Pursuit], period: Period) -> Section:
    """Hand edits and refused agent updates not yet applied. A refused update is worded as the
    tutorial tells it: a request Atlas could not check in its Harness, kept as a Note with the
    owner's words."""
    names = {p.id: p.name for p in pursuits}
    items = []
    for n in pending:
        if n.origin is NoteOrigin.HAND_EDIT:
            items.append(Item((f"You edited {n.via} by hand {_on(n.at, period)}. I put it back and kept your text "
                               f"as a Note. [F]",)))
        elif n.origin is NoteOrigin.REFUSED_UPDATE:
            about = f"{names[n.about]}: a" if n.about in names else "A"
            items.append(Item((f"{about} change {HarnessId(n.via).label} asked for {_on(n.at, period)}. I could not "
                               f"check your words there, so I kept it as a Note with your words. [F]",), frozenset(n.derived_from)))
    footer = 'Reply "apply" to make the change.' if len(items) == 1 else 'Reply "apply" to make the latest change.'
    return Section('WAITING FOR "apply"', tuple(items), footer if items else None)


def imports(log: Sequence[ImportLog], period: Period) -> Section:
    """Export files kept in the inbox, failed or partly read (D Q5). A file imported and deleted
    shows on the Board, as the tutorial's Review does not list it."""
    items = []
    for e in log:
        if e.deleted or not period.holds(e.at):
            continue
        what = f"a {e.harness.label} export" if e.harness else "a file"
        items.append(Item((f"Kept {what} in your inbox {_on(e.at, period)}: I could not read all of it. [F]",)))
    return Section("IMPORTS", tuple(items))


def time_section(proposed: time | None, brief_at: time, days: int) -> Section:
    if proposed is None:
        return Section("TIME PROPOSAL", ())
    return Section("TIME PROPOSAL", (Item((
        f"On {days} of your last 14 working days your first work started an hour or more away from "
        f"{brief_at:%H:%M}, around {proposed:%H:%M}. [F]",
        f'Move the Daily Brief to {proposed:%H:%M}? Reply "move it" or "keep it".')),))


# ------------------------------------------------------------------ the text

def render(start: date, end: date, sections: Sequence[Section]) -> tuple[str, tuple[tuple[EvidenceId, tuple[int, ...]], ...]]:
    """The Review's text, and for each Session it draws on the numbers of its lines that do."""
    lines = [heading(start, end)]
    drawn: dict[EvidenceId, list[int]] = {}
    shown = [s for s in sections if s.items]
    for s in shown:
        lines += ["", s.title]
        for item in s.items:
            first = len(lines) + 1
            lines += [f"- {item.lines[0]}", *(f"  {line}" for line in item.lines[1:])]
            for id in item.cites:
                drawn.setdefault(id, []).extend(range(first, len(lines) + 1))
        if s.footer:
            lines.append(f"  {s.footer}")
    if not shown:
        lines += ["", QUIET]
    return "\n".join(lines), tuple((id, tuple(drawn[id])) for id in sorted(drawn))


def without(r: SentReview, ids: frozenset[EvidenceId]) -> SentReview:
    """The Review with every line drawn from these Sessions gone (Forget)."""
    gone = {n for id, lines in r.drawn_from if id in ids for n in lines}
    if not gone:
        return r
    kept = [(n, line) for n, line in enumerate(r.text.split("\n"), 1) if n not in gone]
    renumber = {old: new for new, (old, _) in enumerate(kept, 1)}
    drawn = tuple((id, kept_lines) for id, lines in r.drawn_from if id not in ids
                  if (kept_lines := tuple(renumber[n] for n in lines if n in renumber)))
    return replace(r, text="\n".join(line for _, line in kept), drawn_from=drawn)


# ------------------------------------------------------------------ composing it

def compose(snap: Snapshot, now: datetime) -> rules.Decision:
    """This ISO week's Review, committed as SENDING before it is sent. When the week's file is
    there, a second attempt with the same text if its delivery allows one (D Q13), else nothing."""
    s = snap.settings()
    local = now.astimezone(ZoneInfo(s.timezone))
    today = local.date()
    week = iso_week(today)
    existing = snap.review(week)
    if existing is not None:
        if not resend(existing.attempts):
            return rules.Decision(snap.changes(), Outcome(True, ()))
        earlier = tuple(replace(a, status=SendStatus.ARRIVAL_UNKNOWN) for a in existing.attempts)
        again = replace(existing, attempts=(*earlier, SendAttempt(now, SendStatus.SENDING, ())))
        return rules.Decision(snap.changes().guard_reads().put_review(again), Outcome(True, ()), send=again)
    cs = snap.changes().guard_reads()
    start, end = covers(snap.reviews(), today)
    period = Period(start, end, local)
    pursuits = []
    for p in snap.pursuits():
        proposed = removal(p, today)
        if proposed != p:
            cs.put_pursuit(proposed)
        pursuits.append(proposed)
    if (slot := rules.free_slot(pursuits, start, end, local.tzinfo)) is not None:
        cs.put_pursuit(slot)
        pursuits = [slot if p.id == slot.id else p for p in pursuits]
    raise_ = rules.parked_to_raise(snap, today)
    quiet = (today - (raise_.last_worked or raise_.created)).days if raise_ else 0
    proposed_time, off_days = rules.time_proposal_of(snap, today)
    replies = frozenset(n.id for n in snap.notes() if n.origin is NoteOrigin.OWNER and n.via == "telegram")
    sections = (finished(pursuits, period), decided(pursuits, replies, period), changed(pursuits, period),
                own(pursuits, snap.profile(), period), focus(pursuits), endings(pursuits),
                reopening(pursuits, period), parked(raise_, quiet, period), waiting(snap.pending_applies(), pursuits, period),
                imports(snap.imports(), period), time_section(proposed_time, s.brief_at, off_days))
    text, drawn = render(start, end, sections)
    silenced = {p.id for p, at in neglect.parked_by_silence(snap) if period.holds(at)}
    asked = tuple(p.id for p in pursuits if p.id in silenced or any(
        isinstance(x, FocusProposal) and p.status is Status.ACTIVE and x.add is not p.focus
        or isinstance(x, EndingProposal) and not x.in_followup and not p.status.is_ending
        or isinstance(x, ReopenProposal) and p.status.is_ending for x in p.proposals))
    if raise_ is not None and raise_.id not in asked:
        asked += (raise_.id,)
    r = SentReview(week, (start, end), (SendAttempt(now, SendStatus.SENDING, ()),), raise_.id if raise_ else None,
                   asked, proposed_time, messages.fit(text), drawn)
    return rules.Decision(cs.put_review(r), Outcome(True, ()), send=r)


def removal(p: Pursuit, today: date) -> Pursuit:
    """A Focus Pursuit whose last three Steps were skipped, by word or silence, with a proposal to take
    it out of Focus, once (W Q10)."""
    if not p.focus or rules.skipped_in_a_row(p) < SKIPS or any(
            isinstance(x, FocusProposal) and not x.add for x in p.proposals):
        return p
    skipped = p.past_steps[-SKIPS:]
    basis = tuple(dict.fromkeys(e for s in skipped for e in cites(s.text)))
    reason = Inference(f"{p.name}: its last {SKIPS} Steps were skipped, so it may not belong in Focus now.", basis)
    return replace(p, proposals=(*p.proposals, FocusProposal(False, reason, today)))


def parked_since(reviews: Sequence[SentReview], p: Pursuit) -> date:
    """The later of the Pursuit's last advancing Session, its owner's "keep parked" or "continue", and
    the last Review sent that raised it: what its 90 days count from (W Q6)."""
    raised = [r.covers[1] for r in reviews if r.raised_parked == p.id and sent_at(r) is not None]
    marks = [d for d in (p.last_worked, p.continued, p.raised_at, *raised) if d is not None]
    return max(marks) if marks else p.created


# ------------------------------------------------------------------ replies to it

UNREAD = object()
"""A reply in a Review's context that answers none of its questions."""


def replied(msg: OwnerMessage, snap: Snapshot) -> SentReview | object | None:
    """The Review a reply answers: the one it replies to, or, when it replies to nothing, the latest
    Review sent when Atlas sent nothing after it. A brief sent after it hands the message to the
    brief; an answer Atlas gave after it leaves the message UNREAD."""
    if msg.reply_to is not None:
        return snap.review(msg.reply_to.key) if msg.reply_to.kind == "review" else None
    last = latest(snap, msg.at)
    if last is None:
        return None
    since = sent_at(last)
    briefs = [a.at for b in snap.briefs() for a in b.attempts if a.status is SendStatus.SENT and a.at <= msg.at]
    if any(at > since for at in briefs):
        return None
    answered = any(n.atlas_reply is not None and since < n.at <= msg.at for n in snap.notes())
    return UNREAD if answered else last


def latest(snap: Snapshot, at: datetime) -> SentReview | None:
    sent = [r for r in snap.reviews() if (when := sent_at(r)) is not None and when <= at]
    return max(sent, key=sent_at, default=None)


def questions(r: SentReview, snap: Snapshot) -> list[tuple[Pursuit, str]]:
    """What the Review asked that is still open, as (Pursuit, kind)."""
    held = {p.id: p for p in snap.pursuits()}
    silenced = {p.id for p, _ in neglect.parked_by_silence(snap)}
    out = []
    for id in r.asked:
        p = held.get(id)
        if p is None:
            continue
        if id == r.raised_parked and p.status is Status.PARKED:
            out.append((p, "parked"))
        if id in silenced:
            out.append((p, "silenced"))
        for x in p.proposals:
            if isinstance(x, FocusProposal) and p.status is Status.ACTIVE and x.add is not p.focus:
                out.append((p, "add" if x.add else "remove"))
            elif isinstance(x, EndingProposal) and not p.status.is_ending:
                out.append((p, "close" if x.to is Status.DONE else "drop ending"))
            elif isinstance(x, ReopenProposal) and p.status.is_ending:
                out.append((p, "reopen"))
    return out


ANSWERS: dict[str, dict[str, Callable[[Pursuit], Command]]] = {
    "continue": {k: lambda p: Continue(p.id) for k in ("parked", "silenced")},
    "drop": {k: lambda p: SetStatus(p.id, Status.DROPPED) for k in ("parked", "drop ending")},
    "keep parked": {k: lambda p: AnswerProposal(p.id, False, keep_parked=True) for k in ("parked", "silenced")},
    "leave": {k: lambda p: AnswerProposal(p.id, False) for k in ("add", "remove", "close", "drop ending", "reopen")},
    "close": {"close": lambda p: SetStatus(p.id, Status.DONE)},
    "reopen": {"reopen": lambda p: SetStatus(p.id, Status.ACTIVE)},
    "add": {"add": lambda p: AnswerProposal(p.id, True)},
    "remove": {"remove": lambda p: AnswerProposal(p.id, True)},
}
"""Each reply word, by the kind of question it answers, and the Command it makes."""


def answer(word: str, words: str | None, msg: OwnerMessage, snap: Snapshot) -> Command | Query | object | None:
    """The Command a reply word makes for the Review's one question it fits; a WhichQ when it fits
    several and no words pick one; UNREAD when it fits none; None outside a Review's context."""
    r = replied(msg, snap)
    if r is None or r is UNREAD:
        return r
    if word not in ANSWERS:
        return UNREAD
    fits = {}
    for p, kind in questions(r, snap):
        if kind in ANSWERS[word] and (words is None or names(words, p)):
            fits.setdefault(p.id, (p, ANSWERS[word][kind]))
    if len(fits) == 1:
        p, make = next(iter(fits.values()))
        return make(p)
    if len(fits) > 1:
        return WhichQ(tuple(sorted(p.name for p, _ in fits.values())), word)
    return UNREAD


def answer_time(word: str, msg: OwnerMessage, snap: Snapshot) -> Command | object | None:
    """"move it" sets the brief time the Review proposed; "keep it" keeps it."""
    r = replied(msg, snap)
    if r is None or r is UNREAD:
        return r
    if r.time_proposed is None or r != latest(snap, msg.at):
        return UNREAD
    at = r.time_proposed if word == "move" else snap.settings().brief_at
    return ChangeSettings({"brief_at": f"{at:%H:%M}"})


FILLER = frozenset({"the", "my", "a", "an", "it", "this", "that", "to", "focus", "from", "of", "in", "out"})


def names(words: str, p: Pursuit) -> bool:
    wanted = {w for w in re.findall(r"\w+", words.casefold()) if w not in FILLER}
    return bool(wanted) and wanted <= set(re.findall(r"\w+", p.name.casefold()))


def reply_word(p: Pursuit, pursuits: Sequence[Pursuit]) -> str:
    """The word a reply names the Pursuit by: the last word of its name when no other Pursuit's name
    holds it, else its whole name."""
    last = re.findall(r"\w+", p.name.casefold())[-1:]
    if last and not any(q.id != p.id and names(last[0], q) for q in pursuits):
        return last[0]
    return p.name


# ------------------------------------------------------------------ helpers

def _on(at: datetime, period: Period) -> str:
    day = at.astimezone(period.now.tzinfo).date()
    ago = (period.now.date() - day).days
    if ago == 0:
        return "today"
    return f"on {day:%A}" if 0 < ago < 7 else f"on {day.day} {day:%b}"


def _and(names: Sequence[str]) -> str:
    names = list(dict.fromkeys(names))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _mark(s: Statement) -> str:
    return "[F]" if isinstance(s, Fact) else "[I]"


def _sessions(*statements: Statement) -> frozenset[EvidenceId]:
    return frozenset(id for s in statements for id in cites(s) if harness_of(id) is not None)


def settle(snap: Snapshot, week: str, outcome: SendAttempt) -> rules.Decision:
    """The outcome of the Review's last attempt replaces its SENDING mark."""
    r = snap.review(week)
    return rules.Decision(snap.changes().put_review(replace(r, attempts=(*r.attempts[:-1], outcome))), Outcome(True, ()))
