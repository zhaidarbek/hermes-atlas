"""A record an older Atlas wrote with its secrets bare is masked by the opens after it (#71): every
file a codec writes, its last-written copy included, and never raw/. Invented data only."""
import json
import logging
import os
import shutil
import signal
from contextlib import contextmanager
from dataclasses import replace
from datetime import time, timedelta
from pathlib import Path

import pytest

import atlas.record as record_module
from atlas.app import Atlas
from atlas.channel import Delivered
from atlas.model import (Actor, Cite, Fact, HistoryLine, Inference, Kind, Membership, Note, NoteOrigin, Pause, Profile,
                         ProfileEntry, Pursuit, ResumeNote, SendAttempt, SendStatus, SessionCard, Status, Step, Who)
from atlas.record import Record, _codec, _masking, _seal, _txn
from test_collect import (BRIEF, CC, HM, KEY, MIRROR, NOW, PROFILE, T, app, claude, held, hermes, record, run,
                          unzstd)

SECRET = "ghp_" + "Inv3ntedT0kenAbCdEfGhIjKlMnOpQrStUvWx"
MASK = "[secret: github token]"
PLANTED = f"Invented line {SECRET}"
NOTE = ".atlas/masked"


@contextmanager
def bare(monkeypatch):
    """Writes as Atlas did before it masked every text."""
    with monkeypatch.context() as before_masking:
        before_masking.setattr(_codec, "_scrubbed", lambda value, hint: value)
        yield


def older(rec: Record) -> Path:
    """The record as an older Atlas leaves it: it never wrote the note that masking ran."""
    (rec.root / NOTE).unlink(missing_ok=True)
    return rec.root


def a_pursuit(evidence) -> Pursuit:
    seen = Fact(PLANTED, Cite(evidence, "Claude Code", NOW))
    return Pursuit(
        id="p-6r2t", name="Shelf brackets", kind=Kind.PROJECT, area="home", status=Status.ACTIVE, focus=True,
        created=NOW.date(), continued=None,
        resume=ResumeNote(where=(), left_off=(seen,), decided=(), open_questions=(), reopen=(evidence,)),
        next_step=Step("s-20261108-1", Inference(PLANTED, (evidence,)), NOW.date()),
        past_steps=(), filing_rules=(), sessions=(Membership(evidence, NOW, True),), proposals=(),
        history=(HistoryLine("h-0001", NOW, Actor.ATLAS, Inference("Created from 1 Session", (evidence,))),))


def written_bare(tmp_path, throwaway_home, monkeypatch, sessions=1) -> Path:
    """A Session collected, a Pursuit, its card, a Note, a sent brief, the Profile and a Pause, each
    holding the invented token, as an older Atlas wrote them."""
    for n in range(sessions):
        claude(throwaway_home, f"shelves-{n}", f"plan the shelves with {SECRET}")
    rec = record(tmp_path, CC)
    with bare(monkeypatch):
        Atlas(rec, None).tick(NOW)
        snap = rec.snapshot()
        evidence = snap.sessions()[0].id
        rec.commit(snap.changes().put_pursuit(a_pursuit(evidence))
                   .put_card(SessionCard(evidence, Inference(PLANTED, (evidence,)),
                                         Inference("Invented outcome.", (evidence,)), None, None))
                   .append_note(Note("nt-1", NOW, NoteOrigin.OWNER, "telegram", PLANTED, atlas_reply=PLANTED))
                   .put_brief(replace(BRIEF, text=f"{BRIEF.text}\n{PLANTED}"))
                   .put_profile(Profile(PROFILE.areas, (ProfileEntry(Inference(PLANTED, (evidence,)), NOW.date(),
                                                                     Who.LIBRARIAN),)))
                   .put_pauses((Pause(NOW, NOW + timedelta(days=7), PLANTED),)))
    return older(rec)


def files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def holding(root: Path, text: str) -> set[str]:
    """The files outside raw/ that hold `text`."""
    return {path for path, data in files(root).items() if "/raw/" not in path and text.encode() in data}


