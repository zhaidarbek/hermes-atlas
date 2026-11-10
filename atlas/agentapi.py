"""Agents in Harnesses: three tools, defined once in TOOLS and served two ways:
- inside Hermes via ctx.register_tool (Hermes's MCP surfaces never expose plugin tools);
- to Claude Code, Codex and others via Atlas's own MCP server, `hermes atlas mcp`, which talks
  over its stdin and stdout to the one Harness process that started it and opens no listener.
plugin.yaml's provides_tools must equal [t.name for t in TOOLS]; a test fails otherwise.

What an agent can read: a Pursuit's Resume Note and a Journal day, as the record holds them, so
masked, and with the Evidence ids they cite. Never a transcript, a file, or a Session the owner
forgot. What it can write: a Note, and a change to the Resume Note of the Pursuit that lists its
own Session, made only on the owner's words found in that Session (agents, sources._caller).
"""
from __future__ import annotations

import logging
import os
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from . import agents
from .agents import Unusable
from .model import AddNote, Agent, Outcome, Settings
from .record import Conflict
from .sources import Calling, _caller, bind_caller, find_instruction
from .sources._adapters import _tilde

log = logging.getLogger("atlas.agentapi")

WRITES_PER_MINUTE = 20                       # atlas_note and atlas_update calls one server process takes a minute
BUSY = "Atlas did not store this: its record was busy with other writes. Nothing was kept; call again."
TOO_MANY = f"Atlas takes at most {WRITES_PER_MINUTE} Notes and changes a minute from one Harness. This one was not kept."
_WAITS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.4, 0.8)


@dataclass(frozen=True)
class Caller:
    """Who is calling, as the server process can establish it, never as the agent claims it."""
    harness: str                             # MCP clientInfo name, or "hermes"
    parent_pid: int | None                   # the Harness process that launched this stdio server
    cwd: str | None
    hermes_session: str | None               # session_id kwarg when called as a Hermes tool
    call_meta: Mapping | None = None         # tools/call params._meta, written by the client, not
                                             # the agent; Codex puts threadId here (ADR 0010)

    def authority(self, settings: Settings, owner_words: str | None) -> Agent:
        """The caller as an Agent: its bound Session, and the owner's words found in it."""
        harness = _caller.harness(self.harness)
        if harness is None:
            raise Unusable("Atlas does not know this Harness, so it takes nothing from it.")
        bound = bind_caller(Calling(harness, self.parent_pid, self.call_meta), settings)
        told = find_instruction(bound, owner_words, settings) if bound and owner_words else None
        return Agent(harness.value, bound, told, cwd=_tilde(Path(self.cwd)) if self.cwd else None)


@dataclass(frozen=True)
class Answered:
    text: str
    citations: str | None = None             # JSON: each statement's mark and Evidence
    error: bool = False


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping                      # JSON schema
    run: Callable[[dict, Caller, object, datetime], Answered]


def _read(args: dict, caller: Caller, atlas, now: datetime) -> Answered:
    """A Resume Note by its Pursuit, or a Journal day."""
    if set(args) - {"pursuit", "day"} or len(args) != 1:
        raise Unusable('atlas_read takes one of "pursuit" or "day".')
    _caller_known(caller)
    snap = atlas.snapshot()
    if "pursuit" in args:
        now = now.astimezone(ZoneInfo(snap.settings().timezone))
        text, cited = agents.resume(snap, agents.pursuit(snap, args["pursuit"]), now)
    else:
        try:
            day = date.fromisoformat(str(args["day"]))
        except ValueError:
            raise Unusable('"day" is a date written YYYY-MM-DD.') from None
        text, cited = agents.day(snap, day, snap.journal(day))
    return Answered(*agents.rendered(text, cited))


def _note(args: dict, caller: Caller, atlas, now: datetime) -> Answered:
    """A Note, unasked if the agent likes (R4 Q4)."""
    if set(args) - {"text", "pursuit"}:
        raise Unusable('atlas_note takes "text" and, if it is about one Pursuit, "pursuit".')
    snap = atlas.snapshot()
    said = agents.text(args.get("text"), agents.TEXT_MAX, "text")
    about = agents.pursuit(snap, args["pursuit"]).id if args.get("pursuit") is not None else None
    outcome = _stored(lambda: atlas.act(AddNote(said, about), caller.authority(snap.settings(), None), now))
    return Answered("\n".join(outcome.lines), error=not outcome.ok)


def _update(args: dict, caller: Caller, atlas, now: datetime) -> Answered:
    """A change to a Resume Note on the owner's words: applied when Atlas finds the words in the
    calling Session and that Session is the Pursuit's; otherwise kept as a Note for "apply"."""
    if set(args) != {"pursuit", "owner_words", "change"}:
        raise Unusable('atlas_update takes "pursuit", "owner_words" and "change".')
    snap = atlas.snapshot()
    said = args["owner_words"]
    words = agents.text(said, agents.WORDS_MAX, "owner_words")
    asked = agents.asked(agents.pursuit(snap, args["pursuit"]), args["change"], said)
    outcome = _stored(lambda: atlas.act(asked, caller.authority(snap.settings(), said), now, words))
    return Answered("\n".join(outcome.lines), error=outcome.refused not in (None, "NOT_TOLD"))


