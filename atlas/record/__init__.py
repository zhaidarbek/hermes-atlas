"""The record: Markdown files with YAML front matter ARE the truth. This package is the only
code that reads or writes them. Callers see domain types (atlas.model) only, never paths,
YAML or Markdown (per boundary-discipline). Public surface: Record, Snapshot, ChangeSet,
Conflict, HandEdit. Everything in _layout/_codec/_txn/_seal/_index/_masking is private; the
plugin's import-linter contract forbids importing them from outside this package.

Single writer
-------------
Many processes load the plugin (gateway, every `hermes atlas ...` CLI run including the cron
tick, the dashboard backend, each MCP server a Harness launches). Any of them may commit, but
commits are serialized and conflict-checked:

  1. Plan outside any lock against a Snapshot (LLM calls happen here, can take seconds).
  2. ``Record.commit(cs)`` takes ``fcntl.flock(LOCK_EX)`` on ``.atlas/write.lock`` (held for
     milliseconds), rolls forward any half-applied transaction, absorbs hand edits, then
     compares the bytes of every file ``cs`` touches with the bytes its Snapshot read
     (compare-and-swap), and for a guarded ChangeSet every file its plan read and the files of
     each kind it listed. Mismatch -> ``Conflict``; the caller re-plans on a fresh Snapshot
     (rules are pure and fast; LLM outputs are cached by input hash).
  3. Staged bytes -> ``.atlas/txn/<id>/``, fsync, write ``.atlas/txn/<id>.ready`` manifest,
     fsync dir, ``os.replace`` each file + its last-written mirror copy, bump ``.atlas/HEAD``,
     delete the txn dir, then update the index.
  A crash before ``.ready`` leaves only staging junk (deleted on next open); a crash after it
  is rolled forward by whoever opens the record next (replace is idempotent). So every commit
  is all-or-nothing across files, which merge, split and Forget need. While a ready transaction
  cannot be applied (an I/O error), each Snapshot shows it as committed and every commit fails.

Secrets (D Q4)
--------------
``_codec.render`` runs ``privacy.scrub`` on every text of the value it renders, whatever the
file: Pursuit files, Journal days, Notes, Profile, session.md, cards, sent briefs. Ids, fixed
words, settings.md and the keys something outside the file matches are kept. So everything
derived or shown masks detected secrets, and anything that reads the record (agents, the
Board, less) sees the masked form. A brief is sent as its file holds it. The raw copy stays
verbatim (R2 Q4); Forget removes it. The opens after an upgrade write the files an older Atlas
wrote bare again through the codec (``_masking``).

Hand edits (W Q15)
------------------
Each sealed record file carries ``sealed: sha256:<hex>`` in its front matter, the hash of the
file's bytes with that value blanked. A file whose seal does not verify was changed outside
Atlas. Its last Atlas-written bytes are in ``.atlas/last-written/<same path>``. Snapshots
always serve Atlas's version; ``reconcile()`` (and every commit) restores the file and appends
a HAND_EDIT Note holding the edited text and a diff, in one transaction. Session transcripts
(``session.md``) are not mirrored: they are regenerated from ``raw/``. Raw parts are checked
against the sha256 in ``session.md`` and re-copied from the Harness if it still has them.

Forget
------
``RemoveEvidence`` ops delete the evidence directory, the mirror copies of rewritten files are
replaced in the same commit, and the commit also deletes index rows and LLM-cache entries
tagged with the Evidence id, and any staged txn copy. No other place holds raw content: the
inbox is emptied after import, and Telegram history is outside Atlas (disclosed).
"""
from __future__ import annotations

import base64
import bisect
import difflib
import hashlib
import io
import itertools
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, tzinfo
from functools import cached_property
from pathlib import Path
from typing import Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import zstandard

from .. import privacy
from ..model import (ONE_OFF, Area, EvidenceId, Forgotten, HarnessId, HistoryId, JournalDay, Note, NoteOrigin, Pause,
                     Profile, RawPart, Pursuit, PursuitId, SentBrief, SentReview, SessionCard, SessionEvidence, Settings,
                     Turn, cites, harness_of, part_file, part_origin, skip_key)
from . import _codec, _index, _layout, _masking, _seal, _txn
from ._codec import SessionRef
from ._txn import NotLocal

__all__ = ["Ambiguous", "Blocked", "ChangeSet", "Conflict", "HandEdit", "Held", "HeldPart", "HeldSession", "NotLocal",
           "Record", "Revision", "SessionRef", "Snapshot", "sessions_cited"]

ABSENT = "absent"
MAIN_FILE = part_file("main")
CACHE = "cache"

log = logging.getLogger(__name__)
_HISTORY_ID = re.compile(r"h-\d+")


IMPORTS_KEPT = 50


class Conflict(Exception):
    """A file this ChangeSet touches changed since its Snapshot was taken. Re-plan."""


class Blocked(Conflict):
    """A link or a file stands where a Session folder this ChangeSet writes or deletes in is, or a
    commit a link holds up waits to write or delete there. Its args are those Session folders: plan
    again with ChangeSet.avoid(*args)."""


class Ambiguous(Exception):
    """A name matched several Pursuits; carries the candidates for the reply."""

    def __init__(self, candidates: Sequence[Pursuit]) -> None:
        super().__init__(", ".join(p.name for p in candidates))
        self.candidates = tuple(candidates)


@dataclass(frozen=True)
class Revision:
    head: int                                # .atlas/HEAD after the commit


@dataclass(frozen=True)
class HandEdit:
    path: str                                # record-relative, e.g. "profile.md"
    note: EvidenceId                         # the Note that keeps the edited text
    at: datetime


@dataclass(frozen=True)
class Hit:
    evidence: EvidenceId
    score: float
    snippet_ref: str                         # where in session.md/card.md; text is read via Snapshot.session


@dataclass(frozen=True)
class Citation:
    """A place in the record that depends on an Evidence id (index backlink)."""
    path: str
    kind: str                                # "pursuit.resume", "pursuit.session", "journal.line", "profile.entry", ...
    owner_id: str                            # PursuitId / day / entry hash


@dataclass(frozen=True)
class ForgetPlan:
    """The Sessions the owner asked to forget, waiting for "forget". What goes with them is worked
    out again from the record when the owner confirms, so a line written since the ask goes too."""
    evidence: tuple[EvidenceId, ...]
    asked_at: datetime


@dataclass(frozen=True)
class FilingRetry:
    """A Session the model gave no usable filing for, as it was read: it waits until `retry_at`,
    unless it grows first."""
    last_at: datetime
    failures: int
    retry_at: datetime


@dataclass(frozen=True)
class ImportLog:
    """One web-chat export file Atlas read: its Harness, how many of its Sessions the record
    holds, and whether the file was deleted or kept, with the type of its failure and how many of
    its conversations were broken or skipped (no text, or one id twice). A file that could not be
    read at all names no Harness. `file` is what the file was (device, inode, size, modified), so
    a kept file is read again only once it changes. No name or text of the file is kept."""
    harness: HarnessId | None
    at: datetime
    sessions: int
    deleted: bool
    failure: str | None
    file: str
    broken: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class HeldPart:
    """Where the record holds a raw part, and the fingerprint its source had when it was read."""
    evidence: EvidenceId
    name: str                                # RawPart.name, "main.jsonl.zst" when it is its Session's own
    fingerprint: str


@dataclass(frozen=True)
class HeldSession:
    """One Session's evidence folder, as its front matter names it."""
    id: EvidenceId
    skip_key: str                            # as forgotten.md would keep it
    interface: str | None


class Held:
    """Every raw part the record holds, by its origin (model.part_origin): what collection
    compares each part a store lists against, whichever root or file the part is read from.
    Derived from session.md front matter, so it is rebuilt from the files."""

    def __init__(self, parts: Mapping[str, HeldPart] | None = None, sessions: Sequence[HeldSession] = ()) -> None:
        self._parts = dict(parts or {})
        self._sessions = {s.id: s for s in sessions}

    def get(self, origin: str) -> HeldPart | None:
        return self._parts.get(origin)

    def home(self, harness: HarnessId, native_key: str) -> HeldPart | None:
        """Where the record holds a native Session's own transcript, when that is inside another
        Session's evidence: a put of the Session goes there."""
        h = self.get(part_origin(harness, native_key, "main"))
        return h if h is not None and h.name != MAIN_FILE else None

    def session(self, id: EvidenceId) -> HeldSession | None:
        return self._sessions.get(id)

    def sessions(self) -> Sequence[HeldSession]:
        return tuple(self._sessions.values())


