"""Seed states: the record as a tutorial scene leaves it, written through atlas.record's ChangeSets.

Usable outside the harness too, for a live check against a throwaway record:

    cd tests && ../.venv/bin/python -c "from pathlib import Path; from harness import seeds; seeds.after_scene_6(Path('/tmp/atlas'))"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from atlas import activetime, converse, rules
from atlas.librarian import JournalLines
from atlas.model import (LOCAL, Actor, Area, BriefStep, ChannelSetting, Cite, DayTime, DecisionAsk, Done, EndingProposal,
                         EvidenceId,
                         Fact, FocusProposal, HarnessId, HarnessSetting, HistoryLine, Inference, InterfaceSetting,
                         JournalDay, Kind, Membership, Note, NoteOrigin, OwnerMessage, Pattern, Profile, ProfileEntry,
                         Pursuit, PursuitId, ResumeNote,
                         SendAttempt, SendStatus, SentBrief, SentRef, SessionCard, SessionEvidence, Settings, Status,
                         Step, StepId, Turn, Who)
from atlas.model import evidence_id as _evidence_id
from atlas.record import Record

from . import tutorial, world
from .home import OWNER

SETTINGS = Settings(timezone="Europe/Berlin", brief_at=time(7, 30), review_day=7, review_at=time(18, 0),
                    gap=timedelta(minutes=15), machine=world.MACHINE_IN_TUTORIAL, harnesses={}, exclusions=(),
                    channel=ChannelSetting("telegram"))
SETUP_PROFILE = Profile(("work", "health", "family"), ())
PROFILE = Profile(("work", "health", "family", "home"), ())
BACKFILLED = datetime(2026, 11, 15, 20, 10, tzinfo=world.TZ)
STARRED = datetime(2026, 11, 15, 21, 0, tzinfo=world.TZ)


def evidence_id(s: world.WorldSession) -> EvidenceId:
    """The id collection gives a world Session."""
    return _evidence_id(s.harness, LOCAL, s.native_key)


def _evidence(s: world.WorldSession) -> SessionEvidence:
    return SessionEvidence(evidence_id(s), s.harness, s.interface, LOCAL, s.native_key, s.title, s.start, s.end,
                           world.MACHINE_IN_TUTORIAL, s.cwd, s.branch, (), ())


AFTER_SETUP = replace(SETTINGS, harnesses={
    harness: HarnessSetting(True, "store", (root,), "probe") for harness, root in (
        (world.CC, "~/.claude"), (world.CX, "~/.codex"), (world.DV, "~/.local/share/devin/cli"), (world.HM, "~/.hermes"))},
    interfaces={"t3_code": InterfaceSetting("probe", "~/.t3")})


def after_scene_1(root: Path) -> None:
    """Scene 1's end: the record setup creates, with every store Harness of the world found."""
    Record.open(root).create(AFTER_SETUP, SETUP_PROFILE)


@dataclass(frozen=True)
class _Card:
    id: str
    name: str
    kind: Kind
    area: str
    sessions: tuple[str, ...]                # world Session titles, oldest first
    left_off: str
    step: str
    where: str | None = None
    status: Status = Status.ACTIVE
    focus: bool = False
    decided: str | None = None
    open_question: str | None = None
    reopen: tuple[str, ...] = ()              # world Session titles; default the latest Session


AFTER_SCENE_6 = (
    _Card("p-2x7b", "Payments CI flakes", Kind.PROBLEM, "work",
          ("payments api retries", "ci matrix", "redis db per worker", "retry flake"),
          "it fails only when two workers share Redis db 0.",
          "Run tests/test_retry.py with one worker, ten times in a row.",
          "ubuntu-pc, ~/work/payments-api, branch fix/retry-flake", focus=True,
          decided="keep xdist; no sleeps in tests.", open_question="does the conftest fixture flush Redis between tests?",
          reopen=("retry flake", "ci matrix")),
    _Card("p-4k7q", "Billing export service", Kind.PROJECT, "work", ("ledger csv export", "pdf vat rounding"),
          "CSV totals match the ledger; the PDF rounds VAT per line.",
          "Export October twice, with per-line and per-invoice VAT rounding, and compare the totals.",
          "ubuntu-pc, ~/work/billing-export, branch pdf-vat", focus=True),
    _Card("p-6r2t", "Greenhouse logger", Kind.PROJECT, "home", ("greenhouse humidity chart", "logger sd card swap"),
          "the logger moved to a new SD card and has not been checked since.",
          "Read the last 20 lines of the logger's log over ssh.", "ubuntu-pc, ~/home/greenhouse/firmware", focus=True),
    _Card("p-5g9d", "Reading tracker for the kids", Kind.IDEA, "family", ("reading tracker ideas",),
          "Listed what the kids would log: book, minutes, one sentence.",
          "Decide between a paper chart and a tablet app.", "ubuntu-pc, ~/family/reading", focus=True),
    _Card("p-9m3c", "Billing export: staging deploy", Kind.PROJECT, "work", ("staging deploy env vars",),
          "The staging deploy reads its settings from env vars.", "Rotate the staging database password.",
          "ubuntu-pc, ~/work/billing-export, branch deploy-staging"),
    _Card("p-7d1c", "Agent-tooling newsletter", Kind.IDEA, "work", ("newsletter issue topics",),
          "you drafted three issue topics.", "Write the first 200 words of issue one.", "ubuntu-pc, ~/writing/newsletter"),
    _Card("p-3b8e", "Bike route planner", Kind.EXPERIMENT, "home", ("gpx elevation smoothing", "gpx tunnel gaps"),
          "elevation smoothing works on one GPX file.", "Try it on a ride that goes through a tunnel.",
          "ubuntu-pc, ~/code/bike-routes, branch main"),
    _Card("p-3n0w", "Home server backups", Kind.PROJECT, "home", ("restic nightly backups",),
          "restic runs nightly; no restore tested.", "Restore one folder into /tmp and compare it.",
          "ubuntu-pc, ~/home/backups"),
    _Card("p-1k2f", "Keyboard firmware", Kind.EXPERIMENT, "home",
          ("keyboard matrix scan", "split layout keymap", "qmk build", "right half drops"),
          "The split layout compiles; the right half drops keys.", "Flash the right half with a 10 ms debounce.",
          "ubuntu-pc, ~/code/kb-firmware, branch split-right", status=Status.PARKED,
          reopen=("right half drops", "qmk build")),
)


