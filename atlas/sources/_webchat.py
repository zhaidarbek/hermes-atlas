"""Web-chat exports (ChatGPT, claude.ai) the owner drops into the record's inbox/ (A5, D Q5).

An export is the most hostile input Atlas reads: a ZIP a stranger could have crafted. So:
- inbox/ is opened without following a link, and so is each file in it; a link, a folder or any
  other file that is not a regular `.zip` is never opened.
- An archive is read in memory, never extracted, and only its `conversations.json` or
  `conversations-<n>.json` shards are opened, by the names its directory lists. `group_chats.json`,
  other people's messages, is never opened.
- Sizes are capped before and while reading: the archive (MAX_ARCHIVE); its directory, by the
  entry count and size its end record states, before zipfile builds an entry (MAX_ENTRIES,
  MAX_DIRECTORY); each shard (MAX_SHARD) and all shards together (MAX_READ), each checked against
  the size the archive declares and the bytes it actually inflates to, so a zip bomb stops at the
  cap; and what parsing the shards would build (MAX_PARSED), bounded from their bytes as they
  inflate, before json builds anything. More than MAX_CONVERSATIONS conversations fail the file.
- A time outside EARLIEST to a day past the tick is no time: a turn without one is placed as an
  undated one, and a conversation left with no time at all is broken.
- A file that fails is kept, and only its failure's type is reported, never its name or text.

The formats are undocumented by both vendors and follow what open-source parsers read
(UNVERIFIED seam, ADR 0008). ChatGPT: a conversation's `mapping` of parent-pointer nodes, its
branch read from `current_node` up; times in epoch seconds, a message without one placed by
activetime.undated (D Q6). claude.ai: `chat_messages` in order, or along `parent_message_uuid`
to the newest leaf when they carry it; ISO times.

A conversation's raw copy is one JSON line for the conversation, then one per message of its
branch, each as the export holds it. A conversation read again adds the messages its copy lacks,
by id, so a message a later export drops stays (D Q14), and turns are parsed from the whole copy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, NamedTuple

from .. import activetime
from ..model import (LOCAL, HarnessId, RawPart, SessionEvidence, Settings, Turn, evidence_id, part_file, part_origin,
                     skip_key)

WEB_CHATS = (HarnessId.CHATGPT, HarnessId.CLAUDE_AI)
MAX_ARCHIVE = 2 << 30
MAX_ENTRIES = 100_000
MAX_DIRECTORY = 8 << 20
MAX_SHARD = 256 << 20
MAX_READ = 1 << 30
MAX_PARSED = 1 << 30
"""What json may build from all shards of one file, as `_cost` bounds it from their bytes."""
MAX_CONVERSATIONS = 20_000
EARLIEST = datetime(2015, 1, 1, tzinfo=timezone.utc)
AHEAD = timedelta(days=1)
"""A time is kept only from EARLIEST to AHEAD past the tick: the record can hold every such time."""
SETTLED = timedelta(minutes=1)
"""A file modified more recently may still be arriving: it waits for a later tick."""

_SHARD = re.compile(r"(?:^|/)conversations(?:-\d+)?\.json$")
_CHUNK = 1 << 20
MAIN = "main"


class Refused(Exception):
    """The export breaks one of the limits above, or is no export Atlas reads."""


class TooLarge(Refused):
    pass


class NotAnExport(Refused):
    pass


@dataclass(frozen=True)
class Stamp:
    """What a file in the inbox is, as Atlas read it: deleted only while it is still this file."""
    device: int
    inode: int
    size: int
    modified_ns: int

    def key(self) -> str:
        return f"{self.device}:{self.inode}:{self.size}:{self.modified_ns}"


@dataclass(frozen=True)
class Conversation:
    harness: HarnessId
    key: str                                  # the vendor's conversation id
    title: str
    lines: tuple[dict, ...]                   # the raw copy's lines: the conversation, then its messages


@dataclass(frozen=True)
class Export:
    """One file in the inbox, read."""
    name: str
    stamp: Stamp
    harness: HarnessId | None                 # None: nothing in it was read
    conversations: tuple[Conversation, ...] = ()
    failed: str | None = None                 # the whole file: its failure's type
    broken: int = 0                           # conversations that could not be read: the file is kept
    group_chats: bool = False                 # it holds other people's group chats, never opened
    skipped: int = 0                          # conversations with no text, or held twice under one id
    other: HarnessId | None = None            # the web chat it is from, when the owner has not added it

    @property
    def whole(self) -> bool:
        return self.failed is None and not self.broken


@dataclass(frozen=True)
class Imported:
    """One conversation as the record should hold it now."""
    evidence: SessionEvidence
    raw: Mapping[str, bytes]
    skip_keys: frozenset[str] = field(default_factory=frozenset)


# ------------------------------------------------------------------ the inbox

def waiting(inbox: Path, now: datetime) -> list[tuple[str, Stamp]]:
    """The regular `.zip` files directly in inbox/, oldest first, that have not changed for
    SETTLED. inbox/ itself and each file are looked at without following a link, and a link is
    never listed."""
    fd = _open_inbox(inbox)
    if fd is None:
        return []
    try:
        found = []
        for name in os.listdir(fd):
            if not name.lower().endswith(".zip"):
                continue
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and now.timestamp() - st.st_mtime >= SETTLED.total_seconds():
                found.append((name, _stamp(st)))
        return sorted(found, key=lambda f: (f[1].modified_ns, f[0]))
    finally:
        os.close(fd)


def read(inbox: Path, name: str, stamp: Stamp, enabled: Iterable[HarnessId]) -> Export:
    """The export as read, or its failure. Never raises."""
    try:
        fd = _open_inbox(inbox)
        if fd is None:
            raise NotAnExport("inbox")
        try:
            file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=fd)
        finally:
            os.close(fd)
        with os.fdopen(file, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or _stamp(st) != stamp:
                raise NotAnExport("changed")
            if st.st_size > MAX_ARCHIVE:
                raise TooLarge("archive")
            directory(f, st.st_size)
            with zipfile.ZipFile(f) as archive:
                got = parse_archive(archive)
    except Exception as error:
        return Export(name, stamp, None, failed=type(error).__name__)
    if got.harness is not None and got.harness not in set(enabled):
        return Export(name, stamp, None, other=got.harness)
    return Export(name, stamp, got.harness, got.conversations, None, got.broken, got.group, got.skipped)


def release(inbox: Path, name: str, stamp: Stamp) -> bool:
    """Deletes the file only while inbox/ is no link and the name still holds the regular file
    Atlas read, so a file swapped in meanwhile stays. Called only after the commit that holds
    every conversation in it."""
    fd = _open_inbox(inbox)
    if fd is None:
        return False
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or _stamp(st) != stamp:
            return False
        os.unlink(name, dir_fd=fd)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _open_inbox(inbox: Path) -> int | None:
    try:
        parent = os.open(inbox.parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        return os.open(inbox.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError:
        return None
    finally:
        os.close(parent)


def _stamp(st: os.stat_result) -> Stamp:
    return Stamp(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


# ------------------------------------------------------------------ the archive

_END = b"PK\x05\x06"
_END64 = b"PK\x06\x06"
_LOCATOR64 = b"PK\x06\x07"


def directory(f, size: int) -> None:
    """Refuses an archive whose end record states more than MAX_ENTRIES entries or a directory
    larger than MAX_DIRECTORY, read from the end record (zip64 too) before zipfile builds an entry
    for each. zipfile reads the directory by its stated size, so the size bounds what it builds."""
    tail_size = min(size, 22 + 0xFFFF)
    f.seek(size - tail_size)
    tail = f.read(tail_size)
    at = len(tail) - 22 if tail[-22:-18] == _END else tail.rfind(_END)
    if at < 0 or len(tail) - at < 22:
        raise NotAnExport("no directory")
    entries, length = struct.unpack("<HI", tail[at + 10:at + 16])
    if (entries == 0xFFFF or length == 0xFFFFFFFF) and at >= 20 and tail[at - 20:at - 16] == _LOCATOR64:
        (offset,) = struct.unpack("<Q", tail[at - 12:at - 4])
        if offset + 56 > size:
            raise NotAnExport("no directory")
        f.seek(offset)
        record = f.read(56)
        if record[:4] != _END64:
            raise NotAnExport("no directory")
        entries, length = struct.unpack("<QQ", record[32:48])
    if entries > MAX_ENTRIES:
        raise TooLarge("entries")
    if length > MAX_DIRECTORY:
        raise TooLarge("directory")
    f.seek(0)


class Parsed(NamedTuple):
    harness: HarnessId | None
    conversations: tuple[Conversation, ...]   # one per id, the copy updated last
    broken: int                               # could not be read
    group: bool                               # it holds group chats
    skipped: int                              # had no text, or was held twice under one id


def parse_archive(archive: zipfile.ZipFile, parsed: int = 0) -> Parsed:
    """The archive's Harness and conversations. Raises Refused past a limit, `parsed` bounding
    what json may build in place of MAX_PARSED when given."""
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES:
        raise TooLarge("entries")
    shards = sorted((e for e in entries if _SHARD.search(e.filename) and not e.is_dir()), key=lambda e: e.filename)
    if not shards:
        raise NotAnExport("no conversations")
    group = any(Path(e.filename).name == "group_chats.json" for e in entries)
    budget, room = MAX_READ, parsed or MAX_PARSED
    harness: HarnessId | None = None
    found: dict[str, tuple[float, Conversation]] = {}
    broken = skipped = 0
    for entry in shards:
        data, cost = _inflate(archive, entry, min(MAX_SHARD, budget), room)
        budget -= len(data)
        room -= cost
        items = json.loads(data)
        del data
        if isinstance(items, dict):
            items = items.get("conversations", [])
        if not isinstance(items, list):
            raise NotAnExport("shape")
        if len(items) > MAX_CONVERSATIONS:
            raise TooLarge("conversations")
        for item in items:
            try:
                kind = _kind(item)
                if kind is None:
                    broken += 1
                    continue
                harness = harness or kind
                if kind is not harness:
                    broken += 1
                    continue
                c = (chatgpt if kind is HarnessId.CHATGPT else claude_ai)(item)
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                broken += 1
                continue
            if c is None:
                skipped += 1
                continue
            updated = _updated(item)
            if c.key in found:
                skipped += 1
            if c.key not in found or updated >= found[c.key][0]:
                found[c.key] = (updated, c)
        if len(found) > MAX_CONVERSATIONS:
            raise TooLarge("conversations")
    return Parsed(harness, tuple(c for _, c in found.values()), broken, group, skipped)


_TOKENS = (b"[", b"{", b",", b":")
_PER_TOKEN = 96
_RAW = re.compile(rb"[\x80-\xff]")
_ASTRAL = re.compile(rb"\\u[dD][89abAB]")
_UCS2 = re.compile(rb"\\u(?:0[1-9a-fA-F]|[1-9a-fA-F])")


def _per_byte(chunk: bytes) -> int:
    """What json builds for each byte at most: the byte, the text it decodes to (four bytes a
    character once any is not ASCII), and the string it lands in, as wide as its widest
    character, which an escape can widen, while json's writer grows and widens it."""
    if _RAW.search(chunk):
        return 10
    if _ASTRAL.search(chunk):
        return 8
    return 6 if _UCS2.search(chunk) else 4


