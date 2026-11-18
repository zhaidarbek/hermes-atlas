"""fsaudit C17: Forget leaves a line Atlas drew from the forgotten Session in notes/, in the answer
Atlas gave the owner before the Forget.

atlas/forget.py: "the owner's word removes a Session and everything Atlas derived from it". README,
Forget: "Forget removes a Session for good: ... every Pursuit line ... that cites it". The answer to
"where did I leave off on <Pursuit>" (no model call) copies the Pursuit's lines into the owner Note's
`atlas_reply`; so does the answer to "find the conversation where ..." (ask.found). forget._closure
removes only hand-edit and agent Notes, so the line the Forget takes out of the Pursuit file stays in
notes/<day>-telegram.md and in its last-written copy under .atlas/.
"""
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


def test_forget_removes_the_answer_that_repeats_the_sessions_line(tmp_path):
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
    atlas = Atlas(rec, None)

    assert MARK in atlas.handle(said("where did I leave off on shelf brackets", 1, "t:1")).text
    assert "1 Fact in the Resume Note of Shelf brackets" in atlas.handle(
        said("forget the Codex Session from 16 Nov 23:50", 2, "t:2")).text
    assert atlas.handle(said("forget", 3, "t:3")).text == "Forgotten."
    assert MARK not in (root / "pursuits/shelf-brackets.md").read_text(), "the Forget did not run"

    holding = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and MARK.encode() in p.read_bytes())
    assert holding == [], f"the line drawn from the forgotten Session is still in {holding}"
