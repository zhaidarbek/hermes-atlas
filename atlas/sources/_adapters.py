"""PRIVATE. One adapter per Harness, the T3 Code Interface reader, and web-chat import.

Probing is the same for every adapter: candidate roots in order (the env override, else the
platform default, then the roots settings.md lists), keep the roots whose store exists, and
count Sessions across them by native key, so a second copy of a store counts once.

Collecting takes four steps. `units` lists every unit of a store with each part's fingerprint
now and reads no transcript: a file's `<size>@<mtime_ns>` from lstat, or for SQLite
`<rows>@<sha256 prefix>` of the identities of the rows it would copy, since Hermes edits rows in
place. `head` reads the first lines that say where a unit ran and which Session started it.
`fetch` reads each part once, through the descriptor it took the fingerprint from. `parse`
merges each part with the copy the record holds of it, line by line by the identity its Harness
gives a line, and parses every turn from that copy, so the raw copy and session.md hold the same
turns once each, whatever the store has since rewritten, copied or dropped. A Session's title,
folder and branch come from its session row as the store holds it now, so a title changed back
reads as it does now, though the copy holds each value once.

A line's identity, which decides both what a raw copy adds and which turns session.md shows:
- Claude Code: its `uuid`; a line with none, all of it.
- Codex: all of it (timestamp, type and payload). Codex only appends, and compressing a
  rollout in place keeps its lines.
- Devin: a message's `message_id` with what its turn is read from (role, content, and whether
  it is the owner's), so a compaction's copy in another chain, or a copy that gains a
  `chisel/...` extension, is the same line; a session row's id, creation time, folder and
  title, so its usage columns add nothing.
- Hermes: the key Hermes v0.23.0 shows a message once by (`_display_dedupe_key`: role,
  content, time, tool call ids and tool name), which `/retry`, `/undo`, ACP persist and
  compaction keep while they delete and insert rows under new ids, and for a row that
  merged others the uids it absorbed; a session row's id,
  source, parent, start, folder, branch and title. A user handoff row, a compaction summary
  folded into the owner's ask, is keyed and shown by the ask it carries, so it and the ask's
  own row are one turn. A row that /retry's alternation repair merged or pruned is no turn when
  rows its part held before it already say its words.

A file store's line ends at a newline (LF) and the copy keeps every byte before it, a CR too.
A Claude Code or Codex line that does not parse as a JSON object is a line too: its identity
is its bytes and the copy holds it once. When a whole JSON object ends it, which is what a
Harness that crashed mid-line writes when it resumes, the shortest such object is its record
and the bytes before it are cut; otherwise the line adds no record. The read that copies such a
line logs a count, never its content. A last line with no newline that does not parse is still
being written, so it waits for a later read. The rest of the part is collected, except for a
Codex rollout whose first line does not parse: its `session_meta` says what the Session is,
so the rollout is logged and skipped.

Every read goes through a descriptor of what was checked. A store's real folder is opened as
the folder listed, by device and inode, and a store file is opened from it one name at a time,
never through a link. A SQLite database is pinned the same way, without opening it to read, and
SQLite is given the pin's name where the system has one (Linux `/proc/self/fd/<n>`), which
SQLite resolves to the file's current name and opens by it. It is read only when the file SQLite
names is still the pinned one, under its root and by no link, and when the WAL and index SQLite
opened beside it are the ones in that folder. So a file, a folder or a folder above it swapped
for a link after listing is never read. A swap inside SQLite's own open of the database or its
WAL is a stated limit (record-layout.md, Raw copies). A refused store is logged by the name of
its refusal (`LinkedElsewhere`, `SwappedWhileRead`), never by a path.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import io
import itertools
import json
import logging
import os
import re
import sqlite3
import stat
import sys
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, NamedTuple, Sequence, TypeVar
from zoneinfo import ZoneInfo

import zstandard

from .. import privacy, telegram
from ..model import (LOCAL, HarnessId, RawPart, Sent, SessionEvidence, Settings, Turn, evidence_id, part_file,
                     part_origin)
from . import MAIN, Fetched, Found, Head, Key, Lineage, NativeSession, Unit, _excluded
from ._handoff import live_ask

log = logging.getLogger("atlas.sources")
T3_CODE = "T3 Code"
_UNPARSED = "unparsed:"
_BACKWARDS = re.compile(rb'"\\*+|[{}\[\]]')
_NONZERO = b"\x00" + b"\xff" * 255
_DESCRIPTORS = "/proc/self/fd"
_OPEN = "/dev/fd"
_MAPS = "/proc/self/maps"
_NEAR = 16
_PIN = getattr(os, "O_PATH", None)
_SEARCH = getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW
T = TypeVar("T")


class Refused(PermissionError):
    """A store Atlas will not read. The log names only the class, which says why, never a path."""


class LinkedElsewhere(Refused):
    """A store file is a link, not a regular file, or has another link, as a hard-link backup makes."""


class SwappedWhileRead(Refused):
    """A store folder or file is not the one Atlas checked, or SQLite opened another file in its place."""


class _Store:
    """Where one Harness or Interface keeps its Sessions. Subclasses set `env` and `name` and
    implement default, store, shown and starts."""
    env: str | None = None
    name: str
    is_interface = False

    @property
    def label(self) -> str:
        return self.name

    def default(self) -> Path | None: return None
    def store(self, root: Path) -> Path: raise NotImplementedError
    def shown(self, root: Path) -> Path: return self.store(root)
    def starts(self, s: Settings, folded: Mapping[Key, Key]) -> dict[str, datetime]:   # native key -> start
        raise NotImplementedError
    def configured(self, s: Settings) -> tuple[str, ...]: return ()
    def opened(self, root: Path) -> tuple[Path, ...]: return (self.store(root),)

    def primary(self) -> Path | None:
        override = os.environ.get(self.env, "").strip() if self.env else ""
        return Path(override).expanduser() if override else self.default()

    def roots(self, s: Settings) -> list[Path]:
        primary = self.primary()
        roots: list[Path] = []
        for root in (primary, *(Path(r).expanduser() for r in self.configured(s))):
            if root is not None and not _usable(root):
                log.warning("Atlas skipped a %s folder named in settings (%s)", self.label, UnicodeEncodeError.__name__)
                continue
            if root is not None and self.store(root).exists() and os.path.realpath(root) not in map(os.path.realpath, roots):
                if root is not primary and linked(root):
                    log.warning("Atlas skipped a %s folder named in settings (%s)", self.label, LinkedElsewhere.__name__)
                    continue
                roots.append(root)
        return roots

    def probe(self, s: Settings, folded: Mapping[Key, Key]) -> Found | None:
        roots = self.roots(s)
        if not roots:
            return None
        sessions = self.starts(s, folded)
        since = min(sessions.values()).astimezone(ZoneInfo(s.timezone)) if sessions else None
        return Found(self.name, _tilde(self.shown(roots[0])), len(sessions), since,
                     self.is_interface, tuple(_tilde(r) for r in roots))


class _Harness(_Store):
    key: HarnessId

    @property
    def name(self) -> HarnessId:
        return self.key

    @property
    def label(self) -> str:
        return self.key.label

    def configured(self, s: Settings) -> tuple[str, ...]:
        setting = s.harnesses.get(self.key)
        return setting.roots if setting else ()

    def units(self, s: Settings, sent: Sent) -> list[Unit]:
        """Every unit of every root. A Session that two roots hold is the copy that holds more
        once decompressed, the first root's on a tie, so the copy chosen changes only when a
        copy does."""
        kept: dict[str, Unit] = {}
        for root in self.roots(s):
            try:
                listed = list(self._units(root, sent))
            except Exception as error:
                log.warning("Atlas skipped %s this time: its store could not be read (%s)",
                            self.key.label, type(error).__name__)
                continue
            for unit in listed:
                other = kept.get(unit.native_key)
                if other is None or _guarded_content(unit) > _guarded_content(other):
                    kept[unit.native_key] = unit
        return list(kept.values())

    def starts(self, s: Settings, folded: Mapping[Key, Key]) -> dict[str, datetime]:
        starts = {}
        for unit in self.units(s, Sent()):
            if (self.key, unit.native_id) in folded:
                continue
            try:
                head = self.head(unit)
            except Exception:
                continue
            if head is not None and head.parent is None and not _excluded(head.cwd, s.exclusions):
                starts[unit.native_key] = head.started
        return starts

    def lineage(self, s: Settings) -> Iterable[Lineage]: return ()

    def can_recopy(self, part_source: str) -> bool:
        return Path(part_source.split(" ", 1)[0]).expanduser().exists()

    def _units(self, root: Path, sent: Sent) -> Iterable[Unit]: raise NotImplementedError
    def head(self, unit: Unit) -> Head | None: raise NotImplementedError
    def fetch(self, unit: Unit, sent: Sent) -> Fetched | None: raise NotImplementedError
    def _draft(self, unit: Unit, copies: dict[str, "_Copy"], sent: Sent) -> "_Draft | None":
        raise NotImplementedError

    def identity(self, line: dict) -> str:
        return _canonical(line)

    def _keeps(self, line: dict, sent: Sent) -> bool:
        return True

    def read(self, s: Settings, unit: Unit, sent: Sent) -> NativeSession | None:
        """The unit as its store holds it now, with nothing of it held before."""
        f = self.fetch(unit, sent)
        return self.parse(s, f, lambda origin: None, sent) if f is not None else None

    def parse(self, s: Settings, f: Fetched, held: Callable[[str], bytes | None],
              sent: Sent) -> NativeSession | None:
        copies = {k: self._copy(held(part_origin(self.key, f.unit.native_key, k)), source, print_, data, sent)
                  for k, (source, print_, data) in f.parts.items()}
        d = self._draft(f.unit, copies, sent)
        if d is None:
            return None
        if unparsed := sum(c.unparsed for c in copies.values()):
            log.warning("Atlas left %d unparseable %s of one %s Session out of its turns",
                        unparsed, "line" if unparsed == 1 else "lines", self.key.label)
        d.parts = {k: (c.source, c.fingerprint, c.data) for k, c in copies.items()}
        return self._native(s, d)

    def _copy(self, held: bytes | None, source: str, print_: str, data: bytes, sent: Sent) -> "_Copy":
        """The copy held, then each line of `data` whose identity neither the copy nor an earlier
        line has, verbatim, leaving out a line the Harness does not keep. A line ends at a newline
        (LF) and keeps every byte before it. A line that does not parse as a JSON object is kept by
        its bytes; when a whole object ends it (a Harness that crashed mid-line, then resumed), that
        object is its record, else it adds none. A last line with no newline that does not parse is
        still being written, so it waits for a later read."""
        held = held or b""
        *ended, last = data.split(b"\n")
        records, seen, added, now, unparsed = [], set(), [], [], 0

        def hold(key: str, r: dict | None, cut: bytes) -> bool:
            if key in seen:
                return False
            seen.add(key)
            if r is not None and (not cut or (key := self.identity(r)) not in seen):
                seen.add(key)
                records.append(r)
            return True

        for line in held.split(b"\n"):
            if line.strip():
                hold(*self._line(line))
        for line in [*ended, last] if _record(last) is not None else ended:
            if not line.strip():
                continue
            key, r, cut = self._line(line)
            if r is not None:
                if not self._keeps(r, sent):
                    continue
                now.append(r)
            if hold(key, r, cut):
                added.append(line + b"\n")
                unparsed += bool(cut)
        joint = b"\n" if added and held and not held.endswith(b"\n") else b""
        return _Copy(source, print_, held + joint + b"".join(added), records, now, unparsed)

    def _line(self, line: bytes) -> tuple[str, dict | None, bytes]:
        """A line's identity, its record, and the bytes in front of its record that do not parse."""
        if (r := _record(line)) is not None:
            return self.identity(r), r, b""
        at, r = _glued(line)
        return _UNPARSED + hashlib.sha256(line).hexdigest(), r, line[:at]

    def _native(self, s: Settings, d: "_Draft") -> NativeSession | None:
        if not d.turns:
            return None
        stamps = [t.at for t in d.turns if t.at] + [d.started]
        title = d.title or next((_title(t.text) for t in d.turns if t.role == "owner"), "")
        evidence = SessionEvidence(
            id=evidence_id(self.key, LOCAL, d.key), harness=self.key, interface=d.interface, namespace=LOCAL,
            native_key=d.key, title=title, started=d.started, last_at=max(stamps),
            machine=s.machine, cwd=_tilde(Path(d.cwd)) if d.cwd else None, git_branch=d.branch or None,
            parts=tuple(RawPart(part_file(k), "", source, print_, part_origin(self.key, d.key, k))
                        for k, (source, print_, _) in d.parts.items()),
            turns=tuple(sorted(d.turns, key=lambda t: (t.at is None, t.at or d.started))))
        return NativeSession(self.key, d.native, d.parent, d.interface,
                             {k: data for k, (_, _, data) in d.parts.items()}, evidence)


