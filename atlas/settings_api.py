"""The Settings inner tab's backend, mounted with the Board's routes at /api/plugins/atlas/.

GET    /settings             -> settings_rules.view of settings.md
PATCH  /settings  {<one>}    -> Atlas.act(ChangeSettings(patch), Owner(via="board"))

A refused change answers 422 with its reason, a record that does not exist yet 404, and a write
that lost a race 503, for the page to retry. A folder the owner typed is never logged and never
echoed back in a refusal. GET answers in ASCII-escaped JSON, so a value written into settings.md
by hand that is not valid UTF-8 still shows, and can be removed.
"""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from . import _now, settings_rules, sources
from .board_api import _atlas
from .model import ChangeSettings, EvidenceId, Owner
from .record import Conflict

router = APIRouter()


class _Escaped(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, separators=(",", ":")).encode("ascii")


@router.get("/settings", response_class=_Escaped)
def read_settings() -> dict:
    try:
        settings = _atlas()._record.snapshot().settings()
    except FileNotFoundError:
        raise HTTPException(404, "Atlas has no record yet. Run hermes atlas setup first.")
    return settings_rules.view(settings, sources.defaults())


@router.patch("/settings")
def change_settings(patch: dict = Body(...)) -> dict:
    note = EvidenceId(f"nt-{secrets.token_hex(4)}")
    try:
        outcome = _atlas().act(ChangeSettings(patch), Owner("board", note), _now())
    except FileNotFoundError:
        raise HTTPException(404, "Atlas has no record yet. Run hermes atlas setup first.")
    except Conflict:
        raise HTTPException(503, "The record changed meanwhile; try again")
    if not outcome.ok:
        raise HTTPException(422, settings_rules.REASONS.get(outcome.refused, outcome.refused))
    return {"lines": list(outcome.lines)}