def _zone(settings: bytes | None) -> tzinfo | None:
    """settings.timezone: body times in every other file are wall time in it."""
    return ZoneInfo(_codec.parse("settings", settings, None).timezone) if settings is not None else None


def _base_of(data: bytes | None) -> str:
    return ABSENT if data is None else "sha256:" + hashlib.sha256(data).hexdigest()


def _front_matter(path: Path) -> bytes:
    """A file's front matter alone, read without its body: a transcript can be large."""
    lines = []
    with open(path, "rb") as fh:
        for line in fh:
            lines.append(line)
            if len(lines) > 1 and line == b"---\n":
                break
    return b"".join(lines)


def _unzstd(data: bytes) -> bytes:
    """Every frame of a raw part: it grows by appended frames, as `zstd -d` reads it."""
    return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data), read_across_frames=True).read()


class Snapshot:
    """Read-only, consistent view at one Revision. Parsed domain objects; Atlas's version of
    every file (a hand-edited file is served from the mirror until reconciled). Every read
    records the hash of the file's bytes so ``changes()`` can build a compare-and-swap ChangeSet."""

    revision: Revision

    def __init__(self, root: Path, revision: Revision, files: dict[str, bytes],
                 unopened: Mapping[str, OSError] | None = None, told: set[tuple[str, str]] | None = None) -> None:
        self._root = root
        self.revision = revision
        self._files = files
        self._unopened = dict(unopened or {})
        self._told = set() if told is None else told
        self._held: Held | None = None
        self._reads: set[str] = set()
        self._listed: set[_layout.FileKind] = set()

    def pursuits(self) -> Sequence[Pursuit]:
        """Every Pursuit whose file Atlas can read. One it cannot read is logged and left out, so
        the rest of Atlas, such as the Daily Brief, goes on."""
        return [f.pursuit for f in self._read_pursuits[0].values()]

    def holds_pursuits(self) -> bool:
        """Whether the record has a Pursuit file, also one Atlas cannot read."""
        return bool(self._paths("pursuit"))

    def misses_pursuits(self) -> bool:
        """Whether the record has a Pursuit file Atlas cannot read."""
        return bool(self._read_pursuits[1])

    def pursuit(self, ref: str | PursuitId) -> Pursuit:
        """Exact id, exact name, then fuzzy name. Raises Ambiguous or KeyError."""
        every = self.pursuits()
        for match in ([p for p in every if p.id == ref], [p for p in every if p.name == ref],
                      [p for p in every if ref.casefold() in p.name.casefold()]):
            if len(match) > 1:
                raise Ambiguous(match)
            if match:
                return match[0]
        raise KeyError(ref)

    def journal(self, day: date) -> JournalDay | None:
        data = self._read(_layout.journal_path(day))
        return _codec.parse("journal", data, self._tz).day if data is not None else None

    def journal_days(self) -> Sequence[date]:
        """Every day the Journal has a file for, oldest first."""
        return [date.fromisoformat(Path(path).stem) for path in self._paths("journal")]

    def profile(self) -> Profile:
        return self._value("profile", _layout.TOP_FILES["profile"])

    def settings(self) -> Settings:
        """Raises FileNotFoundError before setup has created the record."""
        return self._value("settings", _layout.TOP_FILES["settings"])

    def pauses(self) -> Sequence[Pause]:
        return self._value("pauses", _layout.TOP_FILES["pauses"])

    def forgotten(self) -> Forgotten:
        return self._value("forgotten", _layout.TOP_FILES["forgotten"])

    def briefs(self, since: date | None = None) -> Sequence[SentBrief]:
        """Oldest first."""
        every = (self._value("brief", path) for path in self._paths("brief"))
        return [b for b in every if since is None or b.day >= since]

    def brief(self, day: date) -> SentBrief | None:
        path = _layout.brief_path(day)
        return self._value("brief", path) if self._read(path) is not None else None

    def reviews(self, since: date | None = None) -> Sequence[SentReview]: raise NotImplementedError

    def notes(self, since: datetime | None = None) -> Sequence[Note]:
        ids = {privacy.scrub(p.name): p.id for p in self.pursuits()}
        out = []
        for path in self._paths("notes"):
            for e in self._value("notes", path).entries:
                note = replace(e.note, about=ids.get(privacy.scrub(e.about_name))) if e.about_name else e.note
                if since is None or note.at >= since:
                    out.append(note)
        return sorted(out, key=lambda n: (n.at, n.id))

    def session(self, id: EvidenceId) -> SessionEvidence:
        """Raises KeyError for an id the record does not hold."""
        folder = self._evidence_folder(id)
        if folder is None:
            raise KeyError(id)
        return _codec.parse("session", self._read(folder + "session.md"), self._tz)

    def session_ref(self, id: EvidenceId) -> SessionRef:
        """How the Pursuit files name a Session they list: Harness label, title and day. KeyError
        when no Pursuit lists it."""
        for f in self._read_pursuits[0].values():
            if id in f.sessions:
                return f.sessions[id]
        raise KeyError(id)

    def sessions(self, since: datetime | None = None, day: date | None = None) -> Sequence[SessionEvidence]:
        """Oldest first. `since` keeps Sessions with a turn at or after it, reading only the front
        matter of the others; `day` those that ran on it. A Session that Forget removes while
        this lists them is left out, and so is one whose file cannot be read, logged."""
        every = (s for path in self._session_paths()
                 if (since is None or self._ends_after(path, since)) and (s := self._readable_session(path)) is not None)
        return sorted((s for s in every if (since is None or s.last_at.timestamp() >= since.timestamp()) and (
            day is None or s.started.astimezone(self._tz).date() <= day <= s.last_at.astimezone(self._tz).date())),
                      key=lambda s: (s.started.timestamp(), s.id))
    def card(self, id: EvidenceId) -> SessionCard | None:
        folder = self._evidence_folder(id)
        data = self._read(folder + "card.md") if folder else None
        return _codec.parse("card", data, self._tz) if data is not None else None

    def unfiled(self) -> Sequence[EvidenceId]:
        """Evidence no Pursuit lists and that is not marked one-off: the librarian's work queue.
        Derived (index), so a crash between collect and file loses nothing."""
        raise NotImplementedError
    def find(self, text: str, limit: int = 20) -> Sequence[Hit]:
        """The Sessions whose transcript, card or Pursuit statements hold the words of `text`, best
        match first, from the disposable index, which is built again first when the record moved
        since it was built."""
        folder = _txn.machinery(self._root)
        if not folder.is_dir():
            return ()
        if _index.generation(folder) != self.revision.head:
            self.index()
        return tuple(Hit(EvidenceId(id), score, "session.md") for id, score in _index.search(folder, text, limit)
                     if self._evidence_folder(EvidenceId(id)) is not None)

    def index(self) -> int:
        """Builds the index from this Snapshot's files alone; the number of Sessions it holds. Written
        only while the record is still at this Snapshot's Revision, so a Session a later commit
        removed never comes back into it."""
        statements: dict[EvidenceId, list[str]] = {}
        for p in self.pursuits():
            said = [p.name, *(s.text for part in (p.resume.where, p.resume.left_off, p.resume.decided,
                                                  p.resume.open_questions) for s in part)]
            for m in p.sessions:
                statements.setdefault(m.evidence, []).extend(said)
        documents = []
        for s in self.sessions():
            card = self.card(s.id)
            lines = [s.title, *(t.text for t in s.turns), *statements.get(s.id, ())]
            if card is not None:
                lines += [x.text for x in (card.intent, card.outcome, card.unfinished, card.where) if x is not None]
            documents.append((s.id, "\n".join(lines)))
        with _txn.write_lock(self._root):
            if Record(self._root)._head() == self.revision.head:
                _index.build(_txn.machinery(self._root), self.revision.head, documents)
        return len(documents)

    def citing(self, ids: Sequence[EvidenceId]) -> Sequence[Citation]: raise NotImplementedError
    def held(self) -> Held:
        """A part held twice (a folded Session a Pursuit kept as its own) is found where it was
        folded. A Session that Forget removes while this reads is left out."""
        if self._held is None:
            self._held = self._read_held()
        return self._held

    def copy(self, origin: str) -> bytes | None:
        """The raw copy the record holds of the part with this origin, decompressed, wherever it
        holds it: what a part read again is merged with."""
        h = self.held().get(origin)
        folder = self._evidence_folder(h.evidence) if h else None
        data = self._read(f"{folder}raw/{h.name}") if folder else None
        return _unzstd(data) if data is not None else None

    def _read_held(self) -> Held:
        parts: dict[str, HeldPart] = {}
        sessions = []
        for path in self._session_paths():
            try:
                front = _codec.session_front(_front_matter(self._root / path))
            except FileNotFoundError:
                continue
            id = EvidenceId(front["id"])
            sessions.append(HeldSession(id, skip_key(HarnessId(front["harness"]), str(front["native_key"])),
                                        front["interface"]))
            for part in front["parts"] or ():
                origin = part.get("origin")
                current = parts.get(origin)
                if origin and (current is None or current.name == MAIN_FILE):
                    parts[origin] = HeldPart(id, str(part["name"]), str(part["fingerprint"]))
        return Held(parts, sessions)
    def pending_forget(self) -> ForgetPlan | None:
        data = self._read(_layout.PENDING_FORGET)
        if data is None:
            return None
        plan = json.loads(data)
        return ForgetPlan(tuple(map(EvidenceId, plan["evidence"])), datetime.fromisoformat(plan["asked_at"]))

    def filing_retries(self) -> dict[EvidenceId, FilingRetry]:
        data = self._read(_layout.FILING_RETRY)
        if data is None:
            return {}
        return {EvidenceId(id): FilingRetry(datetime.fromisoformat(r["last_at"]), int(r["failures"]),
                                            datetime.fromisoformat(r["retry_at"]))
                for id, r in json.loads(data).items()}

    def imports(self) -> tuple[ImportLog, ...]:
        data = self._read(_layout.IMPORTS)
        if data is None:
            return ()
        return tuple(ImportLog(HarnessId(r["harness"]) if r["harness"] else None, datetime.fromisoformat(r["at"]),
                               int(r["sessions"]), bool(r["deleted"]), r["failure"], str(r["file"]),
                               int(r.get("broken", 0)), int(r.get("skipped", 0))) for r in json.loads(data))

    def pending_applies(self) -> Sequence[Note]:
        """HAND_EDIT and REFUSED_UPDATE Notes not yet applied: what "apply" acts on. Offered once in
        Atlas's next answer (Note.offered), always listed in the Review."""
        return [n for n in self.notes() if n.origin in (NoteOrigin.HAND_EDIT, NoteOrigin.REFUSED_UPDATE)
                and not n.applied]
    def seen(self) -> Mapping[PursuitId, HistoryId]:
        """The Board's seen marks: each Pursuit's newest History id the owner has seen. Machinery, so
        a mark lost or unreadable reads as none, which shows a card marked again."""
        return _seen_marks(self._read(_layout.SEEN))

    def changes(self) -> "ChangeSet":
        """A ChangeSet bound to this Snapshot: every put/remove captures the hash of the
        file it replaces, so a caller cannot forget the compare-and-swap."""
        return ChangeSet(self.revision, _snapshot=self)

    def _read(self, path: str) -> bytes | None:
        """Evidence is read from disk when first asked for, never all at once: it is large and only
        ever added to, and a commit still compares what it replaces. It is read without following a
        link in its Session folder, so a link reads as no file. A Pursuit file the snapshot could not
        open raises the error it met."""
        self._reads.add(path)
        if path in self._unopened:
            raise self._unopened[path]
        if path not in self._files and _layout.evidence_kind(path) is not None:
            self._files[path] = _txn.read_below(self._root, path)
        return self._files.get(path)

    def _ends_after(self, path: str, since: datetime) -> bool:
        try:
            last = _codec._as_datetime(_codec.session_front(_front_matter(self._root / path))["last_at"])
            return _codec.local(last, self._tz) >= since
        except FileNotFoundError:
            return False
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            log.warning("Atlas skipped a Session file it could not read (%s)", type(error).__name__)
            return False

    def _readable_session(self, path: str) -> SessionEvidence | None:
        data = self._read(path)
        if data is None:
            return None
        try:
            return _codec.parse("session", data, self._tz)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            log.warning("Atlas skipped a Session file it could not read (%s)", type(error).__name__)
            return None

    def _session_paths(self) -> list[str]:
        return sorted(str(p.relative_to(self._root)) for p in (self._root / "evidence").glob("*/*/*/*/session.md"))

    def _evidence_folder(self, id: EvidenceId) -> str | None:
        harness = harness_of(id)
        found = sorted((self._root / "evidence").glob(f"{harness.value}/*/*/{id}/session.md")) if harness else []
        return str(found[0].parent.relative_to(self._root)) + "/" if found else None

    def _value(self, kind: _layout.FileKind, path: str):
        data = self._read(path)
        if data is None:
            raise FileNotFoundError(self._root / path)
        return _codec.parse(kind, data, self._tz)

    @cached_property
    def _tz(self) -> tzinfo | None:
        return _zone(self._read(_layout.TOP_FILES["settings"]))

    def _paths(self, kind: _layout.FileKind) -> list[str]:
        self._listed.add(kind)
        return sorted(path for path in (*(p for p, data in self._files.items() if data is not None), *self._unopened)
                      if _layout.kind_of(path) == kind)

    @cached_property
    def _read_pursuits(self) -> tuple[dict[str, _codec.PursuitFile], frozenset[str]]:
        """The Pursuit files Atlas can read, and the paths of those it cannot, each logged once
        by the record that took this snapshot."""
        read, unreadable = {}, set()
        for path in self._paths("pursuit"):
            try:
                read[path] = self._value("pursuit", path)
            except Exception as error:
                if (told := (path, type(error).__name__)) not in self._told:
                    self._told.add(told)
                    log.warning("Atlas left out a Pursuit file it could not read (%s)", told[1])
                unreadable.add(path)
        return read, frozenset(unreadable)


