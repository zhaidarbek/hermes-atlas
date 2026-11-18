"""fsaudit A4. README, Privacy: Atlas reads the Harness stores "never writing them" and "opens each
SQLite store read-only". A WAL-mode store at rest (its Harness closed, so no -wal or -shm beside
it) gains a `-wal` and a `-shm` file from Atlas's own read, and they stay."""
import sqlite3
from contextlib import closing

import pytest

from atlas.app import Atlas
from test_collect import DV, HM, NOW, devin, hermes, held, record


def stores(home):
    return {HM: lambda: hermes(home / ".hermes", [("chat", "cli", None, None, "invented")],
                               [("chat", "user", "an invented turn")]),
            DV: lambda: devin(home / ".local/share/devin/cli", [("slug", NOW.isoformat())],
                              [("slug", "an invented turn")])}


@pytest.mark.parametrize("harness", [HM, DV])
def test_reading_a_wal_mode_store_at_rest_creates_no_file_beside_it(tmp_path, throwaway_home, harness):
    db = stores(throwaway_home)[harness]()
    with closing(sqlite3.connect(db)) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.commit()
    before = sorted(p.name for p in db.parent.iterdir())
    assert before == [db.name]                       # at rest: the Harness removed its WAL as it closed
    rec = record(tmp_path, harness)
    Atlas(rec, None).backfill(NOW)
    assert len(held(rec)) == 1                       # the store was read
    assert sorted(p.name for p in db.parent.iterdir()) == before, "Atlas wrote files into a Harness store"
