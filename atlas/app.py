"""Atlas: the one public entry to the core. Every shell (CLI, cron tick, gateway hooks, slash
command, Hermes tools, MCP server, dashboard API) calls these six methods and nothing else,
except the gateway hooks, which also ask `channel()`, `sent()`, `sending()` and `answered()` before
a message is claimed.

What it hides: snapshot/plan/commit/retry, LLM pre-work outside the lock, the Note for
every owner utterance, hand-edit notices in the next answer, sending after a SENDING commit,
the collect -> file -> journal pipeline, and per-Channel rendering.

Call chain for any change: shell -> Atlas method -> rules.<fn> (pure) -> record.commit.
"""
from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, available_timezones

from . import (__version__, activetime, agents, ask, channel, converse, corrections, filing, forget, neglect, rules, schedule,
               settings_rules, sources)
from .librarian import JournalLines, Librarian, Llm, ModelError, Unusable
from .model import (Agent, Answer, ApplyNote, Authority, BoardQ, ChangeSettings, ChannelSetting, Command, ConfirmForget,
                    Coverage, DayTime, EvidenceId, FindQ, HarnessId, HarnessSetting, InterfaceSetting, NeglectedQ, NoteOrigin,
                    Owner,
                    OwnerMessage, Outcome, PeriodTimeQ, Profile, Query, Reply, RequestForget, ResumePromptQ, ResumeQ,
                    SendStatus, Sent, SentRef, SessionCard, Settings, Split, Status, WhichQ, sent_sha256)
from .record import Blocked, ChangeSet, Conflict, ImportLog, Record, Snapshot
from .sources import webchat
from .rules import Decision

log = logging.getLogger("atlas")
BATCH = 50
IMPORT_BUDGET = 500
"""Web-chat Sessions one tick puts in the record at most; a larger export goes on next tick."""
IMPORT_FILES = 20
"""Export files one tick reads at most; the others wait for the next tick."""
NOT_ADDED = settings_rules.NOT_ADDED

BOARD_URL = "http://127.0.0.1:9119/atlas"   # the Hermes v0.23.0 dashboard's default address, and Atlas's tab
NO_MODEL = ("No model answered, so no Pursuits yet. Set auxiliary.atlas_backfill in Hermes's config and run "
            "backfill again.")
MODEL_LINE = "the one your Hermes uses, for every Area; secrets masked before it reads"   # D Q15
FIRST_AREAS = ("work", "health", "family")
MCP_ADD = {HarnessId.CLAUDE_CODE: "claude mcp add atlas -- hermes atlas mcp",
           HarnessId.CODEX: "codex mcp add atlas -- hermes atlas mcp"}
HAND_EDITS = ("Puts back a file in the record changed outside Atlas and keeps the change as a Note. A file-sync "
              "tool that rewrites the record looks like such an edit.")
FAILED = "I couldn't answer that: my record could not be written."   # D Q8's failure line
OWN_CHAT_OFFER = ("If you already use that chat or its main topic for something else, point Atlas at a topic "
                  "of its own in settings. Every message you type there is then Atlas's.")   # D Q16


@dataclass(frozen=True)
class SetupReport:
    record: Path
    channel: str                              # "Telegram, through the Hermes gateway" (+ Atlas's own chat if set)
    model: str                                # D Q15: "the one your Hermes uses, for every Area; secrets masked"
    found: tuple[sources.Found, ...]
    not_found: tuple[str, ...]                # store Harnesses probed and missing; web chats are always added by hand
    mcp_commands: tuple[str, ...]             # for the owner to run; Atlas never edits Harness config
    disclosures: tuple[str, ...]              # catalog rule 13: reads outside plugin data, background job
    version: str
    machine: str
    exclusions: tuple[str, ...]
    own_chat_offer: str | None                # D Q16: offered, never required; None once a chat is set

    def render(self) -> str:
        harnesses = [f for f in self.found if not f.is_interface]
        interfaces = [f for f in self.found if f.is_interface]
        root_width = max((len(f.root) for f in harnesses), default=0)
        count_width = max((len(str(f.sessions)) for f in harnesses), default=0) + 3
        lines = [
            f"Atlas {self.version} on {self.machine}, inside Hermes",
            f"Record:   {_tilde(self.record)}   (plain files, readable without Hermes or Atlas)",
            f"Channel:  {self.channel}",
            f"Model:    {self.model}",
            "",
            "Found on this machine",
            *(f"  {f.harness.label:<13}{f.root:<{root_width}}{f.sessions:>{count_width}} "
              f"Session{'' if f.sessions == 1 else 's'}" + (f" since {f.since.day} {f.since:%b}" if f.since else "")
              for f in harnesses),
            "Interfaces    " + ("; ".join(f"{f.harness}: its Sessions count once, under their Harness"
                                          for f in interfaces) or "none found"),
            *(["Not found     " + ", ".join(self.not_found) + "."] if self.not_found else []),
            ("              " if self.not_found else "Not found     ")
            + "Harnesses in the browser, such as ChatGPT and claude.ai.",
            "              Add them in Atlas's settings.",
            "Exclusions    " + (", ".join(self.exclusions) or "none named"),
            "Machines      this one only. Sessions on your other machines are not collected.",
        ]
        if self.own_chat_offer:
            lines += ["", self.own_chat_offer]
        if self.mcp_commands:
            lines += ["", "To let your agents read Atlas and leave it Notes, run:", *(f"  {c}" for c in self.mcp_commands)]
        lines += ["", "What Atlas does on this machine:", *(f"  {d}" for d in self.disclosures)]
        return "\n".join(lines)


@dataclass(frozen=True)
class Filed:
    """Where backfill filed the Sessions, as the record holds it now."""
    pursuits: int
    active: int
    parked: int
    one_offs: int
    active_since: date                       # the last day a Pursuit could be worked on and start parked
    board: str


@dataclass(frozen=True)
class Journaled:
    """The days the Journal has a file for."""
    days: int
    first: date
    last: date