@dataclass(frozen=True)
class _Write:
    path: str
    data: bytes | None                       # None removes the file
    base: str                                # the hash of the bytes its Snapshot read, or ABSENT


@dataclass
class ChangeSet:
    """Domain-level operations. The codec decides bytes and paths. Ops are idempotent:
    putting the same Pursuit twice yields the same bytes and seal."""
    base: Revision
    _ops: list[object] = field(default_factory=list)
    _snapshot: Snapshot | None = field(default=None, repr=False, compare=False)
    _placed: dict[PursuitId, str] = field(default_factory=dict, repr=False, compare=False)
    _unforgotten: set[str] = field(default_factory=set, repr=False, compare=False)
    _checks: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    _removed: dict[EvidenceId, str | None] = field(default_factory=dict, repr=False, compare=False)
    _purges: bool = field(default=False, repr=False, compare=False)
    _guarded: set[str] = field(default_factory=set, repr=False, compare=False)
    _avoided: frozenset[str] = field(default=frozenset(), repr=False, compare=False)
    _guards_reads: bool = field(default=False, repr=False, compare=False)
    _seen: dict[PursuitId, HistoryId] = field(default_factory=dict, repr=False, compare=False)

    def guard_reads(self) -> "ChangeSet":
        """Conflict, too, when a file the plan read changed since its Snapshot, or a file of a kind
        it listed came or went: for a plan that reads more files than it writes. The Daily Brief
        reads every Pursuit and writes only its own file."""
        self._guards_reads = True
        return self

    def avoid(self, *folders: str) -> "ChangeSet":
        """A put_session that would write or delete in one of these Session folders, as Blocked names
        them, raises Blocked and leaves this ChangeSet as it was."""
        self._avoided |= frozenset(folders)
        return self

    def put_pursuit(self, p: Pursuit) -> "ChangeSet":
        """Named after the Pursuit, or, when another Pursuit or a file Atlas cannot read holds
        that name, after it and its id. A file Atlas cannot read is never written over."""
        files, unreadable = self._snapshot._read_pursuits
        stored = {f.pursuit.id: path for path, f in files.items()}
        placed = {**stored, **self._placed}
        mine = placed.get(p.id)
        taken = {path for id, path in placed.items() if id != p.id} | unreadable
        slug = _layout.slug(p.name)
        names = itertools.chain((slug, f"{slug}-{p.id}"), (f"{slug}-{p.id}-{n}" for n in itertools.count(2)))
        target = next(path for name in names if (path := _layout.pursuit_path(p.id, name)) == mine or path not in taken)
        named = self._latest(mine, "pursuit") if mine is not None else None
        if mine is not None and mine != target:
            self._write(mine, None)
        if named is not None and named.pursuit.name != p.name:
            self._relink_notes(named.pursuit.name, p.name)
        self._placed[p.id] = target
        known = {e: ref for f in files.values() for e, ref in f.sessions.items()}
        listed = {m.evidence for m in p.sessions} | set(p.resume.reopen)
        refs = {e: known[e] if e in known else self._session_ref(e) for e in sorted(listed)}
        return self._put(target, "pursuit", _codec.PursuitFile(p, refs))

    def require_sessions(self, ids: Sequence[EvidenceId]) -> "ChangeSet":
        """The commit fails with Conflict unless each of these Sessions is still held as this
        Snapshot read it, so a Forget or a fold that commits meanwhile makes the caller plan
        again without it. Raises Conflict at once for a Session already gone."""
        for id in ids:
            folder = self._snapshot._evidence_folder(id)
            if folder is None:
                raise Conflict(f"the evidence of {id}")
            self._require(folder + "session.md")
        return self

    def _session_ref(self, id: EvidenceId) -> _codec.SessionRef:
        s = self._snapshot.session(id)
        return _codec.SessionRef(s.harness.label, s.title, _codec.local(s.started, self._tz()).date())

    def remove_pursuit(self, id: PursuitId, merged_into: PursuitId | None) -> "ChangeSet":
        """Removes its file. With `merged_into`, the Notes about it and the briefs that name it
        name that Pursuit instead, in the same commit."""
        files = self._snapshot._read_pursuits[0]
        path = self._placed.pop(id, None) or next(p for p, f in files.items() if f.pursuit.id == id)
        gone = self._latest(path, "pursuit").pursuit
        self._write(path, None)
        if merged_into is not None:
            into = self._latest(self._placed.get(merged_into) or next(
                p for p, f in files.items() if f.pursuit.id == merged_into), "pursuit").pursuit
            self._relink_notes(gone.name, into.name)
            for brief in self._snapshot._paths("brief"):
                b = self._latest(brief, "brief")
                moved = _relinked(b, id, merged_into)
                if moved != b:
                    self._put(brief, "brief", moved)
        return self

    def _relink_notes(self, old: str, new: str) -> None:
        """Every Note about the Pursuit named `old` names `new` instead."""
        named = privacy.scrub(old)
        for path in self._snapshot._paths("notes"):
            notes = self._latest(path, "notes")
            entries = tuple(replace(e, about_name=new) if e.about_name and privacy.scrub(e.about_name) == named else e
                            for e in notes.entries)
            if entries != notes.entries:
                self._put(path, "notes", replace(notes, entries=entries))

    def remove_journal(self, day: date) -> "ChangeSet":
        return self._write(_layout.journal_path(day), None)

    def put_journal(self, day: JournalDay) -> "ChangeSet":
        """Writes journal/<day>.md, naming each Pursuit its Active Time lines show as the record
        names it now, and each of the day's Sessions that more than one Pursuit lists. The
        commit fails with Conflict unless every Session the day is counted from or cites is
        still held as this Snapshot read it, so a Journal day never outlives a word of its
        Sessions."""
        self.require_sessions([*day.evidence, *(id for s in day.lines for id in cites(s) if harness_of(id))])
        pursuits = {p.id: p for p in self._snapshot.pursuits()}
        shown = [key for split in day.time.areas.values() for key in split if key != ONE_OFF]
        names = {key: pursuits[key].name for key in shown}
        listed: dict[EvidenceId, list[Pursuit]] = {}
        for p in sorted(pursuits.values(), key=lambda p: shown.index(p.id) if p.id in shown else len(shown)):
            for m in p.sessions:
                if m.advances:
                    listed.setdefault(m.evidence, []).append(p)
        shared = " ".join(self._shared(id, day.day, [p.name for p in listed[id]])
                          for id in day.evidence if len(listed.get(id, ())) > 1)
        return self._put(_layout.journal_path(day.day), "journal",
                         _codec.JournalFile(day, names, shared, self._snapshot.settings().machine))

    def _shared(self, id: EvidenceId, day: date, names: Sequence[str]) -> str:
        """The sentence naming a Session that several Pursuits list, by its Harness and its first
        time that day: Codex 12:30 belongs to both A and B."""
        s = self._snapshot.session(id)
        tz = self._tz()
        first = min((t.at for t in s.turns if t.at and _codec.local(t.at, tz).date() == day), default=s.started)
        label = s.harness.label + (f" via {s.interface}" if s.interface else "")
        whom = f"both {names[0]} and {names[1]}" if len(names) == 2 else ", ".join(names[:-1]) + f" and {names[-1]}"
        return f"{label} {_codec.local(first, tz):%H:%M} belongs to {whom}."

    def put_profile(self, p: Profile) -> "ChangeSet":
        return self._put(_layout.TOP_FILES["profile"], "profile", p)

    def append_note(self, n: Note) -> "ChangeSet":
        """Appends to notes/<yyyy>/<mm>/<date>-<via>.md (one file per day per origin)."""
        via = "hand-edits" if n.origin is NoteOrigin.HAND_EDIT else n.via
        day = _codec.local(n.at, self._tz()).date()
        path = _layout.notes_path(day, via)
        current = self._latest(path, "notes") or _codec.NotesFile(day, via, ())
        if any(e.note.id == n.id for e in current.entries):
            return self
        names = {p.id: p.name for p in self._snapshot.pursuits()}
        entry = _codec.NoteEntry(replace(n, about=None), names[n.about] if n.about else None)
        return self._put(path, "notes", _codec.NotesFile(current.day, via, current.entries + (entry,)))

    def put_note(self, n: Note) -> "ChangeSet":
        """Writes again a Note the record holds, such as one whose "apply" was offered or made."""
        via = "hand-edits" if n.origin is NoteOrigin.HAND_EDIT else n.via
        path = _layout.notes_path(_codec.local(n.at, self._tz()).date(), via)
        current = self._latest(path, "notes")
        entries = tuple(replace(e, note=replace(n, about=None)) if e.note.id == n.id else e for e in current.entries)
        return self._put(path, "notes", replace(current, entries=entries))

    def remove_note(self, n: Note) -> "ChangeSet":
        """Takes a Note the record holds out of its Notes file, and the file away once it holds no other."""
        via = "hand-edits" if n.origin is NoteOrigin.HAND_EDIT else n.via
        path = _layout.notes_path(_codec.local(n.at, self._tz()).date(), via)
        current = self._latest(path, "notes")
        entries = tuple(e for e in current.entries if e.note.id != n.id)
        return self._put(path, "notes", replace(current, entries=entries)) if entries else self._write(path, None)

    def put_session(self, s: SessionEvidence, raw: Mapping[str, bytes],
                    folds: Mapping[EvidenceId, str] | None = None,
                    skip_keys: frozenset[str] = frozenset(), alone: bool = False) -> "ChangeSet":
        """New or grown Session. Each part in `raw` maps RawPart.name to the part's whole copy now:
        the copy the record holds of it (Snapshot.copy), then the lines read that the copy lacked.
        What it adds to the copy at its place becomes a new zstd frame, so a raw copy only grows
        and only Forget removes from it. A copy that does not start with the one held raises
        Conflict and leaves this ChangeSet as it was. `s.turns` are every turn of the parts in
        `raw`, parsed from those copies, and replace the turns those parts had; parts not in `raw`
        keep theirs, so a Session grows one part at a time and an Interface once noted stays
        noted. A Session read `alone` goes where the record holds its own transcript, inside
        another Session's evidence, under the key it was folded in with, as that Session's agent
        turns; with that evidence gone it raises Conflict, so a Forget of that Session is never
        undone. Any other put takes each of its parts out of another Session's evidence that holds
        it folded, so a Session T3 Code no longer folds there (a fork, or another parent) leaves
        it. `folds` names Sessions held as their own that now fold into this one, each with the
        part key it folds in under: every part they hold moves here, their turns become the
        agent's, and their folders go. A Session whose card was edited by hand is not folded: it
        stays its own, logged, so the edit is not lost, and the commit deletes a folded Session's
        card only while it holds its seal and what the commit compared. session.md is regenerated.
        The commit fails with Conflict when forgotten.md then holds the Session's skip key or one of
        `skip_keys`. A put that writes or deletes in a folder this ChangeSet avoids raises Blocked, and
        so does one that reads a Session folder a link stands in the way of: a fold, or a part taken
        out of another Session, waits for that folder, so no part is held twice once it is back.
        Putting the same Session twice changes nothing."""
        before = list(self._ops), dict(self._removed), set(self._guarded), set(self._unforgotten)
        try:
            self._put_session(s, raw, folds or {}, skip_keys, alone)
            if held := self._avoided & {*map(_txn.scope, (op.path for op in self._ops)), *self._removed.values()}:
                raise Blocked(*sorted(held))
        except Conflict:
            self._ops, self._removed, self._guarded, self._unforgotten = before
            raise
        return self

    def _put_session(self, s: SessionEvidence, raw: Mapping[str, bytes], folds: Mapping[EvidenceId, str],
                     skip_keys: frozenset[str], alone: bool) -> "ChangeSet":
        guard = {skip_key(s.harness, s.native_key), *skip_keys}
        into = None
        if alone:
            home = self._snapshot.held().home(s.harness, s.native_key)
            folder = self._snapshot._evidence_folder(home.evidence) if home else None
            into = self._session_at(folder) if folder else None
            if into is None:
                raise Conflict(f"the evidence that holds {s.id}")
            s, raw = _rehomed(s, raw, next((t.part for t in into.turns if part_file(t.part) == home.name),
                                           home.name.removesuffix(".jsonl.zst")))
        else:
            folder = self._snapshot._evidence_folder(s.id) or _layout.evidence_dir(
                s.id, s.harness, f"{_codec.local(s.started, self._tz()):%Y-%m}")
        before = into or self._session_at(folder)
        parts = {p.name: p for p in before.parts} if before else {}
        turns = list(before.turns) if before else []
        last = [s.last_at, *([before.last_at] if before else [])]
        for id, key in folds.items():
            if (moved := self._fold_in(id, key, folder, parts)) is not None:
                turns = list(_in_order(turns, moved.turns, moved.turns, set()))
                last.append(moved.last_at)
        for part in s.parts:
            self._take_out(part.origin, folder)
            copy = self._hold(f"{folder}raw/{part.name}", raw[part.name])
            parts[part.name] = replace(part, sha256=hashlib.sha256(copy).hexdigest())
        top = into or replace(s, interface=s.interface or (before.interface if before else None))
        merged = replace(top, parts=tuple(sorted(parts.values(), key=lambda p: (p.name != MAIN_FILE, p.name))),
                         turns=_in_order(turns, s.turns, _codec.shown(top, s.turns, self._tz()),
                                         {p.name for p in s.parts}),
                         last_at=max(last, key=datetime.timestamp))
        self._unforgotten |= guard
        return self._write(folder + "session.md", _codec.render("session", merged, self._tz()))

    def _fold_in(self, id: EvidenceId, key: str, folder: str, parts: dict[str, RawPart]) -> SessionEvidence | None:
        """Moves a Session held as its own into `folder` under `key`, as the fold names it: its own
        parts (its transcript and the files that are part of it) go under the key, and the
        Sessions folded into it keep their names. A part the folder already holds keeps the copy
        there, which has grown from this one. Returns its turns as they read here."""
        source = self._snapshot._evidence_folder(id)
        e = self._session_at(source) if source and source != folder else None
        if e is None:
            return None
        card = self._bytes(source + "card.md")
        if card is not None and not _seal.verifies(card):
            log.warning("Atlas kept a folded Session as its own because its card was edited by hand")
            return None
        own = part_origin(e.harness, e.native_key, "")
        names, kept = {}, set()
        for p in e.parts:
            if p.origin.startswith(own):
                names[p.name] = part_file(_under(key, p.origin[len(own):]))
            else:
                names[p.name] = part_file(key) if not p.origin and p.name == MAIN_FILE else p.name
            stored = self._bytes(f"{source}raw/{p.name}")
            if stored is None:
                continue
            dest = f"{folder}raw/{names[p.name]}"
            if self._bytes(dest) is None:
                self._write(dest, stored)
            else:
                kept.add(names[p.name])
            parts[names[p.name]] = replace(parts.get(names[p.name], p), name=names[p.name],
                                           sha256=hashlib.sha256(_unzstd(self._bytes(dest))).hexdigest())
        self._remove(id)
        self._guarded.add(source + "card.md")
        turns = (replace(t, role="agent", part=_moved(t.part, key, names)) for t in e.turns)
        return replace(e, turns=tuple(t for t in turns if part_file(t.part) not in kept))

    def _take_out(self, origin: str, folder: str) -> None:
        """Removes the part with this origin from another Session's evidence that holds it
        folded: its raw copy, its entry and its turns. A Session's own part stays where it is."""
        h = self._snapshot.held().get(origin)
        source = self._snapshot._evidence_folder(h.evidence) if h else None
        e = self._session_at(source) if source and source != folder else None
        if e is None or origin.startswith(part_origin(e.harness, e.native_key, "")):
            return
        name = next((p.name for p in e.parts if p.origin == origin), None)
        if name is None:
            return
        turns = tuple(t for t in e.turns if part_file(t.part) != name)
        self._write(f"{source}raw/{name}", None)
        self._write(source + "session.md", _codec.render("session", replace(
            e, parts=tuple(p for p in e.parts if p.name != name), turns=turns,
            last_at=max([e.started, *(t.at for t in turns if t.at)])), self._tz()))

    def _session_at(self, folder: str) -> SessionEvidence | None:
        """The Session held in `folder`. Raises Blocked when a link or a file stands where that folder is."""
        held = self._bytes(folder + "session.md")
        if held is None and not _txn.reachable(self._snapshot._root, folder + "session.md"):
            raise Blocked(folder)
        return _codec.parse("session", held, self._tz()) if held is not None else None

    def _hold(self, path: str, data: bytes) -> bytes:
        """Adds what `data` holds beyond the raw copy at `path` as a new frame, and returns the copy now
        held. Raises Conflict when `data` does not start with that copy."""
        stored = self._bytes(path)
        old = _unzstd(stored) if stored is not None else b""
        if not data.startswith(old):
            raise Conflict(path)
        if len(data) > len(old):
            self._write(path, (stored or b"") + zstandard.ZstdCompressor().compress(data[len(old):]))
        return data

    def put_card(self, c: SessionCard) -> "ChangeSet":
        """Writes card.md beside the Session's transcript. The commit fails with Conflict when
        the Session is gone or changed by then, so a card never outlives its Session."""
        self.require_sessions([c.evidence])
        return self._put(self._snapshot._evidence_folder(c.evidence) + "card.md", "card", c)

    def mark_one_off(self, id: EvidenceId, area: Area, day: date) -> "ChangeSet":
        """Marks a Session as a one-off in its card, filed on `day`. Raises KeyError for a Session
        with no card."""
        folder = self._snapshot._evidence_folder(id)
        card = self._latest(folder + "card.md", "card") if folder else None
        if card is None:
            raise KeyError(id)
        return self.put_card(replace(card, one_off_area=area, one_off_day=day))

    def remove_evidence(self, id: EvidenceId) -> "ChangeSet":
        """Deletes the Session's evidence folder: session.md, card.md and every raw part session.md
        names, each only while it holds what this snapshot read, and every other file and link in the
        folder as the commit finds it, also one another commit put there after this was planned. A
        link goes as itself: what it points to is never read or deleted, and the commit raises
        Conflict when a link stands where the folder or its mirror is. The commit also deletes the
        model outputs cached from it, its rows in the index, and the masking pass's staged copies
        of the files it writes."""
        self._purges = True
        return self._remove(id)

    def _remove(self, id: EvidenceId) -> "ChangeSet":
        folder = self._snapshot._evidence_folder(id)
        self._removed[id] = folder
        if folder is None:
            return self
        held = self._bytes(folder + "session.md")
        parts = (_codec.session_front(held)["parts"] or ()) if held is not None else ()
        for name in ("session.md", "card.md", *(f"raw/{part['name']}" for part in parts)):
            if self._bytes(folder + name) is not None:
                self._write(folder + name, None)
        return self
    def put_brief(self, b: SentBrief) -> "ChangeSet":
        """Creating a brief file whose base is 'absent' is the send guard: two ticks racing to
        send the same day's brief -> the second commit raises Conflict and does not send."""
        return self._put(_layout.brief_path(b.day), "brief", b)
    def put_review(self, r: SentReview) -> "ChangeSet": raise NotImplementedError

    def put_pauses(self, pauses: Sequence[Pause]) -> "ChangeSet":
        return self._put(_layout.TOP_FILES["pauses"], "pauses", tuple(pauses))

    def put_forgotten(self, f: Forgotten) -> "ChangeSet":
        return self._put(_layout.TOP_FILES["forgotten"], "forgotten", f)

    def put_settings(self, s: Settings) -> "ChangeSet":
        current = self._tz()
        if current is not None and str(current) != s.timezone:
            raise _codec.Unrepresentable(
                "a new timezone: body times are wall time in settings.timezone, so changing it "
                "must rewrite every file with body times in the same commit")
        return self._put(_layout.TOP_FILES["settings"], "settings", s)

    def set_pending_forget(self, plan: ForgetPlan | None) -> "ChangeSet":
        data = None if plan is None else json.dumps(
            {"evidence": list(plan.evidence), "asked_at": plan.asked_at.isoformat()}).encode()
        return self._write(_layout.PENDING_FORGET, data)

    def set_filing_retries(self, retries: Mapping[EvidenceId, FilingRetry]) -> "ChangeSet":
        data = json.dumps({id: {"last_at": r.last_at.isoformat(), "failures": r.failures,
                                "retry_at": r.retry_at.isoformat()} for id, r in sorted(retries.items())},
                          sort_keys=True).encode() if retries else None
        return self._write(_layout.FILING_RETRY, data)

    def put_imports(self, log: Sequence[ImportLog]) -> "ChangeSet":
        """The import log: every entry of a file kept in the inbox, which the tick drops once the
        file leaves it, and the newest IMPORTS_KEPT of the files deleted."""
        deleted = [e for e in log if e.deleted][-IMPORTS_KEPT:]
        data = json.dumps([{"harness": e.harness.value if e.harness else None, "at": e.at.isoformat(), "sessions": e.sessions,
                            "deleted": e.deleted, "failure": e.failure, "file": e.file, "broken": e.broken,
                            "skipped": e.skipped}
                           for e in log if not e.deleted or e in deleted]).encode()
        return self._write(_layout.IMPORTS, data)

    def mark_seen(self, marks: Mapping[PursuitId, HistoryId]) -> "ChangeSet":
        """Merged into seen.json as the commit finds it, never compared: a mark only moves forward,
        so concurrent Board posts converge instead of conflicting."""
        self._seen = _merged_seen(self._seen, marks)
        return self

    def is_empty(self) -> bool:
        return not self._ops and not self._seen

    def _require(self, path: str) -> None:
        if not any(op.path == path for op in self._ops):
            self._checks.setdefault(path, _base_of(self._snapshot._read(path)))

    def _put(self, path: str, kind: _layout.FileKind, value: object) -> "ChangeSet":
        return self._write(path, _seal.stamp(_codec.render(kind, value, self._tz())))

    def _write(self, path: str, data: bytes | None) -> "ChangeSet":
        earlier = next((op for op in self._ops if op.path == path), None)
        base = earlier.base if earlier else _base_of(self._snapshot._read(path))
        self._ops = [op for op in self._ops if op.path != path] + [_Write(path, data, base)]
        return self

    def _latest(self, path: str, kind: _layout.FileKind):
        data = self._bytes(path)
        return _codec.parse(kind, data, self._tz()) if data is not None else None

    def _tz(self) -> tzinfo | None:
        """The zone this ChangeSet writes in: a settings.md it puts wins over the Snapshot's."""
        return _zone(self._bytes(_layout.TOP_FILES["settings"]))

    def _bytes(self, path: str) -> bytes | None:
        earlier = next((op for op in self._ops if op.path == path), None)
        return earlier.data if earlier else self._snapshot._read(path)