def _cost(size: int, tokens: int, per_byte: int) -> int:
    """At most what json.loads builds from `size` bytes holding `tokens` brackets, commas and
    colons: `per_byte` for each byte, and _PER_TOKEN for each value a token opens or separates, at
    its largest (an empty dict or list, a short string or number). Measured on CPython 3.14 for
    nested and flat empty containers, distinct keys, short strings, numbers, long strings of each
    width and a ChatGPT export: never more than 87 bytes a token, 8 a byte of raw UTF-8, 7.3 of
    ASCII with an astral escape, 4.8 with a two-byte one and 3.1 with none."""
    return size * per_byte + tokens * _PER_TOKEN


def _inflate(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, cap: int, room: int) -> tuple[bytes, int]:
    """The entry's bytes and what parsing them may build, read in chunks and refused as soon as
    they pass `cap`, the size the archive declares for it, or `room` for what parsing builds."""
    if entry.flag_bits & 0x1:
        raise NotAnExport("encrypted")
    if entry.file_size > cap:
        raise TooLarge("shard")
    out = bytearray()
    tokens, per_byte, tail = 0, 4, b""
    with archive.open(entry) as f:
        while chunk := f.read(_CHUNK):
            out += chunk
            if len(out) > entry.file_size or len(out) > cap:
                raise TooLarge("inflated")
            tokens += sum(chunk.count(t) for t in _TOKENS)
            per_byte = max(per_byte, _per_byte(tail + chunk))
            tail = chunk[-5:]
            if _cost(len(out), tokens, per_byte) > room:
                raise TooLarge("parsed")
    return bytes(out), _cost(len(out), tokens, per_byte)


