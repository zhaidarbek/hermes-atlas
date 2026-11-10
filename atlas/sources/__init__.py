"""Sources: find Harnesses, read their stores read-only, and return Sessions in domain shape.
Passive capture is the primary path (R1a Q4). Nothing here writes the record. It returns
values, and atlas.app commits them.

Public: probe, collect, release, Source (the seam for future Sources such as email or a
second-machine drop), CollectedSession, Pending, Found.

Rules every adapter follows:
- Open SQLite stores as ``file:<abs>?mode=ro`` with busy_timeout 5 s and 3x50 ms retry on
  transient disk I/O. Read each store in one short read transaction, and read columns by name.
- Open a store file only as a regular file whose real path is under its store, never through a
  symlink, so no transcript name can lead to a credential file (~/.claude/.credentials.json,
  ~/.codex/auth.json, ~/.t3/userdata/secrets, Hermes .env).
- Default roots come from env overrides first ($CLAUDE_CONFIG_DIR, $CODEX_HOME, $T3CODE_HOME,
  get_hermes_home()), then the platform default, then settings.md roots. No owner path is
  hard-coded.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence

from ..model import (BoundSession, EvidenceId, HarnessId, Instruction, Sent, SessionEvidence, Settings, part_file,
                     part_origin, skip_key)
from ..record import Held, HeldSession

log = logging.getLogger("atlas.sources")
MAIN = "main"
Key = tuple[HarnessId, str]


@dataclass(frozen=True)
class Found:
    harness: HarnessId | str                  # str for Interfaces ("T3 Code")
    root: str                                 # where the Sessions live, as setup shows it ("~/.claude/projects")
    sessions: int
    since: datetime | None                    # first Session start, in the owner's timezone
    is_interface: bool = False
    roots: tuple[str, ...] = ()               # the store roots read, as settings.md records them ("~/.claude")


@dataclass(frozen=True)
class Unit:
    """One native unit as its store lists it, before any of it is read."""
    harness: HarnessId
    native_id: str                            # the store's own id, as Harness links and T3 Code name it
    native_key: str                           # SessionEvidence.native_key: what copies in two roots share
    parts: Mapping[str, tuple[str, str]]      # part key ("main", "subagent:<name>") -> (source, fingerprint now)
    size: int                                 # bytes or rows' bytes: of two copies, the larger is kept
    ref: object = field(default=None, compare=False)   # where the adapter reads it from


@dataclass(frozen=True)
class Head:
    """What a store says about a unit before its turns are read."""
    cwd: str | None                           # as the store wrote it
    parent: str | None                        # native id of the Session whose agent started it
    started: datetime


@dataclass(frozen=True)
class Fetched:
    """One unit's parts as its store holds them now, before they meet what the record holds."""
    unit: Unit
    parts: Mapping[str, tuple[str, str, bytes]]   # part key -> (source, fingerprint, bytes as read)


@dataclass(frozen=True)
class NativeSession:
    """One native unit as read, before folding."""
    harness: HarnessId
    native_id: str
    parent_native_id: str | None              # Harness's own subagent link (Codex thread_spawn, Hermes subagent)
    interface: str | None
    raw: Mapping[str, bytes]                  # part key -> its whole copy, which its turns were parsed from
    evidence: SessionEvidence                 # normalized turns + meta, id = hash(harness, native_key)


@dataclass(frozen=True)
class Lineage:
    """Interface-level facts: T3 Code app_owned delegation children are full native top-level
    Sessions that must fold into their parent (harness-stores-linux.md, CRITICAL). With no
    parent, the fact says only that the child Session ran through the Interface, and with `own`
    that the Interface names it the owner's own conversation (a T3 Code fork), so no fold the
    record remembers keeps it inside another Session."""
    child: tuple[HarnessId, str]
    parent: tuple[HarnessId, str] | None
    interface: str
    own: bool = False


@dataclass(frozen=True)
class CollectedSession:
    evidence: SessionEvidence                 # folded: one Session per owner conversation
    raw: Mapping[str, bytes]                  # RawPart.name -> the part's whole copy, its turns parsed from it
    replaces: Mapping[EvidenceId, str] = field(default_factory=dict)   # a folded Session -> its part key here
    folded: int = 0                           # subagent conversations folded into it
    skip_keys: frozenset[str] = frozenset()   # one per Session folded into it, checked against Forget at commit
    alone: bool = False                       # read without the Session that started it: put where the record holds it