def _title(said: str) -> str:
    """A title from the owner's first turn: its first line, masked, then cut to 80 characters
    before any mask the cut would split, so no part of a secret is kept."""
    line = privacy.scrub(said.splitlines()[0])
    return line[:next((m.start() for m in privacy.MASK.finditer(line) if m.start() < 80 < m.end()), 80)]


class _Copy(NamedTuple):
    """A part's whole copy: what the record held, then the lines read that it lacked."""
    source: str
    fingerprint: str
    data: bytes
    records: list[dict]                       # its lines parsed, once per identity
    now: list[dict]                           # the lines its store holds now, parsed
    unparsed: int                             # the lines read that it lacked and that do not parse

    def row(self, table: str) -> dict:
        """The part's `table` row as its store holds it now."""
        return next(line[table] for line in self.now if table in line)


class _Folder(NamedTuple):
    """A store's real folder as listed: its real path and its stat, whose device and inode it must keep."""
    path: str
    st: os.stat_result


class _File(NamedTuple):
    """A store file as listed: its path, its names below its store's real folder, and its lstat."""
    path: Path
    base: _Folder
    names: tuple[str, ...]
    st: os.stat_result


class _Db(NamedTuple):
    """A store database: its path, the real folder of the root it must stay under, and its names below it."""
    path: Path
    root: _Folder
    names: tuple[str, ...]


class _Rows(NamedTuple):
    """Where a SQLite unit's rows are, and what its session row says."""
    db: _Db
    sessions: tuple[str, ...]                 # the session ids that are its parts, in order
    parent: str | None
    cwd: str | None
    started: object


@dataclass
class _Draft:
    """One native unit while it is parsed, before it becomes a NativeSession."""
    native: str
    key: str                                  # SessionEvidence.native_key
    started: datetime
    cwd: str | None = None
    branch: str | None = None
    interface: str | None = None
    parent: str | None = None
    title: str = ""
    turns: list[Turn] = field(default_factory=list)
    parts: dict[str, tuple[str, str, bytes]] = field(default_factory=dict)  # key -> (source, fingerprint, bytes)