def _label(s: world.WorldSession) -> str:
    return s.harness.label + (f" via {s.interface}" if s.interface else "")


def _star(card: _Card) -> Note:
    return Note(EvidenceId(f"nt-{card.id[2:]}star"), STARRED, NoteOrigin.OWNER, "board",
                f"Added {card.name} to Focus on the Board.")


def _pursuit(card: _Card) -> Pursuit:
    sessions = [world.by_title(t) for t in card.sessions]
    last = sessions[-1]
    ids = tuple(evidence_id(s) for s in sessions)
    history = [HistoryLine("h-0001", BACKFILLED, Actor.ATLAS,
                           Inference(f"Created from {len(ids)} Session{'s' if len(ids) > 1 else ''}", ids))]
    if card.focus:
        star = _star(card)
        history.append(HistoryLine("h-0002", STARRED, Actor.OWNER,
                                   Fact("Added it to Focus on the Board.", Cite(star.id, "your Note", STARRED))))
    where = (Fact(card.where, Cite(ids[-1], _label(last), last.start)),) if card.where else ()
    cited = Cite(ids[-1], _label(last), last.end)
    return Pursuit(
        id=PursuitId(card.id), name=card.name, kind=card.kind, area=card.area, status=card.status, focus=card.focus,
        created=BACKFILLED.date(), continued=None,
        resume=ResumeNote(where=where, left_off=(Fact(card.left_off, cited),),
                          decided=(Fact(card.decided, cited),) if card.decided else (),
                          open_questions=(Inference(card.open_question, (ids[-1],)),) if card.open_question else (),
                          reopen=tuple(evidence_id(world.by_title(t)) for t in card.reopen) or (ids[-1],)),
        next_step=Step(StepId("s-20261115-1"), Inference(card.step, (ids[-1],)), date(2026, 11, 15)),
        past_steps=(), filing_rules=(), sessions=tuple(Membership(i, s.end, True) for i, s in zip(ids, sessions)),
        proposals=(), history=tuple(history))


def after_scene_6(root: Path, starred: bool = True) -> None:
    """Scene 6's end: four Focus Pursuits with open Steps, three Neglected ones (the newsletter,
    the bike route planner and the home server backups), a recent active one outside Focus, and a
    parked one. The Pursuit files name their Sessions, which collection (#5) has not written yet,
    so the Snapshot is lent the fixture world's Sessions. Without `starred`, scene 5's end: the
    same Pursuits before any star, so no owner line in any History."""
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    snap = record.snapshot()
    known = {evidence_id(s): _evidence(s) for s in world.SESSIONS}
    snap.session = known.__getitem__
    cs = snap.changes()
    for card in AFTER_SCENE_6:
        card = card if starred else replace(card, focus=False)
        cs.put_pursuit(_pursuit(card))
        if card.focus:
            cs.append_note(_star(card))
    record.commit(cs)


MONDAY_BRIEF_ID = "8812"
FRIDAY_BRIEF_ID = "8840"
HOME_CHAT = f"telegram:{OWNER}"             # where `hermes send --to telegram` delivers the briefs


def after_scene_7(root: Path) -> None:
    """Scene 7's end: Monday's brief, built by rules.compose_daily and sent as message 8812."""
    after_scene_6(root)
    record = Record.open(root)
    snap = record.snapshot()
    brief = rules.compose_daily(snap, world.FIRST_BRIEF_AT).send
    sent = SendAttempt(world.FIRST_BRIEF_AT, SendStatus.SENT, (MONDAY_BRIEF_ID,), chat=HOME_CHAT)
    record.commit(snap.changes().put_brief(replace(brief, attempts=(sent,))))


def scene_8(root: Path) -> None:
    """Scene 7's end, with the Payments CI flakes Resume Note in the words scene 8's prompt shows.
    Scene 7's brief words its Left off line and Step for the owner; the prompt words them for an
    agent. One Resume Note cannot hold both, so week mode needs a decision on #21."""
    after_scene_7(root)
    record = Record.open(root)
    snap = record.snapshot()
    p = snap.pursuit("p-2x7b")
    left_off = Fact("test_retry fails only when two pytest-xdist workers share Redis db 0.", p.resume.left_off[0].source)
    step = Inference("Run tests/test_retry.py -n 1 ten times and report how many runs pass.", p.next_step.text.basis)
    record.commit(snap.changes().put_pursuit(replace(p, resume=replace(p.resume, left_off=(left_off,)),
                                                     next_step=replace(p.next_step, text=step))))