@dataclass(frozen=True)
class BackfillReport:
    """What backfill read, filed and wrote in the Journal."""
    sessions: int
    folded: int
    via_interface: dict[str, dict[HarnessId, int]] = field(default_factory=dict)   # "T3 Code" -> Sessions by Harness
    filed: Filed | None = None               # None until backfill has built the Pursuits
    journal: Journaled | None = None         # None while the Journal has no day
    unanswered: bool = False                 # no Session has a card: no model answered

    def render(self) -> str:
        lines = [f"Read {self.sessions} Session{_s(self.sessions)}. {self.folded} subagent conversation"
                 + (" was folded into the Session that started it." if self.folded == 1
                    else "s were folded into the Sessions that started them.")]
        for interface, by_harness in self.via_interface.items():
            n = sum(by_harness.values())
            labels = [h.label for h in HarnessId if h in by_harness]
            under = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " or " + labels[-1]
            lines.append(f"{n} Session{_s(n)} came through {interface}. "
                         + ("It counts" if n == 1 else "Each counts") + f" once, under {under}.")
        if self.unanswered:
            lines.append(NO_MODEL)
        if self.filed:
            f = self.filed
            row = lambda label, n: f"{label:<15}{n:>3}"
            lines += ["", row("Pursuits", f.pursuits),
                      row("  active", f.active) + f"   touched since {f.active_since.day} {f.active_since:%b}",
                      row("  parked", f.parked) + "   quiet for more than a month",
                      row("Not a Pursuit", f.one_offs) + f" one-off Session{_s(f.one_offs)}, kept as Evidence"]
            if j := self.journal:
                lines.append(row("Journal", j.days) + f" day{_s(j.days)}, {_span(j.first, j.last)}")
            lines.append(f"{'Board':<15}{f.board}")
        return "\n".join(lines)


@dataclass(frozen=True)
class TickReport:
    collected: int; filed: int; sent: tuple[str, ...]; hand_edits: int; skipped_busy: bool