def _rewritten(path: str, copy: bytes, zone: tzinfo | None) -> bytes:
    """The last-written copy as the codec writes it now, so a copy an older Atlas wrote bare comes
    back masked; the copy itself when the codec cannot read it."""
    kind = _layout.kind_of(path)
    try:
        return _seal.stamp(_codec.render(kind, _codec.parse(kind, copy, zone), zone))
    except (ValueError, KeyError, TypeError):
        return copy


_EVIDENCE_IDS = re.compile(r"\b[a-z0-9]+-[a-z0-9]{8,}\b")


def sessions_cited(*texts: str) -> tuple[EvidenceId, ...]:
    """The Sessions whose ids the texts hold, in order of first appearance."""
    return tuple(dict.fromkeys(EvidenceId(m) for t in texts for m in _EVIDENCE_IDS.findall(t) if harness_of(EvidenceId(m))))


def _hand_edit_note(f: _seal.Found, at: datetime) -> Note:
    """The Note that keeps a file edited by hand: its text, the lines that differ from what Atlas
    last wrote, and the Sessions either version cites."""
    text = _as_text(f.current)
    before = _as_text(f.mirror)
    changed = [line for line in list(difflib.unified_diff(before.splitlines(), text.splitlines(), n=0, lineterm=""))[2:]
               if not line.startswith("@@")]
    digest = hashlib.sha256(f"{f.path}:{at.isoformat()}:".encode() + f.current).digest()
    return Note(EvidenceId("nt-" + base64.b32encode(digest).decode().lower()[:8]), at, NoteOrigin.HAND_EDIT, f.path,
                text, diff="".join(line + "\n" for line in changed), derived_from=sessions_cited(text, before))