KNEE = "p-8h1n"
KNEE_EXPORT = EvidenceId("gpt-5k2m9q1d")
KNEE_CREATED = datetime(2026, 11, 18, 9, 0, tzinfo=world.TZ)
KNEE_STARRED = datetime(2026, 11, 18, 21, 0, tzinfo=world.TZ)


def _knee_star() -> Note:
    return Note(EvidenceId("nt-8h1nstar"), KNEE_STARRED, NoteOrigin.OWNER, "board",
                "Added Knee rehab plan to Focus on the Board.")


def _knee() -> Pursuit:
    """The Knee rehab plan as Wednesday's exports (#15) leave it, in Focus since Wednesday evening."""
    star = _knee_star()
    step = Step(StepId("s-20261119-1"), Inference("Ask for a week-3 progression based on how week 2 felt.", (KNEE_EXPORT,)),
                date(2026, 11, 19))
    return Pursuit(
        id=PursuitId(KNEE), name="Knee rehab plan", kind=Kind.PROJECT, area="health", status=Status.ACTIVE, focus=True,
        created=KNEE_CREATED.date(), continued=None,
        resume=ResumeNote((), (Fact("Physio exercises listed for weeks 1 and 2; week 3 not planned.",
                                    Cite(KNEE_EXPORT, "ChatGPT", datetime(2026, 11, 16, 19, 10, tzinfo=world.TZ))),),
                          (), (), ()),
        next_step=step, past_steps=(), filing_rules=(), sessions=(), proposals=(),
        history=(HistoryLine("h-0001", KNEE_CREATED, Actor.ATLAS,
                             Inference("Created it from your ChatGPT Sessions.", (KNEE_EXPORT,))),
                 HistoryLine("h-0002", KNEE_STARRED, Actor.OWNER,
                             Fact("Added it to Focus on the Board.", Cite(star.id, "your Note", KNEE_STARRED)))))


def after_scene_25(root: Path) -> None:
    """Scene 25's end, as far as scene 26 reads it: Friday's brief, sent as message 8840, whose
    Step 2 is the Knee rehab plan's. That Pursuit came from ChatGPT exports (#15), so it lists no
    Session here, and it joined Focus on Wednesday."""
    after_scene_6(root)
    record = Record.open(root)
    snap = record.snapshot()
    knee = _knee()
    billing = snap.pursuit("p-4k7q")
    brief = SentBrief(
        day=world.FRIDAY_BRIEF_AT.date(),
        attempts=(SendAttempt(world.FRIDAY_BRIEF_AT, SendStatus.SENT, (FRIDAY_BRIEF_ID,), chat=HOME_CHAT),),
        follows_up=date(2026, 11, 19), followups=(),
        steps=(BriefStep(1, billing.id, billing.next_step.id), BriefStep(2, knee.id, knee.next_step.id)),
        decision=None, no_focus_yet=False, text=tutorial.fenced_block("Knee rehab plan         Ask for a week-3"))
    record.commit(snap.changes().put_pursuit(knee).append_note(_knee_star()).put_brief(brief))


MONDAY = world.FIRST_BRIEF_AT.date()
MONDAY_IDS = {"Payments CI flakes": "p-2x7b", "Billing export service": "p-4k7q", "Knee rehab plan": "p-8h1n"}
DONE_SAID_AT = datetime(2026, 11, 16, 7, 58, tzinfo=world.TZ)


def _turns(s: world.WorldSession) -> tuple[Turn, ...]:
    return tuple(Turn("owner" if t.owner else "agent", t.at, t.text, "main") for t in s.turns())


def monday_filed(root: Path) -> None:
    """Monday's Sessions filed as the tutorial's Journal shows them: three Pursuits, one one-off,
    and the ChatGPT Session that Wednesday's export brought (#15). Collection has already put
    the store Sessions in the record."""
    record = Record.open(root)
    snap = record.snapshot()
    cs = snap.changes()
    for s in world.WEB_SESSIONS:
        cs.put_session(replace(_evidence(s), turns=_turns(s)), {})
    record.commit(cs)
    cs = record.snapshot().changes()
    for title, area in world.MONDAY_ONE_OFFS.items():
        id = evidence_id(world.by_title(title))
        cs.put_card(SessionCard(id, Inference(f"Work on {title}.", (id,)), Inference("Answered.", (id,)), None, None,
                                Area(area), MONDAY))
    for name, (area, titles) in world.MONDAY_PURSUITS.items():
        sessions = [world.by_title(t) for t in titles]
        ids = tuple(evidence_id(s) for s in sessions)
        cs.put_pursuit(Pursuit(
            id=PursuitId(MONDAY_IDS[name]), name=name, kind=Kind.PROJECT, area=Area(area), status=Status.ACTIVE,
            focus=False, created=BACKFILLED.date(), continued=None, resume=ResumeNote((), (), (), (), ()),
            next_step=None, past_steps=(), filing_rules=(),
            sessions=tuple(Membership(i, s.end, True) for i, s in zip(ids, sessions)), proposals=(),
            history=(HistoryLine("h-0001", BACKFILLED, Actor.ATLAS,
                                 Inference(f"Created from {len(ids)} Session{'s' if len(ids) > 1 else ''}", ids)),)))
    record.commit(cs)