class Atlas:
    def __init__(self, record: Record, llm: Llm | None) -> None:
        self._record = record
        self._llm = llm

    @classmethod
    def open(cls, root: Path | None = None, llm: Llm | None = None,
             clock: Callable[[], datetime] | None = None) -> "Atlas":
        """root: explicit > plugins.entries.atlas.settings.record_dir > $ATLAS_RECORD > ~/atlas.
        The shell reads the plugin setting and passes it as `root`.
        llm: a ctx.llm adapter when running inside Hermes; None in the dashboard backend
        (Board actions need no model). clock: the shell's, which times hand-edit Notes. Puts back
        every record file edited by hand (record.reconcile)."""
        where = root or os.environ.get("ATLAS_RECORD") or Path.home() / "atlas"
        record = Record.open(Path(where).expanduser(), clock)
        record.reconcile()
        return cls(record, llm)

    # -------------------------------------------------------------- lifecycle

    def setup(self) -> SetupReport:
        """Idempotent. record.create, sources.probe -> settings.md, channel.check,
        schedule.install_tick, MCP command lines. A second run keeps the record's settings
        and finds the cron job already there. Raises record.NotLocal on a network mount."""
        try:
            kept = self._record.snapshot().settings()
        except FileNotFoundError:
            kept = None
        found = tuple(sources.probe(kept or _first_settings(())))
        settings = kept or _first_settings(found)
        self._record.create(settings, Profile(FIRST_AREAS, ()))
        problem = channel.Telegram(settings).check()
        tick = schedule.install_tick(Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser())
        probed = {f.harness for f in found}
        own_chat = f", in Atlas's own chat {settings.channel.atlas_chat}" if settings.channel.dedicated else ""
        return SetupReport(
            record=self._record.root,
            channel=problem or f"{settings.channel.platform.title()}, through the Hermes gateway{own_chat}",
            model=MODEL_LINE,
            found=found,
            not_found=tuple(h.label for h in sources.STORE_HARNESSES if h not in probed),
            mcp_commands=tuple(cmd for h, cmd in MCP_ADD.items() if h in probed),
            disclosures=(*([f"Reads, never writes: {', '.join(f.root for f in found)}"] if found else []),
                         f"Writes: {_tilde(self._record.root)}", tick, HAND_EDITS),
            version=".".join(__version__.split(".")[:2]),
            machine=settings.machine,
            exclusions=settings.exclusions,
            own_chat_offer=None if settings.channel.dedicated else OWN_CHAT_OFFER,
        )

    def backfill(self, now: datetime) -> BackfillReport:
        """Resumable. Holds the tick lease, waiting for a tick's collect to finish. Reads every
        Session in the stores and commits their evidence in batches, writes a card for each
        Session that has none, then, while the record holds no Pursuit, builds the Pursuits
        from every Session (librarian.group_backfill, rules.start_state) in one commit. A re-run
        finds it all done and changes nothing. With no model, or when a model call fails, it
        logs and leaves the rest to the next backfill. Still to come: journal for every day ->
        librarian.profile_pass over the whole history, the first Profile (G3).
        Then it writes the Journal for every day with Active Time up to today."""
        with schedule.TickLease(self._record.root, wait=True):
            report = self._collect(every=True)
            if self._llm is None:
                log.warning("Atlas built no Pursuits: no model to read the Sessions with")
                carded = False
            else:
                librarian = Librarian(self._llm, self._record)
                if carded := self._write_cards(librarian):
                    self._build(librarian, now)
                    self._journal(librarian, None, self._today(now))
            snap = self._record.snapshot()
            unanswered = not carded and not any(_readable_card(snap, h.id) for h in snap.held().sessions())
            return replace(report, filed=_filed(snap, now), journal=_journaled(snap), unanswered=unanswered)

    def tick(self, now: datetime) -> TickReport:
        """Collect, file and send what is due. Collecting and filing run only while the tick lease
        is held; a store or Session it cannot read, or a Session the model gives no filing for, is
        logged and skipped, and the next tick tries again. It files at most filing.BUDGET Sessions,
        each with its own commit. It writes each Journal day closed since the newest day the
        Journal has, that day included, or since backfill built the Pursuits. The send phase runs
        after the lease is released, so another process's long collect never holds back the brief
        and a slow send never makes another tick skip collecting.
        Web-chat exports in the inbox are imported after collecting, IMPORT_BUDGET Sessions a tick
        at most, and each file whose every conversation the record then holds is deleted. A day the
        Journal has already written that a Session filed this tick worked on is written again with
        a "Corrected" line.
        # TODO pseudocode for what is still to come:
        #   if lease.held: for each day closed: librarian.profile_pass(snap, day)       # daily (G3)
        #     weekly: librarian.regroup
        """
        hand_edits = len(self._record.reconcile())
        with schedule.TickLease(self._record.root) as lease:
            busy = not lease.held
            collected = 0 if busy else self._collect_quietly() + self._import_quietly(now)
            filed: list[EvidenceId] = [] if busy else self._file_quietly(now)
            if not busy:
                self._journal_quietly(now, filed)
        sent = self._send_due(now)
        return TickReport(collected=collected, filed=len(filed), sent=sent, hand_edits=hand_edits, skipped_busy=busy)

    # -------------------------------------------------------------- the owner and agents

    def handle(self, msg: OwnerMessage) -> Reply | None:
        """The owner wrote to Atlas. Always: append a Note (W Q14) with Atlas's reply text.
        Then converse.interpret -> for each Command: authorize+decide+commit (Owner authority
        naming the Note); for each Query: read. Appends, once, an "apply" offer for each pending
        HAND_EDIT or REFUSED_UPDATE Note not yet offered (snap.pending_applies, D Q12, W Q15).
        Never sends anything else; a slow answer is finished by channel.claim (D Q8).
        A message already handled (same platform message id) gets the same reply and changes
        nothing. None when Atlas cannot read the words yet: nothing is written, and the caller
        leaves the message to Hermes's agent. Once the words are read, handle never raises: a
        commit that fails is settled from the record (a transaction left ready is rolled forward)
        and answered with the Note's reply, or with FAILED when nothing was written. The reply is
        always the Note's, masked as the record keeps it."""
        snap = self._record.snapshot()
        earlier = rules.redelivered(snap, msg.platform_ref)
        if earlier is not None:
            return Reply(earlier.atlas_reply)
        librarian = Librarian(self._llm, self._record) if self._llm is not None else None
        reading = converse.interpret(msg, snap, librarian)
        if reading.unclear:
            log.info("Atlas could not read a message (%s); it is left to Hermes's agent",
                        reading.unclear.split(":")[0])
            if reading.cancel_pending_forget:
                self._cancel_forget()
            self._heard(msg)
            return None
        answers = {q: self._answered(q, msg.at) for q in reading.asks if not isinstance(q, ResumePromptQ)}
        try:
            applies = {cmd.note: _applied(snap, librarian, cmd.note)
                       for cmd in reading.commands if isinstance(cmd, ApplyNote)}
        except ModelError as unusable:
            log.info("Atlas could not read a hand edit to apply (%s); the message is left to Hermes's agent",
                     type(unusable).__name__)
            return None
        try:
            prework = self._prework(snap, reading.commands)
        except Exception as failed:
            log.warning("Atlas could not do the model's part of a message (%s); it is left to Hermes's agent",
                        type(failed).__name__)
            return None
        try:
            decision = self._decide(lambda s: _conversed(s, msg, reading, answers, applies, prework))
        except Exception as failed:
            log.warning("Atlas read a message and could not commit it (%s)", type(failed).__name__)
            return self._settled(msg)
        if not decision.outcome.ok:
            log.info("Atlas refused a message (%s); it is left to Hermes's agent", decision.outcome.refused)
            return None
        return self._settled(msg)

    def act(self, cmd: Command, who: Authority, now: datetime, words: str | None = None) -> Outcome:
        """Structured change from the Board, the Settings page, or an agent tool. Same
        rules.decide path as handle(), so the rules hold for every writer. An agent change
        refused for want of an Instruction is committed as a REFUSED_UPDATE Note (pending=cmd,
        text quotes the owner's `words` the agent passed) and the Outcome says so (D Q12).
        Built so far: the Board's SetFocus, SetStatus and MarkSeen (rules.from_board), the
        Settings tab's ChangeSettings (settings_rules.from_settings), and an agent's AddNote and
        EditResumeNote (agents.decide)."""
        if isinstance(who, Agent):
            return self._decide(lambda snap: agents.decide(snap, cmd, who, now, words)).outcome
        if not (isinstance(who, Owner) and who.via == "board"):
            raise NotImplementedError("a change by another writer")
        if isinstance(cmd, ChangeSettings):
            typed = settings_rules.roots_typed(cmd.patch)
            here = settings_rules.Here(_tilde(self._record.root / "inbox"),
                                       typed is not None and sources.linked(typed[1], typed[0]), sources.defaults())
            return self._decide(lambda snap: settings_rules.from_settings(snap, cmd, who, now, here)).outcome
        return self._decide(lambda snap: rules.from_board(snap, cmd, who, now)).outcome

    def snapshot(self) -> Snapshot:
        """The record as it is now, for a reader that renders it itself, as agentapi does."""
        return self._record.snapshot()

    def read(self, q: Query, now: datetime | None = None) -> Answer:
        """Never commits. Deterministic for ResumeQ/DayQ/NeglectedQ/PeriodQ/PeriodTimeQ/BoardQ;
        FindQ uses the index then librarian.answer. The Answer's data is the text the owner reads
        in the Channel, as of `now`. Built so far: ResumePromptQ, BoardQ, ResumeQ, PeriodTimeQ, NeglectedQ and FindQ."""
        snap = self._record.snapshot()
        if isinstance(q, ResumePromptQ):
            return rules.resume_prompt(snap, q)
        if isinstance(q, BoardQ):
            return rules.board(snap)
        now = (now or datetime.now().astimezone()).astimezone(ZoneInfo(snap.settings().timezone))
        if isinstance(q, ResumeQ):
            return Answer(None, (), Coverage(), data=ask.resume(snap, snap.pursuit(q.pursuit), now))
        if isinstance(q, PeriodTimeQ):
            days = _days(snap, q.start, min(q.end, now.date()), now.date())
            names = {p.id: p.name for p in snap.pursuits()}
            return Answer(None, (), Coverage(), data=ask.week_time(q.start, q.end, days, names))
        if isinstance(q, NeglectedQ):
            return Answer(None, (), Coverage(), data=neglect.answer(snap, now))
        if isinstance(q, WhichQ):
            return Answer(None, (), Coverage(), data=neglect.which(q.names))
        if isinstance(q, FindQ):
            if self._llm is None:
                raise ModelError("no model to read the Sessions with")
            found = Librarian(self._llm, self._record).answer(snap, q.text, snap.find(q.text))
            if found.data is None:
                return Answer(None, (), Coverage(), data=ask.FOUND_NOTHING)
            line = found.statements[0] if found.statements else None
            return Answer(None, found.statements, Coverage(), data=ask.found(snap, snap.session(found.data), line, now))
        raise NotImplementedError(type(q).__name__)

    def reindex(self) -> int:
        """Rebuilds the disposable index from the record's files; the number of Sessions it holds."""
        return self._record.reindex()

    # -------------------------------------------------------------- the gateway's questions

    def channel(self) -> ChannelSetting:
        """Which chat is Atlas's and how the owner addresses it, for channel.claim. Raises
        FileNotFoundError before setup has created the record."""
        return self._record.snapshot().settings().channel

    def sent(self, chat: str, message_id: str) -> SentRef | None:
        """The brief that a reply in `chat` replies to, for channel.note_inbound (rules.replied_to)."""
        return rules.replied_to(self._record.snapshot(), chat, message_id)

    def sending(self, now: datetime) -> bool:
        """Whether a brief is being sent, so its message id is not on record yet (rules.in_flight)."""
        return rules.in_flight(self._record.snapshot(), now)

    def answered(self, platform_ref: str) -> str | None:
        """The reply Atlas gave a platform message it already handled, for channel.claim."""
        earlier = rules.redelivered(self._record.snapshot(), platform_ref)
        return earlier.atlas_reply if earlier is not None else None

    # -------------------------------------------------------------- private

    def _prework(self, snap: Snapshot, commands: Sequence[Command]) -> dict[Command, object]:
        """The model's part of a message's Commands, done outside the lock: each Split's new Pursuit."""
        splits = [c for c in commands if isinstance(c, Split)]
        if not splits:
            return {}
        librarian = Librarian(self._llm, self._record)
        return {c: librarian.partition(snap, snap.pursuit(c.pursuit), c.criterion, c.new_name) for c in splits}

    def _answered(self, q: Query, now: datetime) -> str:
        """The text that answers a question, written before the Note's commit, so a model call
        holds no lock; a question Atlas could not answer gets D Q8's failure line."""
        try:
            return self.read(q, now).data
        except Exception as failed:
            log.warning("Atlas could not answer a question (%s)", type(failed).__name__)
            return f"I couldn't answer that: {_why(failed)}."

    def _decide(self, plan: Callable[[Snapshot], Decision], attempts: int = 5) -> Decision:
        """snapshot -> plan -> commit; on Conflict re-snapshot and re-plan. Plans are pure and
        LLM pre-work is cached, so a retry costs milliseconds."""
        for attempt in range(1, attempts + 1):
            decision = plan(self._record.snapshot())
            try:
                self._record.commit(decision.changes)
            except Conflict:
                if attempt == attempts:
                    raise
                continue
            return decision

    def _heard(self, msg: OwnerMessage) -> None:
        """Words Atlas could not read about the open decision question still answer it: the brief's
        question is marked heard, so its silence parks nothing."""
        def plan(s: Snapshot) -> Decision:
            b = neglect.heard(s, msg)
            return Decision(s.changes().put_brief(b) if b else s.changes(), Outcome(True, ()))
        try:
            if neglect.heard(self._record.snapshot(), msg) is not None:
                self._decide(plan)
        except Exception as failed:
            log.warning("Atlas could not mark a question answered (%s)", type(failed).__name__)

    def _cancel_forget(self) -> None:
        try:
            self._decide(lambda s: Decision(forget.cancel(s.changes(), s), Outcome(True, ())))
        except Exception as failed:
            log.warning("Atlas could not cancel a pending Forget (%s)", type(failed).__name__)

    def _collect_quietly(self) -> int:
        try:
            return self._collect(every=False).sessions
        except Exception as error:
            log.warning("Atlas skipped collecting Sessions this tick (%s)", type(error).__name__)
            return 0

    def _import_quietly(self, now: datetime) -> int:
        """Imports the web-chat exports waiting in the inbox: the Sessions put in the record."""
        try:
            return self._import(now)
        except Exception as error:
            log.warning("Atlas skipped the web-chat exports this tick (%s)", type(error).__name__)
            return 0

    def _import(self, now: datetime) -> int:
        """Each settled export file in the inbox of a web chat the owner added, oldest first, until
        IMPORT_BUDGET Sessions are put or IMPORT_FILES files are read this tick. A file whose every
        conversation the record then holds, or Forget keeps out or the file skips, is deleted after
        that commit. Every other file read is noted in the import log by what it is, and read again
        only once it changes: one that fails in whole or in part, and one of a web chat the owner
        has not added, until they add it. A file the budget cut short is read again next tick,
        where what the record already holds is skipped. A file that leaves the inbox or changes
        leaves the log."""
        snap = self._record.snapshot()
        enabled = [h for h in webchat.WEB_CHATS if (setting := snap.settings().harnesses.get(h)) is not None
                   and setting.enabled and setting.mode == "export"]
        if not enabled:
            return 0
        inbox = self._record.root / "inbox"
        waiting = webchat.waiting(inbox, now)
        present = {stamp.key() for _, stamp in waiting}
        noted = {e.file for e in snap.imports()
                 if not e.deleted and not (e.failure == NOT_ADDED and e.harness in enabled)}
        put, read, logged = 0, 0, []
        for name, stamp in waiting:
            if put >= IMPORT_BUDGET or read >= IMPORT_FILES:
                break
            if stamp.key() in noted:
                continue
            read += 1
            try:
                entry, n = self._import_one(inbox, name, stamp, enabled, IMPORT_BUDGET - put, now)
            except Exception as error:
                entry, n = ImportLog(None, now, 0, False, type(error).__name__, stamp.key()), 0
            put += n
            if entry is None:
                continue
            logged.append(entry)
            if entry.failure and entry.failure != NOT_ADDED:
                log.warning("Atlas kept a web-chat export in the inbox (%s, %d conversations unread)", entry.failure,
                            entry.broken)
        if logged or any(not e.deleted and e.file not in present for e in snap.imports()):
            try:
                self._decide(lambda s: Decision(s.changes().put_imports(
                    [*(e for e in s.imports() if e.deleted or e.file in present), *logged]), Outcome(True, ())))
            except Exception as error:
                log.warning("Atlas could not note the exports it read (%s)", type(error).__name__)
        return put

    def _import_one(self, inbox: Path, name: str, stamp: "webchat.Stamp", enabled: Sequence[HarnessId], budget: int,
                    now: datetime) -> tuple[ImportLog | None, int]:
        """One file: its import log entry, None when the budget cut it short, and the Sessions put."""
        export = webchat.read(inbox, name, stamp, enabled)
        if export.failed is not None or export.harness is None:
            failure = export.failed or (NOT_ADDED if export.other else "Unreadable")
            return ImportLog(export.other, now, 0, False, failure, stamp.key(), export.broken, export.skipped), 0
        n, whole, broken, error = self._import_file(export, budget, now)
        if not whole:
            return None, n
        broken += export.broken
        deleted = not broken and webchat.release(inbox, name, stamp)
        failure = error or ("Unreadable" if broken else None if deleted else "NotDeleted")
        held = len(export.conversations) - (broken - export.broken)
        return ImportLog(export.harness, now, held, deleted, failure, stamp.key(), broken, export.skipped), n

    def _import_file(self, export: webchat.Export, budget: int, now: datetime,
                     attempts: int = 5) -> tuple[int, bool, int, str | None]:
        """Puts the export's conversations the record does not hold as they are now, BATCH to a
        commit and at most `budget`. A conversation Forget keeps out is skipped; one the record
        cannot hold (no time, text it cannot write) is counted as broken and the others go on.
        Returns how many it put, whether the record now holds every conversation that is neither
        forgotten nor broken, how many are broken, and the type of the first failure."""
        put, pending = 0, list(export.conversations)
        bad: dict[str, str] = {}
        while pending:
            if put >= budget:
                return put, False, len(bad), None
            for _ in range(attempts):
                snap = self._record.snapshot()
                forgotten, settings, held = snap.forgotten().skip_keys, snap.settings(), snap.held()
                batch: list[webchat.Imported] = []
                rest: list[webchat.Conversation] = []
                for n, c in enumerate(pending):
                    if len(batch) >= min(BATCH, budget - put):
                        rest = pending[n:]
                        break
                    try:
                        i = webchat.session(c, snap.copy, settings, now)
                    except Exception as error:
                        bad.setdefault(c.key, type(error).__name__)
                        continue
                    part = held.get(i.evidence.parts[0].origin)
                    if not i.skip_keys & forgotten and (part is None or part.fingerprint != i.evidence.parts[0].fingerprint):
                        batch.append(i)
                if not batch:
                    pending = rest
                    break
                changes, kept = _put_sessions(snap, [sources.CollectedSession(i.evidence, i.raw, skip_keys=i.skip_keys)
                                                     for i in batch])
                try:
                    self._record.commit(changes)
                except Blocked:
                    return put, False, len(bad), None
                except Conflict:
                    continue
                put += len(kept)
                if len(kept) < len(batch):
                    return put, False, len(bad), None
                pending = rest
                break
            else:
                log.warning("Atlas left a web-chat export to the next tick: the record kept changing while it wrote it")
                return put, False, len(bad), None
        return put, True, len(bad), next(iter(bad.values()), None)

    def _collect(self, every: bool) -> BackfillReport:
        """Collect, then read and commit BATCH Sessions at a time: every Session, or those with a
        part the record does not hold as it is now. Each commit is whole, so an interrupted run
        resumes where it stopped. A folded child filed earlier as its own Session moves into the
        Session it folds into, unless a Pursuit lists it (moving that membership comes with
        filing, #10) or a Pursuit file Atlas cannot read might. Evidence whose skip key
        forgotten.md holds is removed first."""
        snap = self._record.snapshot()
        sent = _sent(snap)
        held = snap.held()
        forgotten = snap.forgotten().skip_keys
        self._drop(snap, [h.id for h in held.sessions() if h.skip_key in forgotten])
        pending, _ = sources.collect(snap.settings(), held, forgotten, sent, self._record.root / "inbox", every)
        sessions = folded = 0
        via: dict[str, dict[HarnessId, int]] = {}
        for start in range(0, len(pending), BATCH):
            for c in self._commit(pending[start:start + BATCH], sent):
                if c.alone:
                    folded += 1 + c.folded
                    continue
                sessions, folded = sessions + 1, folded + c.folded
                if c.evidence.interface:
                    counts = via.setdefault(c.evidence.interface, {})
                    counts[c.evidence.harness] = counts.get(c.evidence.harness, 0) + 1
        return BackfillReport(sessions, folded, via)

    def _write_cards(self, librarian: Librarian) -> bool:
        """A card for every Session that has none, or one Atlas cannot read, committed BATCH at a
        time. Stops at the first model call that fails, logged; returns whether every Session has
        its card."""
        snap = self._record.snapshot()
        todo = [h.id for h in snap.held().sessions() if _readable_card(snap, h.id) is None]
        for start in range(0, len(todo), BATCH):
            cards = []
            for id in todo[start:start + BATCH]:
                try:
                    cards.append(librarian.card(snap.session(id)))
                except Exception as error:
                    log.warning("Atlas stopped writing cards: the model gave no card (%s). Run backfill again to "
                                "go on", _why(error))
                    self._put_cards(cards)
                    return False
            self._put_cards(cards)
        return True

    def _put_cards(self, cards: Sequence[SessionCard]) -> None:
        """A card whose Session is gone by the commit is left out."""
        def plan(snap: Snapshot) -> Decision:
            cs = snap.changes()
            for card in cards:
                try:
                    cs.put_card(card)
                except Conflict:
                    continue
            return Decision(cs, Outcome(True, ()))
        if not cards:
            return
        try:
            self._decide(plan)
        except Conflict:
            log.warning("Atlas left %d cards to the next backfill: the record kept changing while it wrote them",
                        len(cards))

    def _build(self, librarian: Librarian, now: datetime, attempts: int = 5) -> None:
        """The first Pursuits, from every Session no card marks as a one-off. Only while the
        record holds no Pursuit: later Sessions are filed one by one (#10). When a Session the
        grouping read is removed before its commit, the grouping is dropped and made again
        without it; the model is asked again only where what it reads changed. When the
        Pursuits cannot be written, it logs and forgets the answers they came from, so the next
        backfill asks again."""
        for _ in range(attempts):
            snap = self._record.snapshot()
            if snap.holds_pursuits():
                return
            cards = {h.id: _readable_card(snap, h.id) for h in snap.held().sessions()}
            unfiled = [id for id, card in cards.items() if card and not card.one_off_area]
            if not unfiled:
                return
            try:
                grouping = librarian.group_backfill(snap, unfiled)
            except Exception as error:
                if self._lost(unfiled):
                    continue
                log.warning("Atlas built no Pursuits: the model gave no grouping (%s). Run backfill again to go on",
                            _why(error))
                return
            try:
                self._record.commit(rules.start_state(snap, grouping, now).changes)
                return
            except Conflict:
                continue
            except Exception as error:
                self._record.uncache(grouping.read)
                log.warning("Atlas built no Pursuits: it could not write them (%s). Run backfill again to go on",
                            type(error).__name__)
                return
        log.warning("Atlas left the Pursuits to the next backfill: the record kept changing while it wrote them")

    def _file_quietly(self, now: datetime) -> list[EvidenceId]:
        """Files the Sessions waiting (filing.queue), each in its own commit, and returns those it
        filed. A Session the model answers unusably for waits out its back-off and the others go
        on; a model call that fails stops filing for this tick, as every call would fail. Logged
        as counts and error types."""
        if self._llm is None:
            return []
        try:
            waiting = filing.queue(self._record.snapshot(), now)
        except Exception as error:
            log.warning("Atlas skipped filing this tick (%s)", type(error).__name__)
            return []
        librarian = Librarian(self._llm, self._record)
        filed: list[EvidenceId] = []
        unusable = 0
        for n, id in enumerate(waiting[:filing.BUDGET]):
            try:
                snap = self._record.snapshot()
                made = librarian.file(snap, snap.session(id))
            except Unusable:
                unusable += 1
                try:
                    self._decide(lambda s: filing.unusable(s, id, now))
                except Exception as error:
                    log.warning("Atlas could not note a Session to file later (%s)", type(error).__name__)
                continue
            except Exception as error:
                log.warning("Atlas left %d Sessions to the next tick: the model call failed (%s)",
                            len(waiting) - n, type(error).__name__)
                break
            try:
                self._decide(lambda s: filing.apply(s, made, now))
                filed.append(id)
            except Exception as error:
                log.warning("Atlas left one Session to the next tick: it could not file it (%s)",
                            type(error).__name__)
        if unusable:
            log.warning("Atlas left %d Sessions to file later: the model's filing was unusable", unusable)
        return filed

    def _journal_quietly(self, now: datetime, filed: Sequence[EvidenceId] = ()) -> None:
        """Writes the days closed since the newest day the Journal has, then the earlier days it
        has written that a Session in `filed` worked on, each with its correction."""
        try:
            snap = self._record.snapshot()
            days = snap.journal_days()
            since = days[-1] if days else _built(snap)
            if since is None or self._llm is None:
                return
            until = self._today(now) - timedelta(days=1)
            librarian = Librarian(self._llm, self._record)
            self._journal(librarian, since, until, now=now)
            late = corrections.late_days(snap, filed, set(days), since)
            if late:
                self._journal(librarian, min(late), until, only=late, now=now)
        except Exception as error:
            log.warning("Atlas skipped the Journal this tick (%s)", type(error).__name__)

    def _journal(self, librarian: Librarian, since: date | None, until: date, attempts: int = 5,
                 only: set[date] | None = None, now: datetime | None = None) -> None:
        """Writes each day from `since` (every day when None) to `until`, of `only` when given,
        whose file is missing or out of date (rules.journal_due), oldest first. Stops at the first
        day the model gives no lines for, logged, and leaves it and the days after it to the next
        run. When a Session a day reads is removed or changed before the day commits, it plans
        again from that day. A day the Journal had already written that gains Sessions is
        corrected as of `now`."""
        for _ in range(attempts):
            snap = self._record.snapshot()
            settings = snap.settings()
            one_offs = {h.id: card.one_off_area for h in snap.held().sessions()
                        if (card := _readable_card(snap, h.id)) and card.one_off_area}
            start = None if since is None else datetime.combine(since, time(), ZoneInfo(settings.timezone))
            held = {h.id for h in snap.held().sessions()}
            timed = rules.timed(snap, one_offs, start - settings.gap if start else None)
            try:
                for day, sessions in rules.workdays(timed, settings, since, until).items():
                    if only is not None and day not in only:
                        continue
                    since = day
                    if not self._journal_day(librarian, snap, day, sessions, held, now):
                        return
                return
            except Conflict:
                continue
        log.warning("Atlas left the Journal from %s to the next run: the record kept changing while it wrote it",
                    since)

    def _journal_day(self, librarian: Librarian, snap: Snapshot, day: date, sessions: Sequence[activetime.TimedSession],
                     held: set[EvidenceId], now: datetime | None = None) -> bool:
        """Writes `day` when it is due. False when the model gave no lines, logged. A day whose
        file Atlas cannot read is logged and never written over; one it cannot write is logged
        and left to the next run. Raises Conflict when a Session
        the day reads is gone or changed."""
        settings = snap.settings()
        active = activetime.day(sessions, day, settings.timezone, settings.gap)
        try:
            current = snap.journal(day)
        except Exception as error:
            log.warning("Atlas left the Journal day %s as it is: it could not read it (%s)", day.isoformat(),
                        type(error).__name__)
            return True
        if not rules.journal_due(current, active, held):
            return True
        new = rules.uncited(snap, current, active)
        try:
            lines = librarian.journal_lines(snap, day, new) if new else JournalLines()
        except Exception as error:
            if self._lost(new):
                raise Conflict(day.isoformat()) from None
            log.warning("Atlas left the Journal from %s to the next run: the model gave no lines (%s)",
                        day.isoformat(), _why(error))
            return False
        try:
            corrected = corrections.line(snap, current, sessions, day, now) if now is not None else None
            self._record.commit(rules.journal_day(snap, day, active, lines, corrected).changes)
        except Conflict:
            raise
        except Exception as error:
            log.warning("Atlas left the Journal of %s to the next run (%s)", day.isoformat(), type(error).__name__)
        return True

    def _today(self, now: datetime) -> date:
        return now.astimezone(ZoneInfo(self._record.snapshot().settings().timezone)).date()

    def _lost(self, ids: Sequence[EvidenceId]) -> bool:
        return not set(ids) <= {h.id for h in self._record.snapshot().held().sessions()}

    def _drop(self, snap: Snapshot, forgotten: Sequence[str]) -> None:
        """Removes the evidence of Sessions that forgotten.md names. A conflicting commit leaves
        them to the next collect."""
        if not forgotten:
            return
        cs = snap.changes()
        for id in forgotten:
            cs.remove_evidence(id)
        try:
            self._record.commit(cs)
        except Conflict:
            log.warning("Atlas will remove a forgotten Session's evidence at the next collect")

    def _commit(self, batch: Sequence[sources.Pending], sent: Sent,
                attempts: int = 5) -> list[sources.CollectedSession]:
        """Reads the batch from the stores, then merges each part with the copy a snapshot taken
        after the reads holds of it and commits. A Session that snapshot's forgotten.md names is
        left out, and the commit fails if a Forget of one of the batch's Sessions commits
        meanwhile, so the batch plans again without it; a Forget of any other Session does not
        stop it. Atlas commits each text before `hermes send` mirrors it, so the snapshot's sent
        ledger has every text a Hermes row read can mirror, and no mirror row reaches a raw copy.
        A Session whose folder a link stands in the way of is left to the next collect, logged,
        and the rest of the batch planned again without it. When the record keeps changing the
        batch is left to the next collect, logged."""
        read = [p.read(sent) for p in batch]
        blocked: set[str] = set()
        for _ in range(attempts):
            snap = self._record.snapshot()
            now = _sent(snap)
            forgotten = snap.forgotten().skip_keys
            found = [c for r in read for c in r.sessions(snap.copy, now) if not c.skip_keys & forgotten]
            changes, kept = _put_sessions(snap, found, blocked)
            try:
                self._record.commit(changes)
                return kept
            except Blocked as error:
                blocked.update(error.args)
            except Conflict:
                continue
        log.warning("Atlas left %d Sessions to the next collect: the record kept changing while it wrote them",
                    len(batch))
        return []

    def _send_due(self, now: datetime) -> tuple[str, ...]:
        """Holds the send lease, so a SENDING attempt found here was left by a crash. A tick that
        finds the lease busy sends nothing; the next tick finds the brief sent."""
        with schedule.SendLease(self._record.root) as lease:
            if not lease.held:
                return ()
            sent = []
            for due in schedule.due(self._record.snapshot(), now):
                if self._send(lambda snap: rules.compose_daily(snap, now)) is SendStatus.SENT:
                    sent.append(f"brief {due.day.isoformat()}")
            return tuple(sent)

    def _settled(self, msg: OwnerMessage) -> Reply:
        """The reply the message's Note holds, masked as the record keeps it, or FAILED when the
        record holds no such Note."""
        try:
            written = rules.noted(Record.open(self._record.root).snapshot(), msg)
        except Exception as failed:
            log.warning("Atlas could not read its record back (%s)", type(failed).__name__)
            written = None
        return Reply(written.atlas_reply if written is not None else FAILED)

    def _send(self, plan: Callable[[Snapshot], Decision]) -> SendStatus | None:
        """Commit (status SENDING) -> channel.send -> commit (SENT + message ids, or
        ARRIVAL_UNKNOWN). The text sent is the one the brief file holds, its secrets masked, so
        it is exactly what was sent and a Hermes mirror row of it matches. The outcome keeps that
        text's hash, so only a row that reads exactly so is taken for the mirror. A send `hermes`
        refused before sending leaves the attempt SENDING, which the next tick takes as arrival
        unknown; any other failure is kept as ARRIVAL_UNKNOWN. Either way the retry rule (D Q13)
        decides whether it is sent again, and the tick goes on. When the record keeps changing under either commit it logs and returns:
        before the send, the brief waits for the next tick; after it, the attempt stays SENDING."""
        try:
            message = self._decide(plan).send
        except Conflict as error:
            log.warning("Atlas left the brief to the next tick: the record kept changing while it wrote it (%s)",
                        type(error).__name__)
            return None
        if message is None:
            return None
        snap = self._record.snapshot()
        text = snap.brief(message.day).text
        attempt = replace(message.attempts[-1], text_sha256=sent_sha256(text))
        try:
            sent = channel.Telegram(snap.settings()).send(text)
            outcome = replace(attempt, status=SendStatus.SENT, message_ids=sent.message_ids, chat=sent.chat)
        except channel.SendRefused as refused:
            log.warning("Atlas could not send the brief (%s); the next tick tries once more",
                        type(refused.__cause__ or refused).__name__)
            return None
        except Exception as failed:
            log.warning("Atlas could not send the brief (%s)", type(failed).__name__)
            outcome = replace(attempt, status=SendStatus.ARRIVAL_UNKNOWN)
        try:
            self._decide(lambda snap: rules.settle_send(snap, message.day, outcome))
        except Conflict as error:
            log.warning("Atlas left the brief's attempt marked sending: the record kept changing while it wrote "
                        "the outcome (%s). The next tick takes its arrival as unknown", type(error).__name__)
        return outcome.status


