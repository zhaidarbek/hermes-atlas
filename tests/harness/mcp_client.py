"""An MCP client in the harness that plays a Harness: it starts `hermes atlas mcp` as its own child
over stdio, names itself as the Harness does, and makes tool calls, with the `_meta` the Harness
adds to each call."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import anyio
import mcp_types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from .world import WorldSession, utc

TESTS = Path(__file__).resolve().parents[1]
CLAUDE_CODE = "Claude Code"                  # clientInfo names as each Harness sends them
CODEX = "codex-mcp-client"
DEVIN = "devin"


@dataclass(frozen=True)
class Call:
    tool: str
    args: dict
    meta: dict | None = None


@dataclass(frozen=True)
class Result:
    text: str
    citations: list | None
    error: bool


def calls(home: Path, client: str, cwd: Path, made: Sequence[Call]) -> tuple[list[str], list[Result]]:
    """The tools the server lists, and each call's result, from one server process."""
    env = {"HOME": str(home), "TZ": os.environ.get("TZ", "Europe/Berlin"), "PATH": os.environ.get("PATH", ""),
           "ATLAS_NOW": os.environ["ATLAS_NOW"], "PYTHONPATH": os.pathsep.join([str(TESTS.parent), str(TESTS)])}
    cwd.mkdir(parents=True, exist_ok=True)
    server = StdioServerParameters(command=sys.executable, args=["-m", "harness.mcp_server"], env=env, cwd=cwd)
    return anyio.run(_session, server, client, list(made))


def served(command: str, args: list[str], env: dict, client: str, made: Sequence[Call]) -> tuple[list[str], list[Result]]:
    """As `calls`, from a server another command starts, such as `verify-atlas mcp`."""
    return anyio.run(_session, StdioServerParameters(command=command, args=args, env=env), client, list(made))


async def _session(server: StdioServerParameters, client: str, made: list[Call]) -> tuple[list[str], list[Result]]:
    with open(os.devnull, "w") as quiet:
        async with stdio_client(server, errlog=quiet) as (read, write), ClientSession(
                read, write, client_info=types.Implementation(name=client, version="0.1")) as session:
            await session.initialize()
            listed = [t.name for t in (await session.list_tools()).tools]
            results = []
            for call in made:
                got = await session.call_tool(call.tool, call.args, meta=call.meta)
                texts = [c.text for c in got.content if isinstance(c, types.TextContent)]
                results.append(Result(texts[0], json.loads(texts[1]) if len(texts) > 1 else None, bool(got.is_error)))
            return listed, results


def claude_code_running(home: Path, session: WorldSession) -> None:
    """What Claude Code keeps for its live process, here the harness's own: this process starts the
    server, so it is the server's parent."""
    pid = os.getpid()
    started = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[19]
    folder = home / ".claude" / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": session.native, "cwd": str(home / session.cwd.removeprefix("~/")),
        "procStart": started, "kind": "interactive", "entrypoint": "cli"}))


def owner_says(home: Path, session: WorldSession, at, text: str) -> None:
    """The owner types into a running Session: its Harness writes the message to its store before
    the agent answers."""
    if session.harness.value == "claude_code":
        [transcript] = (home / ".claude" / "projects").glob(f"*/{session.native}.jsonl")
        line = {"type": "user", "uuid": f"said-{at:%H%M%S}", "parentUuid": None, "sessionId": session.native,
                "timestamp": utc(at), "cwd": str(home / session.cwd.removeprefix("~/")), "isSidechain": False,
                "userType": "external", "message": {"role": "user", "content": text}}
    else:
        [transcript] = (home / ".codex" / "sessions").glob(f"**/rollout-*-{session.native}.jsonl")
        line = {"timestamp": utc(at), "type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "UserMessage", "id": f"said-{at:%H%M%S}", "content": [{"type": "text", "text": text}]}}}
    with transcript.open("a") as fh:
        fh.write(json.dumps(line) + "\n")
