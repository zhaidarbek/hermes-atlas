"""fsaudit: POST /seen on a machine where setup never ran creates the record folder, with the
umask's mode. README: setup creates "the record folder, mode 0700"; nothing else should create it."""
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_the_board_api_does_not_create_a_record_folder_that_setup_never_made(tmp_path, monkeypatch):
    root = tmp_path / "home" / "atlas"
    monkeypatch.setenv("ATLAS_RECORD", str(root))
    from atlas import board_api
    app = FastAPI()
    app.include_router(board_api.router, prefix="/api/plugins/atlas")
    before = os.umask(0o022)
    try:
        answer = TestClient(app).post("/api/plugins/atlas/seen", json={"marks": {}})
    finally:
        os.umask(before)
    made = {str(p.relative_to(tmp_path / "home")): oct(p.stat().st_mode & 0o777) for p in sorted((tmp_path / "home").rglob("*"))}
    assert not root.exists(), f"POST /seen answered {answer.status_code} and created {made}"