class Source(Protocol):
    """Seam for every Source. v1 implements Harness adapters only; email etc. plug in here."""
    key: HarnessId

    def probe(self, s: Settings) -> Found | None: ...
    def units(self, s: Settings, sent: Sent) -> Iterable[Unit]: ...                 # reads no transcript
    def head(self, unit: Unit) -> Head | None: ...                                  # None: not a Session
    def fetch(self, unit: Unit, sent: Sent) -> Fetched | None: ...                  # None: not a Session
    def identity(self, line: dict) -> str: ...                                      # as its Harness gives it
    def parse(self, s: Settings, fetched: Fetched, held: Callable[[str], bytes | None],
              sent: Sent) -> NativeSession | None: ...
    def lineage(self, s: Settings) -> Iterable[Lineage]: ...                        # Interfaces return facts here
    def can_recopy(self, part_source: str) -> bool: ...


@dataclass(frozen=True)
class Pending:
    """One Session to read: the unit that started it and the units that fold into it. `alone`
    names a unit read without the Session that started it, which its store no longer lists: the
    record holds it folded, and puts it there. Each names the skip key of the Session whose
    evidence holds it, so a Forget of that Session stops the put and the unit is never put again
    as a Session of its own."""
    units: tuple[Unit, ...]
    lineage: tuple[Lineage, ...]
    settings: Settings
    alone: Mapping[Key, str] = field(default_factory=dict)   # (harness, native id) -> its home's skip key

    def read(self, sent: Sent) -> "Read":
        """Reads every part of every unit once. A unit that cannot be read is logged and left out,
        and without the unit that started it the Session waits for a later collect."""
        return Read(tuple(f for u in self.units if (f := _guard(u.harness.label, lambda: ADAPTERS[u.harness].fetch(
            u, sent)))), self.lineage, self.settings, self.alone)


@dataclass(frozen=True)
class Read:
    """One Session's units as read from their stores."""
    fetched: tuple[Fetched, ...]
    lineage: tuple[Lineage, ...]
    settings: Settings
    alone: Mapping[Key, str] = field(default_factory=dict)

    def sessions(self, held: Callable[[str], bytes | None], sent: Sent) -> list[CollectedSession]:
        """Each part's copy is the copy the record holds of it (`held`, by origin) and then each line
        read whose identity that copy lacks, and every turn is parsed from that copy, so a turn
        the record holds stays whatever its store has since rewritten or dropped. Then the units
        fold. A unit that cannot be parsed is logged and left out."""
        natives = [n for f in self.fetched if (n := _guard(f.unit.harness.label, lambda: self._native(f, held, sent)))]
        return resolve(natives, self.lineage, self.alone)

    def _native(self, f: Fetched, held: Callable[[str], bytes | None], sent: Sent) -> NativeSession | None:
        n = ADAPTERS[f.unit.harness].parse(self.settings, f, held, sent)
        return n if n is not None and not _excluded(n.evidence.cwd, self.settings.exclusions) else None


def probe(s: Settings) -> list[Found]:
    """Every adapter's probe plus the T3 Code Interface probe. Used by setup and the Settings page.
    A Harness counts the Sessions collect keeps as the owner's: not a unit T3 Code names as
    started by another Session's agent, nor one whose folder is excluded. A store that cannot be
    read is logged and left out."""
    folded = folds(_read(T3Code.name, lambda: T3Code().lineage(s)))
    found = []
    for store in (*ADAPTERS.values(), T3Code()):
        found += _read(store.label, lambda: [store.probe(s, folded)])
    return [f for f in found if f is not None]


def folds(lineage: Iterable[Lineage]) -> dict[Key, Key]:
    """Each native unit an Interface names as started by another Session's agent, by native id,
    and that Session: the unit folds into it, so it is no Session of its own."""
    links: dict[Key, Key] = {}
    for fact in lineage:
        if fact.parent is not None and fact.child != fact.parent:
            links.setdefault(fact.child, fact.parent)
    return links


