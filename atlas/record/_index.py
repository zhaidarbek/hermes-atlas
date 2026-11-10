"""PRIVATE to atlas.record. The disposable SQLite index: full text over the record's files, so
FindQ does not read every transcript. It is built from the files alone, holds nothing they do
not, and is built again whenever the record's HEAD moved since. The commit that removes a
Session writes it again without that Session's rows, so a Forget leaves no row behind, not even
in free pages. Deleting it loses nothing. Written only under the record's write lock.

Built so far: the full-text table. One row per Session: its session.md transcript, its card.md
and the statements of the Pursuits that list it, all as the record holds them, secrets masked.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterable

INDEX = "index.sqlite"
SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE fts USING fts5(evidence UNINDEXED, body);
"""
_STOP = frozenset("a an and are at did do find for from got had have how i in is it its me my of on or that the "
                  "this to was were what when where which with you".split())


def build(folder: Path, generation: int, documents: Iterable[tuple[str, str]]) -> None:
    """Writes the index for `generation` beside the old one, then puts it in place. Deletes the
    staged copies a build cut short left behind."""
    path = folder / INDEX
    for leftover in folder.glob(f"{INDEX}.*.tmp"):
        leftover.unlink(missing_ok=True)
    handle, name = tempfile.mkstemp(prefix=f"{INDEX}.", suffix=".tmp", dir=folder)
    os.close(handle)
    staged = Path(name)
    try:
        with sqlite3.connect(staged) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO meta VALUES ('generation', ?)", (str(generation),))
            db.executemany("INSERT INTO fts VALUES (?, ?)", documents)
        db.close()
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def drop(folder: Path, evidence: set[str]) -> None:
    """Writes the index again without these Sessions' rows, keeping its generation, so none of
    their words stay in the file, its shadow tables or its free pages."""
    try:
        with sqlite3.connect(f"file:{folder / INDEX}?mode=ro", uri=True) as db:
            rows = db.execute("SELECT evidence, body FROM fts").fetchall()
            kept = db.execute("SELECT value FROM meta WHERE key = 'generation'").fetchone()
        db.close()
    except sqlite3.Error:
        (folder / INDEX).unlink(missing_ok=True)
        return
    if any(id in evidence for id, _ in rows):
        build(folder, int(kept[0]) if kept else -1, [(id, body) for id, body in rows if id not in evidence])


def generation(folder: Path) -> int | None:
    try:
        with sqlite3.connect(f"file:{folder / INDEX}?mode=ro", uri=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key = 'generation'").fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else None


def search(folder: Path, text: str, limit: int) -> list[tuple[str, float]]:
    """Evidence ids whose rows hold any of the words of `text`, best match first."""
    words = [w for w in dict.fromkeys(re.findall(r"\w+", text.casefold())) if w not in _STOP and len(w) > 1]
    if not words:
        return []
    with sqlite3.connect(f"file:{folder / INDEX}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT evidence, bm25(fts) FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
                          (" OR ".join(f'"{w}"' for w in words), limit)).fetchall()
    return [(evidence, -score) for evidence, score in rows]