def stat(root: Path) -> dict[str, tuple[bytes, int]]:
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def test_one_open_masks_every_file_an_older_atlas_wrote_bare_and_leaves_raw_as_it_was(tmp_path, throwaway_home,
                                                                                      monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    kinds = {"session.md", "card.md", "pursuits/shelf-brackets.md", "notes/", "briefs/", "profile.md", "pauses.md"}
    before = holding(root, SECRET)
    assert all(any(kind in path for path in before if not path.startswith(".atlas")) for kind in kinds), before
    assert all(any(kind in path for path in before if path.startswith(".atlas/last-written/"))
               for kind in kinds - {"session.md"}), before

    rec = Record.open(root)

    assert holding(root, SECRET) == set()
    assert any(SECRET.encode() in unzstd(p) for p in root.glob("evidence/**/raw/*"))
    snap = rec.snapshot()
    assert snap.pursuits()[0].resume.left_off[0].text == f"Invented line {MASK}"
    assert snap.card(snap.sessions()[0].id).intent.text == f"Invented line {MASK}"
    assert snap.notes()[0].atlas_reply == f"Invented line {MASK}"
    assert snap.brief(NOW.date()).text.endswith(f"Invented line {MASK}")
    assert snap.sessions()[0].turns[0].text == f"plan the shelves with {MASK}"
    assert all(_seal.verifies(data) for path, data in files(root).items()
               if path.startswith(("pursuits/", "notes/", "briefs/", "profile.md", "pauses.md")))
    assert (root / NOTE).exists()


def test_a_second_open_writes_nothing(tmp_path, throwaway_home, monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    Record.open(root)
    masked = stat(root)

    Record.open(root)
    assert stat(root) == masked

    (root / NOTE).unlink()
    Record.open(root)
    assert {p: s for p, s in stat(root).items() if p != NOTE} == {p: s for p, s in masked.items() if p != NOTE}


def test_a_hand_edited_file_and_its_last_written_copy_are_left_as_they_are(tmp_path, throwaway_home, monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    pursuit = root / "pursuits/shelf-brackets.md"
    pursuit.write_bytes(pursuit.read_bytes().replace(b"Invented line", b"Invented line, edited", 1))
    edited, copy = pursuit.read_bytes(), (root / ".atlas/last-written/pursuits/shelf-brackets.md").read_bytes()

    Record.open(root)

    assert pursuit.read_bytes() == edited
    assert (root / ".atlas/last-written/pursuits/shelf-brackets.md").read_bytes() == copy
    assert holding(root, SECRET) == {"pursuits/shelf-brackets.md", ".atlas/last-written/pursuits/shelf-brackets.md"}
    assert (root / NOTE).exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens any file")
def test_a_file_atlas_cannot_open_is_never_written_over(tmp_path, throwaway_home, monkeypatch, caplog):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    pursuit = root / "pursuits/shelf-brackets.md"
    held = pursuit.read_bytes()
    pursuit.chmod(0)
    try:
        with caplog.at_level(logging.INFO, logger="atlas.record"):
            Record.open(root)
    finally:
        pursuit.chmod(0o600)

    assert pursuit.read_bytes() == held
    assert holding(root, SECRET) == {"pursuits/shelf-brackets.md", ".atlas/last-written/pursuits/shelf-brackets.md"}
    assert "PermissionError" in caplog.text
    assert SECRET not in caplog.text and "shelf" not in caplog.text and str(tmp_path) not in caplog.text


def test_names_and_areas_are_masked_in_one_transaction_and_the_other_files_a_step_an_open(
        tmp_path, throwaway_home, monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch, sessions=3)
    written = []
    real = _txn.apply

    def observed(root, txn):
        written.append({entry["target"] for entry in json.loads((root / f".atlas/txn/{txn}.ready").read_bytes())})
        real(root, txn)

    monkeypatch.setattr(_txn, "apply", observed)
    monkeypatch.setattr(_masking, "SECONDS", 0)

    opens = opened_until_masked(root)

    named = ("pursuits/", "profile.md", "pauses.md")
    together = [txn for txn in written if any(path.startswith(named) for path in txn)]
    assert len(together) == 1 and {"pursuits/shelf-brackets.md", "profile.md", "pauses.md"} <= together[0]
    alone = [{path for path in txn if path.startswith(("briefs/", "notes/", "evidence/"))} for txn in written[1:]]
    assert [len(paths) for paths in alone] == [1, 1, 1, 1, 1, 1]
    assert opens > len(written)
    assert holding(root, SECRET) == set()
    assert (root / NOTE).exists() and not (root / ".atlas/masking").exists()


def test_a_failed_write_raises_nothing_and_a_later_open_masks_every_file(tmp_path, throwaway_home, monkeypatch,
                                                                         caplog):
    root = a_masking_pass_cut_short(tmp_path, throwaway_home, monkeypatch)
    monkeypatch.setattr(_masking, "SECONDS", 60)

    def full_disk(root, txn):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as failing, caplog.at_level(logging.WARNING, logger="atlas.record"):
        failing.setattr(_txn, "apply", full_disk)
        Record.open(root)

    assert not (root / NOTE).exists()
    assert "OSError" in caplog.text and SECRET not in caplog.text
    assert holding(root, SECRET)

    Record.open(root)
    assert holding(root, SECRET) == set()
    assert (root / NOTE).exists()
    assert list((root / ".atlas/txn").iterdir()) == []


def test_a_crash_part_way_through_a_write_is_finished_by_the_next_open(tmp_path, throwaway_home, monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch, sessions=2)

    class Crash(BaseException):
        pass

    def cut_short(root, txn):
        entry = json.loads((root / f".atlas/txn/{txn}.ready").read_bytes())[0]
        os.replace(root / f".atlas/txn/{txn}" / entry["staged"], root / entry["target"])
        raise Crash

    with monkeypatch.context() as crashing:
        crashing.setattr(_txn, "apply", cut_short)
        with pytest.raises(Crash):
            Record.open(root)

    opened_until_masked(root)
    assert holding(root, SECRET) == set()
    assert all(data == (root / path.removeprefix(".atlas/last-written/")).read_bytes()
               for path, data in files(root).items() if path.startswith(".atlas/last-written/"))


def test_a_transcript_that_does_not_read_is_never_written_over(tmp_path, throwaway_home, monkeypatch, caplog):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    session = next(root.glob("evidence/**/session.md"))
    cut = session.read_bytes()[:40]
    session.write_bytes(cut)

    with caplog.at_level(logging.WARNING, logger="atlas.record"):
        Record.open(root)

    assert session.read_bytes() == cut
    assert holding(root, SECRET) == set()
    assert "left as they were 1 record files" in caplog.text and str(tmp_path) not in caplog.text


@pytest.mark.parametrize("status", [SendStatus.SENT, SendStatus.SENDING, SendStatus.ARRIVAL_UNKNOWN])
def test_a_brief_sent_bare_and_masked_at_open_keeps_its_mirror_rows_out(tmp_path, throwaway_home, monkeypatch, status):
    db = hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelf brackets")],
                [("chat", "user", "shelf bracket sizes")])
    rec = record(tmp_path, HM, brief_at=time(7, 30))
    text = f"{BRIEF.text}\nInvented {KEY}"
    with bare(monkeypatch):
        rec.commit(rec.snapshot().changes().put_brief(
            replace(BRIEF, attempts=(SendAttempt(NOW, status, ("41",) if status is SendStatus.SENT else ()),),
                    text=text)))
    run(db, MIRROR, text, T + 60)
    rec = Record.open(older(rec))
    masked = f"{BRIEF.text}\nInvented [secret: api key]"
    assert rec.snapshot().brief(NOW.date()).text == masked
    sent = []

    def mirrored(channel, t):
        sent.append(t)
        run(db, MIRROR, t, T + 120)
        return Delivered("telegram:5550001", ("42",))

    monkeypatch.setattr(app.channel.Telegram, "send", mirrored)
    Atlas(rec, None).tick(NOW)
    Atlas(rec, None).tick(NOW + timedelta(minutes=10))
    assert sent == ([] if status is SendStatus.SENT else [masked])
    assert held(rec) == {"chat": [("owner", "shelf bracket sizes")]}
    assert holding(rec.root, KEY) == set()


def test_a_reply_sent_bare_and_masked_at_open_keeps_its_mirror_row_out(tmp_path, throwaway_home, monkeypatch):
    reply = f"Invented reply {KEY}"
    hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelf brackets")],
           [("chat", "user", "shelf bracket sizes"), ("chat", "assistant", reply)])
    rec = record(tmp_path, HM)
    with bare(monkeypatch):
        rec.commit(rec.snapshot().changes().append_note(
            Note("nt-1", NOW, NoteOrigin.OWNER, "telegram", "Invented ask.", atlas_reply=reply)))
    rec = Record.open(older(rec))
    assert rec.snapshot().notes()[0].atlas_reply == "Invented reply [secret: api key]"
    Atlas(rec, None).tick(NOW + timedelta(minutes=2))
    assert held(rec) == {"chat": [("owner", "shelf bracket sizes")]}


def test_a_new_record_is_marked_masked_at_setup(tmp_path):
    rec = record(tmp_path)
    assert (rec.root / NOTE).exists()


def a_masking_pass_cut_short(tmp_path, throwaway_home, monkeypatch, sessions=2) -> Path:
    root = written_bare(tmp_path, throwaway_home, monkeypatch, sessions=sessions)
    monkeypatch.setattr(_masking, "SECONDS", 0)
    Record.open(root)
    assert not (root / NOTE).exists()
    return root


def opened_until_masked(root: Path, opens=60) -> int:
    for n in range(1, opens + 1):
        Record.open(root)
        if (root / NOTE).exists():
            return n
    return opens


@pytest.mark.parametrize("junk", [b"zzzz", b"", b"\xff\xfe", json.dumps(
    {"after": "evidence/zzz/9999/12/cc-zzzz/session.md", "batch": []}).encode()],
                         ids=["past-the-end", "empty", "not-utf8", "well-formed-past-the-end"])
def test_a_garbled_note_of_the_pass_under_way_never_ends_it_with_files_bare(tmp_path, throwaway_home, monkeypatch,
                                                                            junk):
    root = a_masking_pass_cut_short(tmp_path, throwaway_home, monkeypatch)
    note = root / ".atlas/masking"
    for path in [note] if note.is_file() else [p for p in note.rglob("*") if p.is_file()]:
        path.write_bytes(junk)

    opened_until_masked(root)

    assert holding(root, SECRET) == set()
    assert (root / NOTE).exists()


def test_a_seal_that_does_not_decode_leaves_that_file_alone_and_the_others_are_masked(tmp_path, throwaway_home,
                                                                                     monkeypatch, caplog):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    pauses = root / "pauses.md"
    pauses.write_bytes(pauses.read_bytes().replace(b"sealed: sha256:", b"sealed: \xffsha256:", 1))
    garbled = pauses.read_bytes()

    with caplog.at_level(logging.WARNING, logger="atlas.record"):
        Record.open(root)

    assert pauses.read_bytes() == garbled
    assert holding(root, SECRET) == {"pauses.md", ".atlas/last-written/pauses.md"}
    assert "UnicodeDecodeError" in caplog.text and SECRET not in caplog.text
    assert (root / NOTE).exists()


@pytest.mark.parametrize("edit", [
    lambda text: text.replace(b"machine: ", b"# an invented owner comment\nmachine: ", 1),
    lambda text: text.replace(b"\n**You", b"\nAn invented aside the owner wrote.\n\n**You", 1),
    lambda text: text.replace(b"**You", b"**Me", 1),
], ids=["front-matter-comment", "aside-before-the-first-turn", "renamed-heading"])
def test_a_transcript_edited_by_hand_is_left_as_it_is(tmp_path, throwaway_home, monkeypatch, caplog, edit):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    session = next(root.glob("evidence/**/session.md"))
    session.write_bytes(edit(session.read_bytes()))
    edited = session.read_bytes()

    with caplog.at_level(logging.WARNING, logger="atlas.record"):
        Record.open(root)

    assert session.read_bytes() == edited
    assert holding(root, SECRET) == {str(session.relative_to(root))}
    assert "1 edited by hand" in caplog.text


def test_a_file_that_does_not_read_is_logged_by_error_type(tmp_path, throwaway_home, monkeypatch, caplog):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    card = next(root.glob("evidence/**/card.md"))
    card.write_bytes(_seal.stamp(card.read_bytes().replace(b"- Intent:", b"- Invented:", 1)))

    with caplog.at_level(logging.WARNING, logger="atlas.record"):
        Record.open(root)

    assert "ValueError" in caplog.text
    assert SECRET not in caplog.text and str(tmp_path) not in caplog.text and "Invented" not in caplog.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes any folder")
def test_a_write_the_record_refuses_raises_on_no_open_and_is_done_once_it_is_allowed(tmp_path, throwaway_home,
                                                                                      monkeypatch, caplog):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    pursuits = root / "pursuits"
    pursuits.chmod(0o500)
    try:
        with caplog.at_level(logging.WARNING, logger="atlas.record"):
            for _ in range(3):
                Record.open(root).snapshot()
    finally:
        pursuits.chmod(0o700)
    assert "PermissionError" in caplog.text and not (root / NOTE).exists()

    Record.open(root)

    assert holding(root, SECRET) == set()
    assert (root / NOTE).exists()
    assert list((root / ".atlas/txn").iterdir()) == []


def test_a_hand_edit_saved_while_the_pass_runs_is_left_as_it_is(tmp_path, throwaway_home, monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    pursuit = root / "pursuits/shelf-brackets.md"
    copy = (root / ".atlas/last-written/pursuits/shelf-brackets.md").read_bytes()
    edited = pursuit.read_bytes().replace(b"Invented line", b"Invented line, edited", 1)
    real = _codec.render

    def saved_meanwhile(kind, value, tz):
        if kind == "pursuit":
            pursuit.write_bytes(edited)
        return real(kind, value, tz)

    with monkeypatch.context() as editing:
        editing.setattr(_codec, "render", saved_meanwhile)
        Record.open(root)

    assert pursuit.read_bytes() == edited
    assert (root / ".atlas/last-written/pursuits/shelf-brackets.md").read_bytes() == copy
    assert holding(root, SECRET) == {"pursuits/shelf-brackets.md", ".atlas/last-written/pursuits/shelf-brackets.md"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists any folder")
@pytest.mark.parametrize("folder", ["pursuits", "notes/2026", "evidence/claude_code/2026"])
def test_a_folder_atlas_cannot_list_is_logged_by_error_type(tmp_path, throwaway_home, monkeypatch, caplog, folder):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    unlisted = root / folder
    held = {path: data for path, data in files(root).items() if path.startswith(folder)}
    unlisted.chmod(0)
    try:
        with caplog.at_level(logging.WARNING, logger="atlas.record"):
            Record.open(root)
    finally:
        unlisted.chmod(0o700)

    assert {path: data for path, data in files(root).items() if path.startswith(folder)} == held
    assert "could not list 1 record folders (PermissionError)" in caplog.text
    assert str(tmp_path) not in caplog.text and folder not in caplog.text


def copies(root: Path, files: str, path) -> None:
    """300 copies of the one file `files` matches, in the record and its last-written copies."""
    for top in (root, root / ".atlas/last-written"):
        held = next(top.glob(files)).read_bytes()
        for n in range(300):
            (top / path(n)).parent.mkdir(parents=True, exist_ok=True)
            (top / path(n)).write_bytes(held)


@pytest.mark.parametrize("files, path", [
    ("pursuits/*.md", lambda n: f"pursuits/invented-{n:03d}.md"),
    ("briefs/*/*/*.md", lambda n: f"briefs/{2000 + n // 12}/{1 + n % 12:02d}/{2000 + n // 12}-{1 + n % 12:02d}-01.md"),
], ids=["pursuits", "briefs-a-folder-each"])
def test_each_open_holds_the_write_lock_about_a_second_with_every_sync_counted(tmp_path, throwaway_home,
                                                                              monkeypatch, files, path):
    root = written_bare(tmp_path, throwaway_home, monkeypatch, sessions=3)
    copies(root, files, path)
    clock = [0.0]
    real_fsync, real_lock = os.fsync, _txn.write_lock
    holds = []

    def ext4_fsync(fd):
        clock[0] += 0.005
        real_fsync(fd)

    @contextmanager
    def timed(root):
        with real_lock(root):
            start = clock[0]
            try:
                yield
            finally:
                holds.append(clock[0] - start)

    monkeypatch.setattr(os, "fsync", ext4_fsync)
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr(_txn, "write_lock", timed)

    opened_until_masked(root)

    assert holding(root, SECRET) == set()
    assert max(holds) <= 1.25, holds


def staged(root: Path, target: str, opens=30) -> tuple[Record, dict]:
    """Opens the record a step at a time until the pass has staged `target`, and returns that open
    and the pass's state."""
    for _ in range(opens):
        rec = Record.open(root)
        state = json.loads((root / ".atlas/masking/state").read_bytes())
        if any(entry["target"] == target for entry in state["batch"]):
            assert not (root / NOTE).exists()
            return rec, state
    pytest.fail(f"the pass never staged {target}")


def staged_holding(root: Path, text: str) -> list[Path]:
    return [p for p in (root / ".atlas/masking").rglob("*") if p.is_file() and text.encode() in p.read_bytes()]


def test_a_commit_drops_the_staged_copies_of_the_files_it_writes_and_the_pass_goes_on(tmp_path, throwaway_home,
                                                                                     monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch, sessions=2)
    monkeypatch.setattr(_masking, "SECONDS", 0)
    rec, state = staged(root, "pursuits/shelf-brackets.md")
    snap = rec.snapshot()

    rec.commit(snap.changes().put_profile(Profile(("invented area",), ())))

    after = json.loads((root / ".atlas/masking/state").read_bytes())
    assert after["after"] == state["after"]
    assert {e["target"] for e in after["batch"]} == {e["target"] for e in state["batch"]} - {
        "profile.md", ".atlas/last-written/profile.md"}
    committed = (root / "profile.md").read_bytes()
    opened_until_masked(root)
    assert (root / "profile.md").read_bytes() == committed
    assert holding(root, SECRET) == set()


def test_a_session_removed_meanwhile_keeps_what_the_pass_staged(tmp_path, throwaway_home, monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch, sessions=3)
    monkeypatch.setattr(_masking, "SECONDS", 0)
    rec, _ = staged(root, "pursuits/shelf-brackets.md")
    state = (root / ".atlas/masking/state").read_bytes()
    snap = rec.snapshot()

    rec.commit(snap.changes().remove_evidence(snap.sessions()[-1].id))

    assert (root / ".atlas/masking/state").read_bytes() == state
    opened_until_masked(root)
    assert holding(root, SECRET) == set()


FACT = "Invented fact from a forgotten Session"


def a_fact_staged(tmp_path, throwaway_home, monkeypatch, **made) -> tuple[Path, Record, str]:
    """An older Atlas's record whose Profile holds a fact from a Session, its masked copy staged
    by the pass under way. Returns the record's root, the open that staged it and the Session."""
    claude(throwaway_home, "shelves", f"plan the shelves with {SECRET}")
    rec = record(tmp_path, CC, **made)
    with bare(monkeypatch):
        Atlas(rec, None).tick(NOW.replace(hour=6))
        evidence = rec.snapshot().sessions()[0].id
        rec.commit(rec.snapshot().changes().put_profile(Profile(("home",), (
            ProfileEntry(Inference(f"{FACT} {SECRET}", (evidence,)), NOW.date(), Who.LIBRARIAN),))))
    root = older(rec)
    monkeypatch.setattr(_masking, "SECONDS", 0)
    rec, _ = staged(root, "profile.md")
    assert staged_holding(root, FACT)
    return root, rec, evidence


def forgets(rec: Record, evidence) -> record_module.ChangeSet:
    return rec.snapshot().changes().remove_evidence(evidence).put_profile(Profile(("home",), ()))


@pytest.mark.skipif(os.geteuid() == 0, reason="root deletes any file")
def test_a_forget_whose_staged_copies_cannot_be_deleted_fails_and_changes_nothing(tmp_path, throwaway_home,
                                                                                  monkeypatch):
    root, rec, evidence = a_fact_staged(tmp_path, throwaway_home, monkeypatch)
    before = files(root)

    (root / ".atlas/masking").chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            rec.commit(forgets(rec, evidence))
    finally:
        (root / ".atlas/masking").chmod(0o700)

    assert {p: d for p, d in files(root).items() if not p.startswith(".atlas/masking/")} == {
        p: d for p, d in before.items() if not p.startswith(".atlas/masking/")}
    rec = Record.open(root)
    rec.commit(forgets(rec, evidence))
    assert not staged_holding(root, FACT)
    assert holding(root, FACT) == set()


def test_a_forget_cut_short_once_it_is_ready_leaves_no_staged_copy_of_what_it_removes(tmp_path, throwaway_home,
                                                                                      monkeypatch):
    root, rec, evidence = a_fact_staged(tmp_path, throwaway_home, monkeypatch)

    class Crash(BaseException):
        pass

    real_stage = _txn.stage

    def ready_then_crash(root, files, *guards):
        real_stage(root, files, *guards)
        raise Crash

    with monkeypatch.context() as crashing:
        crashing.setattr(_txn, "stage", ready_then_crash)
        with pytest.raises(Crash):
            rec.commit(forgets(rec, evidence))
    assert not staged_holding(root, FACT)

    rec = Record.open(root)
    assert rec.snapshot().profile().entries == () and not rec.snapshot().sessions()
    assert not staged_holding(root, FACT)
    opened_until_masked(root)
    assert holding(root, FACT) == set() and holding(root, SECRET) == set()


@pytest.mark.skipif(os.geteuid() == 0, reason="root deletes any file")
@pytest.mark.parametrize("write", ["librarian", "note"])
def test_a_commit_that_cannot_delete_staged_copies_logs_it_and_commits(tmp_path, throwaway_home, monkeypatch, caplog,
                                                                       write):
    root, rec, _ = a_fact_staged(tmp_path, throwaway_home, monkeypatch)
    (root / ".atlas/masking/state.tmp").write_bytes(b"invented state cut short")
    snap = rec.snapshot()
    cs = (snap.changes().put_profile(Profile(("home", "garden"), ())) if write == "librarian" else
          snap.changes().append_note(Note("nt-2", NOW, NoteOrigin.OWNER, "telegram", "Invented Note")))

    (root / ".atlas/masking").chmod(0o500)
    try:
        with caplog.at_level(logging.WARNING, logger="atlas.record"):
            rec.commit(cs)
    finally:
        (root / ".atlas/masking").chmod(0o700)

    assert "PermissionError" in caplog.text and str(tmp_path) not in caplog.text
    snap = Record.open(root).snapshot()
    assert snap.profile().areas == ("home", "garden") if write == "librarian" else snap.notes()[-1].id == "nt-2"
    opened_until_masked(root)
    assert holding(root, SECRET) == set()


def test_a_commit_that_fails_keeps_the_staged_copies_of_the_files_it_would_write(tmp_path, throwaway_home,
                                                                                   monkeypatch):
    root, rec, evidence = a_fact_staged(tmp_path, throwaway_home, monkeypatch)
    rec.commit(rec.snapshot().changes().put_card(SessionCard(
        evidence, Inference("Invented intent.", (evidence,)), Inference("Invented outcome.", (evidence,)), None, None)))
    claude(throwaway_home, "parent", "invented parent question")
    Atlas(rec, None).tick(NOW.replace(hour=6))
    snap = rec.snapshot()
    parent = next(s for s in snap.sessions() if s.native_key == "parent")
    cs = snap.changes().put_session(parent, {p.name: snap.copy(p.origin) for p in parent.parts},
                                    {evidence: "t3-child:shelves"}).put_profile(Profile(("home", "garden"), ()))
    (card,) = root.glob(f"evidence/*/*/*/{evidence}/card.md")
    stage = _txn.stage

    def edited_once_staged(*args):
        txn = stage(*args)
        card.write_bytes(card.read_bytes() + b"\ninvented hand edit\n")
        return txn
    with monkeypatch.context() as m, pytest.raises(record_module.Conflict):
        m.setattr(_txn, "stage", edited_once_staged)
        rec.commit(cs)

    opened_until_masked(root)
    assert holding(root, SECRET) == set()


@pytest.mark.skipif(os.geteuid() == 0, reason="root deletes any file")
def test_a_tick_sends_the_brief_while_staged_copies_cannot_be_deleted(tmp_path, throwaway_home, monkeypatch):
    root, rec, _ = a_fact_staged(tmp_path, throwaway_home, monkeypatch, brief_at=time(7, 30))
    (root / ".atlas/masking/state.tmp").write_bytes(b"invented state cut short")
    monkeypatch.setattr(app.channel.Telegram, "send", lambda self, text: Delivered("telegram:5550001", ("41",)))

    (root / ".atlas/masking").chmod(0o500)
    try:
        report = Atlas(rec, None).tick(NOW)
    finally:
        (root / ".atlas/masking").chmod(0o700)

    assert report.sent == (f"brief {NOW.date()}",)


def test_a_staged_copy_that_does_not_hold_what_was_written_starts_the_pass_over(tmp_path, throwaway_home,
                                                                                 monkeypatch, caplog):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    monkeypatch.setattr(_masking, "SECONDS", 0)
    _, state = staged(root, "pursuits/shelf-brackets.md")
    entry = next(e for e in state["batch"] if e["target"] == "pursuits/shelf-brackets.md")
    (root / ".atlas/masking" / entry["staged"]).write_bytes(b"")
    monkeypatch.setattr(_masking, "SECONDS", 60)

    with caplog.at_level(logging.WARNING, logger="atlas.record"):
        opened_until_masked(root)

    assert "ValueError" in caplog.text and str(tmp_path) not in caplog.text
    assert [p.name for p in Record.open(root).snapshot().pursuits()] == ["Shelf brackets"]
    assert holding(root, SECRET) == set()


def test_a_cut_short_manifest_of_a_masking_write_raises_on_no_open(tmp_path, throwaway_home, monkeypatch, caplog):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)

    class Crash(BaseException):
        pass

    def cut_short(root, txn):
        raise Crash

    with monkeypatch.context() as crashing:
        crashing.setattr(_txn, "apply", cut_short)
        with pytest.raises(Crash):
            Record.open(root)
    ready, = (root / ".atlas/txn").glob("*.ready")
    ready.write_bytes(b"{")

    with caplog.at_level(logging.WARNING, logger="atlas.record"):
        opened_until_masked(root)

    assert "JSONDecodeError" in caplog.text and str(tmp_path) not in caplog.text and SECRET not in caplog.text
    assert holding(root, SECRET) == set()
    assert list((root / ".atlas/txn").iterdir()) == []


def linked_back(root: Path, folder: str, away: Path) -> Path:
    """`folder` of the record moved to `away` and linked back, as the owner may move one."""
    (root / folder).rename(away)
    (root / folder).symlink_to(away, target_is_directory=True)
    return away


@pytest.mark.parametrize("folder", ["evidence", "evidence/claude_code", "evidence/claude_code/2026",
                                    ".atlas/last-written/evidence/claude_code", "notes/2026", "briefs"])
def test_the_files_under_a_folder_moved_and_linked_back_are_masked(tmp_path, throwaway_home, monkeypatch,
                                                                    folder):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    before = holding(root, SECRET)
    away = linked_back(root, folder, tmp_path / "moved")
    assert any(SECRET.encode() in p.read_bytes() for p in away.rglob("*.md"))

    Record.open(root)

    assert all(SECRET.encode() not in (root / path).read_bytes() for path in before)
    assert (root / folder).is_symlink()
    assert (root / NOTE).exists()


@pytest.mark.parametrize("mirror, dangling", [(False, False), (True, False), (False, True)],
                         ids=["session-folder", "its-mirror", "to-nothing"])
def test_a_link_where_a_session_folder_is_is_never_followed_and_the_pass_ends_once_it_is_gone(
        tmp_path, throwaway_home, monkeypatch, caplog, mirror, dangling):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    folder = str(next(root.glob("evidence/*/*/*/*/")).relative_to(root))
    folder = f".atlas/last-written/{folder}" if mirror else folder
    away = linked_back(root, folder, tmp_path / "moved")
    if dangling:
        away = away.rename(tmp_path / "gone")
    behind = stat(away)
    monkeypatch.setattr(app.channel.Telegram, "send", lambda self, text: Delivered("telegram:5550001", ("41",)))

    with caplog.at_level(logging.WARNING, logger="atlas.record"):
        for _ in range(3):
            Atlas(Record.open(root), None).tick(NOW)

    assert stat(away) == behind
    assert not (root / NOTE).exists()
    assert {path for path in holding(root, SECRET) if "/evidence/" not in f"/{path}"} == set()
    assert "Linked" in caplog.text
    assert str(tmp_path) not in caplog.text and SECRET not in caplog.text

    (root / folder).unlink()
    away.rename(root / folder)
    opened_until_masked(root)

    assert holding(root, SECRET) == set()
    assert (root / NOTE).exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens any file")
def test_a_file_whose_last_written_copy_cannot_be_read_is_masked(tmp_path, throwaway_home, monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    card = str(next(root.glob("evidence/**/card.md")).relative_to(root))
    copy = root / ".atlas/last-written" / card
    copy.chmod(0)
    try:
        Record.open(root)
    finally:
        copy.chmod(0o600)

    assert SECRET.encode() not in (root / card).read_bytes()
    assert (root / card).read_bytes() == copy.read_bytes()


@pytest.mark.parametrize("file", ["card.md", "session.md", ".atlas/last-written/card.md"],
                         ids=["card", "transcript", "card-copy"])
def test_a_link_in_place_of_a_session_file_is_never_followed_and_the_pass_ends_once_it_is_gone(
        tmp_path, throwaway_home, monkeypatch, caplog, file):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    folder = str(next(root.glob("evidence/*/*/*/*/")).relative_to(root))
    mirror, _, name = file.rpartition("/")
    linked = root / (f"{mirror}/" if mirror else "") / folder / name
    away = linked.rename(tmp_path / "moved.md")
    linked.symlink_to(away)
    behind = away.read_bytes()

    with caplog.at_level(logging.WARNING, logger="atlas.record"):
        for _ in range(3):
            Record.open(root)

    assert away.read_bytes() == behind and linked.is_symlink()
    assert not (root / NOTE).exists()
    assert "Linked" in caplog.text and str(tmp_path) not in caplog.text and SECRET not in caplog.text

    linked.unlink()
    away.rename(linked)
    opened_until_masked(root)

    assert holding(root, SECRET) == set()
    assert (root / NOTE).exists()


class Hung(BaseException):
    """An open that waited on a file it should never have opened."""


@contextmanager
def within(seconds: float):
    def hung(signum, frame):
        raise Hung
    kept = signal.signal(signal.SIGALRM, hung)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, kept)


def test_a_session_folder_swapped_for_a_link_once_its_masked_files_are_staged_is_never_read_through(
        tmp_path, throwaway_home, monkeypatch, caplog):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    folder = next(root.glob("evidence/*/*/*/*/"))
    outside = tmp_path / "invented-unrelated"
    outside.mkdir()
    for name in ("card.md", "session.md"):
        os.mkfifo(outside / name)
    real, swapped = _txn.apply, []

    def swap(root, txn):
        targets = [entry["target"] for entry in json.loads((root / f".atlas/txn/{txn}.ready").read_bytes())]
        if not swapped and any(target.startswith("evidence/") for target in targets):
            swapped.append(folder.rename(tmp_path / "displaced"))
            folder.symlink_to(outside, target_is_directory=True)
        real(root, txn)

    monkeypatch.setattr(_masking, "SECONDS", 60)
    with monkeypatch.context() as m, caplog.at_level(logging.WARNING, logger="atlas.record"), within(5):
        m.setattr(_txn, "apply", swap)
        Record.open(root)

    assert swapped and not (root / NOTE).exists()
    assert "Linked" in caplog.text and str(tmp_path) not in caplog.text

    folder.unlink()
    swapped[0].rename(folder)
    opened_until_masked(root)
    assert holding(root, SECRET) == set()
    assert (root / NOTE).exists()


def test_a_link_loop_above_the_session_folders_lists_no_folder_twice_in_an_open(tmp_path, throwaway_home,
                                                                                 monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    outside = linked_back(root, "evidence", tmp_path / "moved")
    year = next((outside / "claude_code").iterdir())
    for name in ("codex", "devin", "hermes", "chatgpt"):
        (outside / name).symlink_to(".", target_is_directory=True)
    for name in ("2000", "2001", "2002", "2003"):
        (outside / "claude_code" / name).symlink_to(".", target_is_directory=True)
    for name in ("00", "01", "02", "03"):
        (year / name).symlink_to(".", target_is_directory=True)
    listed, scandir = [], os.scandir

    def observed(path="."):
        if not isinstance(path, int):
            found = os.stat(path)
            listed.append((found.st_dev, found.st_ino))
        return scandir(path)

    with monkeypatch.context() as m:
        m.setattr(os, "scandir", observed)
        Record.open(root)

    assert len(listed) == len(set(listed))
    assert holding(root, SECRET) == set()
    assert (root / NOTE).exists()


@pytest.mark.parametrize("depth", [1, 2], ids=["harness", "year"])
def test_a_link_in_evidence_named_as_no_harness_is_not_walked_and_hides_no_file(tmp_path, throwaway_home,
                                                                                 monkeypatch, depth):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    folder = next(root.glob("evidence/" + "/".join("*" * depth)))
    (root / "evidence/aaa").symlink_to(folder.relative_to(root / "evidence"), target_is_directory=True)

    Record.open(root)

    assert holding(root, SECRET) == set()
    assert not (root / ".atlas/last-written/evidence/aaa").exists()
    assert (root / NOTE).exists()


@pytest.mark.parametrize("linked", [False, True], ids=["bare", "linked"])
def test_once_the_link_is_gone_the_pass_walks_again_for_what_an_older_atlas_wrote_meanwhile(
        tmp_path, throwaway_home, monkeypatch, linked):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    folder = str(next(root.glob("evidence/*/*/*/*/")).relative_to(root))
    away = linked_back(root, folder, tmp_path / "moved")
    rec = Record.open(root)
    claude(throwaway_home, "invented-later", f"invented later turn {SECRET}")
    with bare(monkeypatch):
        Atlas(rec, None).tick(NOW + timedelta(minutes=5))
    added, = (path.parent for path in root.glob("evidence/*/*/*/*/session.md") if path.parent != root / folder)
    assert SECRET.encode() in (added / "session.md").read_bytes()
    if linked:
        linked_back(root, str(added.relative_to(root)), tmp_path / "moved-later")
    Record.open(root)

    (root / folder).unlink()
    away.rename(root / folder)
    Record.open(root)

    assert (SECRET.encode() in (added / "session.md").read_bytes()) == linked
    assert (root / NOTE).exists() != linked


def test_the_walk_counts_in_the_second_an_open_holds_the_lock_and_goes_on_where_it_stopped(
        tmp_path, throwaway_home, monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    for year in range(1700, 1730):
        for month in range(1, 13):
            (root / f"evidence/claude_code/{year}/{month:02d}").mkdir(parents=True)
    clock = [0.0]
    scandir, real_lock = os.scandir, _txn.write_lock
    holds = []

    def listed(path="."):
        clock[0] += 0.01
        return scandir(path)

    @contextmanager
    def timed(root):
        with real_lock(root):
            start = clock[0]
            try:
                yield
            finally:
                holds.append(clock[0] - start)

    monkeypatch.setattr(os, "scandir", listed)
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr(_txn, "write_lock", timed)

    opens = opened_until_masked(root)

    assert opens > 1 and max(holds) <= 1.25, holds
    assert holding(root, SECRET) == set()
    assert (root / NOTE).exists()


def test_a_record_whose_only_bare_files_a_link_holds_is_not_marked_masked(tmp_path, throwaway_home, monkeypatch,
                                                                         caplog):
    root = written_bare(tmp_path, throwaway_home, monkeypatch)
    for top in (root, root / ".atlas/last-written"):
        for kind, path in record_module._layout.TOP_FILES.items():
            if kind != "settings":
                (top / path).unlink(missing_ok=True)
        for name in ("pursuits", "notes", "briefs"):
            shutil.rmtree(top / name)
    folder = str(next(root.glob("evidence/*/*/*/*/")).relative_to(root))
    away = linked_back(root, folder, tmp_path / "moved")

    with caplog.at_level(logging.WARNING, logger="atlas.record"):
        Record.open(root)

    assert not (root / NOTE).exists()
    assert "Linked" in caplog.text

    (root / folder).unlink()
    away.rename(root / folder)
    opened_until_masked(root)
    assert holding(root, SECRET) == set()
    assert (root / NOTE).exists()


def test_once_the_record_is_walked_an_open_lists_no_folder_and_parses_no_file_while_a_link_stands(
        tmp_path, throwaway_home, monkeypatch):
    root = written_bare(tmp_path, throwaway_home, monkeypatch, sessions=3)
    folder = str(next(root.glob("evidence/*/*/*/*/")).relative_to(root))
    linked_back(root, folder, tmp_path / "moved")
    for _ in range(5):
        Record.open(root)
    rec = Record.open(root)
    rec.commit(rec.snapshot().changes().append_note(Note("nt-2", NOW, NoteOrigin.OWNER, "telegram", "Invented Note")))
    listed, parsed = [], []
    scandir, parse = os.scandir, _codec.parse

    def observed_listing(path="."):
        listed.append(path)
        return scandir(path)

    def observed_parse(kind, *args):
        parsed.append(kind)
        return parse(kind, *args)

    with monkeypatch.context() as m:
        m.setattr(os, "scandir", observed_listing)
        m.setattr(_codec, "parse", observed_parse)
        Record.open(root)

    assert [path for path in listed if not isinstance(path, int)
            and not Path(path).is_relative_to(root / ".atlas")] == []
    assert parsed == ["settings"]
    assert not (root / NOTE).exists()
