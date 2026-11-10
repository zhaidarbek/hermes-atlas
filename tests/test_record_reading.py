"""Reading the record at its edges (#50): a fallback hour, a Pursuit file Atlas may not open,
that was cut short or that lost an empty section, a surrogate in a body. Invented data only."""
import os
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from atlas.app import Atlas
from atlas.model import part_file
from atlas.record import Conflict, Record
from harness import seeds
from harness.home import AtlasHome
from harness.model import world_model
from test_pursuits import NOW, atlas, files_it_as_one_pursuit, holding, invented, one_session, snap

NEW_YORK = ZoneInfo("America/New_York")
EDT_0145 = datetime(2026, 11, 1, 1, 45, tzinfo=NEW_YORK)
EST_0115 = datetime(2026, 11, 1, 1, 15, fold=1, tzinfo=NEW_YORK)
EST_0100 = datetime(2026, 11, 1, 1, 0, fold=1, tzinfo=NEW_YORK)
EST_0110 = datetime(2026, 11, 1, 1, 10, fold=1, tzinfo=NEW_YORK)


def test_sessions_since_a_time_in_the_repeated_hour_compares_instants(tmp_path):
    a = invented("invented-a", EDT_0145 - timedelta(minutes=30), EDT_0145)
    b = invented("invented-b", EST_0110, EST_0115)
    record = holding(tmp_path, "America/New_York", a, b)
    assert [s.id for s in record.snapshot().sessions(since=EST_0100)] == [b.id]
    assert [s.id for s in record.snapshot().sessions()] == [a.id, b.id]


def test_a_session_folded_in_keeps_the_later_instant_as_last_at(tmp_path):
    a = invented("invented-a", EDT_0145 - timedelta(minutes=30), EDT_0145)
    b = invented("invented-b", EST_0110, EST_0115)
    record = holding(tmp_path, "America/New_York", a, b)
    cs = record.snapshot().changes()
    cs.put_session(a, {part_file("main"): b"invented\n"}, folds={b.id: "subagent:invented"})
    record.commit(cs)
    assert record.snapshot().session(a.id).last_at.timestamp() == EST_0115.timestamp()


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene", world_model()) as home:
        home.set_clock(NOW)
        seeds.after_scene_1(home.record_root)
        yield home


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a file of mode 0")
def test_a_pursuit_file_atlas_may_not_open_is_logged_and_the_brief_still_goes_out(home, caplog):
    atlas(home).backfill(NOW)
    path = sorted((home.record_root / "pursuits").glob("*.md"))[0]
    readable = len(snap(home).pursuits())
    path.chmod(0)
    try:
        report = atlas(home).tick(NOW)
        told = caplog.text.count("Atlas left out a Pursuit file it could not read (PermissionError)")
        held = snap(home)
        assert len(held.pursuits()) == readable - 1 and held.misses_pursuits()
    finally:
        path.chmod(0o600)
    assert report.sent == ("brief 2026-11-15",)
    assert told == 1


def test_a_pursuit_file_cut_before_its_sections_is_left_out_and_never_written_over(tmp_path, caplog):
    at = NOW - timedelta(days=2)
    record, _ = one_session(tmp_path, "Europe/Berlin", at, at + timedelta(minutes=5))
    Atlas(record, files_it_as_one_pursuit()).backfill(NOW)
    (p,) = record.snapshot().pursuits()
    (path,) = (record.root / "pursuits").glob("*.md")
    (record.root / ".atlas/last-written" / path.relative_to(record.root)).unlink()
    cut = path.read_bytes().split(b"## Resume Note")[0]
    path.write_bytes(cut)
    assert record.snapshot().pursuits() == [] and record.snapshot().misses_pursuits()
    assert "Atlas left out a Pursuit file it could not read (ValueError)" in caplog.text
    record.commit(record.snapshot().changes().put_pursuit(p))
    assert path.read_bytes() == cut


def test_a_pursuit_file_cut_after_the_snapshot_raises_conflict_and_is_never_written_over(tmp_path):
    at = NOW - timedelta(days=2)
    record, _ = one_session(tmp_path, "Europe/Berlin", at, at + timedelta(minutes=5))
    Atlas(record, files_it_as_one_pursuit()).backfill(NOW)
    (p,) = record.snapshot().pursuits()
    planned = record.snapshot().changes().put_pursuit(replace(p, focus=not p.focus))
    (path,) = (record.root / "pursuits").glob("*.md")
    (record.root / ".atlas/last-written" / path.relative_to(record.root)).unlink()
    cut = path.read_bytes().split(b"## Resume Note")[0]
    path.write_bytes(cut)
    with pytest.raises(Conflict):
        record.commit(planned)
    record.commit(record.snapshot().changes().put_pursuit(replace(p, focus=not p.focus)))
    assert path.read_bytes() == cut


@pytest.mark.parametrize("section", ["Steps", "Filing rules"])
def test_a_pursuit_file_without_one_of_its_empty_sections_still_reads(tmp_path, caplog, section):
    at = NOW - timedelta(days=2)
    record, _ = one_session(tmp_path, "Europe/Berlin", at, at + timedelta(minutes=5))
    Atlas(record, files_it_as_one_pursuit()).backfill(NOW)
    (p,) = record.snapshot().pursuits()
    (path,) = (record.root / "pursuits").glob("*.md")
    text = path.read_text()
    assert f"\n## {section}\n\n## " in text
    path.write_text(text.replace(f"\n## {section}\n", "", 1))
    assert record.snapshot().pursuits() == [p] and not record.snapshot().misses_pursuits()
    assert "could not read" not in caplog.text


@pytest.mark.parametrize("odd, written", [("\ud83d", "\ufffd"), ("\ud83d\ude00", "\U0001f600")], ids=["lone", "pair"])
def test_a_surrogate_in_a_turn_or_a_folder_is_written_as_utf8_can_hold_it(tmp_path, odd, written):
    s = invented("invented-odd", NOW - timedelta(hours=1), NOW, cwd=f"~/work/{odd}", asked=f"invented {odd} ask")
    record = holding(tmp_path, "Europe/Berlin", s)
    (held,) = record.snapshot().sessions()
    assert held.turns[0].text == f"invented {written} ask"
    (path,) = record.root.glob("evidence/**/session.md")
    assert any(line.startswith("# ") and line.endswith(f" · ~/work/{written}") for line in path.read_text().splitlines())