class ClaudeCode(_Harness):
    """$CLAUDE_CONFIG_DIR or ~/.claude; projects/<enc-cwd>/<uuid>.jsonl is one native Session.
    Subagents (<uuid>/subagents/**/agent-*.jsonl, isSidechain) are parts of it.
    Owner turn: type user, not isMeta/isCompactSummary/toolUseResult, origin absent or human
    (a string, or {"kind": "human"} in newer versions), promptSource not 'system'. Interface:
    entrypoint 't3-code' (sdk-ts alone proves nothing).
    Copy matters: Claude Code deletes transcripts after cleanupPeriodDays (default 30)."""
    key = HarnessId.CLAUDE_CODE
    env = "CLAUDE_CONFIG_DIR"

    def default(self) -> Path: return Path.home() / ".claude"
    def store(self, root: Path) -> Path: return root / "projects"

    def _units(self, root: Path, sent: Sent) -> Iterator[Unit]:
        store = self.store(root)
        base = _within(store)
        for transcript in _files(store, "*/*.jsonl", store, base):
            folder = transcript.path.parent / transcript.path.stem / "subagents"
            subagents = _files(folder, "**/agent-*.jsonl", store, base)
            yield _file_unit(self.key, transcript.path.stem, {
                MAIN: transcript, **{f"subagent:{f.path.stem}": f for f in subagents}})

    def head(self, unit: Unit) -> Head:
        stamp = cwd = None
        with _open(unit.ref[MAIN]) as fh:
            for line in itertools.islice(fh, 64):
                r = _record(line) or {}
                stamp, cwd = stamp or r.get("timestamp"), cwd or r.get("cwd")
                if stamp and cwd:
                    break
        if stamp is None:
            raise ValueError("a transcript with no timestamped record")
        return Head(cwd, None, _when(stamp))

    def fetch(self, unit: Unit, sent: Sent) -> Fetched:
        return Fetched(unit, {k: (_tilde(f.path), *reversed(_slurp(f))) for k, f in unit.ref.items()})

    def identity(self, line: dict) -> str:
        uid = line.get("uuid")
        return f"uuid:{uid}" if isinstance(uid, str) and uid else _canonical(line)

    def _draft(self, unit: Unit, copies: dict[str, _Copy], sent: Sent) -> _Draft:
        d: _Draft | None = None
        seen = set()
        for key, c in copies.items():
            for r in c.records:
                if (line := self.identity(r)) in seen:
                    continue
                seen.add(line)
                stamp = r.get("timestamp")
                if d is None and key == MAIN and stamp:
                    d = _Draft(unit.native_id, unit.native_key, _when(stamp))
                if d is None:
                    continue
                if key == MAIN:
                    d.cwd = d.cwd or r.get("cwd")
                    d.branch = d.branch or r.get("gitBranch")
                    if r.get("entrypoint") == "t3-code":
                        d.interface = T3_CODE
                role, text = _claude_turn(r)
                if role and text and stamp:
                    d.turns.append(Turn("agent" if key != MAIN else role, _when(stamp), text, key))
        if d is None:
            raise ValueError("a transcript with no timestamped record")
        return d


def _claude_turn(r: dict) -> tuple[str | None, str]:
    message = r.get("message") or {}
    if r.get("type") == "assistant":
        return "agent", _text(message.get("content"))
    if r.get("type") != "user":
        return None, ""
    origin = r.get("origin")
    injected = (r.get("isMeta") or r.get("isCompactSummary") or r.get("toolUseResult") is not None
                or (origin.get("kind") if isinstance(origin, dict) else origin) not in (None, "human")
                or r.get("promptSource") == "system")
    content = message.get("content")
    if injected or (isinstance(content, list) and any(b.get("type") == "tool_result" for b in content
                                                      if isinstance(b, dict))):
        return None, ""
    return ("agent" if r.get("isSidechain") else "owner"), _text(content)


class Codex(_Harness):
    """$CODEX_HOME or ~/.codex plus owner-added roots (Orca's second Codex home). Identity is the
    Codex thread id, which the rollout file name ends with, so a second copy dedupes by id and
    the larger copy wins. Rollouts are read from sessions/ and archived_sessions/, .jsonl or
    .jsonl.zst. Subagent threads (source.subagent.thread_spawn) fold via parent_thread_id; other
    subagent threads (guardian_review) are not Sessions. Owner turn: event_msg item_completed
    UserMessage, agent turn: AgentMessage. Codex 0.16x writes their text as `content` blocks
    ({"type": "text" or "Text", "text"}); older versions as `text`."""
    key = HarnessId.CODEX
    env = "CODEX_HOME"
    ROLLOUT = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(?P<id>.+?)\.jsonl(?:\.zst)?")

    def default(self) -> Path: return Path.home() / ".codex"
    def store(self, root: Path) -> Path: return root / "sessions"

    def opened(self, root: Path) -> tuple[Path, ...]: return (root / "sessions", root / "archived_sessions")

    def _units(self, root: Path, sent: Sent) -> Iterator[Unit]:
        for folder in self.opened(root):
            if not folder.is_dir():
                continue
            try:
                base = _within(folder)
            except LinkedElsewhere as refused:
                log.warning("Atlas skipped a Codex store folder (%s)", type(refused).__name__)
                continue
            for rollout in _files(folder, "**/rollout-*.jsonl*", folder, base):
                name = rollout.path.name
                if name.endswith((".jsonl", ".jsonl.zst")):
                    named = self.ROLLOUT.fullmatch(name)
                    yield _file_unit(self.key, named["id"] if named else name.split(".", 1)[0], {MAIN: rollout})

    def head(self, unit: Unit) -> Head | None:
        meta = json.loads(_first_line(unit.ref[MAIN]))["payload"]
        spawn = _spawn(meta)
        if spawn is False:
            return None
        return Head(meta.get("cwd"), spawn.get("parent_thread_id") if spawn else None, _when(meta["timestamp"]))

    def fetch(self, unit: Unit, sent: Sent) -> Fetched:
        rollout = unit.ref[MAIN]
        stored, print_ = _slurp(rollout)
        return Fetched(unit, {MAIN: (_tilde(rollout.path), print_,
                                     _unzstd(stored) if rollout.path.suffix == ".zst" else stored)})

    def _draft(self, unit: Unit, copies: dict[str, _Copy], sent: Sent) -> _Draft | None:
        lines = copies[MAIN].records
        meta = lines[0]["payload"]
        spawn = _spawn(meta)
        if spawn is False:
            return None
        d = _Draft(unit.native_id, unit.native_key, _when(meta["timestamp"]), cwd=meta.get("cwd"),
                   branch=(meta.get("git") or {}).get("branch"),
                   interface=T3_CODE if meta.get("originator") == T3_CODE else None,
                   parent=spawn.get("parent_thread_id") if spawn else None)
        for line in lines[1:]:
            payload = line.get("payload") or {}
            item = payload.get("item") or {}
            if line.get("type") == "event_msg" and payload.get("type") == "item_completed":
                role = {"UserMessage": "owner", "AgentMessage": "agent"}.get(item.get("type"))
                text = _text(item.get("text") or item.get("content"))
                if role and text:
                    d.turns.append(Turn(role, _when(line["timestamp"]), text, MAIN))
        return d


