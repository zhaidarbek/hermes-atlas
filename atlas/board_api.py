"""The Atlas tab's backend: a FastAPI `router` that dashboard/plugin_api.py hands Hermes v0.23.0,
which mounts it at /api/plugins/atlas/. One plugin dir gives one dashboard tab, so Board and
Settings are inner tabs of /atlas (A6, A10). The frontend, dashboard/dist/index.js, renders the
BoardView this module turns into JSON.

GET    /board                       -> Atlas.read(BoardQ())
PATCH  /pursuits/{id}  {status}     -> Atlas.act(SetStatus(id, status), Owner(via="board"))
PATCH  /pursuits/{id}  {focus}      -> Atlas.act(SetFocus(id, on), Owner(via="board"))
POST   /seen  {marks: {id: upto}}   -> Atlas.act(MarkSeen(marks), Owner(via="board"))
GET    /transcript/{evidence}       -> the Session a badge cites, as plain text

The seen mark (W Q2, D Q17): once cards have been on screen for 2 s while the page is visible, the
frontend posts them in one batch, each with its newest History id, so a change that lands after
the view stays marked. A Conflict, a write that lost a race, answers 503 for the page to retry.

The dashboard process has no plugin context, so the record folder is read from the plugin's
setting in Hermes's config.yaml, as ctx.get_config would. Atlas.open(llm=None): Board actions
need no model.
"""
from __future__ import annotations

import os
import secrets
from datetime import date, datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from . import _now, messages, rules
from .app import Atlas
from .model import (BoardQ, BoardView, Card, Cite, EndingProposal, EvidenceId, Fact, FocusProposal, HistoryId, MarkSeen,
                    Owner, PursuitId, SetFocus, SetStatus, Statement, Status, harness_of)
from .record import Ambiguous, Conflict

router = APIRouter()


class Change(BaseModel):
    status: Status | None = None
    focus: bool | None = None


class Seen(BaseModel):
    marks: dict[str, str]


@router.get("/board")
def read_board() -> dict:
    view: BoardView = _atlas().read(BoardQ()).data
    now = _now()
    return {"columns": {s.value: [_card(c, now) for c in view.columns[s]] for s in Status},
            "in_focus": view.in_focus, "focus_soft_cap": rules.FOCUS_SOFT_CAP}


@router.patch("/pursuits/{pursuit}")
def change(pursuit: str, body: Change) -> dict:
    if (body.status is None) == (body.focus is None):
        raise HTTPException(422, "Change either status or focus")
    cmd = SetStatus(pursuit, body.status) if body.status is not None else SetFocus(pursuit, body.focus)
    return _act(cmd)


@router.post("/seen")
def seen(body: Seen) -> dict:
    return _act(MarkSeen({PursuitId(k): HistoryId(v) for k, v in body.marks.items()}))


@router.get("/transcript/{evidence}", response_class=PlainTextResponse)
def transcript(evidence: str) -> str:
    try:
        s = _atlas()._record.snapshot().session(EvidenceId(evidence))
    except KeyError:
        raise HTTPException(404, "No such Session in the record")
    head = f"{s.title}\n{s.harness.label}{f' via {s.interface}' if s.interface else ''}, {s.started:%a %d %b %Y %H:%M}\n"
    return head + "".join(f"\n{t.role} {t.at:%H:%M}  {t.text}\n" if t.at else f"\n{t.role}  {t.text}\n"
                          for t in s.turns)


def _act(cmd) -> dict:
    now = _now()
    note = EvidenceId(f"nt-{secrets.token_hex(4)}")
    try:
        outcome = _atlas().act(cmd, Owner("board", note), now)
    except KeyError:
        raise HTTPException(404, "No such Pursuit")
    except Ambiguous:
        raise HTTPException(409, "Several Pursuits match")
    except Conflict:
        raise HTTPException(503, "The record changed meanwhile; try again")
    if outcome.refused == "NOT_A_HISTORY_ID":
        raise HTTPException(422, "Not a History id of that Pursuit")
    if not outcome.ok:
        raise HTTPException(409, outcome.refused)
    return {"lines": list(outcome.lines)}


def _card(c: Card, now: datetime) -> dict:
    return {"id": c.pursuit, "name": c.name, "kind": c.kind_line, "last_worked": _day(c.last_worked),
            "left_off": _statement(c.left_off, now), "next": _statement(c.next_step, now), "focus": c.focus,
            "new": c.new, "changed": c.changed, "proposals": [_proposal(p) for p in c.proposals],
            "resume_prompt": c.resume_prompt, "upto": c.upto}


def _statement(s: Statement | None, now: datetime) -> dict | None:
    if s is None:
        return None
    cited = s.source.evidence if isinstance(s, Fact) and isinstance(s.source, Cite) else None
    return {"text": s.text, "mark": "F" if isinstance(s, Fact) else "I", "citation": messages.citation(s, now),
            "transcript": f"transcript/{cited}" if cited and harness_of(cited) else None}


def _proposal(p) -> dict:
    if isinstance(p, FocusProposal):
        text = "Atlas proposes: add to Focus" if p.add else "Atlas proposes: take it out of Focus"
    elif isinstance(p, EndingProposal):
        text = f"Atlas proposes: mark it {p.to.value}"
    else:
        text = "Atlas proposes: reopen it"
    return {"text": text, "mark": "I", "citation": None, "transcript": None}


def _day(d: date | None) -> str | None:
    return f"{d:%a} {d.day} {d:%b}" if d else None


def _atlas() -> Atlas:
    return Atlas.open(_record_dir(), None)


def _record_dir() -> Path | None:
    """plugins.entries.atlas.settings.record_dir in Hermes's config.yaml, or None for Atlas's default."""
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
    try:
        node = yaml.safe_load((home / "config.yaml").read_text())
    except (OSError, yaml.YAMLError):
        return None
    for key in ("plugins", "entries", "atlas", "settings", "record_dir"):
        node = node.get(key) if isinstance(node, dict) else None
    return Path(node).expanduser() if isinstance(node, str) and node else None