def _as_text(data: bytes) -> str:
    text = data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    return text if not text or text.endswith("\n") else text + "\n"


def _relinked(b: SentBrief, old: PursuitId, new: PursuitId) -> SentBrief:
    """The brief with every mention of Pursuit `old` naming `new`; its sent text stays as sent."""
    swap = lambda x: replace(x, pursuit=new) if x.pursuit == old else x
    return replace(b, followups=tuple(map(swap, b.followups)), steps=tuple(map(swap, b.steps)),
                   decision=swap(b.decision) if b.decision else None)


def _evidence_of(scope: str) -> str:
    """The Session folder whose folder or mirror `scope` is, else `scope`."""
    return scope.removeprefix(f"{_layout.MIRROR}/")


def _rehomed(s: SessionEvidence, raw: Mapping[str, bytes], key: str) -> tuple[SessionEvidence, dict[str, bytes]]:
    """A Session put as folded under `key`: its own parts go under the key, the Sessions folded
    into it keep their names, and every turn is the agent's."""
    own = part_origin(s.harness, s.native_key, "")
    names = {p.name: part_file(_under(key, p.origin[len(own):])) if p.origin.startswith(own) else p.name
             for p in s.parts}
    return (replace(s, parts=tuple(replace(p, name=names[p.name]) for p in s.parts),
                    turns=tuple(replace(t, role="agent", part=_moved(t.part, key, names)) for t in s.turns)),
            {names[name]: data for name, data in raw.items()})