def monday_corrected(root: Path) -> None:
    """Monday's Journal as Friday finds it: the Steps finished by the owner's word (#4) and the
    correction Wednesday's ChatGPT export made (#15), both as the tutorial shows them."""
    day = tutorial.fenced_block(tutorial.JOURNAL_DAY).splitlines()
    finished = day[day.index("Finished") + 1].removeprefix("- ").removesuffix(" [F]")
    corrected = next(line for line in day if line.startswith("Corrected ")).removeprefix("Corrected ")
    said = tutorial.fenced_block("1 done, 10/10").splitlines()[0].removeprefix("You").strip()
    note = Note(EvidenceId("nt-1a9s4d2f"), DONE_SAID_AT, NoteOrigin.OWNER, "telegram", said)
    record = Record.open(root)
    snap = record.snapshot()
    journal = snap.journal(MONDAY)
    record.commit(snap.changes().append_note(note).put_journal(replace(
        journal, finished=(Fact(finished, Cite(note.id, "your Note", note.at)),), corrections=(corrected,))))


# ------------------------------------------------------------------ Tuesday to Friday (scenes 11, 13, 25)

TUESDAY_BRIEF_ID = "8820"
THURSDAY_BRIEF_ID = "8835"
PAYMENTS, BILLING, GREENHOUSE, READING = map(PursuitId, ("p-2x7b", "p-4k7q", "p-6r2t", "p-5g9d"))
BACKUPS, BIKE = PursuitId("p-3n0w"), PursuitId("p-3b8e")
KNEE_SESSION = SessionEvidence(KNEE_EXPORT, HarnessId.CHATGPT, None, LOCAL, "knee-weeks-1-2", "knee weeks 1 and 2",
                               datetime(2026, 11, 16, 18, 40, tzinfo=world.TZ),
                               datetime(2026, 11, 16, 19, 10, tzinfo=world.TZ), world.MACHINE_IN_TUTORIAL, None, None,
                               (), ())


def _lent(record: Record):
    """A Snapshot lent the fixture world's Sessions, which collection has not written yet."""
    snap = record.snapshot()
    known = {evidence_id(s): _evidence(s) for s in world.SESSIONS} | {KNEE_EXPORT: KNEE_SESSION}
    snap.session = known.__getitem__
    return snap


def _cite(title: str, at: datetime | None = None) -> Cite:
    s = world.by_title(title)
    return Cite(evidence_id(s), _label(s), at or s.end)


def _worked(p: Pursuit, title: str) -> Pursuit:
    s = world.by_title(title)
    return replace(p, sessions=(*p.sessions, Membership(evidence_id(s), s.end, True)))


def _seen(p: Pursuit, title: str, at: datetime, then: Step | None, detail: str = "") -> Pursuit:
    """The Pursuit's open Step done, seen in the world Session `title` at `at`, and `then` next."""
    cite = _cite(title, at)
    done = replace(p.next_step, ended=Done("seen", at, Fact(detail, cite)))
    return replace(_worked(p, title), next_step=then, past_steps=(*p.past_steps, done))


def _step(id: str, text: str, basis: EvidenceId) -> Step:
    return Step(StepId(id), Inference(text, (basis,)), datetime.strptime(id.split("-")[1], "%Y%m%d").date())


def _said(record: Record, text: str, at: datetime, brief: str) -> None:
    """The owner's reply to a brief, read and decided by Atlas's own rules."""
    snap = _lent(record)
    msg = OwnerMessage(text, at, "telegram", HOME_CHAT, SentRef("brief", brief), f"{OWNER}:{at:%H%M}")
    read = converse.interpret(msg, snap, None)
    record.commit(rules.converse(snap, msg, read.commands, read.asks).changes)


def _sent_brief(day: datetime, id: str, steps: tuple[tuple[PursuitId, StepId], ...], decision: DecisionAsk | None,
                text: str) -> SentBrief:
    return SentBrief(day.date(), (SendAttempt(day, SendStatus.SENT, (id,), chat=HOME_CHAT),), None, (),
                     tuple(BriefStep(n, p, st) for n, (p, st) in enumerate(steps, 1)), decision, False, text)


def after_scene_9(root: Path) -> None:
    """Monday after the owner's reply to the brief: the CI flakes Step done by their word and the
    newsletter parked."""
    after_scene_7(root)
    _said(Record.open(root), "1 done, 10/10 pass with one worker. park the newsletter", world.DONE_AT, "2026-11-16")


SEEN_BILLING = datetime(2026, 11, 16, 21, 40, tzinfo=world.TZ)


def after_scene_9b(root: Path) -> None:
    """Monday night, after the tick filed the evening's Session: the billing Step seen done in it
    at 21:40, with the next billing Step. The reading tracker's Step and the bike route planner's
    Left off read as Tuesday's brief words them."""
    after_scene_9(root)
    record = Record.open(root)
    snap = _lent(record)
    payments = _worked(snap.pursuit(PAYMENTS), "test_retry one worker")
    billing = _seen(snap.pursuit(BILLING), "per-invoice vat rounding", SEEN_BILLING,
                    _step("s-20261116-1", "Switch the PDF export to per-invoice rounding and rerun October.",
                          evidence_id(world.by_title("per-invoice vat rounding"))),
                    "per-invoice rounding matches the ledger")
    reading = snap.pursuit(READING)
    reading = replace(reading, next_step=replace(reading.next_step, text=Inference(
        "Write down paper chart or tablet app, and why.", reading.next_step.text.basis)))
    bike = snap.pursuit(BIKE)
    bike = replace(bike, resume=replace(bike.resume, left_off=(
        Fact("elevation smoothing worked on one GPX file.", _cite("gpx tunnel gaps")),)))
    record.commit(snap.changes().put_pursuit(payments).put_pursuit(billing).put_pursuit(reading).put_pursuit(bike))


