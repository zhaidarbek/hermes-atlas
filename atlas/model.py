"""Domain types. Pure data, no I/O. Every other module speaks these types.

Invariants live in constructors (``__post_init__``) so a value that exists is valid:
- A Fact is read from one Evidence (Cite), or counted from Evidence by a fixed rule (Count),
  such as Active Time or quiet days (D Q9, GLOSSARY Fact). An Inference is a judgment and names
  at least one Evidence or a Pattern.
- Focus implies Status ACTIVE.
- A Step is open, or ended exactly once, as done or skipped.
- An ending (DONE, DROPPED) carries owner authority (checked in rules.authorize, the one place).
"""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from fractions import Fraction
from typing import Literal, Mapping, NewType, Union

# ---------------------------------------------------------------- identities

EvidenceId = NewType("EvidenceId", str)
"""``<prefix>-<16 base32 chars of sha256(harness + ":" + namespace + ":" + native key)>``: 80
bits, so two native keys never share an evidence folder or a skip sweep. The canonical identity
is (Harness, namespace, native id), never a path or an Interface (graft from candidate 3): a
second Codex home of the same account (Orca) shares the namespace,
so its copy of a thread is the same Evidence; a Devin slug adds its creation time to the key.
Prefixes: cc Claude Code, cx Codex, dv Devin, hm Hermes, gpt ChatGPT, cai claude.ai, nt Note.
Stable across backfill re-runs and re-exports, so ingestion is an idempotent upsert."""

PursuitId = NewType("PursuitId", str)   # "p-4k7q": random at creation, never reused
StepId = NewType("StepId", str)         # "s-20261116-2"
HistoryId = NewType("HistoryId", str)   # "h-0007" (per Pursuit, monotonic)
IsoWeek = NewType("IsoWeek", str)       # "2026-W47": the week a Review is for
Area = NewType("Area", str)


