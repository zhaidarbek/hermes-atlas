"""Shared set-up for the tests/test_audit_*.py files: a Claude Code fixture store under the test's
throwaway HOME, a temporary record, and a scripted model. No real model, send or network."""
import json
import os
import re
import subprocess
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from atlas.model import HarnessId, HarnessSetting
from atlas.record import Record
from test_commit import PROFILE, SETTINGS

NOW = datetime(2026, 11, 8, 10, tzinfo=timezone.utc)
CANARY = "CANARY-7f3a"
FIRST = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"


def claude(home: Path, native: str, rows, cwd: str | None = None, at: datetime = NOW) -> Path:
    """rows: ("user" | "assistant", text). A user row is a message the owner typed."""
    cwd = cwd or str(home / "work")
    path = home / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd) / f"{native}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for n, (role, text) in enumerate(rows):
        when = (at - timedelta(hours=2) + timedelta(minutes=n)).isoformat()
        content = text if role == "user" else [{"type": "text", "text": text}]
        out.append({"type": role, "sessionId": native, "timestamp": when, "cwd": cwd, "gitBranch": None,
                    "uuid": f"u{n}", "message": {"role": role, "content": content}})
    path.write_text("".join(json.dumps(r) + "\n" for r in out))
    os.utime(path, (at.timestamp(), at.timestamp()))
    return path


def record(tmp_path: Path) -> Record:
    rec = Record.open(tmp_path / "atlas")
    rec.create(replace(SETTINGS, timezone="UTC", brief_at=time(23, 59), harnesses={
        HarnessId.CLAUDE_CODE: HarnessSetting(True, "store", (), "owner")}), PROFILE)
    return rec


class Scripted:
    """The Llm port: one answer per schema title, a dict or a function of the input."""

    def __init__(self, **answers) -> None:
        self.answers = {"card": {"intent": "Get the logger reading.", "outcome": "It reads.", "unfinished": None},
                        "journal": {"worked_on": [], "decided": [], "finished": []}, "profile": {"entries": []},
                        "reconcile": {"merges": []}, **answers}
        self.inputs: list[tuple[str, str]] = []

    def structured(self, task, instructions, input, schema):
        self.inputs.append((schema.get("title"), input))
        answer = self.answers[schema.get("title")]
        return answer(input) if callable(answer) else answer


def session_id(input: str) -> str:
    return re.search(r"Session (\S+) ·", input)[1]


def one_pursuit(name: str = "Greenhouse logger", **more):
    """A group answer: the one Session shown is one Pursuit, with `more` in its Resume Note."""
    def group(input: str) -> dict:
        sid = session_id(input)
        rows = {key: [{**row, "sessions": [sid]} for row in more.get(key, [])]
                for key in ("left_off", "decided", "open_questions")}
        return {"pursuits": [{"name": name, "kind": "project", "area": "work", "sessions": [sid], "reopen": [sid],
                              "next_step": None, **rows}], "one_offs": []}
    return group


def no_send(monkeypatch) -> list[tuple]:
    """`hermes` never runs: each call is recorded and answered as a delivered message."""
    from atlas import _hermes
    sent: list[tuple] = []

    def run(*args, **kwargs):
        sent.append(args)
        return subprocess.CompletedProcess(args, 0, '{"chat_id": "1", "message_id": 5}', "")
    monkeypatch.setattr(_hermes, "run", run)
    return sent