def _days(snap: Snapshot, start: date, last: date, today: date) -> list[DayTime]:
    """Active Time of each day from `start` to `last`: a closed day as its Journal file has it,
    and a day with no file yet, today among them, counted live from its Sessions."""
    settings = snap.settings()
    journaled = {d: snap.journal(d) for d in snap.journal_days() if start <= d <= last}
    every = [start + timedelta(days=n) for n in range((last - start).days + 1)]
    live = [d for d in every if d not in journaled or d == today]
    days = [journaled[d].time for d in every if d in journaled and d not in live]
    if live:
        one_offs = {h.id: card.one_off_area for h in snap.held().sessions()
                    if (card := _readable_card(snap, h.id)) and card.one_off_area}
        since = datetime.combine(live[0], time(), ZoneInfo(settings.timezone)) - settings.gap
        timed = rules.timed(snap, one_offs, since)
        days += [activetime.day(timed, d, settings.timezone, settings.gap) for d in live]
    return days


def _sent(snap: Snapshot) -> Sent:
    """Every text Atlas sent. A brief whose every attempt kept the hash of its text was sent as its
    file holds it. Any other brief, and a Note's reply, which keeps no hash, may have been sent
    bare."""
    briefs = snap.briefs()
    replies = [n.atlas_reply.strip() for n in snap.notes() if n.atlas_reply]
    unsure = [b.text.strip() for b in briefs if any(a.text_sha256 != sent_sha256(b.text) for a in b.attempts)]
    return Sent(frozenset([*(b.text.strip() for b in briefs), *replies]), frozenset([*unsure, *replies]))