def _spawn(meta: dict) -> dict | None | bool:
    """A subagent thread's thread_spawn, None for a thread the owner started, False for a
    subagent thread nothing spawned (a guardian review), which is not a Session."""
    source = meta.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    return False if subagent is not None and not spawn else spawn


class Devin(_Harness):
    """Linux ~/.local/share/devin/cli/sessions.db; macOS path unverified -> settings root.
    Session = sessions row, keyed by its slug plus creation time, so a slug reused later or in
    another root is another Session. Its message_nodes form chains (the main one, subagent
    chains, and the chains a compaction starts with copies of earlier messages); a message is
    its chat_message JSON (role, content, message_id, metadata), and a copy shares the
    message_id, so each message counts once. Owner turn: role user that metadata.is_user_input
    does not mark null or false, with no subagent or handoff extension (a subagent's handoff
    prompt). An older store kept role, content, is_user_input and extension as columns.
    Devin via T3 Code (parked Q21): T3 nativeThreadRef driver=acpRegistry nativeId = Devin
    session id -> interface 'T3 Code'; the chisel/acp-content-blocks extension alone is not proof."""
    key = HarnessId.DEVIN

    def default(self) -> Path | None:
        return Path.home() / ".local/share/devin/cli" if sys.platform.startswith("linux") else None

    def store(self, root: Path) -> Path: return root / "sessions.db"

    def shown(self, root: Path) -> Path:
        return root.parent if root.name == "cli" else root

    def _units(self, root: Path, sent: Sent) -> list[Unit]:
        db = _database(self.store(root), root)
        sessions, prints = _read_db(db, lambda con: _prints(con, "message_nodes", "row_id", _devin_part, self.identity))
        units = []
        for sid, row in sessions.items():
            print_, size = prints.get(sid) or _summary(_devin_part(row, []), self.identity)
            units.append(Unit(self.key, sid, f"{sid}@{row['created_at']}",
                              {MAIN: (f"{_tilde(db.path)} sessions/{sid}", print_)}, size,
                              _Rows(db, (sid,), None, row.get("working_directory"), row["created_at"])))
        return units

    def head(self, unit: Unit) -> Head:
        return Head(unit.ref.cwd, None, _when(unit.ref.started))

    def fetch(self, unit: Unit, sent: Sent) -> Fetched | None:
        db = unit.ref.db
        sessions, rows = _read_db(db, lambda con: _session_rows(con, "message_nodes", "row_id", unit.ref.sessions))
        row = sessions.get(unit.native_id)
        if row is None or f"{row['id']}@{row['created_at']}" != unit.native_key:
            return None
        lines = _devin_part(row, rows)
        return Fetched(unit, {MAIN: (f"{_tilde(db.path)} sessions/{row['id']}", _rows_print(lines, self.identity),
                                     _encode(lines))})

    def identity(self, line: dict) -> str:
        if "sessions" in line:
            row = line["sessions"]
            return _canonical(["sessions", *(row.get(c) for c in ("id", "created_at", "working_directory", "title"))])
        node = line.get("message_nodes") or {}
        m = _devin_message(node)
        return _canonical(["message", m.get("message_id") or [node.get("session_id"), node.get("node_id"),
                                                                node.get("row_id")],
                           m.get("role"), m.get("content"), _devin_owner(m)])

    def _draft(self, unit: Unit, copies: dict[str, _Copy], sent: Sent) -> _Draft:
        row = copies[MAIN].row("sessions")
        d = _Draft(row["id"], unit.native_key, _when(row["created_at"]), cwd=row.get("working_directory"),
                   title=row.get("title") or "")
        for node in (line["message_nodes"] for line in copies[MAIN].records if "message_nodes" in line):
            m = _devin_message(node)
            meta = m.get("metadata") or {}
            owner = _devin_owner(m)
            text = _text(m.get("content"))
            if text and (owner or m.get("role") == "assistant"):
                d.turns.append(Turn("owner" if owner else "agent", _when(meta.get("created_at") or node["created_at"]),
                                    text, MAIN))
        return d


def _devin_message(node: dict) -> dict:
    """A message node's chat_message, or the columns an older store kept it in."""
    if "chat_message" in node:
        return json.loads(node["chat_message"] or "{}")
    return {"role": node.get("role"), "content": node.get("content"), "message_id": node.get("row_id"),
            "metadata": {"is_user_input": node.get("is_user_input", 1), "extensions": [node.get("extension") or ""]}}


def _devin_owner(m: dict) -> bool:
    """A user message the owner typed: `is_user_input` not null or false, and no subagent or
    handoff extension."""
    meta = m.get("metadata") or {}
    handoff = any("subagent" in name or "handoff" in name for name in meta.get("extensions") or ())
    return m.get("role") == "user" and bool(meta.get("is_user_input", True)) and not handoff


def _devin_part(session: dict, messages: Sequence[dict]) -> list[dict]:
    return _records([("sessions", session)] + [("message_nodes", m) for m in messages])


class Hermes(_Harness):
    """get_hermes_home()/state.db read-only (parked Q13: state.db, not hooks). The rows Hermes
    shows as the transcript: active ones, and the ones an in-place compaction summarised away
    (active=0, compacted=1); a compaction's summary row (_compressed_summary) is no turn. Rows
    taken back (active=0, compacted=0) are left out.
    A compression split continues its Session: a child whose parent ended with end_reason
    'compression' is a further part of it. A session row with source 'subagent' (a delegated
    task) or 'tool' (an integration's run) and a parent is a subagent and folds into it; one
    with no parent is not the owner's conversation. Any other row with a parent is the owner's
    own Session, started from the old one by /new, an automatic reset or /branch (Hermes
    v0.23.0 gateway/session.py, hermes_state_mutation_branch.py). Excluded: source='cron';
    assistant rows matching Atlas's sent ledger (the `hermes send` mirror), or that /retry's
    repair merged one into. Turns of an exchange whose rows name atlas_* tools are marked
    atlas_tool_turn (filed as owner Notes, W Q14). Claimed replies and /atlas commands never reach state.db, so they need no filter."""
    key = HarnessId.HERMES
    env = "HERMES_HOME"
    AGENTS = ("subagent", "tool")

    def default(self) -> Path: return Path.home() / ".hermes"
    def store(self, root: Path) -> Path: return root / "state.db"

    def _units(self, root: Path, sent: Sent) -> Iterator[Unit]:
        db = _database(self.store(root), root)
        sessions, prints = _read_db(db, lambda con: _prints(
            con, "messages", "id", lambda session, rows: _hermes_part(session, _kept(rows, sent)), self.identity))
        chains: dict[str, list[str]] = {}
        for sid in sessions:
            chains.setdefault(_chain_root(sid, sessions), []).append(sid)
        for head, chain in chains.items():
            row = sessions[head]
            agent = row.get("source") in self.AGENTS
            parent = row.get("parent_session_id")
            if row.get("source") == "cron" or (agent and parent not in sessions):
                continue
            chain = sorted(chain, key=lambda sid: sessions[sid]["started_at"])
            now = {sid: prints.get(sid) or _summary(_hermes_part(sessions[sid], []), self.identity) for sid in chain}
            yield Unit(self.key, head, head, {_hermes_key(sid, head): (f"{_tilde(db.path)} sessions/{sid}", now[sid][0])
                                              for sid in chain}, sum(size for _, size in now.values()),
                       _Rows(db, tuple(chain), _chain_root(parent, sessions) if agent else None, row.get("cwd"),
                             row["started_at"]))

    def head(self, unit: Unit) -> Head:
        return Head(unit.ref.cwd, unit.ref.parent, _when(unit.ref.started))

    def fetch(self, unit: Unit, sent: Sent) -> Fetched | None:
        db = unit.ref.db
        sessions, rows = _read_db(db, lambda con: _session_rows(con, "messages", "id", unit.ref.sessions))
        if unit.native_id not in sessions:
            return None
        parts = {}
        for sid in (sid for sid in unit.ref.sessions if sid in sessions):
            lines = _hermes_part(sessions[sid], _kept([m for m in rows if m["session_id"] == sid], sent))
            parts[_hermes_key(sid, unit.native_id)] = (f"{_tilde(db.path)} sessions/{sid}",
                                                       _rows_print(lines, self.identity), _encode(lines))
        return Fetched(unit, parts)

    def identity(self, line: dict) -> str:
        if "sessions" in line:
            row = line["sessions"]
            return _canonical(["sessions", *(row.get(c) for c in (
                "id", "source", "parent_session_id", "started_at", "cwd", "git_branch", "title"))])
        m = line.get("messages") or {}
        absorbed = _json_list(m.get("absorbed_message_uids"))
        return _canonical(["messages", *_display_key(m), *([absorbed] if absorbed else [])])

    def _keeps(self, line: dict, sent: Sent) -> bool:
        """Not a row that `hermes send` mirrored for a text Atlas sent."""
        return not _mirrored(line.get("messages") or {}, sent)

    def _draft(self, unit: Unit, copies: dict[str, _Copy], sent: Sent) -> _Draft:
        head = copies[MAIN].row("sessions")
        d = _Draft(head["id"], head["id"], _when(head["started_at"]), cwd=head.get("cwd"),
                   branch=head.get("git_branch"), parent=unit.ref.parent, title=head.get("title") or "")
        seen = set()
        for key, c in copies.items():
            rows, said = [], {}
            for line in c.records:
                if "messages" in line and self._keeps(line, sent):
                    repeats = _said_before(line["messages"], said)
                    if (row := self.identity(line)) not in seen and not repeats:
                        rows.append(line["messages"])
                    seen.add(row)
            d.turns += _hermes_turns(rows, key)
        return d