class HarnessId(str, Enum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    DEVIN = "devin"
    HERMES = "hermes"
    CHATGPT = "chatgpt"
    CLAUDE_AI = "claude_ai"
    # Adding a Harness = add a member here + one adapter in sources/_adapters.py.
    # sources.ADAPTERS is keyed by this enum; a test fails if a member has no adapter.

    @property
    def label(self) -> str:
        return _HARNESS_LABELS[self]

    @property
    def prefix(self) -> str:
        return _EVIDENCE_PREFIXES[self]


_HARNESS_LABELS = {HarnessId.CLAUDE_CODE: "Claude Code", HarnessId.CODEX: "Codex", HarnessId.DEVIN: "Devin",
                   HarnessId.HERMES: "Hermes", HarnessId.CHATGPT: "ChatGPT", HarnessId.CLAUDE_AI: "claude.ai"}
_EVIDENCE_PREFIXES = {HarnessId.CLAUDE_CODE: "cc", HarnessId.CODEX: "cx", HarnessId.DEVIN: "dv",
                      HarnessId.HERMES: "hm", HarnessId.CHATGPT: "gpt", HarnessId.CLAUDE_AI: "cai"}


def harness_of(id: EvidenceId) -> HarnessId | None:
    """The Harness a Session's id names; None for a Note."""
    prefix = id.split("-", 1)[0]
    return next((h for h, p in _EVIDENCE_PREFIXES.items() if p == prefix), None)


LOCAL = "local"
"""The account namespace of every store on this machine. v1 reads one machine (ADR 0003) and never
opens a credential file, so the account behind a store is not read; a second copy of a store
(Orca's Codex home) shares it."""


def evidence_id(harness: HarnessId, namespace: str, native_key: str) -> EvidenceId:
    digest = hashlib.sha256(f"{harness.value}:{namespace}:{native_key}".encode()).digest()
    return EvidenceId(f"{harness.prefix}-{base64.b32encode(digest).decode().lower()[:16]}")


def skip_key(harness: HarnessId, native_key: str) -> str:
    """What forgotten.md keeps so a forgotten Session is never collected again."""
    return "sha256:" + hashlib.sha256(f"{harness.value}:{native_key}".encode()).hexdigest()


def part_file(key: str) -> str:
    """The raw/ file of a Session part: "main" -> main.jsonl.zst, "subagent:agent-1" ->
    subagent-agent-1.jsonl.zst. Turn.part holds the key."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", key) + ".jsonl.zst"


def part_origin(harness: HarnessId, native_key: str, key: str) -> str:
    """RawPart.origin: the native Session a part was copied from, as the first 16 hex digits of
    sha256("<harness>:<native key>"), then ":" and the part's key in that Session. It stays the
    same whichever root or file the copy came from and wherever a fold puts it."""
    return hashlib.sha256(f"{harness.value}:{native_key}".encode()).hexdigest()[:16] + ":" + key


# ---------------------------------------------------------------- statements

@dataclass(frozen=True)
class Cite:
    evidence: EvidenceId
    label: str              # "Claude Code via T3 Code", "your Note", "Note from Claude Code"
    at: datetime            # the moment in the Evidence the statement was read from


@dataclass(frozen=True)
class Pattern:
    """Basis of an aggregate Inference (Profile patterns). It still lists every Evidence id the
    model saw (`drawn_from`), because "not cited" does not mean "not derived" (graft from
    candidate 3). Forget lists such an entry in its preview and regenerates it without the
    forgotten Evidence."""
    description: str        # "131 days of Sessions"
    first: date
    last: date
    drawn_from: tuple[EvidenceId, ...]   # model input, possibly large; never empty


@dataclass(frozen=True)
class Count:
    """A figure counted from Evidence by a fixed rule (D Q9): Active Time per day, Area or
    Pursuit, quiet days, a number of Steps or Sessions. Per-Pursuit figures rest on filing, which
    is shown and correctable on the Board, and stay Facts. Record notation:
    `[F counted · <rule> · <n> Sessions]`, with the ids in the file's Evidence list."""
    rule: Literal["active_time", "quiet_days", "steps", "sessions"]
    basis: tuple[EvidenceId, ...]           # may be empty only for "0 Sessions"-style counts


@dataclass(frozen=True)
class Fact:
    """Read directly from Evidence (Cite), or counted from it by a fixed rule (Count). A read
    Fact is constructed only by librarian._ground (quote verified against the scrubbed
    transcript) or from an owner/agent Note; a counted Fact only by activetime/rules."""
    text: str
    source: Cite | Count


@dataclass(frozen=True)
class Inference:
    text: str
    basis: tuple[EvidenceId, ...] | Pattern

    def __post_init__(self) -> None:
        if isinstance(self.basis, tuple) and not self.basis:
            raise ValueError("an Inference names the Evidence it rests on")


Statement = Union[Fact, Inference]


def cites(s: Statement) -> frozenset[EvidenceId]:
    """Evidence a statement depends on. The Forget closure is computed from this alone."""
    if isinstance(s, Fact):
        src = s.source
        return frozenset({src.evidence}) if isinstance(src, Cite) else frozenset(src.basis)
    return frozenset(s.basis) if isinstance(s.basis, tuple) else frozenset(s.basis.drawn_from)


# ---------------------------------------------------------------- pursuits

class Kind(str, Enum):
    IDEA = "idea"; EXPERIMENT = "experiment"; PROJECT = "project"; PROBLEM = "problem"


class Status(str, Enum):
    ACTIVE = "active"; PARKED = "parked"; DROPPED = "dropped"; DONE = "done"

    @property
    def is_ending(self) -> bool:
        return self in (Status.DONE, Status.DROPPED)


@dataclass(frozen=True)
class ResumeNote:
    where: tuple[Statement, ...]
    left_off: tuple[Statement, ...]
    decided: tuple[Statement, ...]
    open_questions: tuple[Statement, ...]
    reopen: tuple[EvidenceId, ...]          # 1..3 Sessions to reopen


@dataclass(frozen=True)
class Done:
    how: Literal["word", "seen"]            # owner said so | seen in a Session
    at: datetime
    basis: Fact                             # the owner's Note, or the Session that shows it


@dataclass(frozen=True)
class Skipped:
    how: Literal["word", "silence", "replaced"]
    at: datetime
    basis: Statement | None                 # owner's Note for "word"; None for silence


@dataclass(frozen=True)
class Step:
    id: StepId
    text: Inference                         # every Step is an Inference
    created: date
    held_until: date | None = None          # "remind me friday"; not an offer, not a skip
    ended: Done | Skipped | None = None     # None = open
    # Offers are NOT stored here: they are read from briefs/*.md (the sent ledger), the single source.


@dataclass(frozen=True)
class EndingProposal:
    to: Status                              # DONE or DROPPED
    basis: Inference
    since: date
    in_followup: bool                       # raised in a Daily Brief follow-up (never again in briefs)


@dataclass(frozen=True)
class FocusProposal:
    add: bool                               # add, or remove after three skipped Steps in a row
    reason: Inference
    since: date


@dataclass(frozen=True)
class ReopenProposal:
    basis: Fact                             # the Session that advanced a done/dropped Pursuit
    since: date


Proposal = Union[EndingProposal, FocusProposal, ReopenProposal]


class Actor(str, Enum):
    OWNER = "You"; ATLAS = "Atlas"; AGENT = "Agent"


@dataclass(frozen=True)
class HistoryLine:
    id: HistoryId
    at: datetime
    actor: Actor
    statement: Statement                    # an agent's line quotes the owner's words it acted on (ADR 0009)
    agent: str | None = None                # Harness label when actor is AGENT

    def __post_init__(self) -> None:
        if (self.actor is Actor.AGENT) != (self.agent is not None):
            raise ValueError("an agent's History line names its Harness, and only an agent's does")


@dataclass(frozen=True)
class Membership:
    evidence: EvidenceId
    at: datetime                            # the Session's last turn, for ordering and "last worked"
    advances: bool                          # False for Sessions that only read Atlas about it (W Q18)


@dataclass(frozen=True)
class Pursuit:
    id: PursuitId
    name: str
    kind: Kind
    area: Area
    status: Status
    focus: bool
    created: date
    continued: date | None                  # owner's last "continue"; restarts the quiet clock
    resume: ResumeNote
    next_step: Step | None                  # None when no small unblocked action is known
    past_steps: tuple[Step, ...]
    filing_rules: tuple[Fact, ...]          # owner corrections that steer future filing, each citing a Note
    sessions: tuple[Membership, ...]
    proposals: tuple[Proposal, ...]
    history: tuple[HistoryLine, ...]
    decision_held_until: date | None = None # "remind me <day>" on a Neglected question
    raised_at: date | None = None           # last time a parked Pursuit was raised in a Review

    def __post_init__(self) -> None:
        if self.focus and self.status is not Status.ACTIVE:
            raise ValueError("Focus holds only active Pursuits")

    @property
    def last_worked(self) -> date | None:
        """Derived: latest advancing Session. Never stored separately."""
        adv = [m.at for m in self.sessions if m.advances]
        return max(adv).date() if adv else None


# ---------------------------------------------------------------- evidence

@dataclass(frozen=True)
class RawPart:
    """One verbatim source file/row-set copied into evidence/<id>/raw/, zstd-compressed."""
    name: str                               # "main.jsonl.zst", "subagent-a1b2.jsonl.zst", "t3-child-<id>.jsonl.zst"
    sha256: str                             # of the decompressed bytes; integrity check for the sweep
    source: str                             # where it came from (path or "state.db sessions/<id>"), for display
    fingerprint: str                        # changes when the source does: what a tick compares to read it again
    origin: str = ""                        # part_origin(): what the record matches a store's part with


@dataclass(frozen=True)
class Turn:
    role: Literal["owner", "agent"]
    at: datetime | None                     # None only when an undated web-chat message cannot be bounded
    text: str
    part: str                               # "main", "subagent:<name>", "t3-child:<native id>"
    atlas_tool_turn: bool = False           # this exchange called atlas_* tools: an Atlas conversation (W Q14)
    at_interpolated: bool = False           # D Q6: time set by interpolation (see sources.webchat)

    @property
    def own(self) -> bool:
        """A turn of the Session's own conversation, not of a part folded into it."""
        return self.part == "main" or self.part.startswith("continued:")


@dataclass(frozen=True)
class SessionEvidence:
    id: EvidenceId
    harness: HarnessId
    interface: str | None                   # "T3 Code", "Orca" ...
    namespace: str                          # account/home identity: same thread in two Codex homes = one Session
    native_key: str                         # thread id / file uuid / conversation id
    title: str
    started: datetime
    last_at: datetime
    machine: str
    cwd: str | None
    git_branch: str | None
    parts: tuple[RawPart, ...]
    turns: tuple[Turn, ...]                 # loaded lazily by Snapshot.session(); folded parts contribute agent turns only


class NoteOrigin(str, Enum):
    OWNER = "owner"                         # the owner wrote to Atlas in a Channel, the Board, or /atlas
    AGENT = "agent"                         # atlas_note from an agent in a Harness
    HAND_EDIT = "hand_edit"                 # a record file edited outside Atlas (W Q15)
    REFUSED_UPDATE = "refused_update"       # atlas_update Atlas could not confirm (D Q12): quotes the
                                            # owner's words; "apply" makes the change through Atlas


@dataclass(frozen=True)
class Note:
    id: EvidenceId                          # nt-...
    at: datetime
    origin: NoteOrigin
    via: str                                # "telegram", "board", "claude_code", "profile.md"
    text: str                               # what was said / the edited file text
    about: PursuitId | None = None
    atlas_reply: str | None = None          # Atlas's answer, kept so the Notes file reads as the conversation
    diff: str | None = None                 # HAND_EDIT only
    pending: "Command | None" = None        # REFUSED_UPDATE: the change "apply" would make
    offered: bool = False                   # HAND_EDIT / REFUSED_UPDATE: "apply" already offered in an answer
    derived_from: tuple[EvidenceId, ...] = ()  # HAND_EDIT: Evidence the edited file or Atlas's last-written copy
                                               # cites; AGENT / REFUSED_UPDATE: the calling Session. Forget of
                                               # one of them removes the Note too
    platform_ref: str | None = None         # inbound platform message id: a redelivered message is one Note
    cwd: str | None = None                  # AGENT / REFUSED_UPDATE: the calling Harness's folder, in the Notes file
    applied: bool = False                   # HAND_EDIT / REFUSED_UPDATE: "apply" made the change
    offer: EvidenceId | None = None         # OWNER: the Note Atlas's reply offered to "apply"; "apply" makes
                                            # the one the latest offer named


@dataclass(frozen=True)
class SessionCard:
    """evidence/<id>/card.md: what one Session was for, how it ended and where it ran. The
    model's lines are Inferences; `where` is read from the Session's metadata."""
    evidence: EvidenceId
    intent: Inference
    outcome: Inference
    unfinished: Inference | None
    where: Fact | None
    one_off_area: Area | None = None        # a one-off Session: Evidence with an Area and no Pursuit
    one_off_day: date | None = None         # the day backfill filed it as a one-off


# ---------------------------------------------------------------- journal, profile, pauses

ONE_OFF = "one-off"


@dataclass(frozen=True)
class DayTime:
    """One day's Active Time in whole minutes, counted from `basis` by a fixed rule (D Q9).
    `areas` holds each Area's time by Pursuit, a one-off Session's under ONE_OFF, in the order
    the Journal shows them. Pursuits add up to their Area and Areas to `total`."""
    total: timedelta
    areas: dict[Area, dict[PursuitId, timedelta]]
    basis: tuple[EvidenceId, ...]           # the Sessions that add time to the day

    def __post_init__(self) -> None:
        if sum((t for split in self.areas.values() for t in split.values()), timedelta()) != self.total:
            raise ValueError("a day's Pursuits add up to its total")

    @property
    def by_area(self) -> dict[Area, timedelta]:
        return {a: sum(split.values(), timedelta()) for a, split in self.areas.items()}

    @property
    def by_pursuit(self) -> dict[str, timedelta]:
        """PursuitId or ONE_OFF, one-offs of every Area together."""
        out: dict[str, timedelta] = {}
        for split in self.areas.values():
            for key, t in split.items():
                out[key] = out.get(key, timedelta()) + t
        return out


@dataclass(frozen=True)
class JournalDay:
    day: date
    time: DayTime
    worked_on: tuple[Statement, ...]
    decided: tuple[Statement, ...]
    finished: tuple[Statement, ...]
    corrections: tuple[str, ...]            # derived from evidence diffs, e.g. "added ChatGPT 19:10 (40 min)"

    @property
    def evidence(self) -> tuple[EvidenceId, ...]:
        """The day's Sessions: those its Active Time is counted from."""
        return self.time.basis

    @property
    def lines(self) -> tuple[Statement, ...]:
        return self.worked_on + self.decided + self.finished


@dataclass(frozen=True)
class Profile:
    areas: tuple[Area, ...]
    entries: tuple[ProfileEntry, ...]


@dataclass(frozen=True)
class ProfileEntry:
    statement: Statement
    added: date                             # the Review lists LIBRARIAN entries added that week
    by: Who                                 # and still as written (G3); an owner correction sets OWNER


@dataclass(frozen=True)
class Pause:
    start: datetime
    end: datetime                           # always set: default start + 7 days (W Q17)
    said: str


@dataclass(frozen=True)
class Forgotten:
    entries: tuple[tuple[datetime, str], ...]   # (when, "a Session") only, never content
    skip_keys: frozenset[str]                   # sha256("<harness>:<native key>") so collectors never re-ingest


# ---------------------------------------------------------------- sent messages (the sent ledger)

class SendStatus(str, Enum):
    SENDING = "sending"   # committed before each attempt
    SENT = "sent"
    ARRIVAL_UNKNOWN = "arrival_unknown"
    """Found SENDING after a crash, or `hermes send` failed after the messenger may have
    accepted it. At least once, at most twice (D Q13): schedule.due offers ONE more attempt on a
    later tick the same local day; after a second unknown the message stays ARRIVAL_UNKNOWN and
    is never sent a third time. An ARRIVAL_UNKNOWN brief whose retry also failed counts as no
    offer; a brief with any SENT attempt counts once."""


@dataclass(frozen=True)
class BriefStep:
    n: int
    pursuit: PursuitId
    step: StepId


@dataclass(frozen=True)
class Followup:
    pursuit: PursuitId
    step: StepId
    state: Literal["done_word", "done_seen", "no_sign", "not_visible_yet", "held", "skipped_word", "skipped_silence"]
    statement: Statement | None             # the line rendered into the brief's text; the sent ledger keeps
                                            # only the text, so a Followup read back from briefs/ has None
    ending_proposed: bool = False


@dataclass(frozen=True)
class DecisionAsk:
    pursuit: PursuitId
    ask: Literal[1, 2]
    quiet_days: int
    heard: bool = False                     # the owner wrote about it in words Atlas could not read: not silence


@dataclass(frozen=True)
class SendAttempt:
    at: datetime
    status: SendStatus
    message_ids: tuple[str, ...]            # a reply to the message of EITHER attempt counts
    text_sha256: str | None = None          # sent_sha256 of the text this attempt sent; None when not known
    chat: str | None = None                 # "telegram:<chat>[:<thread>]": a message id is unique only in its chat


def sent_sha256(text: str) -> str:
    """How a sent text is hashed: stripped, as a Hermes mirror row of it is compared."""
    return hashlib.sha256(text.strip().encode()).hexdigest()


@dataclass(frozen=True)
class Sent:
    """Every text Atlas sent, stripped as a Hermes mirror row of it is compared. `maybe_bare` holds
    the ones an attempt may have sent with a secret the record now masks: a text sent before Atlas
    masked it, or by an attempt that did not keep the hash of what it sent. A row matches those
    with its secrets masked too, and every other text only as it reads."""
    texts: frozenset[str] = frozenset()
    maybe_bare: frozenset[str] = frozenset()


RESEND: dict[tuple[SendStatus, int], bool] = {
    (SendStatus.SENT, 1): False,
    (SendStatus.SENT, 2): False,
    (SendStatus.ARRIVAL_UNKNOWN, 1): True,
    (SendStatus.ARRIVAL_UNKNOWN, 2): False,
    (SendStatus.SENDING, 1): True,
    (SendStatus.SENDING, 2): False,
}
"""Whether a later tick the same day sends a message again, by its last attempt's status and the
number of attempts (D Q13). A tick reads the ledger only while it holds the send lease
(schedule.SendLease), so a SENDING attempt it finds was left by a crash: its arrival is unknown."""


def resend(attempts: tuple[SendAttempt, ...]) -> bool:
    return RESEND[(attempts[-1].status, len(attempts))]


@dataclass(frozen=True)
class SentBrief:
    day: date
    attempts: tuple[SendAttempt, ...]       # 1 or 2 (D Q13); status is the last attempt's
    follows_up: date | None                 # the brief this one follows up (Friday, after a paused Saturday)
    followups: tuple[Followup, ...]
    steps: tuple[BriefStep, ...]
    decision: DecisionAsk | None
    no_focus_yet: bool                      # D Q10: Steps drawn from recent work, owner never picked Focus
    text: str

    @property
    def status(self) -> SendStatus:
        return self.attempts[-1].status


@dataclass(frozen=True)
class SentReview:
    week: IsoWeek
    covers: tuple[date, date]               # since the day after the last Review sent, to the Review's day
    attempts: tuple[SendAttempt, ...]       # 1 or 2 (D Q13); status is the last attempt's
    raised_parked: PursuitId | None         # its 90 days restart from this Review (W Q6)
    asked: tuple[PursuitId, ...]            # Pursuits whose proposal or park this Review asked about
    time_proposed: time | None              # G4: a brief time proposed here; no new one for 4 weeks
    text: str
    drawn_from: tuple[tuple[EvidenceId, tuple[int, ...]], ...] = ()
    """For each Session the text draws on, the numbers (from 1) of the text's lines that do: Forget
    removes those lines."""

    @property
    def status(self) -> SendStatus:
        return self.attempts[-1].status


# ---------------------------------------------------------------- settings (record file settings.md)

@dataclass(frozen=True)
class HarnessSetting:
    enabled: bool
    mode: Literal["store", "export"]        # export = arrives late via inbox (web chats)
    roots: tuple[str, ...]                  # probed defaults + owner-added (Orca's second Codex home, macOS Devin)
    found_by: Literal["probe", "owner"]


@dataclass(frozen=True)
class InterfaceSetting:
    found_by: Literal["probe", "owner"]
    root: str


@dataclass(frozen=True)
class ChannelSetting:
    """ONE setting serves both directions (D Q16): where the Brief and Review go, and which chat
    channel.claim treats as Atlas's own. None = the platform's Hermes home channel, shared with
    Hermes's agent (claim rule: reply to a brief, "atlas" prefix, or 10-minute exchange window,
    D Q1). A configured chat or topic is Atlas's own chat: every owner message there is Atlas's.
    Setup offers it and never requires it."""
    platform: str                           # chosen at setup: "telegram" here
    atlas_chat: str | None = None           # "telegram:<chat>[:<topic>]" or None for the home channel
    address_word: str = "atlas"
    exchange_window: timedelta = timedelta(minutes=10)

    @property
    def target(self) -> str:                # hermes send --to
        return self.atlas_chat or self.platform

    @property
    def dedicated(self) -> bool:            # derived, never stored separately
        return self.atlas_chat is not None


@dataclass(frozen=True)
class Settings:
    timezone: str
    brief_at: time                          # default 07:30
    review_day: int                         # ISO weekday, default 7 (Sunday)
    review_at: time                         # default 18:00
    gap: timedelta                          # Active Time gap, default 15 min
    machine: str
    harnesses: dict[HarnessId, HarnessSetting]
    exclusions: tuple[str, ...]             # cwd prefixes never collected
    channel: ChannelSetting
    interfaces: dict[str, InterfaceSetting] = field(default_factory=dict)   # "t3_code" -> where it was found

    @property
    def late_harnesses(self) -> frozenset[HarnessId]:
        return frozenset(h for h, s in self.harnesses.items() if s.mode == "export")


# ---------------------------------------------------------------- authority

@dataclass(frozen=True)
class Owner:
    via: Literal["telegram", "board", "cli", "slash"]
    note: EvidenceId                        # the Note holding the owner's words; Board clicks write a one-line Note too


@dataclass(frozen=True)
class BoundSession:
    """The live Session an agent tool call comes from, resolved by sources.bind_caller from the
    calling process, never from what the agent says (fixes the cwd/time guess all three
    candidates had). Claude Code: the MCP server's parent pid -> ~/.claude/sessions/<pid>.json
    -> sessionId (structure verified on Linux). Hermes tools: the session_id kwarg. Codex: the
    _meta.threadId Codex adds to each call (ADR 0010). Devin: none in v1; without a binding,
    atlas_update is refused and kept as an applyable Note (D Q12)."""
    harness: HarnessId
    namespace: str
    native_key: str


@dataclass(frozen=True)
class Instruction:
    """The owner's words found in an owner-typed message of the bound Session, read from the
    Harness store at call time (the Harness writes the owner's prompt before the tool runs).
    Only sources.find_instruction constructs one; rules.authorize requires it for any Pursuit
    change by an agent. So an agent change is verified BEFORE it applies, not reverted later."""
    session: BoundSession
    text: str
    at: datetime
    names_ending: Status | None = None      # D Q11: the owner's words name DONE or DROPPED
    _token: object = field(repr=False, compare=False, default=None)


@dataclass(frozen=True)
class Agent:
    harness: str                            # HarnessId value of the calling Harness
    session: BoundSession | None
    instruction: Instruction | None         # required for any Pursuit change; None for Notes
    cwd: str | None = None                  # the folder the calling Harness ran the tool server in


@dataclass(frozen=True)
class Librarian:
    """Atlas acting on its own. Not constructible from any shell: only app/rules create it.
    `basis` is the record HEAD the model worked from: rules.decide drops a Librarian change to
    any field the owner set after it, so stale model output never overrides a correction
    (graft from candidate 2)."""
    evidence: tuple[EvidenceId, ...]
    basis: int


Authority = Union[Owner, Agent, Librarian]


class Who(str, Enum):
    """Kinds of authority a Command may carry. Each Command class declares its own set (below),
    so "only the owner ends a Pursuit" is data on the type, and README's format section is
    generated from the same declarations (graft from candidate 2)."""
    OWNER = "owner"; AGENT_TOLD = "agent_told"; AGENT = "agent"; LIBRARIAN = "librarian"


# ---------------------------------------------------------------- commands (the one write vocabulary)

COMMANDS: list[type] = []


def command(*allowed: Who):
    """Registers a Command class and declares who may issue it. rules.authorize reads
    `cls.allowed`; the README format section and the agent tool schema are generated from
    COMMANDS, so there is no hand-kept list. SetStatus to DONE/DROPPED narrows further in
    rules.authorize: never LIBRARIAN (Atlas proposes endings)."""
    def wrap(cls):
        cls.allowed = frozenset(allowed)
        COMMANDS.append(cls)
        return cls
    return wrap

@command(Who.OWNER, Who.AGENT_TOLD, Who.LIBRARIAN)
@dataclass(frozen=True)
class MarkStep:      pursuit: str; step: StepId | int | None; outcome: Literal["done", "skipped"]; detail: str | None = None
@command(Who.OWNER, Who.AGENT_TOLD)
@dataclass(frozen=True)
class HoldStep:      pursuit: str; until: date
@command(Who.OWNER)
@dataclass(frozen=True)
class HoldDecision:  pursuit: str; until: date
@command(Who.OWNER, Who.AGENT_TOLD, Who.LIBRARIAN)
@dataclass(frozen=True)
class SetStatus:     pursuit: str; status: Status
@command(Who.OWNER, Who.AGENT_TOLD)
@dataclass(frozen=True)
class Continue:      pursuit: str
@command(Who.OWNER)
@dataclass(frozen=True)
class SetFocus:      pursuit: str; on: bool
@command(Who.OWNER)
@dataclass(frozen=True)
class AnswerProposal: pursuit: str; accept: bool; keep_parked: bool = False   # "add backups" / "leave it" / "keep parked"
@command(Who.OWNER, Who.AGENT_TOLD, Who.LIBRARIAN)
@dataclass(frozen=True)
class Merge:         a: str; b: str; into_name: str; rule: str
@command(Who.OWNER, Who.AGENT_TOLD, Who.LIBRARIAN)
@dataclass(frozen=True)
class Split:         pursuit: str; new_name: str; criterion: str
@command(Who.OWNER, Who.AGENT_TOLD, Who.LIBRARIAN)
@dataclass(frozen=True)
class SetKind:       pursuit: str; kind: Kind
@command(Who.OWNER, Who.AGENT_TOLD, Who.LIBRARIAN)
@dataclass(frozen=True)
class SetArea:       pursuit: str; area: Area
@command(Who.OWNER)
@dataclass(frozen=True)
class AddArea:       area: Area
@command(Who.OWNER, Who.AGENT_TOLD, Who.LIBRARIAN)
@dataclass(frozen=True)
class Rename:        pursuit: str; name: str
@command(Who.OWNER, Who.AGENT_TOLD, Who.LIBRARIAN)
@dataclass(frozen=True)
class EditResumeNote: pursuit: str; remove: tuple[str, ...]; add_decided: tuple[str, ...] = (); add_left_off: tuple[str, ...] = (); add_open: tuple[str, ...] = (); next_step: str | None = None
@command(Who.OWNER, Who.AGENT_TOLD, Who.AGENT)
@dataclass(frozen=True)
class AddNote:       text: str; about: str | None = None
@command(Who.OWNER)
@dataclass(frozen=True)
class CorrectProfile: entry: str; replacement: str | None
@command(Who.OWNER)
@dataclass(frozen=True)
class CorrectJournal: day: date; correction: str
@command(Who.OWNER)
@dataclass(frozen=True)
class PauseCmd:      until: datetime | None; said: str
@command(Who.OWNER)
@dataclass(frozen=True)
class EndPause:      pass
@command(Who.OWNER)
@dataclass(frozen=True)
class RequestForget: selector: str                       # "<harness> <minute>", resolved by forget against the Snapshot
@command(Who.OWNER)
@dataclass(frozen=True)
class ConfirmForget: pass                                # only produced by the exact word "forget"
@command(Who.OWNER)
@dataclass(frozen=True)
class ApplyNote:     note: EvidenceId | None             # "apply": a HAND_EDIT or REFUSED_UPDATE Note; None = the latest offered
@command(Who.OWNER)
@dataclass(frozen=True)
class ChangeSettings: patch: dict                        # validated into Settings by rules
@command(Who.OWNER)
@dataclass(frozen=True)
class MarkSeen:      marks: Mapping[PursuitId, HistoryId]
@command()
@dataclass(frozen=True)
class OutsideAction: kind: str; to: str | None; about: str | None = None   # always refused in v1; `about`:
                                                         # the owner's words for the Pursuit it concerns


Command = Union[tuple(COMMANDS)]  # derived from the registry, never listed by hand


# ---------------------------------------------------------------- queries and answers

@dataclass(frozen=True)
class ResumeQ:        pursuit: str
@dataclass(frozen=True)
class ResumePromptQ:  pursuit: str | None = None; brief: date | None = None; item: int | None = None
# "<n> prompt" on a brief -> brief+item; bare "prompt" right after a ResumeQ answer -> that pursuit (D Q2)
@dataclass(frozen=True)
class DayQ:           day: date
@dataclass(frozen=True)
class NeglectedQ:     pass
@dataclass(frozen=True)
class WhichQ:         names: tuple[str, ...]; word: str = "continue"
@dataclass(frozen=True)
class FindQ:          text: str
@dataclass(frozen=True)
class PeriodQ:        start: date; end: date                       # "what did I decide, change, finish"
@dataclass(frozen=True)
class PeriodTimeQ:    start: date; end: date; by: Literal["area", "pursuit"]
@dataclass(frozen=True)
class StatusQ:        pursuit: str
@dataclass(frozen=True)
class BoardQ:         pass


Query = Union[ResumeQ, ResumePromptQ, DayQ, NeglectedQ, WhichQ, FindQ, PeriodQ, PeriodTimeQ, StatusQ, BoardQ]


@dataclass(frozen=True)
class Coverage:
    """Caveats every answer carries (ADR 0003: say so rather than present partial as complete)."""
    machine_only: bool = True
    late: tuple[str, ...] = ()              # "claude.ai: Sessions after 15 Nov arrive with the next export"


@dataclass(frozen=True)
class Answer:
    title: str | None
    statements: tuple[Statement, ...]
    coverage: Coverage
    data: object | None = None              # BoardView, DayTime table, prompt text ... typed per Query


@dataclass(frozen=True)
class Card:
    pursuit: PursuitId
    name: str
    kind_line: str                          # "project (an idea until Wed 18 Nov)"
    last_worked: date | None
    left_off: Statement | None
    next_step: Statement | None
    focus: bool
    new: bool                               # created by Atlas, not seen
    changed: bool                           # Atlas history line after the seen mark
    proposals: tuple[Proposal, ...]
    resume_prompt: str                      # Copy button on the Board
    upto: HistoryId | None = None           # newest History id: what a seen post carries


@dataclass(frozen=True)
class BoardView:
    columns: dict[Status, tuple[Card, ...]]

    @property
    def in_focus(self) -> int:
        return sum(c.focus for cards in self.columns.values() for c in cards)


# ---------------------------------------------------------------- conversation edges

@dataclass(frozen=True)
class SentRef:
    kind: Literal["brief", "review", "reply"]
    key: str                                # brief date, ISO week, or note id
    message_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OwnerMessage:
    text: str
    at: datetime
    via: Literal["telegram", "slash", "cli"]
    chat: str | None                        # "telegram:<chat>[:<thread>]", as ChannelSetting.atlas_chat names one
    reply_to: SentRef | None
    platform_ref: str | None = None         # "<chat>:<message id>": a redelivered message is one Note
    addressed: bool = False                 # written to Atlas by name: "atlas, ..." or /atlas


@dataclass(frozen=True)
class Reply:
    """Over about 25 s (D Q8) the claim hook replies "Looking, I'll send it here." and the answer
    follows as its own `hermes send` message (prompted, so W Q5 holds); a failure is sent as
    "I couldn't answer that: <reason>." rather than silence."""
    text: str
    follow_up_later: bool = False


@dataclass(frozen=True)
class Outcome:
    ok: bool
    lines: tuple[str, ...]                  # rendered per Channel by messages.render_outcome
    refused: str | None = None              # "OUTSIDE_WORLD", "OWNER_ONLY", "NOT_TOLD", "AMBIGUOUS:<names>"
