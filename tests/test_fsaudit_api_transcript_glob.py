"""fsaudit: GET /transcript/{evidence} hands the id from the URL to pathlib's glob unchecked
(Snapshot._evidence_folder), so `*`, `?` and `[...]` in it are wildcards. A caller that knows no
Session id still gets a transcript, and can walk every Session of a Harness one character at a
time. The id never reaches `..` or a slash, so this stays inside evidence/<harness>/*/*/."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.model import Turn
from atlas.record import Record
from test_commit import PROFILE, SETTINGS
from test_evidence import T0, put, session


def test_the_transcript_route_serves_only_the_session_whose_id_the_url_names(tmp_path, monkeypatch):
    root = tmp_path / "home" / "atlas"
    Record.open(root).create(SETTINGS, PROFILE)
    held = session(Turn("owner", T0, "only for whoever knows this Session's id", "main"))
    put(root, held, {"main.jsonl.zst": b'{"text": "x"}\n'})
    monkeypatch.setenv("ATLAS_RECORD", str(root))
    from atlas import board_api
    app = FastAPI()
    app.include_router(board_api.router, prefix="/api/plugins/atlas")
    client = TestClient(app)

    assert client.get(f"/api/plugins/atlas/transcript/{held.id}").status_code == 200
    for not_an_id in ("cx-*", "cx-[a-z0-9]*", "cx-" + "?" * 16):
        answer = client.get(f"/api/plugins/atlas/transcript/{not_an_id}")
        assert answer.status_code == 404, (f"GET /transcript/{not_an_id} answered {answer.status_code} with the "
                                           f"transcript of {held.id}: {answer.text[-45:]!r}")