def _hermes_key(sid: str, head: str) -> str:
    return MAIN if sid == head else f"continued:{sid}"


def _chain_root(sid: str, sessions: dict) -> str:
    """Follow compression splits back to the Session they continue."""
    seen = {sid}
    while True:
        parent = sessions.get(sessions[sid].get("parent_session_id"))
        if parent is None or parent.get("end_reason") != "compression" or parent["id"] in seen:
            return sid
        sid = parent["id"]
        seen.add(sid)


def _kept(messages: Sequence[dict], sent: Sent) -> list[dict]:
    """The rows of the transcript Hermes shows, without the rows `hermes send` mirrored for a text
    Atlas sent."""
    return [m for m in messages if (m.get("active", 1) or m.get("compacted")) and not _mirrored(m, sent)]


def _mirrored(m: dict, sent: Sent) -> bool:
    """An assistant row that `hermes send` mirrored for a text Atlas sent, or one Hermes v0.23.0's
    alternation repair merged such a row into: it joins the merged rows' stripped words with a
    newline, so a sent text is one of its newline-bounded pieces. The record holds a sent text
    masked. A row matches it as it reads, and masked too when an attempt may have sent it bare
    (`Sent.maybe_bare`), so an agent's row that differs from a sent text only by its secret is
    kept."""
    text = m.get("content")
    if m.get("role") != "assistant" or not isinstance(text, str):
        return False
    merged = bool(_json_list(m.get("absorbed_message_uids")))
    return any(_sent_as(shown, sent, merged) for shown in dict.fromkeys((text, telegram.plain(text))))


def _sent_as(text: str, sent: Sent, merged: bool) -> bool:
    """`text` is a sent text, as it reads or with its secrets masked. Atlas sends Telegram a text
    styled, and the row holds it so."""
    if _mirrors(text, sent.texts, merged):
        return True
    masked, heads = _masked(sent.maybe_bare)
    if not heads or not any(text.lstrip().startswith(h) or merged and f"\n{h}" in f"\n{text}" for h in heads):
        return False
    return _mirrors(privacy.scrub(text), masked, merged)


@functools.lru_cache(maxsize=1)
def _masked(sent: frozenset[str]) -> tuple[frozenset[str], tuple[str, ...]]:
    """The sent texts that hold a mask, and the words before each one's first mask. Scrubbing
    leaves a text as it is up to its first mask, so only a row holding those words can match."""
    masked = frozenset(s for s in sent if "[secret: " in s)
    return masked, tuple({s.partition("[secret: ")[0] for s in masked})


def _mirrors(text: str, sent: frozenset[str], merged: bool) -> bool:
    return text.strip() in sent or merged and any(f"\n{s}\n" in f"\n{text}\n" for s in sent if s)


def _hermes_part(session: dict, kept: Sequence[dict]) -> list[dict]:
    return _records([("sessions", session)] + [("messages", m) for m in kept])


def _display_key(m: dict) -> list:
    """How Hermes v0.23.0 shows a message once (hermes_state_messages._display_dedupe_key): an
    assistant row whose tool calls all carry ids by those ids, so a prune that rewrites their
    arguments keeps it, and any other row by role, content, time, tool call and tool name, a user
    handoff row by its live ask's content."""
    if m.get("role") == "assistant":
        ids = [_call_id(call) for call in _json_list(m.get("tool_calls"))]
        if ids and all(ids):
            return ["assistant", None, m.get("timestamp"), m.get("tool_call_id"), ids, m.get("tool_name")]
    return [m.get("role"), _shown(m), m.get("timestamp"), m.get("tool_call_id"), m.get("tool_calls"),
            m.get("tool_name")]


def _said_before(m: dict, said: dict[str, list[tuple[tuple, dict]]]) -> bool:
    """Whether rows held before `m` in its part already say its words, then holds `m` there too.
    Hermes v0.23.0's alternation repair (`repair_message_sequence`, run when /retry loads a
    transcript) writes a merged row under the `message_uid` of the first row it merged, lists the
    others in `absorbed_message_uids`, and joins their words, each stripped as Hermes loads it,
    with a blank line for user rows and a newline for assistant rows, an empty one left out. `m` repeats
    held rows when, for each uid it names in order, a held row of its role names the uids from there
    and their words, joined that way, are its words. A row that names only its own uid, such as one
    the repair wrote back as Hermes loaded it or whose unanswered tool calls it pruned, repeats a held
    row of its uid whose words, stripped as Hermes loads them, are its words. `said` maps a uid to the
    rows held under it, each with the uids it names."""
    uid = m.get("message_uid")
    named = (uid, *_json_list(m.get("absorbed_message_uids")))
    if m.get("_compressed_summary") or not all(isinstance(u, str) and u for u in named):
        return False
    if len(named) == 1:
        repeats = any(span == named and h.get("role") == m.get("role") and _loaded(h) == _loaded(m)
                      for span, h in said.get(uid, ()))
    else:
        repeats = _joins(m, named, said)
    said.setdefault(uid, []).append((named, m))
    return repeats


