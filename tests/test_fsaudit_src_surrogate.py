"""fsaudit A5. sources._guard: "One unreadable Session is logged and skipped; the rest of the store
is still read", and Atlas.tick: "the next tick tries again". A JSON string holding half of a
surrogate pair (`\\ud83d`, which JSON.stringify writes for a string cut inside an emoji) fails
`_canonical`'s `.encode()`: a Claude Code Session with one such line that has no `uuid` is never
collected, at any tick, and one such Devin message takes every Devin Session with it."""
import json
import sqlite3
from contextlib import closing
from datetime import timedelta

from atlas.app import Atlas
from test_collect import CC, DV, NOW, T, claude, devin_store, held, record

CUT = "cut inside an emoji \\ud83d"


def test_a_claude_code_session_with_half_a_surrogate_pair_in_a_line_without_uuid_is_collected(tmp_path,
                                                                                               throwaway_home):
    path = claude(throwaway_home, "chat", "an invented ask")
    with path.open("a") as fh:
        fh.write('{"type": "summary", "summary": "%s", "leafUuid": "invented"}\n' % CUT)
    rec = record(tmp_path, CC)
    atlas = Atlas(rec, None)
    atlas.backfill(NOW)
    atlas.tick(NOW + timedelta(minutes=10))
    assert "chat" in held(rec), "one line with a lone surrogate keeps the whole Session out of the record for good"


def test_one_devin_message_with_half_a_surrogate_pair_leaves_the_other_devin_sessions_collected(tmp_path,
                                                                                                 throwaway_home):
    db = devin_store(throwaway_home / ".local/share/devin/cli", [("user", "an invented ask", True, None, "m1")])
    with closing(sqlite3.connect(db)) as con:
        con.execute("INSERT INTO sessions (id, created_at, title) VALUES ('calm-heron', ?, 'other')", (int(T) + 60,))
        message = json.dumps({"role": "user", "content": "PLACE", "message_id": "m9",
                              "metadata": {"is_user_input": True}}).replace("PLACE", CUT)
        con.execute("INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) "
                    "VALUES ('calm-heron', 0, ?, ?)", (message, int(T) + 60))
        con.commit()
    rec = record(tmp_path, DV)
    Atlas(rec, None).backfill(NOW)
    assert any(key.startswith("brave-otter@") for key in held(rec)), \
        "one message with a lone surrogate keeps every Devin Session out of the record"