def after_scene_10(root: Path) -> None:
    """Tuesday morning: scene 9b's end and Tuesday's brief, sent as message 8820, offering the
    greenhouse, reading tracker and billing Steps and asking about the bike route planner."""
    after_scene_9b(root)
    record = Record.open(root)
    snap = _lent(record)
    billing, reading, greenhouse = (snap.pursuit(id) for id in (BILLING, READING, GREENHOUSE))
    brief = _sent_brief(world.TUESDAY_BRIEF_AT, TUESDAY_BRIEF_ID, (
        (GREENHOUSE, greenhouse.next_step.id), (READING, reading.next_step.id), (BILLING, billing.next_step.id)),
        DecisionAsk(BIKE, 1, 22), tutorial.fenced_block("2 · Reading tracker for the kids Write down"))
    record.commit(snap.changes().put_brief(brief))


def after_scene_12(root: Path) -> None:
    """Wednesday morning: scene 11's replies, the greenhouse Step seen done on Tuesday evening with
    no Next Step yet, and new Steps for the CI flakes and the reading tracker. The backups' Left off
    line reads as Wednesday's decision quotes it."""
    after_scene_10(root)
    record = Record.open(root)
    _said(record, "3 remind me friday. drop the bike planner", world.HOLD_AT, "2026-11-17")
    _said(record, "2 done. tablet app, nothing fancy", world.TABLET_AT, "2026-11-17")
    snap = _lent(record)
    greenhouse = _seen(snap.pursuit(GREENHOUSE), "greenhouse log interval",
                       datetime(2026, 11, 17, 21, 15, tzinfo=world.TZ), None)
    payments = snap.pursuit(PAYMENTS)
    payments = replace(payments, next_step=_step(
        "s-20261117-1", "Run the suite 50 times with a separate Redis db per xdist worker.", payments.sessions[-1].evidence))
    reading = snap.pursuit(READING)
    reading = replace(reading, next_step=_step(
        "s-20261117-2", "Create the app and get one screen that logs a book and minutes.", reading.sessions[-1].evidence))
    backups = snap.pursuit(BACKUPS)
    backups = replace(backups, resume=replace(backups.resume, left_off=(
        Fact("you set up the nightly restic job.", _cite("restic nightly backups")),)))
    record.commit(snap.changes().put_pursuit(greenhouse).put_pursuit(payments).put_pursuit(reading)
                  .put_pursuit(backups))


WEDNESDAY_BRIEF_ID = "8828"
FINISHED_BY = "The flake is gone in 50 of 50 runs, which is what this Pursuit was for."


def _brief_sent(record: Record, at: datetime, id: str) -> None:
    """The day's brief as rules.compose_daily builds it at `at`, sent as message `id`."""
    record.commit(rules.compose_daily(record.snapshot(), at).changes)
    record.commit(rules.settle_send(record.snapshot(), at.date(), SendAttempt(at, SendStatus.SENT, (id,),
                                                                                chat=HOME_CHAT)).changes)


def after_scene_13(root: Path) -> None:
    """Wednesday morning: scene 12's end and Wednesday's brief, sent as message 8828, which asks
    about the backups for the first time."""
    after_scene_12(root)
    _brief_sent(Record.open(root), world.WEDNESDAY_BRIEF_AT, WEDNESDAY_BRIEF_ID)


def wednesday_afternoon(root: Path) -> None:
    """Wednesday after the reading tracker's Codex Session was filed (scene 14): its Step seen done
    at 14:30, with its next Step, the greenhouse logger's next Step, and the Knee rehab plan in
    Focus from Wednesday's exports (#15). The CI flakes Step is still open."""
    after_scene_13(root)
    record = Record.open(root)
    snap = _lent(record)
    app = evidence_id(world.by_title("reading tracker app screen"))
    reading = _seen(snap.pursuit(READING), "reading tracker app screen", datetime(2026, 11, 18, 14, 30, tzinfo=world.TZ),
                    _step("s-20261118-1", 'Add the "one sentence" field.', app))
    reading = replace(reading, resume=replace(reading.resume, left_off=(
        Fact("The app logs a book and minutes on one screen.", reading.past_steps[-1].ended.basis.source),)))
    greenhouse = snap.pursuit(GREENHOUSE)
    greenhouse = replace(greenhouse, next_step=_step(
        "s-20261118-2", "Check that the logger now writes every 5 minutes.", greenhouse.sessions[-1].evidence))
    knee = _knee()
    knee = replace(knee, next_step=replace(knee.next_step, id=StepId("s-20261118-1"), created=KNEE_CREATED.date()))
    record.commit(snap.changes().put_pursuit(replace(reading, kind=Kind.PROJECT)).put_pursuit(greenhouse)
                  .put_pursuit(knee).append_note(_knee_star()))


