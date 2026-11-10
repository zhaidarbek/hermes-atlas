"""Fake Harness stores, in each Harness's own format (docs/research/harness-and-hermes-findings.md
§2-§3 and "Stores on the Linux PC", Hermes's state.db columns from its v0.23.0 schema). A store
holds only what its Harness had written by the fake clock's `now`: Sessions that have started,
and their turns so far.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import zstandard

from atlas.model import HarnessId

from .world import CC, CX, DV, HM, WorldSession, utc as _utc, visible

T3 = "t3_code"
DEFAULT_ROOTS = {CC: ".claude", CX: ".codex", DV: ".local/share/devin/cli", HM: ".hermes", T3: ".t3"}


def materialise(home: Path, now: datetime) -> None:
    """Rewrite every store at its default root under `home` as of `now`. A file whose bytes are
    the same keeps its modification time, as a store a Harness has not written to does."""
    for store in DEFAULT_ROOTS:
        root = home / DEFAULT_ROOTS[store]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()} \
            if root.exists() else {}
        write_store(store, root, home, now)
        for path, (data, mtime) in before.items():
            if path.is_file() and path.read_bytes() == data:
                os.utime(path, ns=(mtime, mtime))


def write_store(store: HarnessId | str, root: Path, home: Path, now: datetime) -> None:
    sessions = visible(now)
    mine = [s for s in sessions if s.interface] if store == T3 else [s for s in sessions if s.harness is store]
    WRITERS[store](root, mine, home, now)


def _abs(cwd: str, home: Path) -> str:
    return str(home / cwd.removeprefix("~").lstrip("/"))


def _claude(root: Path, sessions: Sequence[WorldSession], home: Path, now: datetime) -> None:
    projects = root / "projects"
    shutil.rmtree(projects, ignore_errors=True)
    for s in sessions:
        cwd = _abs(s.cwd, home)
        folder = projects / re.sub(r"[^A-Za-z0-9]", "-", cwd)
        folder.mkdir(parents=True, exist_ok=True)
        records, parent = [], None
        for turn in (t for t in s.turns() if t.at <= now):
            me = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{s.native}:{turn.at.isoformat()}"))
            records.append({
                "type": "user" if turn.owner else "assistant", "uuid": me, "parentUuid": parent,
                "sessionId": s.native, "timestamp": _utc(turn.at), "cwd": cwd, "gitBranch": s.branch or "",
                "entrypoint": "t3-code" if s.interface else "cli", "isSidechain": False, "userType": "external",
                "version": "2.1.0",
                "message": {"role": "user", "content": turn.text} if turn.owner
                else {"role": "assistant", "content": [{"type": "text", "text": turn.text}]}})
            parent = me
        (folder / f"{s.native}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        for n in range(s.subagents):
            agent = folder / s.native / "subagents" / f"agent-{s.native.replace('-', '')[:16]}{n}.jsonl"
            agent.parent.mkdir(parents=True, exist_ok=True)
            first = s.turns()[1]
            agent.write_text(json.dumps({
                "type": "user", "sessionId": s.native, "timestamp": _utc(first.at), "cwd": cwd,
                "isSidechain": True, "message": {"role": "user", "content": f"Subtask {n} of {s.title}"}}) + "\n")


def _codex(root: Path, sessions: Sequence[WorldSession], home: Path, now: datetime) -> None:
    shutil.rmtree(root / "sessions", ignore_errors=True)
    natives = {s.title: s.native for s in sessions}
    for s in sessions:
        source = ({"subagent": {"thread_spawn": {"parent_thread_id": natives[s.parent], "depth": 1}}}
                  if s.parent else "cli")
        lines = [{"timestamp": _utc(s.start), "type": "session_meta", "payload": {
            "id": s.native, "session_id": s.native, "timestamp": _utc(s.start), "cwd": _abs(s.cwd, home),
            "originator": "T3 Code" if s.interface else "codex_cli_rs", "cli_version": "0.167.0",
            "source": source, "model_provider": "openai", "git": {"branch": s.branch} if s.branch else None}}]
        for turn in (t for t in s.turns() if t.at <= now):
            me = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{s.native}:{turn.at.isoformat()}"))
            item = ({"type": "UserMessage", "id": me, "content": [
                        {"type": "text", "text": turn.text, "text_elements": []}]} if turn.owner
                    else {"type": "AgentMessage", "id": me, "phase": "final", "content": [
                        {"type": "Text", "text": turn.text}]})
            lines.append({"timestamp": _utc(turn.at), "type": "event_msg",
                          "payload": {"type": "item_completed", "item": item}})
        utc = s.start.astimezone(timezone.utc)
        path = root / "sessions" / f"{utc:%Y/%m/%d}" / f"rollout-{utc:%Y-%m-%dT%H-%M-%S}-{s.native}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = "".join(json.dumps(line) + "\n" for line in lines).encode()
        if s.start.month < 10:
            path.with_name(path.name + ".zst").write_bytes(zstandard.ZstdCompressor().compress(data))
        else:
            path.write_bytes(data)


def _devin(root: Path, sessions: Sequence[WorldSession], home: Path, now: datetime) -> None:
    """Each message is a node of a chain, its role, content and owner-input flag inside chat_message."""
    db = _fresh_db(root / "sessions.db", """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, working_directory TEXT, backend_type TEXT, model TEXT,
            agent_mode TEXT, created_at INTEGER, last_activity_at INTEGER, title TEXT, main_chain_id INTEGER,
            shell_last_seen_index INTEGER, cogs_json TEXT, workspace_dirs TEXT, hidden INTEGER, metadata TEXT);
        CREATE TABLE message_nodes (row_id INTEGER PRIMARY KEY, session_id TEXT, node_id INTEGER, parent_node_id INTEGER,
            chat_message TEXT, created_at INTEGER, metadata TEXT);""")
    with closing(sqlite3.connect(db)) as con:
        for s in sessions:
            shown = [(s.start, "system", "You are Devin, a software engineer.", None),
                     *((t.at, "user" if t.owner else "assistant", t.text, t.owner or None)
                       for t in s.turns() if t.at <= now)]
            con.execute("INSERT INTO sessions (id, working_directory, backend_type, model, agent_mode, created_at, "
                        "last_activity_at, title, main_chain_id, hidden, metadata) "
                        "VALUES (?, ?, 'windsurf', 'swe-1', 'agent', ?, ?, ?, ?, 0, '{}')",
                        (s.native, _abs(s.cwd, home), int(s.start.timestamp()), int(shown[-1][0].timestamp()),
                         s.title, len(shown) - 1))
            con.executemany(
                "INSERT INTO message_nodes (session_id, node_id, parent_node_id, chat_message, created_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(s.native, n, n - 1 if n else None, json.dumps({
                    "role": role, "content": text,
                    "message_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{s.native}:{n}")),
                    "metadata": {"created_at": at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f000Z"),
                                 "is_user_input": owner, "finish_reason": None if role == "user" else "stop"}}),
                  int(at.timestamp()), json.dumps({"is_system_prefix": True}) if role == "system" else None)
                 for n, (at, role, text, owner) in enumerate(shown)])
        con.commit()


def _hermes(root: Path, sessions: Sequence[WorldSession], home: Path, now: datetime) -> None:
    db = _fresh_db(root / "state.db", """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, parent_session_id TEXT,
            started_at REAL NOT NULL, ended_at REAL, end_reason TEXT, message_count INTEGER DEFAULT 0, cwd TEXT,
            title TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,
            content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL,
            token_count INTEGER, finish_reason TEXT, _compressed_summary INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1, compacted INTEGER NOT NULL DEFAULT 0);""")
    natives = {s.title: s.native for s in sessions}
    with closing(sqlite3.connect(db)) as con:
        for s in sessions:
            shown = [t for t in s.turns() if t.at <= now]
            con.execute("INSERT INTO sessions VALUES (?, ?, 'owner', ?, ?, ?, NULL, ?, ?, ?)",
                        (s.native, s.source, natives.get(s.parent), s.start.timestamp(), shown[-1].at.timestamp(),
                         len(shown), _abs(s.cwd, home), s.title))
            con.executemany("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                            [(s.native, "user" if t.owner else "assistant", t.text, t.at.timestamp()) for t in shown])
        con.commit()


def _t3(root: Path, sessions: Sequence[WorldSession], home: Path, now: datetime) -> None:
    db = _fresh_db(root / "userdata" / "statev2.sqlite", """
        CREATE TABLE orchestration_v2_projection_threads (thread_id TEXT PRIMARY KEY, project_id TEXT, title TEXT,
            default_provider TEXT, created_at TEXT, updated_at TEXT, archived_at TEXT, deleted_at TEXT,
            payload_json TEXT);
        CREATE TABLE orchestration_v2_projection_provider_threads (thread_id TEXT PRIMARY KEY, payload_json TEXT);""")
    drivers = {CC: "claudeAgent", CX: "codex", DV: "acpRegistry"}
    threads = {s.title: str(uuid.uuid5(uuid.NAMESPACE_URL, f"t3:{s.native}")) for s in sessions}
    with closing(sqlite3.connect(db)) as con:
        for s in sessions:
            thread, parent = threads[s.title], threads.get(s.parent)
            lineage = {"parentThreadId": parent, "relationshipToParent": "subagent" if parent else None,
                       "rootThreadId": parent or thread}
            con.execute("INSERT INTO orchestration_v2_projection_threads VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
                        (thread, _abs(s.cwd, home), s.title, drivers[s.harness], _utc(s.start), _utc(s.end),
                         json.dumps({"branch": s.branch, "lineage": lineage})))
            con.execute("INSERT INTO orchestration_v2_projection_provider_threads VALUES (?, ?)", (thread, json.dumps(
                {"appThreadId": thread, "nativeThreadRef": {"driver": drivers[s.harness], "nativeId": s.native}})))
        con.commit()


def _fresh_db(path: Path, schema: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    for stale in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        stale.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as con:
        con.executescript(schema)
    return path


WRITERS: dict[HarnessId | str, Callable[[Path, Sequence[WorldSession], Path, datetime], None]] = {
    CC: _claude, CX: _codex, DV: _devin, HM: _hermes, T3: _t3}