def _in_order(held: Sequence[Turn], read: Sequence[Turn], shown: Sequence[Turn], parts: set[str]) -> tuple[Turn, ...]:
    """The turns session.md holds, with those of `parts` replaced by `read`; `shown` is `read` as
    session.md shows it. In each part, the turns read are paired with the held turns they show as
    (part, role, minute and text) by a longest common subsequence, and each takes its pair's
    place, so session.md's order holds for every turn paired. A turn read that pairs with none
    goes in by time after the turns held at or before its minute, between its part's neighbours."""
    held_of: dict[str, list[int]] = {}
    read_of: dict[str, list[int]] = {}
    for n, t in enumerate(held):
        if part_file(t.part) in parts:
            held_of.setdefault(part_file(t.part), []).append(n)
    for i, t in enumerate(read):
        read_of.setdefault(part_file(t.part), []).append(i)
    of = {}
    for part, rs in read_of.items():
        hs = held_of.get(part, [])
        for a, b in _paired([_shows(held[n]) for n in hs], [_shows(shown[i]) for i in rs]):
            of[rs[b]] = hs[a]
    by_held = {n: read[i] for i, n in of.items()}
    base = [(by_held.get(n, t), _when(t), n) for n, t in enumerate(held)
            if part_file(t.part) not in parts or n in by_held]
    at = {n: k for k, (_, _, n) in enumerate(base)}
    whens = list(itertools.accumulate((w for _, w, _ in base), max))
    ceiling, until = [len(base)] * len(read), {}
    for i in reversed(range(len(read))):
        ceiling[i] = until.get(part_file(read[i].part), len(base))
        if i in of:
            until[part_file(read[i].part)] = at[of[i]]
    gaps, floor = [[] for _ in range(len(base) + 1)], {}
    for i, t in enumerate(read):
        if i in of:
            floor[part_file(t.part)] = at[of[i]] + 1
            continue
        gap = min(max(bisect.bisect_right(whens, _when(t)), floor.get(part_file(t.part), 0)), ceiling[i])
        floor[part_file(t.part)] = gap
        gaps[gap].append(t)
    return (*gaps[0], *itertools.chain.from_iterable((t, *gap) for (t, _, _), gap in zip(base, gaps[1:])))


