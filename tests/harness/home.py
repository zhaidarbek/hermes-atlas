"""A throwaway Atlas home, and a fake Hermes that drives the plugin only through what
register(ctx) registered."""
from __future__ import annotations

import argparse
import asyncio
import inspect
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from atlas.model import Profile, Settings
from atlas.record import ChangeSet, Record, Snapshot

from .fake_llm import FakeLlm
from .stores import materialise

OUTSIDE_HOME = ("HERMES_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "T3CODE_HOME", "ATLAS_RECORD", "ATLAS_NOW",
                "XDG_DATA_HOME", "XDG_CONFIG_HOME")
LAZY = ("llm", "state", "platform_actions")


@dataclass
class FakePluginContext:
    """What Hermes hands register(ctx), recording each registration. Reading a lazy attribute
    is recorded too, so a test can prove register() reads none."""
    config: Mapping[str, object] = field(default_factory=dict)
    fake_llm: FakeLlm = field(default_factory=FakeLlm)
    tools: dict = field(default_factory=dict)
    hooks: dict = field(default_factory=dict)
    commands: dict = field(default_factory=dict)
    cli_commands: dict = field(default_factory=dict)
    skills: dict = field(default_factory=dict)
    auxiliary_tasks: dict = field(default_factory=dict)
    lazy_reads: list = field(default_factory=list)

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, hook_name, callback, **kw):
        self.hooks.setdefault(hook_name, []).append(callback)

    def register_command(self, name, handler, description="", args_hint="", **kw):
        self.commands[name] = handler

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli_commands[name] = (setup_fn, handler_fn)

    def register_skill(self, name, path, description="", frontmatter=None):
        self.skills[name] = Path(path)

    def register_auxiliary_task(self, key, *, display_name, description, defaults=None, inherit_from=None):
        self.auxiliary_tasks[key] = display_name

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    @property
    def llm(self):
        self.lazy_reads.append("llm")
        return self.fake_llm

    @property
    def state(self):
        self.lazy_reads.append("state")
        return {}

    @property
    def platform_actions(self):
        self.lazy_reads.append("platform_actions")
        return None


OWNER = "5550001"            # the owner's Telegram user id, and so their direct chat with the bot


@dataclass(frozen=True)
class FakePlatform:
    value: str


@dataclass(frozen=True)
class FakeSource:
    """gateway.session.SessionSource, as far as Atlas reads it."""
    platform: FakePlatform
    chat_id: str
    user_id: str | None
    thread_id: str | None = None
    chat_type: str = "dm"
    message_id: str | None = None

    def to_dict(self) -> dict:
        return {"platform": self.platform.value, "chat_id": self.chat_id, "chat_name": None,
                "chat_type": self.chat_type, "user_id": self.user_id, "user_name": None,
                "thread_id": self.thread_id, "chat_topic": None,
                **({"message_id": self.message_id} if self.message_id else {})}


@dataclass(frozen=True)
class FakeEvent:
    """gateway.platforms.event.MessageEvent, as far as Atlas reads it. Telegram quotes the text of
    the message replied to as reply_to_text."""
    text: str
    source: FakeSource
    message_id: str
    reply_to_message_id: str | None = None
    reply_to_text: str | None = None


@dataclass(frozen=True)
class FakeHomeChannel:
    """gateway.config.HomeChannel: /sethome names the chat and the user who ran it."""
    chat_id: str
    name: str
    thread_id: str | None = None
    user_id: str | None = None


@dataclass
class FakeGateway:
    """The gateway runner pre_gateway_dispatch receives: Atlas reads its live config."""
    homes: dict[str, FakeHomeChannel] = field(
        default_factory=lambda: {"telegram": FakeHomeChannel(OWNER, "Owner (home)", user_id=OWNER)})

    @property
    def config(self) -> "FakeGateway":
        return self

    def get_home_channel(self, platform: FakePlatform) -> FakeHomeChannel | None:
        return self.homes.get(platform.value)


def invoke_command(handler: Callable, raw_args: str, **context) -> object:
    """hermes_cli/plugin_command_dispatch.invoke_plugin_command of v0.23.0: the handler gets raw_args
    and the context keywords it names, or every one of them when it takes **kwargs."""
    params = list(inspect.signature(handler).parameters.values())
    if params and params[0].kind is not inspect.Parameter.KEYWORD_ONLY:
        if params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
            context = {k: v for k, v in context.items() if k != params[0].name}
        params = params[1:]
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return handler(raw_args, **context)
    keywords = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    named = {p.name for p in params if p.kind in keywords}
    return handler(raw_args, **{k: v for k, v in context.items() if k in named})