def collect(s: Settings, held: Held, skip_keys: frozenset[str], sent: Sent, inbox: Path,
            every: bool = False) -> tuple[list[Pending], list[ImportOutcome]]:
    """The Sessions to read from every enabled store Harness: each with a part the record does
    not hold at the fingerprint it has now, or every Session when `every` (backfill), and each
    Session the record holds as its own that T3 Code now names as a subagent or as run through
    it, and each Session the record holds folded where T3 Code no longer puts it (a fork, or
    another parent). A Session whose starting Session did not change brings it along, so a
    family always folds under its own Session. A Session the record holds folded into another
    stays folded unless T3 Code says otherwise: once the Session that started it is gone from its
    store, it is read alone and the record puts it where it folded it. A store that cannot be
    read is logged and skipped. Forgotten Sessions and Sessions whose folder is excluded are left
    out before anything of them is read.
    The inbox (web-chat exports, #15) is not read yet, so no ImportOutcome is returned."""
    enabled = {h: a for h, a in ADAPTERS.items()
               if (setting := s.harnesses.get(h)) is not None and setting.enabled and setting.mode == "store"}
    units: dict[Key, Unit] = {(u.harness, u.native_key): u for h, a in enabled.items()
                              for u in _read(h.label, lambda: a.units(s, sent))}
    named: dict[Key, Key] = {}
    for key, u in units.items():
        named.setdefault((u.harness, u.native_id), key)
    facts = _read(T3Code.name, lambda: T3Code().lineage(s))
    late = {fact.child: fact.parent for fact in facts if fact.parent is not None}
    noted = {fact.child for fact in facts if fact.parent is None}
    forks = {fact.child for fact in facts if fact.own}
    lineage = (*facts, *_kept_folds(units, held, forks | late.keys()))
    links = folds(lineage)

    def moved(u: Unit) -> bool:
        """Held folded where T3 Code no longer puts it: a fork, or the child of a listed Session
        that the record holds in other evidence or not at all."""
        home, native = held.home(u.harness, u.native_key), (u.harness, u.native_id)
        parent = named.get(late.get(native))
        there = held.get(part_origin(*parent, MAIN)) if parent else None
        return home is not None and (native in forks or (parent is not None and (
            there is None or there.evidence != home.evidence)))

    queue = [key for key, u in units.items() if every or _changed(u, held) or moved(u) or (
        (own := _alone(u, held)) is not None and (
            (u.harness, u.native_id) in late or ((u.harness, u.native_id) in noted and own.interface is None)))]
    heads: dict[Key, Head | None] = {}
    parent_of: dict[Key, Key] = {}
    alone: dict[Key, str] = {}
    while queue:
        key = queue.pop()
        if key in heads:
            continue
        unit = units[key]
        heads[key] = head = _head(unit, s, skip_keys)
        if head is None:
            continue
        native = (unit.harness, unit.native_id)
        parent = (unit.harness, head.parent) if head.parent else links.get(native)
        parent = named.get(parent, parent) if parent is not None else None
        home = held.home(unit.harness, unit.native_key)
        if parent in units:
            parent_of[key] = parent
            queue.append(parent)
        elif home is not None and (parent is not None or native not in forks):
            alone[native] = held.session(home.evidence).skip_key
        elif parent is not None:
            parent_of[key] = parent
    families: dict[Key, list[Unit]] = {}
    for key, head in heads.items():
        top = _top(key, parent_of)
        if head is not None and heads.get(top) is not None:
            families.setdefault(top, []).append(units[key])
    return [Pending(tuple(members), lineage, s, {
        k: alone[k] for u in members if (k := (u.harness, u.native_id)) in alone})
        for members in (families[top] for top in sorted(families))], []


def _read(what: str, read: Callable[[], Iterable]) -> list:
    try:
        return list(read())
    except Exception as error:
        log.warning("Atlas skipped %s this time: its store could not be read (%s)", what, type(error).__name__)
        return []


def _guard(what: str, read: Callable):
    """One unreadable Session is logged and skipped; the rest of the store is still read."""
    try:
        return read()
    except Exception as error:
        log.warning("Atlas skipped one %s Session it could not read (%s)", what, type(error).__name__)
        return None


def _changed(u: Unit, held: Held) -> bool:
    return any((h := held.get(part_origin(u.harness, u.native_key, key))) is None or h.fingerprint != fingerprint
               for key, (_, fingerprint) in u.parts.items())


def _alone(u: Unit, held: Held) -> HeldSession | None:
    """The Session the record holds this unit as, when it holds it as a Session of its own."""
    h = held.get(part_origin(u.harness, u.native_key, MAIN))
    return held.session(h.evidence) if h is not None and h.name == part_file(MAIN) else None