def _paired(held: Sequence[tuple], read: Sequence[tuple]) -> list[tuple[int, int]]:
    """The (held, read) index pairs of a longest common subsequence of `held` and `read`."""
    same = next((n for n, (h, r) in enumerate(zip(held, read)) if h != r), min(len(held), len(read)))
    h, r = held[same:], read[same:]
    once = Counter(h) + Counter(r)
    end = 0
    while end < min(len(h), len(r)) and h[-1 - end] == r[-1 - end] and once[r[-1 - end]] == 2:
        end += 1
    h, r = h[:len(h) - end], r[:len(r) - end]
    longest = [[0] * (len(r) + 1) for _ in range(len(h) + 1)]
    for i in reversed(range(len(h))):
        row, below = longest[i], longest[i + 1]
        for j in reversed(range(len(r))):
            row[j] = below[j + 1] + 1 if h[i] == r[j] else max(below[j], row[j + 1])
    pairs, i, j = [(n, n) for n in range(same)], 0, 0
    while i < len(h) and j < len(r):
        if h[i] == r[j]:
            pairs.append((same + i, same + j))
            i, j = i + 1, j + 1
        elif longest[i][j + 1] >= longest[i + 1][j]:
            j += 1
        else:
            i += 1
    return pairs + [(same + len(h) + n, same + len(r) + n) for n in range(end)]


def _shows(t: Turn) -> tuple:
    return part_file(t.part), t.role, t.at, t.text