@dataclass(frozen=True)
class Ran:
    code: int
    out: str
    err: str


class AtlasHome:
    """HOME, the Harness stores, Hermes's home and the fake `hermes` all live under `base`.
    Inside `with`, the process environment points there and the plugin is registered."""

    def __init__(self, base: Path, llm: FakeLlm | None = None, config: Mapping[str, object] | None = None) -> None:
        self.base = base
        self.home = base / "home"
        self.fake_hermes = base / "fake-hermes"
        self.bin = base / "bin"
        for folder in (self.home, self.fake_hermes, self.bin):
            folder.mkdir(parents=True, exist_ok=True)
        hermes = self.bin / "hermes"
        source = (Path(__file__).parent / "hermes_bin.py").read_text()
        hermes.write_text(f"#!{sys.executable}\n{source}")
        hermes.chmod(0o755)
        self.ctx = FakePluginContext(config or {}, llm or FakeLlm())
        self.gateway = FakeGateway()
        self.chat: list[tuple[str, str]] = []
        self._message_ids = iter(range(9001, 10000))
        self.last_message_id: str | None = None
        self.last_reply_id: str | None = None
        self._bot_said: dict[tuple[str, str | None, str], str] = {}
        self.hook_failures: list[Exception] = []
        self.now: datetime | None = None
        self._saved: dict[str, str] | None = None
        self._dashboard = None

    def __enter__(self) -> "AtlasHome":
        self._saved = dict(os.environ)
        for name in OUTSIDE_HOME:
            os.environ.pop(name, None)
        os.environ.update(HOME=str(self.home), TZ="Europe/Berlin", FAKE_HERMES_DIR=str(self.fake_hermes),
                          PATH=f"{self.bin}{os.pathsep}{self._saved.get('PATH', '')}")
        import atlas
        atlas.register(self.ctx)
        return self

    def __exit__(self, *exc) -> None:
        os.environ.clear()
        os.environ.update(self._saved)

    def set_clock(self, now: datetime) -> None:
        """Move the fake clock: every entry point reads ATLAS_NOW, and the stores show what
        their Harness had written by then."""
        self.now = now
        os.environ["ATLAS_NOW"] = now.isoformat()
        materialise(self.home, now)

    def cli(self, *argv: str) -> Ran:
        """`hermes <argv>` for a command the plugin registered with register_cli_command."""
        parser = argparse.ArgumentParser(prog="hermes")
        commands = parser.add_subparsers(dest="command", required=True)
        for name, (setup_fn, handler_fn) in self.ctx.cli_commands.items():
            sub = commands.add_parser(name)
            setup_fn(sub)
            sub.set_defaults(func=handler_fn)
        args = parser.parse_args(argv)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = args.func(args)
        return Ran(code or 0, out.getvalue(), err.getvalue())

    def telegram(self, text: str, reply_to: str | None = None, message_id: str | None = None,
                 sender: str | None = OWNER, chat: str = OWNER, thread: str | None = None,
                 quoting: str | None = None) -> str | None:
        """The owner types `text` in Telegram: the gateway runs pre_gateway_dispatch, then
        post_gateway_admission with the payload v0.23.0 gives it. Returns the reply a plugin
        handled the message with, or None when it goes to Hermes's agent. A reply the gateway
        sends gets its own message id in the chat (`last_reply_id`), and a later reply to it quotes
        its text, as Telegram does; `quoting` is the part of the replied-to message the owner
        selected instead. Each owner message and each Atlas reply joins `self.chat`. A given
        `message_id` is a redelivery by Telegram."""
        source, event = self._typed(text, reply_to, message_id, sender, chat, thread, quoting)
        reply = None
        for hook in self.ctx.hooks["post_gateway_admission"]:
            try:
                result = hook(session_key=f"agent:main:telegram:dm:{chat}", platform="telegram",
                              source=source.to_dict(), message_id=event.message_id, text=text,
                              telemetry_schema_version=1)
                result = asyncio.run(result) if inspect.isawaitable(result) else result
            except Exception as failed:      # v0.23.0 fails open: the message goes on to the agent
                self.hook_failures.append(failed)
                continue
            if isinstance(result, dict) and result.get("action") == "handled":
                reply = result.get("reply")
                break
        return self._answered(source, reply)

    def slash(self, words: str, message_id: str | None = None, sender: str | None = OWNER, chat: str = OWNER,
              thread: str | None = None) -> str | None:
        """The owner sends `/atlas <words>` in Telegram: the gateway runs pre_gateway_dispatch, then
        the command handler with the context v0.23.0's invoke_plugin_command gives a handler that
        takes **kwargs. Returns what Hermes sends back, or None for nothing."""
        source, _ = self._typed(f"/atlas {words}".rstrip(), None, message_id, sender, chat, thread, None)
        reply = invoke_command(self.ctx.commands["atlas"], words, source=source, session_id="", platform="telegram",
                               session_key=f"agent:main:telegram:dm:{chat}")
        return self._answered(source, reply)

    def restart_gateway(self) -> None:
        """A new gateway process: the plugin registers again, and what its hooks remembered is gone."""
        self.ctx = FakePluginContext(self.ctx.config, self.ctx.fake_llm)
        import atlas
        atlas.register(self.ctx)

    def _typed(self, text: str, reply_to: str | None, message_id: str | None, sender: str | None, chat: str,
               thread: str | None, quoting: str | None) -> tuple[FakeSource, FakeEvent]:
        redelivered = message_id is not None
        message_id = message_id or str(next(self._message_ids))
        self.last_message_id = message_id
        source = FakeSource(FakePlatform("telegram"), chat, sender, thread, message_id=message_id)
        event = FakeEvent(text, source, message_id, reply_to, quoting or self._bot_said.get((chat, thread, reply_to)))
        for hook in self.ctx.hooks["pre_gateway_dispatch"]:
            hook(event=event, gateway=self.gateway, session_store=None, telemetry_schema_version=1)
        self.chat.append(("You (redelivered)" if redelivered else "You", text))
        return source, event

    def _answered(self, source: FakeSource, reply: str | None) -> str | None:
        if reply is not None:
            self.last_reply_id = str(next(self._message_ids))
            self._bot_said[(source.chat_id, source.thread_id, self.last_reply_id)] = reply
            self.chat.append(("Atlas", reply))
        return reply

    def dashboard(self, method: str, path: str, body: dict | None = None):
        """A request to the Atlas tab's backend, mounted as Hermes v0.23.0's dashboard mounts it: the
        manifest's api file imported by path as a module of its own, its router under
        /api/plugins/atlas. The body is sent as a browser's JSON.stringify sends it, a lone
        surrogate escaped. Returns the response."""
        if self._dashboard is None:
            import importlib.util
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            import atlas
            folder = Path(atlas.__file__).parent / "dashboard"
            api = json.loads((folder / "manifest.json").read_text())["api"]
            spec = importlib.util.spec_from_file_location("hermes_dashboard_plugin_atlas", folder / api)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            app = FastAPI()
            app.include_router(module.router, prefix="/api/plugins/atlas")
            self._dashboard = TestClient(app)
        sent = {} if body is None else {"content": json.dumps(body), "headers": {"content-type": "application/json"}}
        return self._dashboard.request(method, f"/api/plugins/atlas{path}", **sent)

    def hermes_calls(self) -> list[list[str]]:
        log = self.fake_hermes / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def sent(self) -> list[dict]:
        """Each message the fake `hermes send` delivered: its target, text and message id."""
        log = self.fake_hermes / "sent.jsonl"
        rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [{"to": r["argv"][r["argv"].index("--to") + 1], "text": r["argv"][-1], "message_id": r["message_id"]}
                for r in rows]

    def cron_jobs(self) -> list[dict]:
        jobs = self.fake_hermes / "jobs.json"
        return json.loads(jobs.read_text()) if jobs.exists() else []

    def script_hermes(self, route: str, exit: int = 0, stdout: str = "", stderr: str = "") -> None:
        path = self.fake_hermes / "script.json"
        script = json.loads(path.read_text()) if path.exists() else {}
        script[route] = {"exit": exit, "stdout": stdout, "stderr": stderr}
        path.write_text(json.dumps(script))

    @property
    def record_root(self) -> Path:
        return self.home / "atlas"

    def seed_record(self, settings: Settings, profile: Profile) -> None:
        Record.open(self.record_root).create(settings, profile)

    def seed(self, plan: Callable[[Snapshot], ChangeSet]) -> None:
        """Seed state the way Atlas writes it: a ChangeSet committed through atlas.record."""
        record = Record.open(self.record_root)
        record.commit(plan(record.snapshot()))

    def files(self) -> dict[str, tuple[bytes, int]]:
        """Every file under HOME with its bytes and mtime, to prove a run changed nothing."""
        return {str(p.relative_to(self.home)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in sorted(self.home.rglob("*")) if p.is_file()}