def _kind(item: object) -> HarnessId | None:
    if not isinstance(item, dict):
        return None
    if isinstance(item.get("mapping"), dict):
        return HarnessId.CHATGPT
    if isinstance(item.get("chat_messages"), list):
        return HarnessId.CLAUDE_AI
    return None


def _updated(item: dict) -> float:
    at = _time(item.get("update_time", item.get("updated_at")))
    return at.timestamp() if at else 0.0


# ------------------------------------------------------------------ one conversation

def chatgpt(item: dict) -> Conversation | None:
    """The branch that ends at `current_node`, or at the newest leaf when it names none."""
    key = str(item.get("conversation_id") or item.get("id") or "")
    if not key:
        raise ValueError("a conversation with no id")
    mapping: dict = item["mapping"]
    node = item.get("current_node")
    if node not in mapping:
        leaves = [k for k, n in mapping.items() if isinstance(n, dict) and not n.get("children")]
        node = max(leaves, key=lambda k: _node_time(mapping[k]), default=None)
    branch, seen = [], set()
    while node is not None and node in mapping and node not in seen:
        seen.add(node)
        n = mapping[node]
        if isinstance(n, dict) and isinstance(n.get("message"), dict):
            branch.append(n["message"])
        node = n.get("parent") if isinstance(n, dict) else None
    messages = [m for m in reversed(branch) if _gpt_role(m) is not None and _gpt_text(m)]
    if not messages:
        return None
    head = {"id": key, "title": item.get("title"), "create_time": item.get("create_time"),
            "update_time": item.get("update_time")}
    return Conversation(HarnessId.CHATGPT, key, _str(item.get("title")), ({"conversation": head},
                                                                          *({"message": m} for m in messages)))


