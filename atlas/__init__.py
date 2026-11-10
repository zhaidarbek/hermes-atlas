"""Hermes plugin entry. register(ctx) only wires shells to Atlas; it reads nothing lazy-only
(ctx.llm, ctx.state, ctx.platform_actions are touched in handlers, never here, because the
catalog validator rejects that) and starts no threads (it runs in every hermes process).

plugin.yaml (manifest_version 2), tested equal to the file:
  manifest_version: 2
  name: atlas
  version: 0.1.0
  description: Keeps your Pursuits current across your AI Harnesses and pushes you to continue them.
  license: MIT
  homepage: https://github.com/zhaidarbek/hermes-atlas
  requires_hermes: ">=0.23.0"
  python_dependencies: ["zstandard>=0.22,<1", "mcp>=2.0,<3", "pyyaml>=6.0,<7"]   # == pyproject (tested)
  provides_tools: [atlas_read, atlas_note, atlas_update]            # == what register() registers (tested)
  provides_hooks: [pre_gateway_dispatch, post_gateway_admission]   # == what register() registers (tested)
  provides_middleware: []
  requires_env: []
  config_schema: {record_dir: {type: str, description: "Folder for Atlas's record; default ~/atlas"}}
                                                                     # the only setting kept in Hermes config;
                                                                     # everything else is settings.md in the record
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

__version__ = "0.1.0"

SKILL = Path(__file__).parent / "skills" / "atlas" / "SKILL.md"
AUXILIARY_TASKS = (
    ("atlas_file", "Atlas: filing", "Files each new Session or Note into a Pursuit.", None),
    ("atlas_backfill", "Atlas: backfill", "Builds Pursuits from the Session history.", {"timeout": 300}),
    ("atlas_journal", "Atlas: Journal", "Writes each day's Journal lines.", None),
    ("atlas_converse", "Atlas: conversation", "Reads the owner's plain words as changes or questions.", None),
    ("atlas_answer", "Atlas: answers", "Answers questions about the record with checked quotes.", None),
)


def register(ctx) -> None:
    from . import agentapi, channel
    agentapi.register_hermes_tools(ctx, lambda: _atlas(ctx), _now)
    listener = channel.Listener(lambda: _atlas(ctx), _now)
    ctx.register_hook("pre_gateway_dispatch", listener.note_inbound)
    ctx.register_hook("post_gateway_admission", listener.claim)
    ctx.register_command("atlas", listener.slash, description="Talk to Atlas", args_hint="<words>")
    ctx.register_cli_command("atlas", "Atlas: Pursuits, Daily Brief, Journal", _setup_cli,
                             lambda args: _run_cli(ctx, args))
    ctx.register_skill("atlas", SKILL, description="How agents read Atlas, leave it Notes, and change it when told")
    for key, display_name, description, defaults in AUXILIARY_TASKS:
        ctx.register_auxiliary_task(key, display_name=display_name, description=description, defaults=defaults)


def _now() -> datetime:
    """The shells' clock. ATLAS_NOW (ISO 8601) fixes it; the proof harness uses that."""
    fixed = os.environ.get("ATLAS_NOW")
    return datetime.fromisoformat(fixed) if fixed else datetime.now().astimezone()


def _record_dir(ctx) -> Path | None:
    configured = ctx.get_config("record_dir")
    return Path(configured).expanduser() if configured else None


def _setup(atlas, args) -> int:
    from .record import NotLocal
    try:
        print(atlas.setup().render())
    except NotLocal as refused:
        print(f"Atlas needs a folder on this machine's own disk for its record: {refused}. Point "
              "plugins.entries.atlas.settings.record_dir at a local folder.", file=sys.stderr)
        return 1
    return 0


def _backfill(atlas, args) -> int:
    print(atlas.backfill(_now()).render())
    return 0


def _tick(atlas, args) -> int:
    atlas.tick(_now())
    return 0


def _mcp(atlas, args) -> int:
    from . import agentapi
    agentapi.serve_mcp(atlas, _now)
    return 0


def _reindex(atlas, args) -> int:
    n = atlas.reindex()
    print(f"Indexed {n} Session{'' if n == 1 else 's'}.")
    return 0


def _not_built(atlas, args) -> int:
    raise NotImplementedError(f"hermes atlas {args.atlas_command}")


SUBCOMMANDS = {
    "setup": ("Find your Harnesses, create the record and schedule the tick", _setup),
    "backfill": ("Build your Pursuits from the Sessions already on this machine", _backfill),
    "tick": ("Collect, file and send what is due (run by the atlas-tick cron job)", _tick),
    "mcp": ("Serve atlas_read, atlas_note and atlas_update to one Harness over stdio MCP", _mcp),
    "reindex": ("Rebuild the disposable index from the record", _reindex),
    "status": ("Show what Atlas did last", _not_built),
}


def _setup_cli(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="atlas_command", required=True)
    for name, (help, _) in SUBCOMMANDS.items():
        sub = commands.add_parser(name, help=help)
        if name == "tick":
            sub.add_argument("--quiet", action="store_true", help="Print nothing")


def _atlas(ctx):
    from .app import Atlas
    from .librarian import HostLlm
    return Atlas.open(_record_dir(ctx), HostLlm(ctx), _now)


def _run_cli(ctx, args: argparse.Namespace) -> int:
    return SUBCOMMANDS[args.atlas_command][1](_atlas(ctx), args)