def _put_sessions(snap: Snapshot, batch: Sequence[sources.CollectedSession], blocked: Iterable[str] = ()
                  ) -> tuple[ChangeSet, list[sources.CollectedSession]]:
    """The batch's puts, and the Sessions put. A Session whose raw copy changed since it was
    merged, that was read alone and whose home evidence is gone, or whose put writes or deletes
    in a `blocked` folder, is left to the next collect."""
    cs = snap.changes().avoid(*blocked)
    listed = {m.evidence for p in snap.pursuits() for m in p.sessions}
    unsure = snap.misses_pursuits()
    kept = []
    for c in batch:
        apart = set(c.replaces) if unsure else listed & c.replaces.keys()
        if apart:
            log.warning("Atlas kept a folded Session as its own because a Pursuit %s",
                        "file it could not read may list it" if unsure else "lists it")
        try:
            cs.put_session(c.evidence, c.raw, {id: key for id, key in c.replaces.items() if id not in apart},
                           c.skip_keys, c.alone)
        except Blocked:
            log.warning("Atlas left one %s Session to the next collect: a link stands where its folder is",
                        c.evidence.harness.label)
            continue
        except Conflict:
            log.warning("Atlas left one %s Session to the next collect: the record changed under it",
                        c.evidence.harness.label)
            continue
        kept.append(c)
    return cs, kept