def _joins(m: dict, named: tuple[str, ...], said: dict[str, list[tuple[tuple, dict]]]) -> bool:
    """Whether `m`'s words are held rows' words for the uids it names, joined as the repair joins them."""
    role, text = m.get("role"), m.get("content")
    if not isinstance(text, str):
        return False
    sep = "\n" if role == "assistant" else "\n\n"

    def at(i: int, pos: int) -> bool:
        if i == len(named):
            return pos == len(text)
        for span, h in said.get(named[i], ()):
            words = h.get("content")
            if h.get("role") != role or named[i:i + len(span)] != span or not isinstance(words, str):
                continue
            words = words.strip()
            piece = sep + words if pos and words else words
            if text.startswith(piece, pos) and at(i + len(span), pos + len(piece)):
                return True
        return False

    return at(0, 0)


def _loaded(m: dict) -> object:
    """A row's shown words as Hermes v0.23.0 loads them: a string stripped, whitespace inside kept."""
    text = m.get("content")
    words = _shown({**m, "content": text.strip()} if isinstance(text, str) else m)
    return words.strip() if isinstance(words, str) else words


def _shown(m: dict) -> object:
    """A row's content, or for a user handoff row the live ask it carries."""
    ask = live_ask(m.get("content"), m.get("display_kind")) if m.get("role") == "user" else None
    return m.get("content") if ask is None else ask


def _call_id(call: object) -> str:
    """A tool call's id as Hermes pairs it: `call_id`, else `id`, up to a `|`."""
    for value in (call.get("call_id"), call.get("id")) if isinstance(call, dict) else ():
        if isinstance(value, str) and value.strip():
            return value.strip().split("|", 1)[0].strip() or value.strip()
    return ""


def _json_list(text: object) -> list:
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _hermes_turns(rows: list[dict], part: str) -> list[Turn]:
    exchanges: list[list[dict]] = []
    for m in sorted(rows, key=lambda m: m.get("timestamp") or 0):
        if not exchanges or (m.get("role") == "user" and not m.get("_compressed_summary")):
            exchanges.append([])
        exchanges[-1].append(m)
    turns = []
    for exchange in exchanges:
        atlas = any((m.get("tool_name") or "").startswith("atlas_") or '"atlas_' in (m.get("tool_calls") or "")
                    for m in exchange)
        for m in exchange:
            text = (_shown(m) or "").strip()
            role = None if m.get("_compressed_summary") else {"user": "owner", "assistant": "agent"}.get(m.get("role"))
            if role and text:
                turns.append(Turn(role, _when(m["timestamp"]), text, part, atlas_tool_turn=atlas))
    return turns


class T3Code(_Store):
    """Interface, not a Harness. $T3CODE_HOME or ~/.t3, userdata/statev2.sqlite. Each thread's
    provider row names the native Session it ran (nativeThreadRef: driver claudeAgent | codex |
    acpRegistry, and nativeId), so that Session is noted as run through T3 Code. A thread whose
    payload_json.lineage has relationshipToParent 'subagent' was started by the agent of its
    parentThreadId: its native Session folds into that thread's. A 'fork' is the owner's own
    conversation and stays its own Session. T3 prunes its own data, so a fold is kept in the
    parent's evidence the first time it is seen, and stays there until T3 Code names the thread
    a fork or the child of another thread."""
    name = T3_CODE
    env = "T3CODE_HOME"
    is_interface = True
    DRIVERS = {"claudeAgent": HarnessId.CLAUDE_CODE, "codex": HarnessId.CODEX, "acpRegistry": HarnessId.DEVIN}

    def default(self) -> Path: return Path.home() / ".t3"
    def store(self, root: Path) -> Path: return root / "userdata" / "statev2.sqlite"
    def shown(self, root: Path) -> Path: return root

    def configured(self, s: Settings) -> tuple[str, ...]:
        setting = s.interfaces.get("t3_code")
        return (setting.root,) if setting else ()

    def starts(self, s: Settings, folded: Mapping[Key, Key]) -> dict[str, datetime]:
        out: dict[str, datetime] = {}
        for root in self.roots(s):
            for thread, created in _rows(_database(self.store(root), root),
                                         "SELECT thread_id, created_at FROM orchestration_v2_projection_threads "
                                         "WHERE deleted_at IS NULL AND json_extract(payload_json, "
                                         "'$.lineage.relationshipToParent') IS NOT 'subagent'"):
                out.setdefault(thread, _when(created))
        return out

    def lineage(self, s: Settings) -> Iterable[Lineage]:
        facts: list[Lineage] = []
        for root in self.roots(s):
            db = _database(self.store(root), root)
            native: dict[str, tuple[HarnessId, str]] = {}
            for row in _dicts(db, "SELECT * FROM orchestration_v2_projection_provider_threads"):
                ref = (json.loads(row.get("payload_json") or "{}").get("nativeThreadRef") or {})
                if ref.get("driver") in self.DRIVERS and ref.get("nativeId"):
                    native[row["thread_id"]] = (self.DRIVERS[ref["driver"]], ref["nativeId"])
            facts += [Lineage(child, None, T3_CODE) for child in native.values()]
            for row in _dicts(db, "SELECT * FROM orchestration_v2_projection_threads"):
                lineage = json.loads(row.get("payload_json") or "{}").get("lineage") or {}
                child, parent = native.get(row["thread_id"]), native.get(lineage.get("parentThreadId"))
                relationship = lineage.get("relationshipToParent")
                if relationship == "subagent" and child and parent:
                    facts.append(Lineage(child, parent, T3_CODE))
                elif relationship == "fork" and child:
                    facts.append(Lineage(child, None, T3_CODE, own=True))
        return facts


ADAPTERS = {HarnessId.CLAUDE_CODE: ClaudeCode(), HarnessId.CODEX: Codex(), HarnessId.DEVIN: Devin(),
            HarnessId.HERMES: Hermes()}
# ChatGPT and claude.ai are export-mode Sources, read from the inbox by sources/_webchat.py.


def linked(root: Path | str, harness: str | None = None) -> bool:
    """Whether a folder the owner named, or a store folder Atlas opens under it for `harness`, is
    reached through a link: any part of it below the home folder, or below / when it is outside
    home, is a symbolic link. A part that does not exist yet ends the walk."""
    path = Path(root).expanduser()
    adapter = next((a for h, a in ADAPTERS.items() if h.value == harness), None)
    return any(_through_link(p) for p in (path, *(adapter.opened(path) if adapter else ())))


def defaults() -> dict[HarnessId, str]:
    """Each store Harness's folder that Atlas always reads, as settings.md writes it."""
    return {h: _tilde(primary) for h, a in ADAPTERS.items() if (primary := a.primary()) is not None}


def _usable(path: Path) -> bool:
    """Whether the system can be given this path: it encodes and holds no NUL."""
    try:
        return b"\0" not in os.fsencode(path)
    except (UnicodeError, ValueError):
        return False


def _through_link(path: Path) -> bool:
    home = Path.home()
    base = home if path.is_relative_to(home) else Path(path.anchor or "/")
    current = base
    for part in path.relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            return True
        if not current.exists():
            return False
    return False


def _tilde(path: Path) -> str:
    home = Path.home()
    if path == home:
        return "~"
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def _when(value: object) -> datetime:
    """A store timestamp as an aware datetime: epoch seconds or milliseconds, or ISO 8601."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _text(content: object) -> str:
    """A message's text: a string, or its text blocks ({"type": "text" or "Text", "text"})."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n\n".join(b["text"].strip() for b in content if isinstance(b, dict)
                           and str(b.get("type")).lower() == "text" and isinstance(b.get("text"), str)).strip()
    return ""