def _kept_folds(units: Mapping[Key, Unit], held: Held, stated: set[Key]) -> list[Lineage]:
    """A Session folded into another stays folded once T3 Code prunes the lineage that folded
    it, and whichever root its copy is read from: the record holds its parts, by their origin,
    inside the other Session's evidence. A Session that T3 Code places itself (`stated`, by
    native id) goes where T3 Code puts it."""
    where = {key: h for key, u in units.items() if (h := held.get(part_origin(u.harness, u.native_key, MAIN)))}
    tops = {h.evidence: units[key] for key, h in where.items() if h.name == part_file(MAIN)}
    return [Lineage((units[key].harness, units[key].native_id), (top.harness, top.native_id), T3_CODE)
            for key, h in where.items() if h.name != part_file(MAIN) and (top := tops.get(h.evidence))
            and (units[key].harness, units[key].native_id) not in stated]


def _head(unit: Unit, s: Settings, skip_keys: frozenset[str]) -> Head | None:
    if skip_key(unit.harness, unit.native_key) in skip_keys:
        return None

    def read() -> Head | None:
        head = ADAPTERS[unit.harness].head(unit)
        return head if head is not None and not _excluded(head.cwd, s.exclusions) else None

    return _guard(unit.harness.label, read)


def _excluded(cwd: str | None, exclusions: Sequence[str]) -> bool:
    """Compared both as written and with symlinks resolved, on both sides, so neither an alias
    of an excluded folder nor a store that records the physical path gets past it. A link that
    loops resolves as far as it can. A path the system cannot be given is compared as written."""
    if not cwd or not exclusions:
        return False
    seen = _forms(cwd)
    return any(p.is_relative_to(r) for exclusion in exclusions for r in _forms(exclusion) for p in seen)


def _forms(path: str) -> set[Path]:
    full = Path(path).expanduser()
    try:
        return {Path(os.path.normpath(full)), Path(os.path.realpath(full))}
    except (UnicodeError, ValueError):
        return {Path(os.path.normpath(full))}


@dataclass(frozen=True)
class ImportOutcome:
    """One export file in inbox/ (D Q5)."""
    file: Path
    harness: HarnessId
    sessions: int
    failed: str | None                        # whole or partial failure: the file is KEPT and this is reported
    group_chats_skipped: int                  # other people's messages, never taken in


def release(outcomes: Sequence[ImportOutcome]) -> list[Path]:
    """Called only after the commit that holds the imported Sessions. Deletes each file whose
    outcome has failed=None; returns the deleted paths, which the tick records for the Board and
    the Review ("Imported 143 ChatGPT Sessions and deleted the export"). A failed file stays."""
    raise NotImplementedError


def bind_caller(caller: "object") -> "BoundSession | None":
    """Resolve the live Session an agent tool call comes from, from the calling process.
    - hermes:      caller.hermes_session (the session_id kwarg Hermes passes to tool handlers).
    - claude_code: ~/.claude/sessions/<parent_pid>.json holds sessionId and cwd for a live
                   Claude Code process (structure verified on Linux, 2026-11-08).
    - codex:       caller.call_meta["threadId"], which Codex adds to every tools/call (the
                   agent writes only arguments) -> the rollout file
                   ~/.codex/sessions/YYYY/MM/DD/rollout-*-<threadId>.jsonl (ADR 0010, G2).
                   Verified live with codex exec 0.167.0, resume included. Only for Codex
                   versions the adapter has verified; otherwise None. The TUI and T3 Code's
                   app server are checked before release (D Q12).
    - devin:       None in v1: Devin sends no Session id (D Q12, G2).
    Unknown Harness or no match -> None: the update becomes a REFUSED_UPDATE Note quoting the
    owner's words, which "apply" makes through Atlas (D Q12)."""
    raise NotImplementedError


def find_instruction(bound: "BoundSession", owner_words: str) -> "Instruction | None":
    """Read the bound Session's newest owner-typed messages straight from the Harness store
    (same owner-message predicate as collection) and look for owner_words (normalized,
    substring or close paraphrase checked by exact token overlap, no model). Returns a sealed
    Instruction, the only way one is built. `Instruction.names_ending` is set when those words
    themselves name an ending ("done", "finished", "close it", "drop it" and the like, D Q11);
    rules.authorize requires it for SetStatus(DONE|DROPPED) from an agent. A Step being done is
    not enough."""
    raise NotImplementedError


from ._adapters import ADAPTERS, T3_CODE, T3Code, defaults, linked  # noqa: E402  (the adapters build on the types above)
from ._fold import _top, resolve  # noqa: E402

STORE_HARNESSES = tuple(ADAPTERS)