def wednesday_evening(root: Path) -> None:
    """Wednesday evening, after the tick filed the CI flakes Session of 15:35: its Step seen done at
    16:05, and the ending that Session shows proposed on the Pursuit."""
    wednesday_afternoon(root)
    record = Record.open(root)
    snap = _lent(record)
    filed = world.TICK_FILINGS["suite 50 runs per worker"]
    session = evidence_id(world.by_title("suite 50 runs per worker"))
    payments = _seen(snap.pursuit(PAYMENTS), "suite 50 runs per worker", datetime(2026, 11, 18, 16, 5, tzinfo=world.TZ),
                     None, filed.done)
    payments = replace(payments, proposals=(
        EndingProposal(Status.DONE, Inference(FINISHED_BY, (session,)), world.ENDING_TICK_AT.date(), False),))
    record.commit(snap.changes().put_pursuit(payments))


def after_scene_20(root: Path) -> None:
    """Thursday morning: Thursday's brief, sent as message 8835, proposing to close the CI flakes
    and asking about the backups a second time."""
    wednesday_evening(root)
    _brief_sent(Record.open(root), world.THURSDAY_BRIEF_AT, THURSDAY_BRIEF_ID)


def after_scene_20b(root: Path) -> None:
    """Thursday after "close it. continue the backups", with every Session collected so far."""
    after_scene_20(root)
    _said(Record.open(root), "close it. continue the backups", world.CLOSE_AT, world.THURSDAY_BRIEF_AT.date().isoformat())
    _hold(root, [s for s in world.SESSIONS if s.is_owner_session and s.start < world.FIND_AT])


def after_scene_24(root: Path) -> None:
    """Friday morning, as far as scene 25 reads it: the CI flakes closed and the backups continued
    on Thursday, the Knee rehab plan in Focus from a ChatGPT Session, and Thursday's brief, sent as
    message 8835, offering the knee, reading tracker and greenhouse Steps, none acted on."""
    after_scene_12(root)
    record = Record.open(root)
    snap = _lent(record)
    record.commit(snap.changes().put_settings(replace(snap.settings(), harnesses={
        **snap.settings().harnesses, HarnessId.CHATGPT: HarnessSetting(True, "export", (), "owner")})))
    snap = _lent(record)
    thursday = world.THURSDAY_BRIEF_AT
    payments = replace(snap.pursuit(PAYMENTS), status=Status.DONE, focus=False, next_step=None)
    backups = replace(snap.pursuit(BACKUPS), continued=thursday.date())
    reading = snap.pursuit(READING)
    reading = replace(reading, next_step=_step(
        "s-20261118-1", 'Add the "one sentence" field.', reading.sessions[-1].evidence))
    greenhouse = snap.pursuit(GREENHOUSE)
    greenhouse = replace(greenhouse, next_step=_step(
        "s-20261118-2", "Check that the logger now writes every 5 minutes.", greenhouse.sessions[-1].evidence))
    knee = _knee()
    knee = replace(knee, sessions=(Membership(KNEE_EXPORT, KNEE_SESSION.last_at, True),))
    brief = _sent_brief(thursday, THURSDAY_BRIEF_ID, (
        (knee.id, knee.next_step.id), (READING, reading.next_step.id), (GREENHOUSE, greenhouse.next_step.id)),
        DecisionAsk(BACKUPS, 2, 21), tutorial.fenced_block("(three Steps: Knee rehab plan"))
    cs = snap.changes()
    for p in (payments, backups, reading, greenhouse, knee):
        cs.put_pursuit(p)
    record.commit(cs.append_note(_knee_star()).put_brief(brief))


THURSDAY = date(2026, 11, 19)
STAGING = "p-9m3c"


def thursday_filed(root: Path) -> None:
    """Friday lunch as scene 30 finds it, after collection has put the store Sessions in the record:
    Thursday's 14:02 Codex Session, where the owner pasted a password, is filed under Billing export:
    staging deploy, gives its Resume Note one Fact and Thursday's Journal one line, and has a card
    and a cached model output. Thursday's other Session gives Payments CI flakes and the Journal
    theirs, which Forget must leave alone."""
    record = Record.open(root)
    snap = record.snapshot()
    envvars, config, flake, retry = (world.by_title(t) for t in (
        "staging deploy env vars", "staging deploy config", "flake fix merged", "retry flake"))
    ids = {s.title: evidence_id(s) for s in (envvars, config, flake, retry)}

    def pursuit(id: str, name: str, sessions, left_off: Fact, step: str) -> Pursuit:
        first = (ids[sessions[0].title],)
        return Pursuit(
            id=PursuitId(id), name=name, kind=Kind.PROJECT, area=Area("work"), status=Status.ACTIVE, focus=False,
            created=BACKFILLED.date(), continued=None, resume=ResumeNote((), (left_off,), (), (), first),
            next_step=Step(StepId("s-20261115-1"), Inference(step, first), date(2026, 11, 15)), past_steps=(),
            filing_rules=(), sessions=tuple(Membership(ids[s.title], s.end, True) for s in sessions), proposals=(),
            history=(HistoryLine("h-0001", BACKFILLED, Actor.ATLAS, Inference("Created from 1 Session", first)),))

    cs = snap.changes()
    cs.put_pursuit(pursuit(STAGING, "Billing export: staging deploy", (envvars, config),
                           Fact("The staging deploy reads its settings from env vars.",
                                Cite(ids[config.title], "Codex", config.end)),
                           "Rotate the staging database password."))
    cs.put_pursuit(pursuit("p-2x7b", "Payments CI flakes", (retry, flake),
                           Fact("The retry flake fix is merged.", Cite(ids[flake.title], "Codex", flake.end)),
                           "Run tests/test_retry.py with one worker, ten times in a row."))
    id = ids[config.title]
    cs.put_card(SessionCard(id, Inference("Set the staging deploy config.", (id,)),
                            Inference("Its settings moved to env vars.", (id,)), None, None))
    record.commit(cs)
    record.cache_put("seeded-staging-deploy-config", b'"Its settings moved to env vars."', [id])

    snap = record.snapshot()
    timed = rules.timed(snap, {})
    active = activetime.day(rules.workdays(timed, snap.settings(), THURSDAY, THURSDAY)[THURSDAY], THURSDAY,
                            snap.settings().timezone, snap.settings().gap)
    lines = JournalLines(worked_on=(
        Fact("Merged the retry flake fix.", Cite(ids[flake.title], "Codex", flake.start)),
        Fact("Moved the staging deploy settings into env vars.", Cite(id, "Codex", config.start))))
    record.commit(rules.journal_day(snap, THURSDAY, active, lines).changes)