def _within(folder: Path) -> _Folder:
    """The real folder a store's files must stay under. A folder Atlas may not list raises, so the
    store is logged as unreadable instead of read as empty, and so does a folder that is a link."""
    if folder.is_symlink():
        raise LinkedElsewhere(folder.name)
    with os.scandir(folder):
        return _folder(folder)


def _folder(folder: Path) -> _Folder:
    st = os.stat(folder)
    real = os.path.realpath(folder)
    if not os.path.samestat(os.stat(real), st):
        raise SwappedWhileRead("a store folder changed while Atlas listed it")
    return _Folder(real, st)


def _files(folder: Path, pattern: str, store: Path, base: _Folder) -> list[_File]:
    """Regular files in folder that match pattern, reached from the store without a link: a
    symlink is never followed, so it never leads out of the store."""
    found = []
    for path in sorted(folder.glob(pattern)):
        names = path.relative_to(store).parts
        try:
            st = path.lstat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and os.path.realpath(path) == os.path.join(base.path, *names):
            found.append(_File(path, base, names, st))
    return found


def _file_unit(harness: HarnessId, native: str, files: dict[str, _File]) -> Unit:
    return Unit(harness, native, native, {k: (_tilde(f.path), _file_print(f.st)) for k, f in files.items()},
                sum(f.st.st_size for f in files.values()), files)


def _file_print(st: os.stat_result) -> str:
    return f"{st.st_size}@{st.st_mtime_ns}"


def _open(f: _File) -> io.BufferedReader:
    return open(_descriptor(f.base, f.names), "rb")


def _descriptor(base: _Folder, names: Sequence[str]) -> int:
    folder = _inside(base, names[:-1])
    try:
        return _regular(folder, names[-1])
    finally:
        os.close(folder)


def _inside(base: _Folder, names: Sequence[str]) -> int:
    """The store's real folder, which must be the folder listed, then the folders `names` below it,
    opened one name at a time with O_NOFOLLOW, so no folder swapped for a link after listing is
    followed. A folder needs no listing to be walked."""
    fd = os.open(base.path, _SEARCH)
    try:
        if not os.path.samestat(os.fstat(fd), base.st):
            raise SwappedWhileRead("a store folder changed after Atlas listed it")
        for name in names:
            inner = os.open(name, _SEARCH, dir_fd=fd)
            os.close(fd)
            fd = inner
        return fd
    except BaseException:
        os.close(fd)
        raise


def _regular(folder: int, name: str) -> int:
    """A regular file in folder, never through a link, opened without waiting, so a FIFO or a
    device in its place is refused instead of waited on."""
    opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    try:
        if not stat.S_ISREG(os.fstat(opened).st_mode):
            raise IsADirectoryError(name)
        os.set_blocking(opened, True)
        return opened
    except BaseException:
        os.close(opened)
        raise


def _slurp(f: _File) -> tuple[bytes, str]:
    """A file's bytes and the fingerprint of what was read, taken from the same descriptor
    before reading: a write after it shows at the next listing."""
    with _open(f) as fh:
        print_ = _file_print(os.fstat(fh.fileno()))
        return fh.read(), print_


def _first_line(f: _File) -> str:
    with _open(f) as fh:
        if f.path.suffix != ".zst":
            return fh.readline().decode()
        with zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True) as reader:
            return io.TextIOWrapper(reader, encoding="utf-8").readline()


def _unzstd(data: bytes) -> bytes:
    return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data), read_across_frames=True).read()


def _guarded_content(unit: Unit) -> int:
    """How much a copy holds: a file's bytes once decompressed, a SQLite unit's rows. A copy
    that cannot be read holds nothing, so the other is read."""
    if not isinstance(unit.ref, dict):
        return unit.size
    try:
        return sum(_decompressed(f) if f.path.suffix == ".zst" else f.st.st_size for f in unit.ref.values())
    except Exception:
        return -1


def _decompressed(f: _File) -> int:
    size = 0
    with _open(f) as fh, zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True) as reader:
        while chunk := reader.read(1 << 20):
            size += len(chunk)
    return size


def _database(path: Path, root: Path) -> _Db:
    """A store database, read only as a regular file reached from its root without a link. Its
    root need not be listable, since Atlas only walks down known names."""
    folder, names = _folder(root), path.relative_to(root).parts
    if not stat.S_ISREG(path.lstat().st_mode) or os.path.realpath(path) != os.path.join(folder.path, *names):
        raise LinkedElsewhere(path.name)
    return _Db(path, folder, names)


def _read_db(db: _Db, read: Callable[[sqlite3.Connection], T]) -> T:
    """`read` inside one read transaction, so all its queries see the same rows. Busy timeout
    5 s, then three tries 50 ms apart on a transient error."""
    for _ in range(2):
        try:
            return _transaction(db, read)
        except sqlite3.OperationalError:
            time.sleep(0.05)
    return _transaction(db, read)


def _transaction(db: _Db, read: Callable[[sqlite3.Connection], T]) -> T:
    """Atlas pins the database as it opens a store file and gives SQLite the pin's name, which
    SQLite resolves to the file's current name and opens by it, or the real path where the system
    names no descriptor. The read counts only when the file SQLite names is the pin's or, under the
    root and by no link, still the pinned file: one swapped in between is never read. SQLite opens
    the WAL and its index by name beside that file, so it reads them only from the checked folder,
    as `_wal` says."""
    folder = _inside(db.root, db.names[:-1])
    try:
        pin, held = _pinned(folder, db.names[-1])
    except BaseException:
        os.close(folder)
        raise
    try:
        own, base = f"{_DESCRIPTORS}/{pin}" if pin is not None else None, db.names[-1]
        name = own if own and _same_file(own, held) else os.path.join(db.root.path, *db.names)
        _sidecars(folder, base)
        with closing(sqlite3.connect(Path(name).as_uri() + "?mode=ro", uri=True, timeout=5,
                                     isolation_level=None)) as con:
            con.row_factory = sqlite3.Row
            con.execute("BEGIN")
            result = read(con)
            opened = os.fsdecode(con.execute("SELECT CAST(file AS BLOB) FROM pragma_database_list "
                                             "WHERE name = 'main'").fetchone()[0])
            if opened != own and not (Path(opened).is_relative_to(db.root.path)
                                       and os.path.realpath(opened) == opened and _same_file(opened, held)):
                raise SwappedWhileRead(db.path.name)
            sidecars = _sidecars(folder, base)
            if con.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
                _wal(db.path.name, sidecars, folder)
            return result
    finally:
        if pin is not None:
            os.close(pin)
        os.close(folder)


def _pinned(folder: int, name: str) -> tuple[int | None, os.stat_result]:
    """The database in folder, never through a link, and never opened to read: closing a descriptor
    that reads a file drops every POSIX lock the process holds on it, those of Hermes's own
    connection too. Where the system has O_PATH it is pinned by a descriptor SQLite can be given the
    name of; elsewhere only its stat is taken."""
    if _PIN is None:
        pin, st = None, os.stat(name, dir_fd=folder, follow_symlinks=False)
    else:
        pin = os.open(name, _PIN | os.O_NOFOLLOW, dir_fd=folder)
        st = os.fstat(pin)
    if not stat.S_ISREG(st.st_mode):
        if pin is not None:
            os.close(pin)
        raise LinkedElsewhere(name)
    return pin, st


