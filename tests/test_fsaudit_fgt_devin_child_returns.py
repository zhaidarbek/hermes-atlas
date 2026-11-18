"""fsaudit C17: a Devin Session folded into a forgotten Session through T3 Code is collected again,
as a Session of its own, once T3 Code no longer lists the thread.

atlas/forget.py: "forgotten.md keeps when, never what, and the skip key of the Session and of each
Session folded into it, so collection never reads them again." forget.folded_keys rebuilds each folded
Session's skip key from the native id in its part name, but the skip key hashes the native KEY, and
for Devin that is "<id>@<created_at>" (sources/_adapters.py Devin._units), so no key is found and
forgotten.md holds the parent's key only. The same fold with a Codex child (key == id) stays out.
"""
import json
from datetime import timedelta

from atlas.app import Atlas
from atlas.model import OwnerMessage, skip_key
from atlas.record import Record
from test_collect import CC, DV, NOW, claude, devin_store, every_raw, held, record, run, t3

MARK = b"zebra7quartz9marker"


def said(text, minutes):
    return OwnerMessage(text, NOW + timedelta(hours=2, minutes=minutes), "telegram", None, None, f"t:{minutes}",
                        addressed=True)


def test_a_devin_session_folded_into_a_forgotten_session_is_never_collected_again(tmp_path, throwaway_home):
    home = throwaway_home
    claude(home, "parent", "invented owner task: plan the export")
    devin_store(home / ".local/share/devin/cli", [("user", f"delegated: the vault phrase is {MARK.decode()}", True, None, "m1"),
                                                  ("assistant", "noted", None, None, "m2")])
    db = t3(home, [("parent-thread", "parent", None)])
    run(db, "INSERT INTO orchestration_v2_projection_threads VALUES ('child-thread', ?, NULL, ?)", NOW.isoformat(),
        json.dumps({"lineage": {"parentThreadId": "parent-thread", "relationshipToParent": "subagent"}}))
    run(db, "INSERT INTO orchestration_v2_projection_provider_threads VALUES ('child-thread', ?)",
        json.dumps({"nativeThreadRef": {"driver": "acpRegistry", "nativeId": "brave-otter"}}))
    rec = record(tmp_path, CC, DV)
    Atlas(rec, None).backfill(NOW)
    assert list(held(rec)) == ["parent"] and MARK in every_raw(rec), "the Devin thread was not folded into its parent"
    child_key = next(skip_key(DV, f"brave-otter@{created}") for (created,) in [__import__("sqlite3").connect(
        home / ".local/share/devin/cli/sessions.db").execute("SELECT created_at FROM sessions").fetchone()])

    atlas = Atlas(rec, None)
    atlas.handle(said("forget the Claude Code Session from 8 Nov 10:00", 1))
    assert atlas.handle(said("forget", 2)).text == "Forgotten."
    assert held(rec) == {} and MARK not in every_raw(rec), "the Forget did not run"

    run(db, "DELETE FROM orchestration_v2_projection_threads")          # T3 Code prunes the threads
    run(db, "DELETE FROM orchestration_v2_projection_provider_threads")
    Atlas(Record.open(rec.root), None).tick(NOW + timedelta(hours=3))

    assert MARK not in every_raw(rec), f"the folded Session's text is back in the record, as {list(held(rec))}"
    assert child_key in rec.snapshot().forgotten().skip_keys
