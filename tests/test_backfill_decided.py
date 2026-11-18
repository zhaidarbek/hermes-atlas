"""Backfill keeps a Decided line whose quote is in a message the owner typed, as filing does."""
from atlas.app import Atlas
from atlas.model import Fact
from audit_fixtures import FIRST, NOW, Scripted, claude, one_pursuit, record

TYPED = "we decided to keep the auth check on the logger"


def test_backfill_keeps_a_decided_line_whose_quote_the_owner_typed(tmp_path, throwaway_home):
    claude(throwaway_home, FIRST, [("user", TYPED), ("assistant", "Understood, the auth check stays.")])
    rec = record(tmp_path)
    llm = Scripted(group=one_pursuit(decided=[{"text": "Keep the auth check on the logger.", "quote": TYPED}]))
    Atlas(rec, llm).backfill(NOW)
    (p,) = rec.snapshot().pursuits()
    assert [d.text for d in p.resume.decided if isinstance(d, Fact)] == ["Keep the auth check on the logger."]