def _filed(snap: Snapshot, now: datetime) -> Filed | None:
    """The record's Pursuits and one-offs, once backfill has built them, counted from the day it
    built the Pursuits, or the one-offs' day when it built none."""
    pursuits = snap.pursuits()
    one_offs = [card for h in snap.held().sessions() if (card := _readable_card(snap, h.id)) and card.one_off_area]
    if not pursuits and not one_offs:
        return None
    built = _built(snap) or now.astimezone(ZoneInfo(snap.settings().timezone)).date()
    count = lambda status: sum(p.status is status for p in pursuits)
    return Filed(len(pursuits), count(Status.ACTIVE), count(Status.PARKED), len(one_offs),
                 built - timedelta(days=rules.ACTIVE_DAYS + 1), BOARD_URL)


def _built(snap: Snapshot) -> date | None:
    """The day backfill built the Pursuits, or filed its one-offs when it built none."""
    days = [p.created for p in snap.pursuits()] or [
        card.one_off_day for h in snap.held().sessions() if (card := _readable_card(snap, h.id)) and card.one_off_day]
    return min(days, default=None)


def _conversed(snap: Snapshot, msg: OwnerMessage, reading: converse.Interpretation, answers: Mapping[Query, str],
               applies: Mapping[EvidenceId, Sequence[Command]], prework: Mapping[Command, object]) -> Decision:
    """The owner's words decided: a Forget by its own rules, any other message cancelling a Forget
    that waits for "forget"."""
    if forgets := [c for c in reading.commands if isinstance(c, (RequestForget, ConfirmForget))]:
        return forget.converse(snap, msg, forgets[0])
    decision = rules.converse(snap, msg, reading.commands, reading.asks, applies, prework, answers)
    if reading.cancel_pending_forget:
        forget.cancel(decision.changes, snap)
    return decision


