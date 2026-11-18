"""What the hardened store reads must still do for an ordinary store."""
import sqlite3
from contextlib import closing

from atlas.app import Atlas
from atlas.model import Sent
from atlas.sources import _adapters
from test_collect import CX, HM, NOW, held, hermes, record, rollout


def wal_store(home):
    db = hermes(home / ".hermes", [("chat", "cli", None, None, "invented")], [("chat", "user", "an invented turn")])
    with closing(sqlite3.connect(db)) as con:
        con.execute("PRAGMA journal_mode=WAL")
    return db


def test_a_writer_that_comes_while_a_store_at_rest_is_read_gets_the_read_done_again(throwaway_home):
    """A store read with no WAL beside it is read without locks. A Harness that starts and
    checkpoints meanwhile makes that read count for nothing, and the next try sees its rows."""
    db = wal_store(throwaway_home)
    root = db.parent
    writers, tries = [], []

    def read(con):
        tries.append(con.execute("SELECT count(*) FROM messages").fetchone()[0])
        if not writers:
            writers.append(sqlite3.connect(db))
            writers[0].execute("INSERT INTO messages (session_id, role, content, timestamp) "
                               "VALUES ('chat', 'user', 'written meanwhile', 9.0)")
            writers[0].commit()
            writers[0].execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return tries[-1]

    try:
        assert _adapters._read_db(_adapters._database(db, root), read) == 2
    finally:
        for writer in writers:
            writer.close()
    assert tries == [1, 2]


def test_a_writer_that_came_and_went_while_a_store_at_rest_was_read_gets_the_read_done_again(throwaway_home):
    db = wal_store(throwaway_home)
    tries = []

    def read(con):
        tries.append(con.execute("SELECT count(*) FROM messages").fetchone()[0])
        if len(tries) == 1:
            with closing(sqlite3.connect(db)) as writer:
                writer.execute("DELETE FROM messages")
                writer.commit()
        return tries[-1]

    assert _adapters._read_db(_adapters._database(db, db.parent), read) == 0
    assert tries == [1, 0]
    assert sorted(p.name for p in db.parent.iterdir()) == [db.name]


def test_a_store_its_harness_has_open_is_read_with_its_wal(tmp_path, throwaway_home):
    db = wal_store(throwaway_home)
    with closing(sqlite3.connect(db)) as harness:
        harness.execute("INSERT INTO messages (session_id, role, content, timestamp) "
                        "VALUES ('chat', 'assistant', 'only in the WAL', 9.0)")
        harness.commit()
        rec = record(tmp_path, HM)
        Atlas(rec, None).backfill(NOW)
    assert sorted(held(rec)["chat"]) == [("agent", "only in the WAL"), ("owner", "an invented turn")]


def test_a_store_with_a_view_atlas_does_not_read_is_still_collected(tmp_path, throwaway_home):
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented")],
                [("chat", "user", "an invented turn")])
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE VIEW recent AS SELECT * FROM messages")
        con.execute("CREATE TRIGGER touched AFTER INSERT ON messages BEGIN SELECT 1; END")
        con.commit()
    assert [u.native_id for u in _adapters.Hermes()._units(db.parent, Sent())] == ["chat"]


def test_a_compressed_rollout_of_ordinary_text_is_collected(tmp_path, throwaway_home):
    said = "the same sentence, said again and again. " * 2000
    rollout(throwaway_home / ".codex", "long", [said], ["ok"], zst=True)
    rec = record(tmp_path, CX)
    Atlas(rec, None).backfill(NOW)
    assert held(rec)["long"][0] == ("owner", said.strip())
