import re
import time as time_module
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from atlas import converse, rules
from atlas.app import Atlas
from atlas.librarian import HostLlm, Librarian
from atlas.model import (ApplyNote, ChannelSetting, CorrectProfile, Inference, NoteOrigin, OwnerMessage, Pause,
                         Profile, ProfileEntry, Settings, Who)
from atlas.record import Conflict, Record, _codec, _seal
from harness.fake_llm import FakeLlm
from harness.home import FakePluginContext

SETTINGS = Settings(timezone="Europe/Berlin", brief_at=time(7, 30), review_day=7, review_at=time(18, 0),
                    gap=timedelta(minutes=15), machine="box", harnesses={}, exclusions=(),
                    channel=ChannelSetting("telegram"))
BERLIN = ZoneInfo(SETTINGS.timezone)
NOW = datetime(2026, 11, 20, 8, 50, tzinfo=BERLIN)
WRONG = "You skip invented Steps."
PROFILE = Profile(("work",), (ProfileEntry(Inference("You work invented hours.", ("cc-aaaaaaaaaaaaaaaa",)),
                                           date(2026, 11, 16), Who.LIBRARIAN),
                              ProfileEntry(Inference(WRONG, ("cx-bbbbbbbbbbbbbbbb",)), date(2026, 11, 16), Who.LIBRARIAN)))
SECRET = "ghp_" + "Inv3ntedT0kenAbCdEfGhIjKlMnOpQrStUvWx"


@pytest.fixture
def record(tmp_path) -> Record:
    rec = Record.open(tmp_path / "atlas", lambda: NOW)
    rec.create(SETTINGS, PROFILE)
    return rec


def head(rec: Record) -> int:
    return int((rec.root / ".atlas/HEAD").read_text())


def edit_profile(rec: Record, typed: str = "- I skip invented Steps in the morning") -> str:
    path = rec.root / "profile.md"
    edited = re.sub(rf"^- {re.escape(WRONG)}.*$", typed, path.read_text(), flags=re.M)
    path.write_text(edited)
    return edited


def test_an_edited_file_is_put_back_and_kept_as_a_note_in_one_commit(record):
    written, before = (record.root / "profile.md").read_bytes(), head(record)
    edited = edit_profile(record)
    [edit] = record.reconcile()
    assert (record.root / "profile.md").read_bytes() == written
    assert head(record) == before + 1
    [note] = record.snapshot().pending_applies()
    assert (edit.path, edit.note, edit.at) == ("profile.md", note.id, NOW)
    assert (note.origin, note.via, note.text, note.offered) == (NoteOrigin.HAND_EDIT, "profile.md", edited, False)
    assert note.diff.startswith(f"-- {WRONG}") and note.diff.endswith("+- I skip invented Steps in the morning\n")
    assert note.derived_from == ("cc-aaaaaaaaaaaaaaaa", "cx-bbbbbbbbbbbbbbbb")
    assert record.reconcile() == []


def test_a_deleted_file_is_put_back_without_a_note(record):
    written = (record.root / "pauses.md").read_bytes()
    (record.root / "pauses.md").unlink()
    assert record.reconcile() == []
    assert (record.root / "pauses.md").read_bytes() == written
    assert record.snapshot().notes() == []