def _profile() -> Profile:
    """The Profile the tutorial reads with `cat ~/atlas/profile.md` before scene 28, its entries'
    words read from that block and resting on this world's Sessions."""
    texts = [re.sub(r" \[I from .*\]$", "", line[2:]) for line in tutorial.fenced_block("# Profile").splitlines()
             if line.startswith("- ")]
    ids = {title: evidence_id(world.by_title(title)) for title in (
        "logger sd card swap", "keyboard matrix scan", "right half drops", "pi-hole dns loop")}
    blocks = Pattern("131 days of Sessions", date(2026, 7, 7), date(2026, 11, 15), tuple(ids.values()))
    bases = (blocks, (ids["logger sd card swap"],), (ids["right half drops"], ids["pi-hole dns loop"]))
    return replace(PROFILE, entries=tuple(ProfileEntry(Inference(text, basis), date(2026, 11, 16), Who.LIBRARIAN)
                                          for text, basis in zip(texts, bases, strict=True)))


def _learned(root: Path) -> None:
    record = Record.open(root)
    record.commit(record.snapshot().changes().put_profile(_profile()))


def scene_28(root: Path) -> None:
    """Scene 6's end, with the Profile Atlas has learned by Friday."""
    after_scene_6(root)
    _learned(root)


def scene_29(root: Path) -> None:
    """Friday morning as scene 26 starts from, with the Profile Atlas has learned by then."""
    after_scene_25(root)
    _learned(root)
KNEE_SESSION_AT = datetime(2026, 11, 16, 19, 10, tzinfo=world.TZ)
KNEE_FILED = datetime(2026, 11, 18, 9, 0, tzinfo=world.TZ)


def after_scene_16(root: Path) -> None:
    """Scene 16's end, as far as scene 17 reads it: scene 6's Board plus the Knee rehab plan, which
    Atlas created on Wednesday from a ChatGPT export Session of Monday 19:10, with a proposal to add
    it to Focus. Its Session comes from an export (#15), so the Snapshot is lent it."""
    after_scene_6(root)
    record = Record.open(root)
    snap = record.snapshot()
    session = SessionEvidence(KNEE_EXPORT, HarnessId.CHATGPT, None, LOCAL, "knee-weeks-1-2", "knee weeks 1 and 2",
                              KNEE_SESSION_AT - timedelta(minutes=25), KNEE_SESSION_AT, world.MACHINE_IN_TUTORIAL,
                              None, None, (), ())
    snap.session = {KNEE_EXPORT: session}.__getitem__
    left, step = tutorial.fenced_block("Knee rehab plan · problem").splitlines()[1:3]
    basis = Inference("Three ChatGPT Sessions this month are about the knee, and none is in Focus.", (KNEE_EXPORT,))
    knee = Pursuit(
        id=PursuitId(KNEE), name="Knee rehab plan", kind=Kind.PROBLEM, area="health", status=Status.ACTIVE, focus=False,
        created=KNEE_FILED.date(), continued=None,
        resume=ResumeNote((), (Fact(left.split("Left off", 1)[1].split(" [F")[0].strip(),
                                    Cite(KNEE_EXPORT, "ChatGPT", KNEE_SESSION_AT)),), (), (), ()),
        next_step=Step(StepId("s-20261118-1"),
                       Inference(step.split("Next", 1)[1].split(" [I]")[0].strip(), (KNEE_EXPORT,)), KNEE_FILED.date()),
        past_steps=(), filing_rules=(), sessions=(Membership(KNEE_EXPORT, KNEE_SESSION_AT, True),),
        proposals=(FocusProposal(True, basis, KNEE_FILED.date()),),
        history=(HistoryLine("h-0001", KNEE_FILED, Actor.ATLAS, Inference("Created it from your ChatGPT Sessions.",
                                                                          (KNEE_EXPORT,))),))
    record.commit(snap.changes().put_pursuit(knee))


def _hold(root: Path, sessions: list[world.WorldSession]) -> None:
    """The record holds these Sessions with their turns, as collection would have written them."""
    record = Record.open(root)
    cs = record.snapshot().changes()
    for s in sessions:
        cs.put_session(replace(_evidence(s), turns=_turns(s)), {})
    record.commit(cs)