def _applied(snap: Snapshot, librarian: Librarian | None, id: EvidenceId) -> Sequence[Command]:
    """What "apply" makes of a Note: a refused agent change as it was kept, a hand edit as the model reads it."""
    note = next(n for n in snap.pending_applies() if n.id == id)
    return (note.pending,) if note.origin is NoteOrigin.REFUSED_UPDATE else librarian.edit_to_commands(snap, note)


def _journaled(snap: Snapshot) -> Journaled | None:
    days = snap.journal_days()
    return Journaled(len(days), days[0], days[-1]) if days else None


def _span(first: date, last: date) -> str:
    """The Journal's days as "2 Jun to 15 Nov", each with its year when the two years differ."""
    day = (lambda d: f"{d.day} {d:%b} {d.year}") if first.year != last.year else (lambda d: f"{d.day} {d:%b}")
    return f"{day(first)} to {day(last)}"


def _readable_card(snap: Snapshot, id: EvidenceId) -> SessionCard | None:
    try:
        return snap.card(id)
    except Exception:
        log.warning("Atlas will write one Session's card again: it could not be read")
        return None


def _why(error: Exception) -> str:
    """Atlas's own reason, or only the type of an error the model call raised: Hermes's messages,
    such as a schema mismatch, can quote the conversation."""
    return str(error) if isinstance(error, ModelError) else type(error).__name__


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _first_settings(found: Sequence[sources.Found]) -> Settings:
    """Setup's defaults (07:30 brief, Sunday 18:00 Review, 15-minute gap) plus what the probe found."""
    return Settings(
        timezone=_local_timezone(), brief_at=time(7, 30), review_day=7, review_at=time(18, 0),
        gap=timedelta(minutes=15), machine=socket.gethostname(),
        harnesses={f.harness: HarnessSetting(True, "store", f.roots, "probe") for f in found if not f.is_interface},
        exclusions=(), channel=ChannelSetting("telegram"),
        interfaces={"t3_code": InterfaceSetting("probe", f.roots[0]) for f in found if f.is_interface})


def _local_timezone() -> str:
    """The machine's IANA zone: $TZ, then /etc/timezone, then the /etc/localtime link."""
    named = os.environ.get("TZ", "").lstrip(":")
    if named in available_timezones():
        return named
    etc = Path("/etc/timezone")
    if etc.exists() and etc.read_text().strip() in available_timezones():
        return etc.read_text().strip()
    link = Path("/etc/localtime")
    if link.is_symlink() and "zoneinfo/" in str(link.readlink()):
        return str(link.readlink()).split("zoneinfo/", 1)[1]
    return "UTC"


def _tilde(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)
