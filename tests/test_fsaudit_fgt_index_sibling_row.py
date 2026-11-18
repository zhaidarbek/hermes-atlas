"""fsaudit C17: after a Forget the search index still holds a Pursuit line drawn from the forgotten
Session, in the row of another Session of the same Pursuit.

atlas/record/_index.py: "The commit that removes a Session writes it again without that Session's
rows, so a Forget leaves no row behind". README, Forget: removes "its rows in the search index, every
Pursuit line ... that cites it". Snapshot.index puts every Resume Note statement of a Pursuit into the
row of each Session the Pursuit lists; _index.drop removes only the forgotten Session's own row and
keeps the others as they were built, so the line the Forget takes out of the Pursuit file stays in
.atlas/index.sqlite until something asks the index again (only a find or `hermes atlas reindex` does).
"""
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta

from atlas.app import Atlas
from atlas.model import (Actor, Cite, Fact, HistoryLine, Inference, Kind, Membership, OwnerMessage, Pursuit, ResumeNote,
                         Status, Turn)
from atlas.record import Record
from test_commit import BERLIN, PROFILE, SETTINGS
from test_evidence import T0, put, session

MARK = "zebra7quartz9marker"
SAID = f"the vault phrase is {MARK} remember it"


def said(text, minutes, ref):
    return OwnerMessage(text, T0 + timedelta(days=1, minutes=minutes), "telegram", None, None, ref, addressed=True)


def test_forget_leaves_no_line_of_the_session_in_the_index(tmp_path):
    root = tmp_path / "home" / "atlas"
    rec = Record.open(root)
    rec.create(SETTINGS, PROFILE)
    gone = session(Turn("owner", T0, SAID, "main"), native="native-forget-me")
    kept = replace(session(Turn("owner", T0 + timedelta(minutes=5), "tidy the shelf brackets", "main"),
                           native="native-keep-me"), started=T0 + timedelta(minutes=5))
    put(root, gone, {"main.jsonl.zst": b'{"n": 1}\n'})
    put(root, kept, {"main.jsonl.zst": b'{"n": 2}\n'})
    basis = (kept.id,)
    p = Pursuit(id="p-4k7q", name="Shelf brackets", kind=Kind.PROJECT, area="work", status=Status.ACTIVE, focus=False,
                created=date(2026, 11, 15), continued=None,
                resume=ResumeNote((), (Inference("Left the brackets.", basis),),
                                  (Fact(SAID, Cite(gone.id, "Codex via T3 Code", T0)),), (), ()),
                next_step=None, past_steps=(), filing_rules=(),
                sessions=(Membership(gone.id, gone.last_at, True), Membership(kept.id, kept.last_at, True)),
                proposals=(), history=(HistoryLine("h-0001", datetime(2026, 11, 15, 20, tzinfo=BERLIN), Actor.ATLAS,
                                                   Inference("Created it from your Sessions.", basis)),))
    rec = Record.open(root)
    rec.commit(rec.snapshot().changes().put_pursuit(p))
    assert rec.reindex() == 2
    index = root / ".atlas/index.sqlite"
    assert MARK.encode() in index.read_bytes()

    atlas = Atlas(rec, None)
    atlas.handle(said("forget the Codex Session from 16 Nov 23:50", 2, "t:2"))
    assert atlas.handle(said("forget", 3, "t:3")).text == "Forgotten."
    assert MARK not in (root / "pursuits/shelf-brackets.md").read_text(), "the Forget did not run"

    with closing(sqlite3.connect(index)) as db:
        rows = [id for id, body in db.execute("SELECT evidence, body FROM fts") if MARK in body]
    assert rows == [], f"the index row of the kept Session {rows} still holds the forgotten Session's line"
    assert MARK.encode() not in index.read_bytes()