def _changed(root: Path, id: str, change) -> None:
    record = Record.open(root)
    snap = record.snapshot()
    record.commit(snap.changes().put_pursuit(change(snap.pursuit(id))))


BIKE = "p-3b8e"


def thursday(root: Path) -> None:
    """Thursday as scenes 22 and 24 read it: the bike route planner dropped on Tuesday (#11), the
    billing export's Left off as Monday's Session left it (#10), and every Session collected so far."""
    after_scene_6(root)
    _hold(root, [s for s in world.SESSIONS if s.is_owner_session and s.start < world.FIND_AT])
    _changed(root, BIKE, lambda p: replace(p, status=Status.DROPPED))
    status = tutorial.fenced_block("send Dana the billing").split('"')[1].split(": ", 1)[1]
    _changed(root, "p-4k7q", lambda p: replace(p, resume=replace(p.resume, left_off=(
        Fact(status, p.resume.left_off[0].source),))))


_WEEK = {    # the tutorial's week by Area, spread over its days, in minutes
    date(2026, 11, 16): {"work": {"p-4k7q": 120, "p-2x7b": 70, "one-off": 10}, "health": {KNEE: 40}},
    date(2026, 11, 17): {"work": {"p-4k7q": 90, "p-2x7b": 60}, "home": {"p-6r2t": 15}},
    date(2026, 11, 18): {"work": {"p-4k7q": 90, "p-9m3c": 75, "one-off": 60}, "family": {"p-5g9d": 85}},
    date(2026, 11, 19): {"work": {"p-2x7b": 115, "p-4k7q": 60}, "family": {"p-5g9d": 60}},
    date(2026, 11, 20): {"work": {"p-4k7q": 30, "one-off": 40}, "home": {"p-6r2t": 115}},
}


def sunday_evening(root: Path) -> None:
    """Sunday as scene 37 reads it: the Journal's days Monday to Friday, with the tutorial's
    week of Active Time spread over them, each counted from that day's Sessions, and nothing
    worked on Saturday or yet today."""
    after_scene_25(root)
    week = [s for s in (*world.SESSIONS, *world.WEB_SESSIONS)
            if s.is_owner_session and min(_WEEK) <= s.start.date() <= max(_WEEK)]
    _hold(root, week)
    record = Record.open(root)
    cs = record.snapshot().changes()
    for day, areas in _WEEK.items():
        split = {Area(a): {k: timedelta(minutes=m) for k, m in ps.items()} for a, ps in areas.items()}
        total = sum((t for ps in split.values() for t in ps.values()), timedelta())
        basis = tuple(evidence_id(s) for s in week if s.start.date() == day)
        cs.put_journal(JournalDay(day, DayTime(total, split, basis), (), (), (), ()))
    record.commit(cs)


def keyboard_parked(root: Path) -> None:
    """Scene 6's end, with the keyboard firmware's Resume Note in the words scene 38 shows."""
    after_scene_6(root)
    block = tutorial.fenced_block("where did I leave off on the keyboard").splitlines()
    said = {line.split(": ", 1)[0].split()[-1]: line.split(": ", 1)[1] for line in block if ": " in line}
    last = world.by_title("right half drops")
    cite = Cite(evidence_id(last), "Claude Code", last.end)

    def text(key: str) -> str:
        return re.sub(r" \[[FI][^\]]*\]$", "", said[key])

    def resumed(p: Pursuit) -> Pursuit:
        basis = (evidence_id(last),)
        return replace(p, resume=replace(p.resume, left_off=(Fact(text("off"), cite),), decided=(Fact(text("Decided"), cite),),
                                         open_questions=(Inference(text("question"), basis),)),
                       next_step=replace(p.next_step, text=Inference(text("Step"), basis)))
    _changed(root, "p-1k2f", resumed)
SPLIT_SAID_AT = datetime(2026, 11, 15, 20, 41, tzinfo=world.TZ)


def after_scene_5_split(root: Path) -> None:
    """Scene 6's end with the Filing rules Sunday's split wrote, worded as #13 words them, each
    citing the owner's Note."""
    after_scene_6(root)
    record = Record.open(root)
    snap = _lent(record)
    said = Note(EvidenceId("nt-5p1i7s0t"), SPLIT_SAID_AT, NoteOrigin.OWNER, "telegram",
                "split the staging deploy chores out of Billing export service")
    cite = Cite(said.id, "your Note", SPLIT_SAID_AT)
    staging, billing = snap.pursuit(STAGING), snap.pursuit(BILLING)
    staging = replace(staging, filing_rules=(
        Fact(f'Staging deploy chores belong here, not in "{billing.name}".', cite),))
    billing = replace(billing, filing_rules=(
        Fact(f'Staging deploy chores belong in "{staging.name}", not here.', cite),))
    record.commit(snap.changes().append_note(said).put_pursuit(staging).put_pursuit(billing))


def collecting(seed):
    """`seed`, with the Harnesses setup found on this machine enabled, so a tick collects the
    world's Sessions from their stores."""
    def seeded(root: Path) -> None:
        seed(root)
        record = Record.open(root)
        snap = record.snapshot()
        record.commit(snap.changes().put_settings(replace(snap.settings(), harnesses={
            **AFTER_SETUP.harnesses, **snap.settings().harnesses}, interfaces=AFTER_SETUP.interfaces)))
    return seeded
