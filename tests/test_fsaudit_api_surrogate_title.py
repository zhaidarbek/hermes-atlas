"""fsaudit: a Claude Code Session whose first owner line ends in half an emoji (JavaScript writes a
string cut there as the JSON escape \\ud83d) gets that lone surrogate as its title. The record's
codec keeps it, and GET /transcript/{evidence} then fails with 500 for that Session, because the
plain-text response cannot encode it. settings_api._Escaped exists for the same reason; the Board's
routes have no such guard."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas import _hermes
from atlas.app import Atlas
from test_collect import CC, NOW, claude, record


def test_a_session_with_a_lone_surrogate_in_its_title_still_has_a_transcript(tmp_path, throwaway_home, monkeypatch):
    path = claude(throwaway_home, "s1", "ship it CUT", "second")
    lines = [dict(json.loads(line), uuid=f"u{n}") for n, line in enumerate(path.read_text().splitlines())]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines).replace("CUT", "\\ud83d"))
    rec = record(tmp_path, CC)
    monkeypatch.setattr(_hermes, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no hermes in tests")))
    assert Atlas(rec, None).tick(NOW).collected == 1
    (held,) = rec.snapshot().sessions()
    monkeypatch.setenv("ATLAS_RECORD", str(rec.root))
    from atlas import board_api
    app = FastAPI()
    app.include_router(board_api.router, prefix="/api/plugins/atlas")
    answer = TestClient(app, raise_server_exceptions=False).get(f"/api/plugins/atlas/transcript/{held.id}")
    assert answer.status_code == 200, f"the Session titled {held.title!r} has no transcript: HTTP {answer.status_code}"