def claude_ai(item: dict) -> Conversation | None:
    key = str(item.get("uuid") or "")
    if not key:
        raise ValueError("a conversation with no id")
    messages = [m for m in item["chat_messages"] if isinstance(m, dict)]
    if any("parent_message_uuid" in m for m in messages):
        by_id = {m.get("uuid"): m for m in messages}
        parents = {m.get("parent_message_uuid") for m in messages}
        leaf = max((m for m in messages if m.get("uuid") not in parents),
                   key=lambda m: (_time(m.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)), default=None)
        branch, seen = [], set()
        while leaf is not None and leaf.get("uuid") not in seen:
            seen.add(leaf.get("uuid"))
            branch.append(leaf)
            leaf = by_id.get(leaf.get("parent_message_uuid"))
        messages = list(reversed(branch))
    messages = [m for m in messages if _claude_role(m) is not None and _claude_text(m)]
    if not messages:
        return None
    head = {"id": key, "name": item.get("name"), "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at")}
    return Conversation(HarnessId.CLAUDE_AI, key, _str(item.get("name")), ({"conversation": head},
                                                                           *({"message": m} for m in messages)))


def identity(line: dict) -> str:
    """A raw line's identity: the conversation, or a message by its id."""
    if "conversation" in line:
        return "conversation"
    m = line.get("message")
    id = m.get("id", m.get("uuid")) if isinstance(m, dict) else None
    return f"message:{id}" if isinstance(id, str) and id else "line:" + hashlib.sha256(
        json.dumps(line, sort_keys=True).encode()).hexdigest()


def session(c: Conversation, held: Callable[[str], bytes | None], s: Settings, now: datetime) -> Imported:
    """The conversation as the record should hold it: the copy it holds, then each line read
    whose identity that copy lacks, and every turn parsed from that whole copy, as of `now`.
    Raises ValueError for a conversation with no time, UnicodeError for one the record cannot
    write."""
    origin = part_origin(c.harness, c.key, MAIN)
    copy = held(origin) or b""
    have = {identity(line) for line in _lines(copy)}
    added = [line for line in c.lines if identity(line) not in have]
    if not copy and added and "conversation" not in added[0]:
        added.insert(0, c.lines[0])
    data = copy + b"".join(json.dumps(line, ensure_ascii=False).encode() + b"\n" for line in added)
    lines = _lines(data)
    turns, started, last = parse(c.harness, lines, now)
    title = c.title or next((_title(t.text) for t in turns if t.role == "owner"), "")
    print_ = hashlib.sha256("\n".join(identity(line) for line in lines).encode()).hexdigest()
    label = c.harness.label
    evidence = SessionEvidence(
        id=evidence_id(c.harness, LOCAL, c.key), harness=c.harness, interface=None, namespace=LOCAL,
        native_key=c.key, title=title, started=started, last_at=last, machine=s.machine, cwd=None, git_branch=None,
        parts=(RawPart(part_file(MAIN), "", f"{label} export", print_, origin),), turns=turns)
    return Imported(evidence, {part_file(MAIN): data}, frozenset({skip_key(c.harness, c.key)}))