def _stored(act: Callable[[], Outcome]) -> Outcome:
    """`act` committed, tried again while other writers keep the record busy; when they never let
    it, nothing was stored and the answer says so."""
    for wait in (*_WAITS, None):
        try:
            return act()
        except Conflict:
            if wait is None:
                return Outcome(False, (BUSY,), "BUSY")
            time.sleep(wait * (1 + random.random()))


_PURSUIT = {"type": "string", "description": "The Pursuit's name, as Atlas shows it."}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("atlas_read", "Read Atlas: a Pursuit's Resume Note, or what the Journal holds for one day.",
             {"type": "object", "properties": {
                 "pursuit": _PURSUIT, "day": {"type": "string", "description": "A day, YYYY-MM-DD."}}},
             _read),
    ToolSpec("atlas_note", "Tell Atlas something on purpose (a decision, a fact). You may add Notes unasked. A Note "
                           "Atlas cannot tie to your Session stays in the record, and Forget does not remove it.",
             {"type": "object", "properties": {
                 "text": {"type": "string", "maxLength": agents.TEXT_MAX}, "pursuit": _PURSUIT},
              "required": ["text"]},
             _note),
    ToolSpec("atlas_update", "Change a Pursuit's Resume Note ONLY when the owner told you to in this Session. Pass "
                             "the owner's whole message exactly as they typed it; it must name Atlas. Atlas adds that "
                             "message itself as the decision, and removes the Open question only when the message "
                             "says it is settled and the Pursuit has just one.",
             {"type": "object", "properties": {
                 "pursuit": _PURSUIT,
                 "owner_words": {"type": "string", "maxLength": agents.WORDS_MAX},
                 "change": {"type": "object", "properties": {
                     "remove": {"type": "array", "items": {"type": "string"}, "maxItems": 1,
                                "description": "Your words for the Open question the owner settled."},
                     "add_decided": {"type": "boolean",
                                     "description": "true: add the owner's message as a decision."}}}},
              "required": ["pursuit", "owner_words", "change"]},
             _update),
)
_BY_NAME = {t.name: t for t in TOOLS}


def call(name: str, args: object, caller: Caller, atlas, now: datetime) -> Answered:
    """One tool call. Never raises: a call Atlas cannot act on writes nothing and says why, and
    any other failure is answered and logged by its type only."""
    tool = _BY_NAME.get(name)
    if tool is None or not isinstance(args, dict):
        return Answered(f"Atlas has no tool {name!r} taking those arguments.", error=True)
    try:
        return tool.run(args, caller, atlas, now)
    except Unusable as refused:
        return Answered(str(refused), error=True)
    except Exception as failed:
        log.warning("Atlas could not answer %s (%s)", name, type(failed).__name__)
        return Answered(f"Atlas could not do that ({type(failed).__name__}).", error=True)


def _caller_known(caller: Caller) -> None:
    if _caller.harness(caller.harness) is None:
        raise Unusable("Atlas does not know this Harness, so it takes nothing from it.")


def register_hermes_tools(ctx, open_atlas: Callable[[], object], clock: Callable[[], datetime]) -> None:
    for tool in TOOLS:
        ctx.register_tool(name=tool.name, toolset="atlas", handler=_as_hermes_handler(tool.name, open_atlas, clock),
                          schema={"name": tool.name, "description": tool.description, "parameters": tool.parameters})


def _as_hermes_handler(name: str, open_atlas: Callable[[], object], clock: Callable[[], datetime]) -> Callable[..., str]:
    def handle(args: dict, **context) -> str:
        try:
            atlas = open_atlas()
        except Exception as failed:
            log.warning("Atlas could not open its record for %s (%s)", name, type(failed).__name__)
            return f"Atlas could not open its record ({type(failed).__name__})."
        answer = call(name, args, Caller("hermes", None, None, context.get("session_id")), atlas, clock())
        return answer.text + (f"\n\nCitations: {answer.citations}" if answer.citations else "")
    return handle


def serve_mcp(atlas, clock: Callable[[], datetime]) -> None:
    """Serves TOOLS over this process's stdin and stdout until the Harness closes them."""
    import anyio

    anyio.run(_serve, atlas, clock)


async def _serve(atlas, clock: Callable[[], datetime]) -> None:
    import anyio
    import mcp_types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    from . import __version__

    parent, cwd = os.getppid(), os.getcwd()
    writes: deque[float] = deque(maxlen=WRITES_PER_MINUTE)

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool(name=t.name, description=t.description,
                                                       input_schema=dict(t.parameters)) for t in TOOLS])

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        client = ctx.session.client_params.client_info.name if ctx.session.client_params else None
        caller = Caller(client or "", parent, cwd, None, dict(params.meta or {}))
        if params.name != "atlas_read":
            if len(writes) == WRITES_PER_MINUTE and time.monotonic() - writes[0] < 60:
                return types.CallToolResult(content=[types.TextContent(type="text", text=TOO_MANY)], is_error=True)
            writes.append(time.monotonic())
        answer = await anyio.to_thread.run_sync(call, params.name, params.arguments or {}, caller, atlas, clock())
        blocks = [answer.text, *([answer.citations] if answer.citations else [])]
        return types.CallToolResult(content=[types.TextContent(type="text", text=b) for b in blocks],
                                    is_error=answer.error)

    server = Server("atlas", version=__version__, on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