def _wal(name: str, sidecars: tuple[tuple[int, int] | None, ...], near: int) -> None:
    """A read that used a WAL counts only when the WAL and its index are both in the checked folder
    and both files this process holds. So a WAL or index that SQLite reached through a folder swapped
    while it opened them, or under another name of a hard-linked database, is never read."""
    files = set(sidecars)
    if None in files or not _held(near, files):
        raise SwappedWhileRead(name)


def _sidecars(folder: int, base: str) -> tuple[tuple[int, int] | None, ...]:
    """The device and inode of the database's `-wal` and `-shm` in its checked folder, None for one
    that is not there. One that is not a regular file, or has another link, raises. A link count of
    0 is a file its writer is removing, so it is still the store's own."""
    found = []
    for name in (f"{base}-wal", f"{base}-shm"):
        try:
            st = os.stat(name, dir_fd=folder, follow_symlinks=False)
        except FileNotFoundError:
            found.append(None)
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            raise LinkedElsewhere(name)
        found.append((st.st_dev, st.st_ino))
    return tuple(found)


def _held(near: int, files: set[tuple[int, int]]) -> bool:
    """Whether this process holds open or maps each of `files`, by device and inode, or True where
    the system lists no descriptors, so only the names are checked. It looks first at the few
    descriptors above `near`, where SQLite opens a connection's own, then at the files the process
    maps, where SQLite keeps the index it uses, and lists every descriptor only when those miss,
    since a listing costs about 1 ms per thousand descriptors."""
    found = _near(near)
    if files <= found:
        return True
    found |= _mapped()
    if files <= found:
        return True
    listed = _open_files(near)
    return listed is None or files <= found | listed


def _near(near: int) -> set[tuple[int, int]]:
    found = set()
    for n in range(near + 1, near + _NEAR):
        try:
            st = os.fstat(n)
        except OSError:
            continue
        found.add((st.st_dev, st.st_ino))
    return found


def _mapped() -> set[tuple[int, int]]:
    try:
        with open(_MAPS) as maps:
            rows = [line.split(maxsplit=5) for line in maps]
    except OSError:
        return set()
    found = set()
    for row in rows:
        if len(row) > 4 and row[4] != "0":
            major, minor = row[3].split(":")
            found.add((os.makedev(int(major, 16), int(minor, 16)), int(row[4])))
    return found


def _open_files(held: int) -> set[tuple[int, int]] | None:
    """Each file this process holds a descriptor to, or None where the system lists no descriptors,
    which a listing without `held` shows."""
    try:
        listed = os.listdir(_OPEN)
    except OSError:
        return None
    if str(held) not in listed:
        return None
    found = set()
    for n in listed:
        try:
            st = os.fstat(int(n))
        except OSError:
            continue
        found.add((st.st_dev, st.st_ino))
    return found


def _same_file(name: str, held: os.stat_result) -> bool:
    try:
        return os.path.samestat(os.stat(name), held)
    except OSError:
        return False


def _prints(con: sqlite3.Connection, table: str, order: str, part: Callable[[dict, list[dict]], list[dict]],
            identity: Callable[[dict], str]) -> tuple[dict[str, dict], dict[str, tuple[str, int]]]:
    """Every session row, and the fingerprint and size of each session's part as it would be
    copied. Messages stream in session order, so one session's rows are in memory at a time."""
    sessions = {r["id"]: dict(r) for r in con.execute("SELECT * FROM sessions")}
    rows = con.execute(f"SELECT * FROM {table} ORDER BY session_id, {order}")
    return sessions, {sid: _summary(part(sessions[sid], [dict(r) for r in group]), identity)
                      for sid, group in itertools.groupby(rows, key=lambda r: r["session_id"]) if sid in sessions}


def _session_rows(con: sqlite3.Connection, table: str, order: str,
                  ids: Sequence[str]) -> tuple[dict[str, dict], list[dict]]:
    marks = ",".join("?" * len(ids))
    sessions = {r["id"]: dict(r) for r in con.execute(f"SELECT * FROM sessions WHERE id IN ({marks})", tuple(ids))}
    return sessions, [dict(m) for m in con.execute(
        f"SELECT * FROM {table} WHERE session_id IN ({marks}) ORDER BY {order}", tuple(ids))]


def _rows_print(lines: Sequence[dict], identity: Callable[[dict], str]) -> str:
    """`<rows>@<sha256 prefix of their identities>`: it changes when a row Atlas reads does, and
    not when only a column it does not read does."""
    digest = hashlib.sha256("\n".join(map(identity, lines)).encode()).hexdigest()
    return f"{len(lines)}@{digest[:16]}"


def _summary(lines: Sequence[dict], identity: Callable[[dict], str]) -> tuple[str, int]:
    return _rows_print(lines, identity), len(_encode(lines))


def _records(rows: Sequence[tuple[str, dict]]) -> list[dict]:
    """Store rows as a raw copy holds them, one JSON object per row: {"<table>": {column: value}}."""
    return [{table: {k: _jsonable(v) for k, v in row.items()}} for table, row in rows]


def _encode(lines: Sequence[dict]) -> bytes:
    return "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines).encode()


def _record(line: bytes) -> dict | None:
    """A line as the JSON object its Harness wrote, or None when it does not parse as one."""
    try:
        r = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return r if isinstance(r, dict) else None


def _glued(line: bytes) -> tuple[int, dict | None]:
    """Where the shortest tail of a line that parses as one whole JSON object starts, and that
    object, or the line's length and None. json reads a tail as UTF-8, or as UTF-16 or 32 LE when
    a NUL follows its `{`. In the units of each, only the `{` that `_opening` finds can start one."""
    starts = {_opening(line, width) for width in (1, 2, 4)}
    for at in sorted(starts - {None, 0}, reverse=True):
        if (r := _record(line[at:])) is not None:
            return at, r
    return len(line), None


def _opening(line: bytes, width: int) -> int | None:
    """Where the `{` that opens the object ending the line is, in units of `width` bytes aligned
    to its end, matching brackets back from its last `}` outside strings, or None when none does.
    A quote is escaped when an odd run of backslashes precedes it."""
    lead = len(line) % width
    body = _units(line[lead:], width).rstrip(b" \t\n\r")
    if not body.endswith(b"}"):
        return None
    depth, quoted = 0, False
    for m in _BACKWARDS.finditer(body[::-1]):
        c = m[0][0]
        if c == ord('"'):
            quoted ^= len(m[0]) % 2 == 1
        elif not quoted:
            depth += 1 if c in b"}]" else -1
            if depth == 0:
                return lead + width * (len(body) - 1 - m.start()) if c == ord("{") else None
    return None


def _units(data: bytes, width: int) -> bytes:
    """One byte per little-endian unit of `width` bytes: its low byte when the rest are NUL,
    else 0xFF, which no bracket, quote, backslash or space is."""
    low, rest = data[::width], 0
    for i in range(1, width):
        rest |= int.from_bytes(data[i::width].translate(_NONZERO))
    return (int.from_bytes(low) | rest).to_bytes(len(low))


def _canonical(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _jsonable(value: object) -> object:
    return {"base64": base64.b64encode(value).decode()} if isinstance(value, bytes) else value


def _rows(db: _Db, sql: str) -> list[tuple]:
    return _read_db(db, lambda con: [tuple(r) for r in con.execute(sql)])


def _dicts(db: _Db, sql: str) -> list[dict]:
    return _read_db(db, lambda con: [dict(r) for r in con.execute(sql)])
