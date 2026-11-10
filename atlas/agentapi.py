"""Agents in Harnesses: three tools, defined once in TOOLS and served two ways:
- inside Hermes via ctx.register_tool (Hermes's MCP surfaces never expose plugin tools);
- to Claude Code, Codex and others via Atlas's own stdio MCP server, `hermes atlas mcp`
  (register_cli_command; the server process loads the plugin, so ctx.llm is available).
  Fallback when `plugins.isolation: host` skips plugin CLI commands: `python -m atlas.mcp`
  with Hermes's interpreter, printed by setup.
plugin.yaml's provides_tools must equal [t.name for t in TOOLS]; a test fails otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .model import Agent


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping                      # JSON schema
    run: Callable[[dict, "Caller"], str]


@dataclass(frozen=True)
class Caller:
    """Who is calling, as the server process can establish it, never as the agent claims it."""
    harness: str                             # MCP clientInfo.name, or "hermes"
    parent_pid: int | None                   # the Harness process that launched this stdio server
    cwd: str | None
    hermes_session: str | None               # session_id kwarg when called as a Hermes tool
    call_meta: Mapping | None = None         # tools/call params._meta, written by the client, not
                                             # the agent; Codex puts threadId here (ADR 0010)

    def authority(self, owner_words: str | None) -> Agent:
        """# TODO pseudocode:
        # bound = sources.bind_caller(self)                 # None when the Harness gives no binding
        # instr = sources.find_instruction(bound, owner_words) if bound and owner_words else None
        # return Agent(self.harness, bound, instr)          # rules.authorize: no instr -> Notes only
        """
        raise NotImplementedError


def _read(args: dict, caller: Caller) -> str:
    """atlas_read {pursuit?, day?, search?, neglected?, period?, prompt_for?} -> Atlas.read(Query)
    rendered in Style.MCP (text + JSON citations). period = the fifth question
    (decided/changed/finished between two dates)."""
    raise NotImplementedError


def _note(args: dict, caller: Caller) -> str:
    """atlas_note {text, pursuit?} -> Atlas.act(AddNote, caller.authority(None)). Agents may add
    Notes unasked (R4 Q4)."""
    raise NotImplementedError


def _update(args: dict, caller: Caller) -> str:
    """atlas_update {pursuit, change: {...}, owner_words} -> Atlas.act(<Command>, caller.authority(owner_words)).
    The owner's words must be found in an owner-typed message of the caller's own live Session,
    read from the Harness store at call time (the Harness writes the prompt before running the
    tool). Found -> applied at once, history line quotes them. An ending (done/dropped) also
    needs the owner's words to name it (D Q11). Not found, or no binding for this Harness
    (Codex until verified, a v1 release condition; Devin may stay so in v1) -> refused and kept
    as a REFUSED_UPDATE Note quoting the owner's words, and Atlas's next answer to the owner
    offers "apply" (D Q12). Tool result: "Atlas could not confirm your instruction in this
    Session; it kept it as a Note and will offer to apply it." No apply-then-revert."""
    raise NotImplementedError


_NO_PARAMETERS = {"type": "object", "properties": {}}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("atlas_read", "Read Atlas: Resume Notes, Journal days, Neglected, search, weekly changes.",
             _NO_PARAMETERS, _read),
    ToolSpec("atlas_note", "Tell Atlas something on purpose (a decision, a fact).", _NO_PARAMETERS, _note),
    ToolSpec("atlas_update", "Change a Pursuit ONLY when the owner told you to in this Session.",
             _NO_PARAMETERS, _update),
)


def call(name: str, args: dict, caller: Caller) -> str: raise NotImplementedError


def register_hermes_tools(ctx) -> None:
    for tool in TOOLS:
        ctx.register_tool(name=tool.name, toolset="atlas", handler=_as_hermes_handler(tool.name),
                          schema={"name": tool.name, "description": tool.description, "parameters": tool.parameters})


def _as_hermes_handler(name: str) -> Callable[..., str]:
    def handle(args: dict, **context) -> str:
        return call(name, args, Caller("hermes", None, None, context.get("session_id")))
    return handle


def serve_mcp() -> None: raise NotImplementedError                     # stdio, loops TOOLS
