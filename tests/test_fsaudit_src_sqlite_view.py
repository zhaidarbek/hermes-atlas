"""fsaudit A4. Atlas.tick: "a store or Session it cannot read ... is logged and skipped". A store
database is opened with a busy timeout only: no progress handler, no sqlite limit, and its schema
is trusted. A 12 KiB database whose `messages` is a view over an endless recursive query keeps
`SELECT * FROM messages` running for good, so the tick never ends and holds the tick lease. (The
same view with `zeroblob(900000000) AS content` makes one cell of about a gigabyte.)"""
import sqlite3
import threading
from contextlib import closing

from atlas.app import Atlas
from atlas.sources import _adapters
from test_collect import CC, HM, NOW, claude, held, record


def test_a_store_whose_table_is_an_endless_view_is_skipped_not_waited_on(tmp_path, throwaway_home, monkeypatch):
    claude(throwaway_home, "chat", "an invented ask")
    root = throwaway_home / ".hermes"
    root.mkdir()
    with closing(sqlite3.connect(root / "state.db")) as con:
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, parent_session_id TEXT,
                started_at REAL NOT NULL, ended_at REAL, end_reason TEXT, cwd TEXT, title TEXT);
            INSERT INTO sessions (id, source, started_at) VALUES ('hermes', 'cli', 1.0);
            CREATE VIEW messages AS WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n)
                SELECT i AS id, 'nobody' AS session_id, 'user' AS role, 'x' AS content, 1.0 AS timestamp FROM n;""")
    rec = record(tmp_path, CC, HM)
    opened, connect = [], sqlite3.connect

    def watched(*args, **kwargs):
        opened.append(connect(*args, **kwargs))
        return opened[-1]

    monkeypatch.setattr(_adapters.sqlite3, "connect", watched)
    tick = threading.Thread(target=lambda: Atlas(rec, None).backfill(NOW), daemon=True)
    tick.start()
    tick.join(5)
    waited = tick.is_alive()
    while tick.is_alive():                           # end the read from outside, so the test leaves nothing running
        for con in opened:
            try:
                con.interrupt()
            except sqlite3.Error:
                pass
        tick.join(0.1)
    assert not waited, "an endless view in a store kept the collect running until it was interrupted from outside"
    assert list(held(rec)) == ["chat"]
