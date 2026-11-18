"""fsaudit C16: README, Privacy: "Outside the Session folders under evidence/ and the exports in
inbox/, it never changes or deletes a file in the record folder that it did not write." A file
the owner put where Atlas next writes a Journal day is replaced, and no copy of it is kept."""
from datetime import date, timedelta

from atlas.model import ONE_OFF, DayTime, JournalDay
from atlas.record import Record
from test_commit import PROFILE, SETTINGS, files


def test_a_journal_day_never_replaces_a_file_atlas_did_not_write(tmp_path):
    rec = Record.open(tmp_path / "atlas")
    rec.create(SETTINGS, PROFILE)
    mine = rec.root / "journal" / "2026-11-17.md"
    mine.write_text("my own diary, not Atlas's\n")
    day = JournalDay(date(2026, 11, 17), DayTime(timedelta(minutes=5), {"work": {ONE_OFF: timedelta(minutes=5)}}, ()),
                     (), (), (), ())

    try:
        rec.commit(rec.snapshot().changes().put_journal(day))
    except Exception:
        pass   # refusing is one way to keep the guarantee

    kept = [path for path, data in files(rec.root).items() if b"my own diary" in data]
    assert mine.read_text() == "my own diary, not Atlas's\n", f"Atlas replaced a file it did not write; copies kept: {kept}"