def _when(t: Turn) -> tuple:
    return (1,) if t.at is None else (0, t.at.timestamp() // 60)


def _under(key: str, part: str) -> str:
    return key if part == "main" else f"{key}/{part}"


def _moved(part: str, key: str, names: Mapping[str, str]) -> str:
    """A folded Session's turn part key, once its parts moved under `key`."""
    return _under(key, part) if names.get(part_file(part)) == part_file(_under(key, part)) else part


class Record:
    """Owner of every byte under the record root."""

    def __init__(self, root: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.root = root
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._swept = False
        self._told: set[tuple[str, str]] = set()
        self._waiting: set[str] = set()

    @classmethod
    def open(cls, root: Path, clock: Callable[[], datetime] | None = None) -> "Record":
        """Creates nothing (setup does). Rolls forward ready transactions, deletes unready
        staging, checks .atlas/HEAD against the index generation and re-indexes changed files
        (stat sweep). Safe to call concurrently from many processes. `clock` times the hand-edit
        Notes this record writes."""
        record = cls(Path(root), clock)
        if _txn.machinery(record.root).is_dir():
            with _txn.write_lock(record.root):
                try:
                    record._recover()
                except OSError as failed:
                    log.warning("A committed transaction could not be applied yet (%s); the next commit "
                                "tries again", type(failed).__name__)
        return record

    def snapshot(self) -> Snapshot:
        """Reads every record file under the write lock, so the view is one Revision even
        while other processes commit. A Pursuit file it cannot open is kept as such, so the
        Pursuits read it as a file Atlas cannot read. A ready transaction not applied yet is part
        of it."""
        if not _txn.machinery(self.root).is_dir():
            return Snapshot(self.root, Revision(0), {})
        with _txn.write_lock(self.root):
            files, unopened = {}, {}
            for path in self._listing():
                try:
                    files[path] = (self.root / path).read_bytes()
                except FileNotFoundError:
                    continue
                except OSError as error:
                    if _layout.kind_of(path) != "pursuit":
                        raise
                    unopened[path] = error
            held = _txn.pending(self.root)
            for path, data in held.items():
                if data is None:
                    files.pop(path, None)
                elif _layout.in_snapshot(path):
                    files[path] = data
            head = held.get(_layout.HEAD)
            return Snapshot(self.root, Revision(int(head) if head else self._head()), files, unopened, self._told)

    def commit(self, cs: ChangeSet) -> Revision:
        """All-or-nothing, serialized, compare-and-swap. Raises Conflict, also when forgotten.md
        holds a skip key a put Session requires it not to. Raises Blocked, naming every such Session
        folder, when a link stands where a folder it writes in is. A link that takes that place after
        staging leaves the commit ready, its other writes made and no delete, for a replay to finish
        once the link is gone, and raises Blocked; until then a commit that writes or deletes in a
        Session folder it has left raises Blocked. A write that leaves a file's bytes as they are
        drops out, and a commit with nothing left changes nothing."""
        with _txn.write_lock(self.root):
            self._recover()
            self._absorb()
            if cs._guards_reads:
                self._check_reads(cs._snapshot)
            if cs._unforgotten and self._forgets(cs._unforgotten):
                raise Conflict(_layout.TOP_FILES["forgotten"])
            for path, base in cs._checks.items():
                if not any(op.path == path for op in cs._ops) and _base_of(self._read(path)) != base:
                    raise Conflict(path)
            files: dict[str, bytes | None] = {}
            guards: dict[str, str] = {}
            for op in cs._ops:
                current = self._read(op.path)
                if _base_of(current) != op.base:
                    raise Conflict(op.path)
                if current == op.data:
                    continue
                files[op.path] = op.data
                if _layout.RESTORE.get(_layout.kind_of(op.path)) == "mirror":
                    files[_layout.mirror_path(op.path)] = op.data
            if cs._seen:
                current = self._read(_layout.SEEN)
                data = _seen_bytes(_merged_seen(_seen_marks(current), cs._seen))
                if data != current:
                    files[_layout.SEEN] = data
            written = {op.path for op in cs._ops if op.data is not None}
            held: set[str] = set()
            for folder in filter(None, cs._removed.values()):
                if (found := self._files_and_mirrors(folder)) is None:
                    held.add(folder)
                    continue
                for path in found:
                    if path not in written:
                        files.setdefault(path, None)
            held |= {_evidence_of(_txn.scope(path)) for path, data in files.items()
                     if _txn.scope(path) in self._waiting or (data is not None and not _txn.reachable(self.root, path))}
            if held:
                raise Blocked(*sorted(held))
            for path in cs._guarded:
                if (current := self._read(path)) is not None:
                    if not _seal.verifies(current):
                        raise Conflict(path)
                    guards[path] = hashlib.sha256(current).hexdigest()
            head = self._head()
            if not files:
                return Revision(head)
            self._uncache(set(cs._removed))
            if cs._removed:
                _index.drop(_txn.machinery(self.root), set(cs._removed))
            files[_layout.HEAD] = f"{head + 1}\n".encode()
            if cs._purges:
                _masking.forget(self.root, files)
            txn = _txn.stage(self.root, files, guards)
            try:
                _txn.apply(self.root, txn)
            except _txn.Changed as error:
                _txn.discard(self.root, txn)
                raise Conflict(str(error)) from error
            except _txn.Linked as error:
                raise Blocked(*sorted({_evidence_of(scope) for scope in error.args})) from error
            _masking.drop(self.root, files)
            return Revision(head + 1)

    def reconcile(self) -> Sequence[HandEdit]:
        """Puts back every record file changed outside Atlas, as every commit does first. Called
        at the start of every tick and by Atlas.open in every process."""
        if not _txn.machinery(self.root).is_dir():
            return ()
        try:
            with _txn.write_lock(self.root):
                self._recover()
                return self._absorb()
        except OSError as failed:
            log.warning("Atlas could not look for files edited by hand (%s)", type(failed).__name__)
            return ()

    def create(self, settings: Settings, profile: Profile) -> Revision:
        """Setup: README.md, settings.md, profile.md, pauses.md, forgotten.md, folders,
        .atlas/. Idempotent: existing files are left alone. Raises NotLocal on a network mount."""
        _txn.require_local(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.stat().st_mode & 0o777 != 0o700:
            os.chmod(self.root, 0o700)
        for folder in (*_layout.FOLDERS, _txn.MACHINERY):
            (self.root / folder).mkdir(exist_ok=True)
        first = {"readme": None, "settings": settings, "profile": profile, "pauses": (),
                 "forgotten": Forgotten((), frozenset())}
        snap = self.snapshot()
        cs = snap.changes()
        for kind, path in _layout.TOP_FILES.items():
            if snap._read(path) is None:
                cs._put(path, kind, first[kind])
        return self.commit(cs)

    def cache_get(self, key: str) -> bytes | None:
        try:
            return json.loads(self._cache_file(key).read_text())["value"].encode()
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return None

    def cache_put(self, key: str, value: bytes, evidence: Sequence[EvidenceId]) -> None:
        """LLM-output cache under .atlas/cache/, tagged with the Evidence ids in the input so
        Forget can purge it. Disposable. Written under the write lock and only while the record
        holds every one of those Sessions, so an output read from a Session a commit has removed
        is never kept."""
        folder = _txn.machinery(self.root) / CACHE
        with _txn.write_lock(self.root):
            self._recover()
            if not all(self._holds(id) for id in evidence):
                return
            folder.mkdir(exist_ok=True)
            staged = folder / f".{key}.tmp"
            staged.write_text(json.dumps({"evidence": sorted(evidence), "value": value.decode()}))
            os.replace(staged, self._cache_file(key))

    def uncache(self, evidence: Sequence[EvidenceId]) -> None:
        """Deletes every cached model output read from one of these Sessions."""
        with _txn.write_lock(self.root):
            self._uncache(set(evidence))

    def reindex(self) -> int:
        """Rebuilds .atlas/index.sqlite from the files; the number of Sessions it holds. Always safe."""
        return self.snapshot().index()

    def _absorb(self) -> list[HandEdit]:
        """Under the write lock, in one transaction: each record file edited by hand or deleted is
        written again from its last-written copy through the codec, so it comes back masked. A
        hand-edit Note keeps each edited file's text and diff. A file Atlas did not write is left
        as it is. Never raises: a failure is logged and the next commit tries again."""
        try:
            found = _seal.sweep(self.root)
            if not found:
                return []
            return self._put_back(found)
        except (OSError, ValueError, _txn.Changed, _txn.Linked) as failed:
            log.warning("Atlas could not put back a file edited by hand (%s); it tries again at the next commit",
                        type(failed).__name__)
            return []

    def _put_back(self, found: Sequence[_seal.Found]) -> list[HandEdit]:
        mirrored = self._read(_layout.mirror_path(_layout.TOP_FILES["settings"]))
        zone = _zone(mirrored or self._read(_layout.TOP_FILES["settings"]))
        at = self._clock().astimezone(zone).replace(second=0, microsecond=0)
        path = _layout.notes_path(at.date(), _codec.HAND_EDITS)
        if _seal.linked(self.root, path):
            log.warning("Atlas did not put back files edited by hand: a link stands where its notes folder is")
            return []
        files: dict[str, bytes] = {}
        notes: list[Note] = []
        for f in found:
            files[f.path] = files[_layout.mirror_path(f.path)] = _rewritten(f.path, f.mirror, zone)
            if f.current is not None:
                notes.append(_hand_edit_note(f, at))
            log.info("Atlas put back a record file %s", "edited by hand" if f.current is not None else "deleted")
        if notes:
            held = files.get(path) or self._read(path)
            day = _codec.parse("notes", held, zone) if held else _codec.NotesFile(at.date(), _codec.HAND_EDITS, ())
            held_ids = {e.note.id for e in day.entries}
            day = replace(day, entries=day.entries + tuple(_codec.NoteEntry(n) for n in notes if n.id not in held_ids))
            files[path] = files[_layout.mirror_path(path)] = _seal.stamp(_codec.render("notes", day, zone))
        files[_layout.HEAD] = f"{self._head() + 1}\n".encode()
        txn = _txn.stage(self.root, files)
        try:
            _txn.apply(self.root, txn)
        except _txn.Changed:
            _txn.discard(self.root, txn)
            raise
        _masking.drop(self.root, files)
        return [HandEdit(n.via, n.id, n.at) for n in notes]

    def _check_reads(self, snap: Snapshot) -> None:
        for path in sorted(snap._reads):
            try:
                now = _base_of(self._read(path))
            except OSError as error:
                now = type(error).__name__
            then = type(snap._unopened[path]).__name__ if path in snap._unopened else _base_of(snap._files.get(path))
            if now != then:
                raise Conflict(path)
        listed = set(self._listing())
        held = {p for p, data in snap._files.items() if data is not None} | set(snap._unopened)
        for kind in sorted(snap._listed):
            if {p for p in listed if _layout.kind_of(p) == kind} != {p for p in held if _layout.kind_of(p) == kind}:
                raise Conflict(kind)

    def _listing(self) -> list[str]:
        """The record-relative paths a Snapshot holds: the top files and the Markdown files of the
        snapshot folders."""
        tops = [self.root / path for path in (*_layout.TOP_FILES.values(), _layout.SEEN)]
        nested = [p for folder in _layout.SNAPSHOT_FOLDERS for p in (self.root / folder).glob("**/*.md")]
        pending = (self.root / _layout.PENDING_FORGET, self.root / _layout.FILING_RETRY, self.root / _layout.IMPORTS)
        return [str(p.relative_to(self.root)) for p in (*tops, *nested, *pending) if p.is_file()]

    def _read(self, path: str) -> bytes | None:
        if _layout.session_folder(path):
            return _txn.read_below(self.root, path)
        try:
            return (self.root / path).read_bytes()
        except FileNotFoundError:
            return None

    def _cache_file(self, key: str) -> Path:
        return _txn.machinery(self.root) / CACHE / f"{key}.json"

    def _recover(self) -> None:
        """Under the write lock: finishes the commits a crash or a link left ready, keeps what a link
        still holds up for the next commit to stay out of, logged once per Record, deletes the cache
        writes a crash cut short and, once per Record, sweeps the empty folders older versions left
        and masks the files they wrote bare."""
        self._waiting = _txn.roll_forward(self.root)
        if self._waiting and (told := (_txn.MACHINERY, "Linked")) not in self._told:
            self._told.add(told)
            log.warning("Atlas left a transaction ready: a link stands where a folder it writes in was")
        if not self._swept:
            self._swept = True
            _txn.sweep(self.root)
            _masking.run(self.root, self._head, _zone)
        for staged in (_txn.machinery(self.root) / CACHE).glob(".*.tmp"):
            staged.unlink(missing_ok=True)

    def _files_and_mirrors(self, folder: str) -> list[str] | None:
        """Every file and link in the Session folder and its mirror. None, logged, when a link stands
        where either folder or a folder in them is."""
        try:
            return [*_txn.files_below(self.root, folder), *_txn.files_below(self.root, _layout.mirror_path(folder))]
        except OSError as error:
            if error.errno not in _txn.UNREACHABLE:
                raise
            log.warning("Atlas left a Session's evidence in place: a link stands where its folder was")
            return None

    def _holds(self, id: EvidenceId) -> bool:
        harness = harness_of(id)
        return harness is not None and any((self.root / "evidence").glob(f"{harness.value}/*/*/{id}/session.md"))

    def _uncache(self, removed: set[EvidenceId]) -> None:
        """Deletes every cached model output read from one of these Sessions."""
        folder = _txn.machinery(self.root) / CACHE
        if not removed or not folder.is_dir():
            return
        for entry in folder.glob("*.json"):
            try:
                tagged = set(json.loads(entry.read_text())["evidence"])
            except (FileNotFoundError, ValueError, KeyError, TypeError):
                tagged = removed
            if tagged & removed:
                entry.unlink(missing_ok=True)

    def _forgets(self, keys: set[str]) -> bool:
        data = self._read(_layout.TOP_FILES["forgotten"])
        zone = _zone(self._read(_layout.TOP_FILES["settings"]))
        return data is not None and bool(_codec.parse("forgotten", data, zone).skip_keys & keys)

    def _head(self) -> int:
        data = self._read(_layout.HEAD)
        return int(data) if data else 0


def _seen_marks(data: bytes | None) -> dict[PursuitId, HistoryId]:
    """A value that is not a History id reads as no mark for that Pursuit."""
    try:
        marks = json.loads(data or b"{}")
    except ValueError:
        return {}
    if not isinstance(marks, dict):
        return {}
    return {PursuitId(k): HistoryId(v) for k, v in marks.items() if isinstance(v, str) and _HISTORY_ID.fullmatch(v)}


def _merged_seen(marks: Mapping[PursuitId, HistoryId], new: Mapping[PursuitId, HistoryId]) -> dict[PursuitId, HistoryId]:
    merged = dict(marks)
    for pursuit, upto in new.items():
        if pursuit not in merged or int(upto[2:]) > int(merged[pursuit][2:]):
            merged[pursuit] = upto
    return merged


def _seen_bytes(marks: Mapping[PursuitId, HistoryId]) -> bytes:
    return json.dumps(dict(sorted(marks.items())), indent=1).encode() + b"\n"