def parse(harness: HarnessId, lines: Iterable[dict], now: datetime) -> tuple[tuple[Turn, ...], datetime, datetime]:
    """The turns of a raw copy, in order, its start and its last moment. A message without a time,
    or with one outside EARLIEST to AHEAD past `now`, is placed by activetime.undated between its
    neighbours on the branch."""
    def sane(value: object) -> datetime | None:
        at = _time(value)
        return at if at is not None and EARLIEST <= at <= now + AHEAD else None

    head: dict = {}
    said: list[tuple[str, str, datetime | None]] = []
    for line in lines:
        if "conversation" in line and isinstance(line["conversation"], dict):
            head = head or line["conversation"]
            continue
        m = line.get("message")
        if not isinstance(m, dict):
            continue
        if harness is HarnessId.CHATGPT:
            role, text, at = _gpt_role(m), _gpt_text(m), sane(m.get("create_time"))
        else:
            role, text, at = _claude_role(m), _claude_text(m), sane(m.get("created_at"))
        if role is not None and text:
            said.append((role, text, at))
    created = sane(head.get("create_time", head.get("created_at")))
    updated = sane(head.get("update_time", head.get("updated_at")))
    placed = activetime.undated([at for _, _, at in said], created, updated)
    turns = tuple(Turn(role, at, text, MAIN, at_interpolated=was is None and at is not None)
                  for (role, text, was), at in zip(said, placed))
    times = [t.at for t in turns if t.at is not None]
    started = created or (min(times) if times else None) or updated
    if started is None:
        raise ValueError("a conversation with no time")
    last = max([*times, started], key=lambda t: t.timestamp())
    return turns, started, last


def _lines(data: bytes) -> list[dict]:
    out = []
    for raw in data.splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if isinstance(line, dict):
            out.append(line)
    return out


def _gpt_role(m: dict) -> str | None:
    meta = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
    if meta.get("is_visually_hidden_from_conversation"):
        return None
    role = (m.get("author") or {}).get("role") if isinstance(m.get("author"), dict) else None
    return {"user": "owner", "assistant": "agent"}.get(role)


def _gpt_text(m: dict) -> str:
    content = m.get("content")
    if not isinstance(content, dict):
        return ""
    if content.get("content_type") not in (None, "text", "multimodal_text", "code"):
        return ""
    parts = content.get("parts")
    if isinstance(parts, list):
        texts = [p if isinstance(p, str) else p.get("text") if isinstance(p, dict) else None for p in parts]
        return "\n".join(t for t in texts if isinstance(t, str) and t.strip()).strip()
    text = content.get("text")
    return text.strip() if isinstance(text, str) else ""


def _claude_role(m: dict) -> str | None:
    return {"human": "owner", "assistant": "agent"}.get(m.get("sender"))


def _claude_text(m: dict) -> str:
    blocks = m.get("content")
    if isinstance(blocks, list):
        texts = [b.get("text") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        joined = "\n".join(t for t in texts if isinstance(t, str) and t.strip()).strip()
        if joined:
            return joined
    text = m.get("text")
    return text.strip() if isinstance(text, str) else ""


def _node_time(n: object) -> float:
    m = n.get("message") if isinstance(n, dict) else None
    at = _time(m.get("create_time")) if isinstance(m, dict) else None
    return at.timestamp() if at else 0.0


def _time(value: object) -> datetime | None:
    """Epoch seconds or milliseconds, or ISO 8601; anything else is no time."""
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(seconds, timezone.utc)
        if isinstance(value, str) and value:
            at = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _title(said: str) -> str:
    first = said.strip().splitlines()[0] if said.strip() else ""
    return first[:80]