@pytest.mark.parametrize("name, data", [
    ("invented-todo.txt", b"invented line one\ninvented line two"),
    ("invented-photo.jpg", bytes(range(256)) * 4),
    ("invented-latin1.txt", "invent\xe9 caf\xe9\n".encode("latin-1")),
    ("invented-big.bin", b"\x00\xff" * (5 * 1024 * 1024 // 2)),
])
def test_a_file_atlas_did_not_write_is_left_as_it_is(record, name, data):
    stray = record.root / "notes" / name
    stray.write_bytes(data)
    before = head(record)
    started = time_module.monotonic()
    assert record.reconcile() == []
    assert time_module.monotonic() - started < 1
    assert stray.read_bytes() == data
    assert head(record) == before and record.snapshot().notes() == []


def test_a_record_folder_that_is_a_link_is_never_followed(record, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "invented-todo.txt").write_text("invented line\n")
    (outside / "invented-photo.jpg").write_bytes(bytes(range(256)))
    for f in (record.root / "notes").iterdir():
        f.rename(outside / f.name)
    (record.root / "notes").rmdir()
    (record.root / "notes").symlink_to(outside)
    held = {p: p.read_bytes() for p in outside.rglob("*") if p.is_file()}
    edit_profile(record)
    record.reconcile()
    assert {p: p.read_bytes() for p in outside.rglob("*") if p.is_file()} == held


@pytest.mark.parametrize("name", [".profile.md.swp", "profile.md~", "#profile.md#", "invented.tmp"])
def test_editor_droppings_are_never_hand_edits(record, name):
    (record.root / "notes" / name).write_text("invented swap\n")
    assert record.reconcile() == []
    assert (record.root / "notes" / name).exists()


def test_a_file_named_as_a_record_file_without_a_copy_is_left_alone(record):
    stray = record.root / "pursuits" / "invented.md"
    stray.write_text("invented unreadable Pursuit\n")
    assert record.reconcile() == []
    assert stray.read_text() == "invented unreadable Pursuit\n"


def test_a_hand_edit_made_during_another_commit_is_absorbed_before_it_compares(record):
    written = (record.root / "profile.md").read_bytes()
    planned = record.snapshot().changes().put_pauses((Pause(NOW, NOW + timedelta(days=1), "invented pause"),))
    edit_profile(record)
    record.commit(planned)
    snap = record.snapshot()
    assert (record.root / "profile.md").read_bytes() == written
    assert [n.via for n in snap.pending_applies()] == ["profile.md"]
    assert [p.said for p in snap.pauses()] == ["invented pause"]


def test_a_commit_planned_on_the_edited_file_conflicts_and_the_edit_is_kept(record):
    edit_profile(record)
    planned = record.snapshot().changes().put_profile(Profile(("work", "home"), PROFILE.entries))
    with pytest.raises(Conflict):
        record.commit(planned)
    assert [n.via for n in record.snapshot().pending_applies()] == ["profile.md"]


def test_a_file_saved_with_crlf_endings_is_put_back_and_the_tick_goes_on(record, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    path = record.root / "profile.md"
    written = path.read_bytes()
    path.write_bytes(written.replace(b"\n", b"\r\n"))
    report = Atlas(record, None).tick(NOW.replace(hour=6))
    assert report.hand_edits == 1 and path.read_bytes() == written
    assert "\r" not in record.snapshot().pending_applies()[0].text


def test_a_copy_an_older_atlas_wrote_bare_comes_back_masked(record):
    bare = Profile(("work",), (ProfileEntry(Inference(f"You keep {SECRET} invented.", ("cc-aaaaaaaaaaaaaaaa",)),
                                            date(2026, 11, 16), Who.LIBRARIAN),))
    data = _seal.stamp(_codec.render_bare("profile", bare, BERLIN))
    for path in ("profile.md", ".atlas/last-written/profile.md"):
        (record.root / path).write_bytes(data)
    (record.root / "profile.md").write_bytes(data.replace(b"You keep", b"You kept", 1))
    record.reconcile()
    for path in ("profile.md", ".atlas/last-written/profile.md"):
        assert SECRET.encode() not in (record.root / path).read_bytes()
    assert SECRET not in (record.root / "notes/2026/11/2026-11-20-hand-edits.md").read_text()


def message(text: str, minutes: int) -> OwnerMessage:
    return OwnerMessage(text, NOW + timedelta(minutes=minutes), "telegram", "telegram:1", None, f"telegram:1:{minutes}")


def test_the_apply_offer_is_made_once(record):
    edit_profile(record)
    record.reconcile()
    first = rules.converse(record.snapshot(), message("invented words", 1), (), ())
    record.commit(first.changes)
    second = rules.converse(record.snapshot(), message("more invented words", 2), (), ())
    assert first.outcome.lines == ('You edited profile.md by hand. I put it back and kept your text as a Note. '
                                   'Reply "apply" to make the change.',)
    assert second.outcome.lines == ()
    assert [n.offered for n in record.snapshot().pending_applies()] == [True]


def llm(answer: dict) -> HostLlm:
    return HostLlm(FakePluginContext(fake_llm=FakeLlm({"atlas_converse": lambda call: answer})))


def test_edit_to_commands_reads_the_diff_with_the_model(record):
    edit_profile(record)
    record.reconcile()
    snap = record.snapshot()
    [note] = snap.pending_applies()
    librarian = Librarian(llm({"changes": [{"entry": 2, "text": "You do invented Steps at night."}]}), record)
    assert librarian.edit_to_commands(snap, note) == [CorrectProfile(WRONG, "You do invented Steps at night.")]


def test_apply_changes_the_profile_on_the_hand_edit_note_and_marks_it_applied(record):
    edit_profile(record)
    record.reconcile()
    record.commit(rules.converse(record.snapshot(), message("invented words", 1), (), ()).changes)
    [note] = record.snapshot().pending_applies()
    applied = rules.converse(record.snapshot(), message("apply", 2), (ApplyNote(note.id),), (),
                             {note.id: (CorrectProfile(WRONG, "You do invented Steps at night."),)})
    record.commit(applied.changes)
    snap = record.snapshot()
    assert applied.outcome.lines == ('Changed: "You do invented Steps at night." [F your Note, Fri 08:50]',)
    assert snap.profile().entries[1].statement.source.evidence == note.id
    assert snap.pending_applies() == []
    assert list(snap.profile_corrections()) == [WRONG]


def test_profile_words_and_apply_are_unread_without_a_model_or_an_offer(record):
    snap = record.snapshot()
    assert converse.interpret(message("profile: I work invented hours", 1), snap, None).unclear == converse.UNREAD
    librarian = Librarian(llm({"entry": 2, "text": "You do invented Steps at night."}), record)
    assert converse.interpret(message("apply", 1), snap, librarian).unclear == converse.UNREAD
    assert converse.interpret(message("profile: I work invented hours", 1), snap, librarian).commands == (
        CorrectProfile(WRONG, "You do invented Steps at night."),)


def test_the_same_edit_saved_again_in_the_same_minute_is_kept_once(record):
    edit_profile(record)
    record.reconcile()
    edit_profile(record)
    record.reconcile()
    assert [n.via for n in record.snapshot().notes()] == ["profile.md"]


def test_a_reply_carries_one_offer_however_many_edits_wait(record):
    for typed in ("- I skip invented Steps at dawn", "- I skip invented Steps at noon"):
        edit_profile(record, typed)
        record.reconcile()
    (record.root / "pauses.md").write_text("invented hand edit\n")
    record.reconcile()
    first = rules.converse(record.snapshot(), message("invented words", 1), (), ())
    record.commit(first.changes)
    [offer] = first.outcome.lines
    assert offer.endswith('Reply "apply" to make the change.')
    assert [n.offered for n in record.snapshot().pending_applies()] == [True, True, True]


def test_apply_makes_the_hand_edit_the_latest_offer_named_and_no_other(record):
    librarian = Librarian(llm({"changes": []}), record)
    edit_profile(record)
    record.reconcile()
    record.commit(rules.converse(record.snapshot(), message("invented words", 1), (), ()).changes)
    [profile_edit] = record.snapshot().pending_applies()
    assert converse.interpret(message("apply", 2), record.snapshot(), librarian).commands == (
        ApplyNote(profile_edit.id),)
    (record.root / "pauses.md").write_text("invented hand edit\n")
    record.reconcile()
    record.commit(rules.converse(record.snapshot(), message("more invented words", 3), (), ()).changes)
    assert converse.interpret(message("apply", 4), record.snapshot(), librarian).unclear == converse.UNREAD
