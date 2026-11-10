"""Which live Session an agent's tool call comes from, and whether the owner's words are in it.

Read from the calling process and the Harness's own store at call time, never from what the agent
says (ADR 0009, ADR 0010):
- Claude Code: the server's parent process is the Claude Code process, and Claude Code keeps
  `<config dir>/sessions/<pid>.json` for each live process: its pid, the Session's id, and the
  process start time, which /proc must confirm so a stale file of a reused pid binds nothing.
- Codex: `_meta.threadId`, which Codex adds to every `tools/call` and the agent cannot write,
  names a rollout file; that rollout's own Codex version must be one verified (ADR 0010).
- Devin, Hermes and any other client: no binding in v1 (D Q12), so an update becomes a
  refused-update Note the owner can "apply".
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from ..model import LOCAL, BoundSession, EvidenceId, HarnessId, Instruction, Sent, Settings, Status, evidence_id
from ._adapters import ADAPTERS, MAIN, ClaudeCode, Codex, _claude_turn, _first_line, _record, _when

log = logging.getLogger("atlas.sources")

VERIFIED_CODEX = frozenset({"0.167.0"})       # ADR 0010: versions whose _meta.threadId was checked live
CLIENTS = {"claude code": HarnessId.CLAUDE_CODE, "claude-code": HarnessId.CLAUDE_CODE,
           "codex-mcp-client": HarnessId.CODEX, "codex": HarnessId.CODEX, "devin": HarnessId.DEVIN,
           "hermes": HarnessId.HERMES}
"""MCP clientInfo names as each Harness sends them (Claude Code 2.1, codex-cli 0.167.0), and "hermes"
for a call made as a Hermes tool."""
NEWEST = 5                                     # the owner's newest messages an instruction is looked for in
INSTRUCTION_MAX = 400                          # one typed message; a longer one is not an instruction
_ATLAS = re.compile(r"(?<![^\W_])atlas(?![^\W_])", re.I)
_TAG = r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*"
_INSERTED = re.compile(rf"<({_TAG})(?:\s[^<>]*)?>.*?</\1\s*>", re.S)
_MARKUP = re.compile(rf"</?{_TAG}(?:\s[^<>]*)?/?>")
_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]{7,63}")
_FILE_MAX = 64 * 1024
_SEAL = object()
_ENDINGS = ((Status.DROPPED, re.compile(r"\b(drop(ped)?|abandon(ed)?|give up on)\b")),
            (Status.DONE, re.compile(r"\b(done|finished|close it|closed|completed?)\b")))


@dataclass(frozen=True)
class Calling:
    """What the server process knows of its caller."""
    harness: HarnessId | None
    parent_pid: int | None
    call_meta: Mapping | None


def harness(client: str | None) -> HarnessId | None:
    """The Harness an MCP client names itself as; None for a client Atlas does not know."""
    return CLIENTS.get((client or "").strip().casefold())


def bind_caller(calling: Calling, settings: Settings) -> BoundSession | None:
    """The live Session the call comes from, or None when the Harness gives no binding Atlas can
    prove. Never raises: a store it cannot read binds nothing."""
    try:
        if calling.harness is HarnessId.CLAUDE_CODE and calling.parent_pid:
            return _claude_code(calling.parent_pid, settings)
        if calling.harness is HarnessId.CODEX and calling.call_meta:
            return _codex(calling.call_meta.get("threadId"), settings)
    except Exception as error:
        log.info("Atlas bound no Session to an agent's call (%s)", type(error).__name__)
    return None


def session_id(bound: BoundSession) -> EvidenceId:
    return evidence_id(bound.harness, bound.namespace, bound.native_key)


@dataclass(frozen=True)
class Typed:
    """A message the owner typed into the bound Session, as its Harness stored it."""
    at: datetime
    text: str | None                           # None: the owner's turn held more than typed text


def typed(bound: BoundSession, settings: Settings) -> list[Typed]:
    """The owner's turns of the bound Session, oldest first, from its store now. A turn counts as
    typed text only when the Harness's record shows nothing else in it: Claude Code, a `user`
    record collection reads as the owner's (not isMeta, a tool result, a sidechain or a system
    prompt) whose content is a string or text blocks only and that names no pasted image; Codex, a
    UserMessage of text blocks only, none with `text_elements` (where Codex marks pasted content
    and mentions). Blocks a Harness inserts as markup (`<system-reminder>...</system-reminder>`,
    `<local-command-stdout>...`, `<command-name>...` and the like) are not the owner's words and
    are taken out; a turn with markup left over holds no typed text."""
    adapter = ADAPTERS[bound.harness]
    unit = next((u for u in adapter.units(settings, Sent()) if u.native_key == bound.native_key), None)
    if unit is None:
        return []
    read = _claude_typed if bound.harness is HarnessId.CLAUDE_CODE else _codex_typed
    stored = adapter.fetch(unit, Sent()).parts[MAIN][2]
    return [t for line in stored.splitlines() if (r := _record(line)) and (t := read(r))]


def _claude_typed(r: dict) -> Typed | None:
    if _claude_turn(r)[0] != "owner" or not r.get("timestamp"):
        return None
    content = (r.get("message") or {}).get("content")
    blocks = [content] if isinstance(content, str) else content if isinstance(content, list) else None
    texts = [b if isinstance(b, str) else b.get("text") if isinstance(b, dict) and b.get("type") == "text" else None
             for b in blocks or [None]]
    whole = None if None in texts or r.get("imagePasteIds") else "\n".join(texts)
    return Typed(_when(r["timestamp"]), _owner_text(whole))


def _codex_typed(r: dict) -> Typed | None:
    payload = r.get("payload") or {}
    item = payload.get("item") or {}
    if r.get("type") != "event_msg" or payload.get("type") != "item_completed" or item.get("type") != "UserMessage":
        return None
    blocks = item.get("content") if isinstance(item.get("content"), list) else [{"type": "text", "text": item.get("text")}]
    plain = all(isinstance(b, dict) and str(b.get("type")).lower() == "text" and isinstance(b.get("text"), str)
                and not b.get("text_elements") for b in blocks) and blocks
    return Typed(_when(r["timestamp"]), _owner_text("\n".join(b["text"] for b in blocks) if plain else None))


def _owner_text(text: str | None) -> str | None:
    if text is None:
        return None
    left = _INSERTED.sub("", text)
    return None if _MARKUP.search(left) else left.strip()


def find_instruction(bound: BoundSession, owner_words: str, settings: Settings) -> Instruction | None:
    """The owner's instruction to Atlas, when `owner_words` is one whole message the owner typed in
    the bound Session, among their NEWEST newest turns: equal to it once case and runs of
    whitespace are ignored, naming Atlas, and, as the store holds it, on one line and at most
    INSTRUCTION_MAX characters. The Instruction's text is the message as the store holds it.
    `names_ending` is set when those words themselves end the Pursuit (D Q11). None otherwise.
    Never raises."""
    words = _normal(owner_words)
    if not names_atlas(owner_words) or "\n" in owner_words.strip() or len(owner_words) > INSTRUCTION_MAX:
        return None
    try:
        turns = typed(bound, settings)[-NEWEST:]
    except Exception as error:
        log.info("Atlas could not read the calling Session for an instruction (%s)", type(error).__name__)
        return None
    found = next((t for t in reversed(turns) if t.text is not None and len(t.text.splitlines()) == 1
                  and len(t.text) <= INSTRUCTION_MAX and _normal(t.text) == words), None)
    if found is None:
        return None
    return Instruction(bound, found.text, found.at, ending(found.text), _token=_SEAL)


def names_atlas(words: str) -> bool:
    """Whether the words name Atlas, as the owner does when they tell an agent to tell Atlas."""
    return bool(_ATLAS.search(words))


def sealed(instruction: Instruction | None) -> bool:
    """Whether find_instruction made this Instruction."""
    return instruction is not None and instruction._token is _SEAL


def ending(words: str) -> Status | None:
    """The ending the words name, when they name exactly one (D Q11)."""
    named = [status for status, said in _ENDINGS if said.search(words.casefold())]
    return named[0] if len(named) == 1 else None


def _normal(text: str) -> str:
    return " ".join(text.casefold().split())


def _claude_code(pid: int, settings: Settings) -> BoundSession | None:
    for root in ClaudeCode().roots(settings):
        live = _json_file(root / "sessions" / f"{pid}.json")
        if live is None or live.get("pid") != pid or not _started_as(pid, live.get("procStart")):
            continue
        sid = live.get("sessionId")
        if isinstance(sid, str) and _ID.fullmatch(sid):
            return _held(HarnessId.CLAUDE_CODE, sid, settings)
    return None


def _codex(thread: object, settings: Settings) -> BoundSession | None:
    if not isinstance(thread, str) or not _ID.fullmatch(thread):
        return None
    adapter = Codex()
    for unit in adapter.units(settings, Sent()):
        if unit.native_id != thread:
            continue
        meta = json.loads(_first_line(unit.ref["main"])).get("payload") or {}
        if meta.get("cli_version") not in VERIFIED_CODEX or adapter.head(unit) is None:
            return None
        return BoundSession(HarnessId.CODEX, LOCAL, unit.native_key)
    return None


def _held(harness: HarnessId, native_id: str, settings: Settings) -> BoundSession | None:
    unit = next((u for u in ADAPTERS[harness].units(settings, Sent()) if u.native_id == native_id), None)
    return BoundSession(harness, LOCAL, unit.native_key) if unit is not None else None


def _json_file(path: Path) -> dict | None:
    """A small regular file, never through a link, as JSON."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        info = os.fstat(fh.fileno())
        if not (info.st_mode & 0o170000 == 0o100000) or info.st_size > _FILE_MAX:
            return None
        data = json.loads(fh.read(_FILE_MAX))
    return data if isinstance(data, dict) else None


def _started_as(pid: int, started: object) -> bool:
    """The process `pid` is the one that wrote the file: its start time, in clock ticks after boot,
    is the one the file records."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    fields = stat.rsplit(")", 1)[-1].split()
    return len(fields) > 19 and str(started) == fields[19]
