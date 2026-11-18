"""fsaudit C12/C19: a part name longer than the file system allows is found only when the
transaction is applied, after it was marked ready, so it can never be applied and every later
commit fails. app.Atlas.tick: "a store or Session it cannot read ... is logged and skipped, and
the next tick tries again"."""
import json

from atlas.app import Atlas
from atlas.model import HarnessId, Profile
from atlas.record import Record
from test_collect import NOW, claude, held, record

CC = HarnessId.CLAUDE_CODE


def test_a_subagent_file_with_a_long_name_never_stops_every_later_commit(tmp_path, throwaway_home):
    main = claude(throwaway_home, "long-name", "dns loop")
    subagent = main.parent / "long-name" / "subagents" / ("agent-" + "a" * 242 + ".jsonl")   # a legal 254-byte name
    subagent.parent.mkdir(parents=True)
    first = json.loads(main.read_text().splitlines()[0])
    subagent.write_text(json.dumps({**first, "isSidechain": True}) + "\n")
    rec = record(tmp_path, CC)

    Atlas(rec, None).tick(NOW)
    claude(throwaway_home, "ordinary", "shelf sizes")
    Atlas(Record.open(rec.root), None).tick(NOW)

    stuck = sorted(p.name for p in (rec.root / ".atlas/txn").glob("*.ready"))
    assert stuck == [], "a transaction that can never be applied was left ready"
    assert "ordinary" in held(Record.open(rec.root)), "the Session written after it was never collected"
    again = Record.open(rec.root)
    again.commit(again.snapshot().changes().put_profile(Profile(("work", "home"), ())))
