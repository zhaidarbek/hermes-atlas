"""Collection through Atlas.backfill and Atlas.tick over small invented stores: what a Session
store does while Atlas reads it, and what the record holds afterwards."""
import codecs
import errno
import io
import json
import os
import random
import re
import resource
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from threading import Event
from time import perf_counter

import pytest
import zstandard

from atlas.channel import Delivered
from atlas import app, rules, sources
from atlas.app import Atlas
from atlas.librarian import Grouping, PursuitDraft
from atlas.model import (Area, Forgotten, HarnessId, HarnessSetting, Inference, Kind, Membership, Note, NoteOrigin,
                         ResumeNote, SendAttempt, SendStatus, SentBrief, SessionCard, skip_key)
from atlas.record import ChangeSet, Record, _codec, _txn
from atlas.sources import _adapters
from test_commit import PROFILE, SETTINGS

CC, CX, DV, HM = HarnessId.CLAUDE_CODE, HarnessId.CODEX, HarnessId.DEVIN, HarnessId.HERMES
NOW = datetime(2026, 11, 8, 10, tzinfo=timezone.utc)
T = NOW.timestamp()
KEY = "sk-proj-" + "Ab3dE5gH7jK9mN1pQ3sT5vX7zB9dF1hJ"
MIRROR = "INSERT INTO messages (session_id, role, content, timestamp) VALUES ('chat', 'assistant', ?, ?)"
BRIEF = SentBrief(NOW.date(), (SendAttempt(NOW, SendStatus.SENT, ("41",)),), None, (), (), None, False,
                  "Atlas · Daily Brief\n\n1. Shelf brackets")


def record(tmp_path, *harnesses, roots=(), exclusions=(), brief_at=time(23, 59)) -> Record:
    rec = Record.open(tmp_path / "atlas")
    rec.create(replace(SETTINGS, timezone="UTC", brief_at=brief_at, exclusions=exclusions, harnesses={
        h: HarnessSetting(True, "store", tuple(map(str, roots)), "owner") for h in harnesses}), PROFILE)
    return rec


def held(rec) -> dict[str, list[tuple[str, str]]]:
    return {s.native_key: [(t.role, t.text) for t in s.turns] for s in rec.snapshot().sessions()}


def texts(rec, native) -> list[str]:
    return [text for _, text in held(rec)[native]]


def claude(home, native, *said, cwd=None, branch=None, mtime=None) -> Path:
    cwd = cwd or str(home / "work")
    path = home / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd) / f"{native}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"type": "user", "sessionId": native, "timestamp": NOW.isoformat(), "cwd": cwd,
                                        "gitBranch": branch, "message": {"role": "user", "content": text}}) + "\n"
                            for text in said))
    if mtime:
        os.utime(path, ns=(mtime, mtime))
    return path


def say(path, text, mtime=None) -> None:
    first = json.loads(path.read_text().splitlines()[0])
    with path.open("a") as fh:
        fh.write(json.dumps({**first, "message": {"role": "user", "content": text}}) + "\n")
    if mtime:
        os.utime(path, ns=(mtime, mtime))


def hermes(root, sessions, messages) -> Path:
    """sessions: (id, source, parent, end_reason, title); messages: (session, role, content)."""
    root.mkdir(parents=True, exist_ok=True)
    db = root / "state.db"
    with closing(sqlite3.connect(db)) as con:
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, parent_session_id TEXT,
                started_at REAL NOT NULL, ended_at REAL, end_reason TEXT, cwd TEXT, title TEXT);
            CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT, tool_name TEXT, tool_calls TEXT, timestamp REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1, _compressed_summary INTEGER NOT NULL DEFAULT 0);""")
        con.executemany("INSERT INTO sessions (id, source, parent_session_id, started_at, end_reason, title) "
                        "VALUES (?, ?, ?, ?, ?, ?)", [(id, source, parent, T + n, end, title)
                                                     for n, (id, source, parent, end, title) in enumerate(sessions)])
        con.executemany("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                        [(sid, role, text, T + n) for n, (sid, role, text) in enumerate(messages)])
        con.commit()
    return db


def devin(root, sessions, messages) -> Path:
    """sessions: (slug, created_at); messages: (slug, content), all typed by the owner."""
    root.mkdir(parents=True, exist_ok=True)
    db = root / "sessions.db"
    with closing(sqlite3.connect(db)) as con:
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT, working_directory TEXT);
            CREATE TABLE message_nodes (row_id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                created_at TEXT, is_user_input INTEGER, extension TEXT);""")
        con.executemany("INSERT INTO sessions (id, created_at) VALUES (?, ?)", sessions)
        con.executemany("INSERT INTO message_nodes (session_id, role, content, created_at, is_user_input) "
                        "VALUES (?, 'user', ?, ?, 1)", [(sid, text, NOW.isoformat()) for sid, text in messages])
        con.commit()
    return db


def t3(home, threads) -> Path:
    """threads: (thread, claude native id, parent thread or None)."""
    db = home / ".t3/userdata/statev2.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as con:
        con.executescript("""
            CREATE TABLE orchestration_v2_projection_threads (thread_id TEXT PRIMARY KEY, created_at TEXT,
                deleted_at TEXT, payload_json TEXT);
            CREATE TABLE orchestration_v2_projection_provider_threads (thread_id TEXT PRIMARY KEY,
                payload_json TEXT);""")
        for thread, native, parent in threads:
            lineage = {"lineage": {"parentThreadId": parent, "relationshipToParent": "subagent"}} if parent else {}
            con.execute("INSERT INTO orchestration_v2_projection_threads VALUES (?, ?, NULL, ?)",
                        (thread, NOW.isoformat(), json.dumps(lineage)))
            con.execute("INSERT INTO orchestration_v2_projection_provider_threads VALUES (?, ?)", (thread, json.dumps(
                {"nativeThreadRef": {"driver": "claudeAgent", "nativeId": native}})))
        con.commit()
    return db


def run(db, statement, *params) -> None:
    with closing(sqlite3.connect(db)) as con:
        con.execute(statement, params)
        con.commit()


def unzstd(path: Path) -> bytes:
    return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(path.read_bytes()), read_across_frames=True).read()


def test_a_turn_written_while_a_tick_reads_another_transcript_is_collected(tmp_path, throwaway_home, monkeypatch):
    shelves = claude(throwaway_home, "shelves", "plan the shelves")
    printer = claude(throwaway_home, "printer", "fix it")
    rec = record(tmp_path, CC)
    Atlas(rec, None).tick(NOW)
    say(shelves, "one more shelf")
    say(printer, "it is offline")
    real, read = _adapters._claude_turn, []

    def the_store_writes_meanwhile(r):
        if not read:
            read.append(shelves if r["sessionId"] == "shelves" else printer)
            other = printer if read[0] is shelves else shelves
            say(read[0], "a late line")
            later = read[0].stat().st_mtime_ns + 1_000_000
            say(other, "another line", mtime=later)
        return real(r)

    monkeypatch.setattr(_adapters, "_claude_turn", the_store_writes_meanwhile)
    Atlas(rec, None).tick(NOW)
    monkeypatch.setattr(_adapters, "_claude_turn", real)
    Atlas(rec, None).tick(NOW)
    assert "a late line" in texts(rec, read[0].stem)


def test_a_session_unreadable_at_one_tick_is_collected_at_the_next(tmp_path, throwaway_home):
    blocked = claude(throwaway_home, "blocked", "dns loop", mtime=1_000)
    claude(throwaway_home, "readable", "shelf sizes", mtime=2_000)
    rec = record(tmp_path, CC)
    blocked.chmod(0)
    try:
        Atlas(rec, None).tick(NOW)
    finally:
        blocked.chmod(0o600)
    Atlas(rec, None).tick(NOW)
    assert set(held(rec)) == {"blocked", "readable"}


def test_a_tick_a_link_holds_up_never_raises_and_the_tick_after_the_link_is_gone_holds_the_raw_copy(
        tmp_path, throwaway_home, monkeypatch, caplog):
    claude(throwaway_home, "linked", "invented collected turn")
    rec = record(tmp_path, CC)
    replace_below, outside, raws = _txn._replace_below, tmp_path / "outside", []

    def swapped(root, staged, target):
        if "/raw/" in target and not raws:
            raws.append(root / target)
            folder = raws[0].parent
            folder.mkdir(parents=True, exist_ok=True)
            folder.rename(outside)
            folder.symlink_to(outside, target_is_directory=True)
        return replace_below(root, staged, target)

    with monkeypatch.context() as m:
        m.setattr(_txn, "_replace_below", swapped)
        Atlas(rec, None).tick(NOW)
    raw = raws[0]
    assert "left one Claude Code Session to the next collect: a link stands" in caplog.text and not raw.exists()
    raw.parent.unlink()
    outside.rename(raw.parent)
    Atlas(Record.open(rec.root), None).tick(NOW)
    assert raw.is_file() and texts(rec, "linked") == ["invented collected turn"]


def evidence_folder(rec, said) -> Path:
    return next(p.parent for p in rec.root.glob("evidence/*/*/*/*/session.md") if said.encode() in p.read_bytes())


@pytest.mark.parametrize("where", ["folder", "raw"])
def test_a_session_whose_folder_is_a_link_is_left_and_the_rest_of_its_batch_collected(tmp_path, throwaway_home,
                                                                                     caplog, where):
    linked = claude(throwaway_home, "linked", "invented first task")
    others = [claude(throwaway_home, f"other-{n}", "invented other task") for n in range(3)]
    rec = record(tmp_path, CC)
    Atlas(rec, None).tick(NOW)
    folder = evidence_folder(rec, "invented first task")
    stood_in, outside = folder / "raw" if where == "raw" else folder, tmp_path / "outside"
    stood_in.rename(outside)
    stood_in.symlink_to(outside, target_is_directory=True)
    kept = {p: p.read_bytes() for p in outside.rglob("*") if p.is_file()}
    for path in (linked, *others):
        say(path, "invented later turn")
    assert Atlas(rec, None).tick(NOW).collected == 3
    assert all(texts(rec, f"other-{n}") == ["invented other task", "invented later turn"] for n in range(3))
    assert {p: p.read_bytes() for p in outside.rglob("*") if p.is_file()} == kept
    assert "left one Claude Code Session to the next collect: a link stands where its folder is" in caplog.text
    assert str(tmp_path) not in caplog.text


def test_a_session_a_waiting_commit_holds_is_left_and_the_rest_of_its_batch_collected(tmp_path, throwaway_home,
                                                                                    monkeypatch, caplog):
    claude(throwaway_home, "linked", "invented first task")
    other = claude(throwaway_home, "other", "invented other task")
    rec = record(tmp_path, CC)
    replace_below, outside, raws = _txn._replace_below, tmp_path / "outside", []

    def swapped(root, staged, target):
        if "/raw/" in target and not raws and b"invented first task" in unzstd(staged):
            raws.append(root / target)
            folder = raws[0].parent
            folder.mkdir(parents=True, exist_ok=True)
            folder.rename(outside)
            folder.symlink_to(outside, target_is_directory=True)
        return replace_below(root, staged, target)

    with monkeypatch.context() as m:
        m.setattr(_txn, "_replace_below", swapped)
        assert Atlas(rec, None).tick(NOW).collected == 1
    say(other, "invented later turn")
    assert Atlas(Record.open(rec.root), None).tick(NOW).collected == 1
    assert texts(rec, "other") == ["invented other task", "invented later turn"]
    assert list(outside.iterdir()) == [] and len(list(rec.root.glob(".atlas/txn/*.ready"))) == 1
    assert "left one Claude Code Session to the next collect: a link stands where its folder is" in caplog.text
    raws[0].parent.unlink()
    outside.rename(raws[0].parent)
    Atlas(Record.open(rec.root), None).tick(NOW)
    assert raws[0].is_file() and texts(rec, "linked") == ["invented first task"]


def linked_late_folds(tmp_path, home, count) -> tuple[Record, list[Path]]:
    """`count` T3 children, each carded and then linked to its parent after the first tick, and a
    healthy Session with a later turn. Returns the record and each child's evidence folder."""
    threads = []
    for n in range(count):
        claude(home, f"parent-{n}", f"invented parent {n}")
        claude(home, f"child-{n}", f"invented child {n}")
        threads += [(f"parent-thread-{n}", f"parent-{n}", None), (f"child-thread-{n}", f"child-{n}", None)]
    healthy = claude(home, "healthy", "invented healthy first")
    db = t3(home, threads)
    rec = record(tmp_path, CC)
    Atlas(rec, None).tick(NOW)
    for s in rec.snapshot().sessions():
        if s.native_key.startswith("child-"):
            rec.commit(rec.snapshot().changes().put_card(SessionCard(
                s.id, Inference("Invented intent.", (s.id,)), Inference("Invented outcome.", (s.id,)), None, None)))
    children = [evidence_folder(rec, f"invented child {n}") for n in range(count)]
    for n in range(count):
        link(db, f"child-thread-{n}", f"parent-thread-{n}")
    say(healthy, "invented healthy later")
    return rec, children


def test_many_folds_a_link_holds_up_leave_the_rest_of_the_batch_collected_in_the_same_tick(tmp_path,
                                                                                          throwaway_home):
    rec, children = linked_late_folds(tmp_path, throwaway_home, 6)
    for n, folder in enumerate(children):
        stand_in = rec.root / ".atlas/last-written" / folder.relative_to(rec.root)
        stand_in.rename(tmp_path / f"outside-{n}")
        stand_in.symlink_to(tmp_path / f"outside-{n}", target_is_directory=True)
    assert Atlas(rec, None).tick(NOW).collected == 1
    assert texts(rec, "healthy") == ["invented healthy first", "invented healthy later"]


def test_a_fold_whose_child_folder_is_a_link_waits_and_holds_one_copy_once_it_is_back(tmp_path, throwaway_home,
                                                                                    caplog):
    rec, (child,) = linked_late_folds(tmp_path, throwaway_home, 1)
    outside = tmp_path / "outside"
    child.rename(outside)
    child.symlink_to(outside, target_is_directory=True)
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "healthy") == ["invented healthy first", "invented healthy later"]
    assert texts(rec, "parent-0") == ["invented parent 0"]
    child.unlink()
    outside.rename(child)
    Atlas(Record.open(rec.root), None).tick(NOW)
    assert set(held(rec)) == {"parent-0", "healthy"} and not child.exists()
    assert every_raw(rec).count(b"invented child 0") == 1
    assert "left one Claude Code Session to the next collect: a link stands" in caplog.text
    assert str(tmp_path) not in caplog.text


def test_a_tick_stopped_after_its_first_commit_collects_the_rest_at_the_next(tmp_path, throwaway_home, monkeypatch):
    claude(throwaway_home, "early", "first task", mtime=2_000)
    claude(throwaway_home, "late", "second task", mtime=1_000)
    rec = record(tmp_path, CC)
    monkeypatch.setattr(app, "BATCH", 1)
    commit, calls = rec.commit, []

    def stopped_at_the_second(cs):
        calls.append(cs)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return commit(cs)

    monkeypatch.setattr(rec, "commit", stopped_at_the_second)
    with pytest.raises(KeyboardInterrupt):
        Atlas(rec, None).tick(NOW)
    monkeypatch.setattr(rec, "commit", commit)
    Atlas(rec, None).tick(NOW)
    assert set(held(rec)) == {"early", "late"}


def test_a_hermes_reply_written_while_a_tick_reads_another_chat_is_collected(tmp_path, throwaway_home, monkeypatch):
    db = hermes(throwaway_home / ".hermes", [("bike", "telegram", None, None, "bike gears"),
                                             ("tax", "cli", None, None, "tax receipts")],
                [("bike", "user", "bike gears slip"), ("tax", "user", "where are the receipts")])
    rec = record(tmp_path, HM)
    real, read = _adapters._hermes_turns, []

    def hermes_writes_meanwhile(rows, part):
        if not read:
            read.append(rows[0]["session_id"])
            run(db, "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                read[0], "a late reply", T + 10)
            run(db, "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)",
                "tax" if read[0] == "bike" else "bike", "and one more thing", T + 11)
        return real(rows, part)

    monkeypatch.setattr(_adapters, "_hermes_turns", hermes_writes_meanwhile)
    Atlas(rec, None).tick(NOW)
    monkeypatch.setattr(_adapters, "_hermes_turns", real)
    Atlas(rec, None).tick(NOW)
    assert "a late reply" in texts(rec, read[0])


def test_a_second_hermes_store_is_collected_whatever_the_first_stores_row_ids(tmp_path, throwaway_home):
    hermes(throwaway_home / ".hermes", [("seeds", "cli", None, None, "seed starter")],
           [("seeds", "user" if n % 2 == 0 else "assistant", f"line {n}") for n in range(40)])
    second = hermes(throwaway_home / "work-profile", [("invoices", "cli", None, None, "invoice script")],
                    [("invoices", "user", "the invoice script fails")])
    rec = record(tmp_path, HM, roots=[throwaway_home / "work-profile"])
    Atlas(rec, None).tick(NOW)
    run(second, "INSERT INTO messages (session_id, role, content, timestamp) VALUES ('invoices', 'user', ?, ?)",
        "it fails on the totals", T + 60)
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "invoices") == ["the invoice script fails", "it fails on the totals"]


def test_a_second_devin_store_is_collected_whatever_the_first_stores_row_ids(tmp_path, throwaway_home):
    devin(throwaway_home / ".local/share/devin/cli", [("site-images-a1b2c3", "2026-11-01T10:00:00Z")],
          [("site-images-a1b2c3", f"step {n}") for n in range(40)])
    second = devin(throwaway_home / "devin-two", [("dns-notes-d4e5f6", "2026-11-02T10:00:00Z")],
                   [("dns-notes-d4e5f6", "dns forwarding notes")])
    rec = record(tmp_path, DV, roots=[throwaway_home / "devin-two"])
    Atlas(rec, None).tick(NOW)
    run(second, "INSERT INTO message_nodes (session_id, role, content, created_at, is_user_input) "
                "VALUES ('dns-notes-d4e5f6', 'user', 'and the upstream servers', ?, 1)", NOW.isoformat())
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "dns-notes-d4e5f6@2026-11-02T10:00:00Z") == ["dns forwarding notes", "and the upstream servers"]


def test_a_transcript_written_within_one_clock_tick_is_collected(tmp_path, throwaway_home):
    shelves = claude(throwaway_home, "shelves", "plan the shelves", mtime=5_000)
    claude(throwaway_home, "printer", "fix the printer", mtime=5_000)
    rec = record(tmp_path, CC)
    Atlas(rec, None).tick(NOW)
    say(shelves, "one more shelf", mtime=5_000)
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "shelves") == ["plan the shelves", "one more shelf"]


def test_a_hermes_message_rewritten_in_place_is_collected_beside_what_it_said(tmp_path, throwaway_home):
    db = hermes(throwaway_home / ".hermes", [("notes", "cli", None, None, "notes")],
                [("notes", "user", "summarise @notes.md")])
    rec = record(tmp_path, HM)
    Atlas(rec, None).tick(NOW)
    run(db, "UPDATE messages SET content = 'summarise the notes: buy brackets, measure the wall' WHERE id = 1")
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "notes") == ["summarise @notes.md", "summarise the notes: buy brackets, measure the wall"]


def test_a_session_folded_through_t3_code_stays_folded_after_t3_code_prunes_it(tmp_path, throwaway_home):
    claude(throwaway_home, "parent", "check the export", mtime=1_000)
    child = claude(throwaway_home, "child", "delegated check", mtime=2_000)
    db = t3(throwaway_home, [("parent-thread", "parent", None), ("child-thread", "child", "parent-thread")])
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    run(db, "UPDATE orchestration_v2_projection_threads SET payload_json = '{}'")
    Atlas(rec, None).tick(NOW)
    say(child, "and the totals")
    Atlas(rec, None).tick(NOW)
    assert list(held(rec)) == ["parent"]
    assert ("agent", "and the totals") in held(rec)["parent"]


def test_lineage_t3_code_records_late_folds_the_session_at_the_next_tick(tmp_path, throwaway_home):
    claude(throwaway_home, "parent", "check the export")
    claude(throwaway_home, "child", "delegated check")
    db = t3(throwaway_home, [("parent-thread", "parent", None), ("child-thread", "child", None)])
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    run(db, "UPDATE orchestration_v2_projection_threads SET payload_json = ? WHERE thread_id = 'child-thread'",
        json.dumps({"lineage": {"parentThreadId": "parent-thread", "relationshipToParent": "subagent"}}))
    Atlas(rec, None).tick(NOW)
    assert list(held(rec)) == ["parent"]


def test_the_t3_code_note_stays_after_t3_code_prunes_its_data(tmp_path, throwaway_home):
    chat = claude(throwaway_home, "chat", "check the export")
    db = t3(throwaway_home, [("thread", "chat", None)])
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    run(db, "DELETE FROM orchestration_v2_projection_provider_threads")
    say(chat, "and the totals")
    Atlas(rec, None).tick(NOW)
    assert [s.interface for s in rec.snapshot().sessions()] == ["T3 Code"]


def test_a_transcript_removed_while_backfill_runs_is_skipped_and_the_rest_committed(tmp_path, throwaway_home,
                                                                                     monkeypatch, caplog):
    gone = claude(throwaway_home, "gone", "an old task")
    claude(throwaway_home, "kept", "a new task")
    rec = record(tmp_path, CC)
    real = sources.collect

    def cleaned_up_meanwhile(*args, **kwargs):
        found = real(*args, **kwargs)
        gone.unlink()
        return found

    monkeypatch.setattr(sources, "collect", cleaned_up_meanwhile)
    report = Atlas(rec, None).backfill(NOW)
    assert list(held(rec)) == ["kept"] and report.sessions == 1
    assert "could not read" in caplog.text


def test_session_md_and_the_raw_copy_come_from_one_read(tmp_path, throwaway_home, monkeypatch):
    chat = claude(throwaway_home, "chat", "first question")
    rec = record(tmp_path, CC)
    real = sources.collect

    def grows_meanwhile(*args, **kwargs):
        found = real(*args, **kwargs)
        say(chat, "second question")
        return found

    monkeypatch.setattr(sources, "collect", grows_meanwhile)
    Atlas(rec, None).backfill(NOW)
    raw = unzstd(next(rec.root.glob("evidence/claude_code/*/*/*/raw/main.jsonl.zst")))
    assert texts(rec, "chat") == [json.loads(line)["message"]["content"] for line in raw.splitlines()]


def test_a_transcript_linked_to_a_credential_file_is_never_opened(tmp_path, throwaway_home, monkeypatch):
    secrets = {throwaway_home / ".claude/.credentials.json": throwaway_home / ".claude/projects/-work/link.jsonl",
               throwaway_home / ".codex/auth.json": throwaway_home / ".codex/sessions/2026/11/08/"
                                                                       "rollout-2026-11-08T10-00-00-link.jsonl"}
    for secret, link in secrets.items():
        link.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text(json.dumps({"type": "user", "sessionId": "link", "timestamp": NOW.isoformat(),
                                      "payload": {"id": "link", "timestamp": NOW.isoformat()},
                                      "message": {"role": "user", "content": "an invented credential"}}) + "\n")
        link.symlink_to(secret)
    claude(throwaway_home, "real", "a real task")
    rec = record(tmp_path, CC, CX)
    opened, real_io, real_os = [], io.open, os.open

    def watched(real):
        def open_(file, *args, **kwargs):
            if not isinstance(file, int) and Path(file).resolve() in secrets:
                opened.append(Path(file).name)
            return real(file, *args, **kwargs)
        return open_

    with monkeypatch.context() as patched:
        patched.setattr(io, "open", watched(real_io))
        patched.setattr(os, "open", watched(real_os))
        Atlas(rec, None).backfill(NOW)
    assert opened == [] and list(held(rec)) == ["real"]


def test_a_forget_committed_while_backfill_reads_is_not_undone(tmp_path, throwaway_home, monkeypatch):
    claude(throwaway_home, "pasted", "I pasted a password here")
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    evidence = rec.snapshot().sessions()[0].id
    real = sources.collect

    def forgotten_meanwhile(*args, **kwargs):
        found = real(*args, **kwargs)
        rec.commit(rec.snapshot().changes().remove_evidence(evidence).put_forgotten(
            Forgotten(((NOW, "a Session"),), frozenset({skip_key(CC, "pasted")}))))
        return found

    monkeypatch.setattr(sources, "collect", forgotten_meanwhile)
    Atlas(rec, None).backfill(NOW)
    assert held(rec) == {}


def test_a_brief_sent_while_backfill_reads_never_reaches_the_evidence(tmp_path, throwaway_home, monkeypatch):
    db = hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelf brackets")],
                [("chat", "user", "shelf bracket sizes")])
    rec = record(tmp_path, HM)
    real = sources.collect

    def a_tick_sends_meanwhile(*args, **kwargs):
        rec.commit(rec.snapshot().changes().put_brief(BRIEF))
        run(db, "INSERT INTO messages (session_id, role, content, timestamp) VALUES ('chat', 'assistant', ?, ?)",
            BRIEF.text, T + 60)
        return real(*args, **kwargs)

    monkeypatch.setattr(sources, "collect", a_tick_sends_meanwhile)
    Atlas(rec, None).backfill(NOW)
    assert held(rec) == {"chat": [("owner", "shelf bracket sizes")]}


def test_a_tick_that_sends_during_a_backfill_leaks_no_mirror_row(tmp_path, throwaway_home, monkeypatch):
    db = hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelf brackets")],
                [("chat", "user", "shelf bracket sizes")])
    rec = record(tmp_path, HM, brief_at=time(7, 30))
    reading, go_on, sent = Event(), Event(), []
    real = sources.collect

    def paused(*args, **kwargs):
        reading.set()
        assert go_on.wait(10)
        return real(*args, **kwargs)

    def mirrored(channel, text):
        sent.append(text)
        run(db, "INSERT INTO messages (session_id, role, content, timestamp) VALUES ('chat', 'assistant', ?, ?)",
            text, T + 60)
        return Delivered("telegram:5550001", ("41",))

    monkeypatch.setattr(sources, "collect", paused)
    monkeypatch.setattr(app.channel.Telegram, "send", mirrored)
    with ThreadPoolExecutor(max_workers=1) as pool:
        backfill = pool.submit(Atlas(rec, None).backfill, NOW)
        try:
            assert reading.wait(10)
            assert Atlas(rec, None).tick(NOW).skipped_busy
        finally:
            go_on.set()
        backfill.result(timeout=10)
    assert sent and sent[0].strip() not in texts(rec, "chat")


def test_the_next_tick_removes_a_mirror_row_read_before_its_text_was_known(tmp_path, throwaway_home):
    hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelf brackets")],
           [("chat", "user", "shelf bracket sizes"), ("chat", "assistant", BRIEF.text)])
    rec = record(tmp_path, HM)
    Atlas(rec, None).tick(NOW)
    rec.commit(rec.snapshot().changes().put_brief(BRIEF))
    Atlas(rec, None).tick(NOW)
    assert held(rec) == {"chat": [("owner", "shelf bracket sizes")]}


@pytest.mark.parametrize("status", [SendStatus.SENDING, SendStatus.ARRIVAL_UNKNOWN])
def test_a_brief_sent_bare_before_masking_and_resent_masked_leaves_both_mirror_rows_out(tmp_path, throwaway_home,
                                                                                       monkeypatch, status):
    db = hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelf brackets")],
                [("chat", "user", "shelf bracket sizes")])
    rec = record(tmp_path, HM, brief_at=time(7, 30))
    text = f"{BRIEF.text}\nInvented {KEY}"
    with monkeypatch.context() as before_masking:
        before_masking.setattr(_codec, "_scrubbed", lambda value, hint: value)
        rec.commit(rec.snapshot().changes().put_brief(
            replace(BRIEF, attempts=(SendAttempt(NOW, status, ()),), text=text)))
    run(db, MIRROR, text, T + 60)
    sent = []

    def mirrored(channel, t):
        sent.append(t)
        run(db, MIRROR, t, T + 120)
        return Delivered("telegram:5550001", ("42",))

    monkeypatch.setattr(app.channel.Telegram, "send", mirrored)
    Atlas(rec, None).tick(NOW)
    Atlas(rec, None).tick(NOW + timedelta(minutes=10))
    assert sent == [f"{BRIEF.text}\nInvented [secret: api key]"]
    assert held(rec) == {"chat": [("owner", "shelf bracket sizes")]}


def test_an_agent_row_that_differs_from_a_sent_brief_only_by_its_secret_is_collected(tmp_path, throwaway_home,
                                                                                     monkeypatch):
    db = hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelf brackets")],
                [("chat", "user", "shelf bracket sizes")])
    rec = record(tmp_path, HM, brief_at=time(7, 30))
    composed = rules.messages.brief
    monkeypatch.setattr(rules.messages, "brief", lambda *args: f"{composed(*args)}\nInvented {KEY}")
    sent = []

    def mirrored(channel, text):
        sent.append(text)
        run(db, MIRROR, text, T + 60)
        run(db, MIRROR, text.replace("[secret: api key]", "sk-proj-" + "Zy9xW7vU5tS3rQ1pO9nM7lK5jI3hG1fE"), T + 120)
        return Delivered("telegram:5550001", ("42",))

    monkeypatch.setattr(app.channel.Telegram, "send", mirrored)
    Atlas(rec, None).tick(NOW)
    Atlas(rec, None).tick(NOW + timedelta(minutes=10))
    assert len(sent) == 1 and sent[0].endswith("\nInvented [secret: api key]")
    assert held(rec) == {"chat": [("owner", "shelf bracket sizes"), ("agent", sent[0])]}
    assert b"Zy9xW7vU5tS3rQ1pO9nM7lK5jI3hG1fE" in every_raw(rec) and b"[secret: api key]" not in every_raw(rec)


def test_a_brief_whose_file_keeps_no_hash_still_leaves_its_bare_mirror_row_out(tmp_path, throwaway_home):
    text = f"{BRIEF.text}\nInvented {KEY}"
    hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelf brackets")],
           [("chat", "user", "shelf bracket sizes"), ("chat", "assistant", text)])
    rec = record(tmp_path, HM)
    rec.commit(rec.snapshot().changes().put_brief(replace(BRIEF, text=text)))
    assert rec.snapshot().brief(BRIEF.day).attempts[0].text_sha256 is None
    Atlas(rec, None).tick(NOW)
    assert held(rec) == {"chat": [("owner", "shelf bracket sizes")]}


def test_a_reply_sent_bare_before_masking_stays_out_once_its_notes_file_is_written_masked(tmp_path, throwaway_home,
                                                                                         monkeypatch):
    reply = f"Invented reply {KEY}"
    hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelf brackets")],
           [("chat", "user", "shelf bracket sizes"), ("chat", "assistant", reply)])
    rec = record(tmp_path, HM)
    with monkeypatch.context() as before_masking:
        before_masking.setattr(_codec, "_scrubbed", lambda value, hint: value)
        rec.commit(rec.snapshot().changes().append_note(
            Note("nt-1", NOW, NoteOrigin.OWNER, "telegram", "Invented ask.", atlas_reply=reply)))
    rec.commit(rec.snapshot().changes().append_note(
        Note("nt-2", NOW + timedelta(minutes=1), NoteOrigin.OWNER, "telegram", "Invented next ask.")))
    Atlas(rec, None).tick(NOW + timedelta(minutes=2))
    assert held(rec) == {"chat": [("owner", "shelf bracket sizes")]}


def test_hermes_new_or_an_automatic_reset_starts_the_owners_next_session(tmp_path, throwaway_home):
    hermes(throwaway_home / ".hermes", [("monday", "telegram", None, "session_reset", "bike gears"),
                                        ("tuesday", "telegram", "monday", None, "tax receipts")],
           [("monday", "user", "bike gears slip under load"), ("monday", "assistant", "Check the cable tension."),
            ("tuesday", "user", "where did I put the tax receipts"), ("tuesday", "assistant", "In the blue folder.")])
    rec = record(tmp_path, HM)
    Atlas(rec, None).backfill(NOW)
    assert held(rec) == {"monday": [("owner", "bike gears slip under load"), ("agent", "Check the cable tension.")],
                         "tuesday": [("owner", "where did I put the tax receipts"), ("agent", "In the blue folder.")]}


def test_a_hermes_branch_is_the_owners_own_session(tmp_path, throwaway_home):
    hermes(throwaway_home / ".hermes", [("garage", "cli", None, None, "garage shelves"),
                                        ("garage-2", "cli", "garage", None, "garage shelves #2")],
           [("garage", "user", "plan garage shelves"), ("garage-2", "user", "plan garage shelves"),
            ("garage-2", "user", "what if I use steel brackets")])
    rec = record(tmp_path, HM)
    Atlas(rec, None).backfill(NOW)
    assert held(rec)["garage-2"] == [("owner", "plan garage shelves"), ("owner", "what if I use steel brackets")]


def test_a_hermes_delegated_subagent_folds_into_the_session_that_started_it(tmp_path, throwaway_home):
    hermes(throwaway_home / ".hermes", [("backups", "cli", None, None, "backup logs"),
                                        ("reader", "subagent", "backups", None, "read the log")],
           [("backups", "user", "summarise the backup logs"), ("reader", "user", "read last night's log")])
    rec = record(tmp_path, HM)
    report = Atlas(rec, None).backfill(NOW)
    assert held(rec) == {"backups": [("owner", "summarise the backup logs"), ("agent", "read last night's log")]}
    assert report.folded == 1


def test_two_devin_sessions_that_share_a_slug_are_two_sessions(tmp_path, throwaway_home):
    devin(throwaway_home / ".local/share/devin/cli", [("dns-notes-a1b2c3", "2026-07-15T14:00:00Z")],
          [("dns-notes-a1b2c3", "dns forwarding notes")])
    devin(throwaway_home / "devin-two", [("dns-notes-a1b2c3", "2026-11-02T10:00:00Z")],
          [("dns-notes-a1b2c3", "dns notes, again")])
    rec = record(tmp_path, DV, roots=[throwaway_home / "devin-two"])
    Atlas(rec, None).backfill(NOW)
    assert held(rec) == {"dns-notes-a1b2c3@2026-07-15T14:00:00Z": [("owner", "dns forwarding notes")],
                         "dns-notes-a1b2c3@2026-11-02T10:00:00Z": [("owner", "dns notes, again")]}


def test_session_md_masks_a_secret_in_its_folder_and_branch(tmp_path, throwaway_home):
    claude(throwaway_home, "chat", f"use {KEY}", cwd=str(throwaway_home / KEY / "work"), branch=KEY)
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    folder = next(rec.root.glob("evidence/claude_code/*/*/*/"))
    assert KEY not in (folder / "session.md").read_text() and "[secret: api key]" in (folder / "session.md").read_text()
    assert KEY in unzstd(folder / "raw/main.jsonl.zst").decode()


def test_an_excluded_folder_is_excluded_through_a_symlink_either_way(tmp_path, throwaway_home):
    physical = throwaway_home / "data/clients"
    (physical / "acme").mkdir(parents=True)
    (throwaway_home / "clients").symlink_to(physical)
    (throwaway_home / "alias").symlink_to(physical)
    claude(throwaway_home, "physical", "invoice for acme", cwd=str(physical / "acme"))
    claude(throwaway_home, "aliased", "quote for acme", cwd=str(throwaway_home / "alias/acme"))
    claude(throwaway_home, "dotted", "acme again", cwd=str(throwaway_home / "work/../clients/acme"))
    claude(throwaway_home, "kept", "shelf sizes")
    rec = record(tmp_path, CC, exclusions=("~/clients",))
    Atlas(rec, None).backfill(NOW)
    assert list(held(rec)) == ["kept"]


def test_an_unreadable_store_folder_is_logged_not_read_as_empty(tmp_path, throwaway_home, caplog):
    claude(throwaway_home, "chat", "plan the shelves")
    projects = throwaway_home / ".claude/projects"
    rec = record(tmp_path, CC)
    projects.chmod(0)
    try:
        Atlas(rec, None).tick(NOW)
    finally:
        projects.chmod(0o700)
    assert "Claude Code" in caplog.text and "could not be read" in caplog.text


def link(db, child_thread, parent_thread) -> None:
    run(db, "UPDATE orchestration_v2_projection_threads SET payload_json = ? WHERE thread_id = ?",
        json.dumps({"lineage": {"parentThreadId": parent_thread, "relationshipToParent": "subagent"}}), child_thread)


@pytest.mark.parametrize("pursuit_file", ["readable", "unreadable"])
def test_a_session_a_pursuit_file_lists_or_might_list_is_not_folded_away(tmp_path, throwaway_home, caplog,
                                                                          pursuit_file):
    claude(throwaway_home, "parent", "invented parent question")
    claude(throwaway_home, "child", "invented delegated check")
    db = t3(throwaway_home, [("parent-thread", "parent", None), ("child-thread", "child", None)])
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    snap = rec.snapshot()
    child = next(s for s in snap.sessions() if s.native_key == "child")
    draft = PursuitDraft("Invented pursuit", Kind.PROJECT, Area("work"), (Membership(child.id, child.last_at, True),),
                         ResumeNote((), (), (), (), (child.id,)), None)
    rec.commit(rules.start_state(snap, Grouping((child.id,), (draft,), {}), NOW).changes)
    (path,) = (rec.root / "pursuits").glob("*.md")
    if pursuit_file == "unreadable":
        (rec.root / ".atlas/last-written" / path.relative_to(rec.root)).unlink()
        path.write_text(path.read_text().replace("## Sessions\n", "## Sessions\n\ninvented line\n", 1))
    written = path.read_bytes()
    link(db, "child-thread", "parent-thread")
    Atlas(rec, None).tick(NOW)
    assert set(held(rec)) == {"parent", "child"} and path.read_bytes() == written
    assert "Atlas kept a folded Session as its own" in caplog.text and "invented" not in caplog.text


def test_a_tick_leaves_a_session_whose_card_was_edited_by_hand_unfolded(tmp_path, throwaway_home, caplog):
    claude(throwaway_home, "parent", "invented parent question")
    claude(throwaway_home, "child", "invented delegated check")
    db = t3(throwaway_home, [("parent-thread", "parent", None), ("child-thread", "child", None)])
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    snap = rec.snapshot()
    child = next(s for s in snap.sessions() if s.native_key == "child")
    rec.commit(snap.changes().put_card(SessionCard(child.id, Inference("Invented intent.", (child.id,)),
                                                   Inference("Invented outcome.", (child.id,)), None, None)))
    (card,) = rec.root.glob(f"evidence/*/*/*/{child.id}/card.md")
    card.write_bytes(card.read_bytes() + b"\ninvented hand edit\n")
    edited = card.read_bytes()
    link(db, "child-thread", "parent-thread")
    Atlas(rec, None).tick(NOW)
    assert set(held(rec)) == {"parent", "child"} and card.read_bytes() == edited
    assert "Atlas kept a folded Session as its own" in caplog.text and "invented" not in caplog.text
    caplog.clear()
    Atlas(rec, None).tick(NOW)
    assert set(held(rec)) == {"parent", "child"} and card.read_bytes() == edited
    assert "Atlas kept a folded Session as its own" not in caplog.text


def every_raw(rec) -> bytes:
    return b"".join(unzstd(p) for p in sorted(rec.root.glob("evidence/**/raw/*.zst")))


def forget(rec, harness, native) -> None:
    """What Forget commits (#18): the Session's folder goes and its skip key is kept."""
    snap = rec.snapshot()
    cs = snap.changes()
    for s in snap.sessions():
        if s.harness is harness and s.native_key == native:
            cs.remove_evidence(s.id)
    rec.commit(cs.put_forgotten(Forgotten(((NOW, "a Session"),), frozenset({skip_key(harness, native)}))))


def rollout(root, native, owner, agent=(), zst=False) -> Path:
    """A Codex rollout as Codex 0.16x writes it: each message's text in content blocks."""
    def item(kind, text):
        block = {"type": "text", "text": text, "text_elements": []} if kind == "UserMessage" else {
            "type": "Text", "text": text}
        return {"type": kind, "id": f"{kind}-{text[:8]}", "content": [block]}
    lines = [{"timestamp": NOW.isoformat(), "type": "session_meta", "payload": {
        "id": native, "session_id": native, "timestamp": NOW.isoformat(), "cwd": str(root.parent / "work"),
        "originator": "codex_vscode", "cli_version": "0.167.0", "source": "vscode"}}]
    said = [("UserMessage", t) for t in owner] + [("AgentMessage", t) for t in agent]
    lines += [{"timestamp": (NOW + timedelta(minutes=n)).isoformat(), "type": "event_msg",
               "payload": {"type": "item_completed", "item": item(kind, text)}} for n, (kind, text) in enumerate(said)]
    data = "".join(json.dumps(line) + "\n" for line in lines).encode()
    path = root / "sessions/2026/11/08" / f"rollout-2026-11-08T10-00-00-{native}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if zst:
        path = path.with_name(path.name + ".zst")
        path.write_bytes(zstandard.ZstdCompressor().compress(data))
    else:
        path.write_bytes(data)
    return path


def devin_store(root, messages, title="shelf brackets", cwd=None) -> Path:
    """The schema Devin writes: role, content and the owner-input flag sit in each node's chat_message
    JSON. messages: (role, content, is_user_input, extensions, message_id), one node each, in a chain."""
    root.mkdir(parents=True, exist_ok=True)
    db = root / "sessions.db"
    with closing(sqlite3.connect(db)) as con:
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, working_directory TEXT, backend_type TEXT, model TEXT,
                agent_mode TEXT, created_at INTEGER, last_activity_at INTEGER, title TEXT, main_chain_id INTEGER,
                shell_last_seen_index INTEGER, cogs_json TEXT, workspace_dirs TEXT, hidden INTEGER, metadata TEXT);
            CREATE TABLE message_nodes (row_id INTEGER PRIMARY KEY, session_id TEXT, node_id INTEGER,
                parent_node_id INTEGER, chat_message TEXT, created_at INTEGER, metadata TEXT);""")
        con.execute("INSERT INTO sessions (id, working_directory, created_at, title, main_chain_id) VALUES "
                    "('brave-otter', ?, ?, ?, ?)", (cwd or str(root / "work"), int(T), title, len(messages) - 1))
        for n, (role, content, owner, extensions, mid) in enumerate(messages):
            meta = {"created_at": f"{(NOW + timedelta(minutes=n)).isoformat()[:19]}.123456789Z",
                    "is_user_input": owner, "finish_reason": None, **({"extensions": extensions} if extensions else {})}
            con.execute("INSERT INTO message_nodes (session_id, node_id, parent_node_id, chat_message, created_at, "
                        "metadata) VALUES ('brave-otter', ?, ?, ?, ?, NULL)",
                        (n, n - 1 if n else None, json.dumps({"role": role, "content": content, "message_id": mid,
                                                              "metadata": meta}), int(T) + n))
        con.commit()
    return db


def test_a_codex_rollout_in_the_shape_codex_0_16x_writes_is_collected(tmp_path, throwaway_home):
    rollout(throwaway_home / ".codex", "thread-1", ["plan the shelf brackets"], ["Measure the wall first."])
    rec = record(tmp_path, CX)
    Atlas(rec, None).backfill(NOW)
    assert held(rec) == {"thread-1": [("owner", "plan the shelf brackets"), ("agent", "Measure the wall first.")]}


def test_a_devin_session_in_the_shape_devin_writes_is_collected(tmp_path, throwaway_home):
    devin_store(throwaway_home / ".local/share/devin/cli", [
        ("system", "You are Devin.", None, None, "m0"),
        ("user", "plan the shelf brackets", True, {"chisel/acp-content-blocks": []}, "m1"),
        ("assistant", "Measure the wall first.", None, None, "m2"),
        ("tool", "ok", None, None, "m3"),
        ("user", "check the anchors", True, {"subagent/handoff": {}}, "m4"),
        ("user", "<system-reminder>", None, None, "m5"),
        ("user", "plan the shelf brackets", True, {"chisel/acp-content-blocks": []}, "m1")])
    rec = record(tmp_path, DV)
    Atlas(rec, None).backfill(NOW)
    assert list(held(rec).values()) == [[("owner", "plan the shelf brackets"), ("agent", "Measure the wall first.")]]


def test_hermes_in_place_compaction_keeps_the_turns_and_rows_the_record_holds(tmp_path, throwaway_home):
    db = hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelf brackets")],
                [("chat", "user", "which brackets hold 40 kg"), ("chat", "assistant", "Steel L brackets, 4 per shelf."),
                 ("chat", "user", "and the wall plugs"), ("chat", "assistant", "8 mm plugs for brick.")])
    run(db, "ALTER TABLE messages ADD COLUMN compacted INTEGER NOT NULL DEFAULT 0")
    rec = record(tmp_path, HM)
    Atlas(rec, None).tick(NOW)
    before = texts(rec, "chat")
    run(db, "UPDATE messages SET active = 0, compacted = 1 WHERE session_id = 'chat' AND active = 1")
    run(db, "INSERT INTO messages (session_id, role, content, timestamp, _compressed_summary) VALUES "
            "('chat', 'assistant', '[summary] brackets and plugs chosen', ?, 1)", T + 100)
    run(db, "INSERT INTO messages (session_id, role, content, timestamp) VALUES ('chat', 'user', 'what drill bit', ?)",
        T + 200)
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "chat") == before + ["what drill bit"]
    raw = unzstd(next(rec.root.glob("evidence/hermes/*/*/*/raw/main.jsonl.zst")))
    assert json.loads(raw.splitlines()[1])["messages"]["active"] == 1
    assert all(f'"content": "{text}"'.encode() in raw for text in before)


def test_a_forget_committed_while_a_batch_is_put_is_not_undone(tmp_path, throwaway_home, monkeypatch):
    claude(throwaway_home, "first", "shelf sizes")
    pasted = claude(throwaway_home, "pasted", "I pasted a password here")
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    say(pasted, "one more line")
    claude(throwaway_home, "aaa-new", "a new task")
    real, done = ChangeSet.put_session, []

    def forget_lands_meanwhile(self, *args, **kwargs):
        if not done:
            done.append(1)
            forget(rec, CC, "pasted")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ChangeSet, "put_session", forget_lands_meanwhile)
    Atlas(rec, None).tick(NOW)
    monkeypatch.setattr(ChangeSet, "put_session", real)
    Atlas(rec, None).tick(NOW)
    assert set(held(rec)) == {"first", "aaa-new"} and b"I pasted a password" not in every_raw(rec)


@pytest.mark.parametrize("collect", ["tick", "backfill"])
def test_a_forget_committed_just_before_the_commit_is_not_undone(tmp_path, throwaway_home, monkeypatch, collect):
    claude(throwaway_home, "forgotten", "invented private task")
    rec = record(tmp_path, CC)
    real, raced = rec.commit, []

    def forget_first(cs):
        if not raced and any(op.path.endswith("session.md") for op in cs._ops):
            raced.append(1)
            forget(rec, CC, "forgotten")
        return real(cs)

    monkeypatch.setattr(rec, "commit", forget_first)
    Atlas(rec, None).tick(NOW) if collect == "tick" else Atlas(rec, None).backfill(NOW)
    Atlas(rec, None).tick(NOW)
    assert held(rec) == {}


@pytest.mark.parametrize("collect", ["tick", "backfill"])
def test_a_forget_committed_before_the_held_evidence_is_read_is_not_undone(tmp_path, throwaway_home, monkeypatch,
                                                                           collect):
    transcript = claude(throwaway_home, "forgotten", "invented private task")
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    say(transcript, "invented new line")
    real, raced = app._put_sessions, []

    def forget_first(snap, batch, *blocked):
        if not raced:
            raced.append(1)
            forget(rec, CC, "forgotten")
        return real(snap, batch, *blocked)

    monkeypatch.setattr(app, "_put_sessions", forget_first)
    Atlas(rec, None).tick(NOW) if collect == "tick" else Atlas(rec, None).backfill(NOW)
    assert held(rec) == {}


def test_a_collect_removes_evidence_whose_skip_key_forgotten_md_holds(tmp_path, throwaway_home):
    claude(throwaway_home, "forgotten", "invented private task")
    claude(throwaway_home, "kept", "shelf sizes")
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    rec.commit(rec.snapshot().changes().put_forgotten(
        Forgotten(((NOW, "a Session"),), frozenset({skip_key(CC, "forgotten")}))))
    Atlas(rec, None).tick(NOW)
    assert list(held(rec)) == ["kept"] and b"invented private task" not in every_raw(rec)


def test_a_collect_removes_forgotten_evidence_whose_folder_holds_a_stray_file(tmp_path, throwaway_home, caplog):
    claude(throwaway_home, "forgotten", "invented private task")
    claude(throwaway_home, "kept", "shelf sizes")
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    gone = next(s.id for s in rec.snapshot().sessions() if s.native_key == "forgotten")
    next(rec.root.glob(f"evidence/*/*/*/{gone}")).joinpath(".DS_Store").write_bytes(b"invented")
    rec.commit(rec.snapshot().changes().put_forgotten(
        Forgotten(((NOW, "a Session"),), frozenset({skip_key(CC, "forgotten")}))))
    Atlas(rec, None).tick(NOW)
    assert list(held(rec)) == ["kept"] and b"invented private task" not in every_raw(rec)
    assert not list(rec.root.glob(f"evidence/*/*/*/{gone}")) and "remove a forgotten" not in caplog.text


def test_a_tick_survives_a_forgotten_folders_subfolder_turned_into_a_link_after_staging(tmp_path, throwaway_home,
                                                                                        monkeypatch):
    from atlas.record import _txn
    claude(throwaway_home, "forgotten", "invented private task")
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    gone = rec.snapshot().sessions()[0].id
    nested = next(rec.root.glob(f"evidence/*/*/*/{gone}")) / "notes"
    nested.mkdir()
    (nested / "invented.txt").write_bytes(b"invented")
    outside = tmp_path / "outside"
    outside.mkdir()
    rec.commit(rec.snapshot().changes().put_forgotten(
        Forgotten(((NOW, "a Session"),), frozenset({skip_key(CC, "forgotten")}))))
    stage = _txn.stage

    def swapped(root, files, *guards):
        txn = stage(root, files, *guards)
        if not nested.is_symlink():
            nested.rename(tmp_path / "moved")
            nested.symlink_to(outside, target_is_directory=True)
            outside.chmod(0o111)
        return txn

    monkeypatch.setattr(_txn, "stage", swapped)
    try:
        Atlas(rec, None).tick(NOW)
        Atlas(rec, None).tick(NOW + timedelta(minutes=5))
    finally:
        outside.chmod(0o700)
    assert not list(rec.root.glob(".atlas/txn/*.ready")) and list(held(rec)) == []


def test_a_forget_while_backfill_lists_what_the_record_holds_does_not_stop_it(tmp_path, throwaway_home, monkeypatch):
    import atlas.record as record_module
    claude(throwaway_home, "forgotten", "invented private task")
    claude(throwaway_home, "healthy", "invented retained task")
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    real, raced = record_module._front_matter, []

    def forget_first(path):
        if not raced:
            raced.append(1)
            forget(rec, CC, "forgotten")
        return real(path)

    monkeypatch.setattr(record_module, "_front_matter", forget_first)
    Atlas(rec, None).backfill(NOW)
    assert list(held(rec)) == ["healthy"]


def test_a_late_fold_keeps_a_session_already_folded_into_the_late_one(tmp_path, throwaway_home):
    claude(throwaway_home, "top", "plan the release")
    claude(throwaway_home, "parent", "check the export")
    child = claude(throwaway_home, "child", "delegated check of the totals")
    db = t3(throwaway_home, [("top-thread", "top", None), ("parent-thread", "parent", None),
                             ("child-thread", "child", "parent-thread")])
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    child.unlink()
    link(db, "parent-thread", "top-thread")
    Atlas(rec, None).tick(NOW)
    Atlas(rec, None).tick(NOW)
    assert {native: sorted(turns) for native, turns in held(rec).items()} == {"top": [
        ("agent", "check the export"), ("agent", "delegated check of the totals"), ("owner", "plan the release")]}
    assert b"delegated check of the totals" in every_raw(rec)


def test_a_late_fold_stays_after_t3_code_prunes_the_session_folded_before_it(tmp_path, throwaway_home):
    claude(throwaway_home, "top", "plan the release")
    claude(throwaway_home, "parent", "check the export")
    child = claude(throwaway_home, "child", "delegated check of the totals")
    db = t3(throwaway_home, [("top-thread", "top", None), ("parent-thread", "parent", None),
                             ("child-thread", "child", "parent-thread")])
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    link(db, "parent-thread", "top-thread")
    Atlas(rec, None).tick(NOW)
    run(db, "DELETE FROM orchestration_v2_projection_threads WHERE thread_id = 'child-thread'")
    say(child, "and the subtotals")
    Atlas(rec, None).tick(NOW)
    assert list(held(rec)) == ["top"] and ("agent", "and the subtotals") in held(rec)["top"]


def test_of_two_copies_the_one_that_holds_more_once_decompressed_is_read(tmp_path, throwaway_home):
    said = [f"step {n}: " + "check the gpx tunnel gaps again and compare the tracks " * 3 for n in range(12)]
    rollout(throwaway_home / ".codex", "thread-1", said, zst=True)
    rollout(tmp_path / "orca", "thread-1", said[:10])
    rec = record(tmp_path, CX, roots=(tmp_path / "orca",))
    Atlas(rec, None).backfill(NOW)
    assert texts(rec, "thread-1") == [text.strip() for text in said]


def test_adding_a_second_codex_home_never_shrinks_what_the_record_holds(tmp_path, throwaway_home):
    said = [f"step {n}: check the tunnel gaps" for n in range(12)]
    rollout(throwaway_home / ".codex", "thread-1", said)
    rollout(tmp_path / "orca", "thread-1", said[:10] + ["a longer line the orca copy alone holds " * 20])
    rec = record(tmp_path, CX)
    Atlas(rec, None).backfill(NOW)
    snap = rec.snapshot()
    rec.commit(snap.changes().put_settings(replace(snap.settings(), harnesses={
        CX: HarnessSetting(True, "store", (str(tmp_path / "orca"),), "owner")})))
    Atlas(rec, None).tick(NOW)
    assert set(said) <= set(texts(rec, "thread-1"))


def test_a_fold_stays_when_a_larger_copy_in_another_root_is_read(tmp_path, throwaway_home):
    claude(throwaway_home, "parent", "invented owner task")
    claude(throwaway_home, "child", "invented child task")
    db = t3(throwaway_home, [("parent-thread", "parent", None), ("child-thread", "child", "parent-thread")])
    second = throwaway_home / "second-profile"
    rec = record(tmp_path, CC, roots=[second / ".claude"])
    Atlas(rec, None).backfill(NOW)
    run(db, "DELETE FROM orchestration_v2_projection_threads")
    run(db, "DELETE FROM orchestration_v2_projection_provider_threads")
    claude(second, "child", "invented child task", "invented child continuation in the larger copy")
    Atlas(rec, None).tick(NOW)
    Atlas(rec, None).tick(NOW)
    assert list(held(rec)) == ["parent"]
    assert ("agent", "invented child continuation in the larger copy") in held(rec)["parent"]


@pytest.mark.parametrize("exclusions", [(), ("~/clients",)])
def test_a_folder_that_links_to_itself_skips_one_session_not_the_backfill(tmp_path, throwaway_home, monkeypatch,
                                                                          exclusions):
    loop = throwaway_home / "old-project"
    loop.symlink_to(loop)
    real = os.path.realpath

    def looping(path, *args, **kwargs):
        """Resolving the folder fails as Python 3.12 fails on a link that loops, whatever runs the test."""
        if os.fspath(path).startswith(str(loop)):
            raise OSError(errno.ELOOP, "Too many levels of symbolic links", os.fspath(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", looping)
    claude(throwaway_home, "old", "invented old task", cwd=str(loop))
    claude(throwaway_home, "healthy", "invented healthy task")
    rec = record(tmp_path, CC, exclusions=exclusions)
    Atlas(rec, None).backfill(NOW)
    say(next((throwaway_home / ".claude/projects").glob("*/healthy.jsonl")), "invented next step")
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "healthy") == ["invented healthy task", "invented next step"]


def test_t3_code_noting_an_unchanged_session_late_notes_it_at_the_next_tick(tmp_path, throwaway_home):
    claude(throwaway_home, "chat", "invented owner turn")
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    t3(throwaway_home, [("thread", "chat", None)])
    Atlas(rec, None).tick(NOW)
    assert [s.interface for s in rec.snapshot().sessions()] == ["T3 Code"]


def test_two_sessions_whose_masked_sources_match_are_both_collected(tmp_path, throwaway_home, monkeypatch):
    store = throwaway_home / "TOKEN=Ab3dE5gH7jK9mN1pQ3sT5vX7zB9dF1hJ"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(store))
    first = claude(throwaway_home, "session-a", "invented task", mtime=1000)
    moved = store / "projects/project/session-a.jsonl"
    moved.parent.mkdir(parents=True)
    first.rename(moved)
    rec = record(tmp_path, CC)
    Atlas(rec, None).tick(NOW)
    second = moved.with_name("session-b.jsonl")
    second.write_bytes(moved.read_bytes().replace(b"session-a", b"session-b"))
    os.utime(second, ns=(1000, 1000))
    Atlas(rec, None).tick(NOW)
    assert set(held(rec)) == {"session-a", "session-b"}


def test_a_project_folder_swapped_for_a_link_after_listing_is_not_read(tmp_path, throwaway_home, monkeypatch):
    transcript = claude(throwaway_home, "chat", "invented inside turn")
    outside = throwaway_home / "outside-store"
    outside.mkdir()
    row = json.loads(transcript.read_text())
    (outside / transcript.name).write_text(json.dumps({**row, "message": {"role": "user",
                                                                          "content": "invented outside turn"}}) + "\n")
    rec = record(tmp_path, CC)
    real = sources.collect

    def swapped_meanwhile(*args, **kwargs):
        found = real(*args, **kwargs)
        transcript.parent.rename(transcript.parent.with_name("saved-project"))
        transcript.parent.symlink_to(outside, target_is_directory=True)
        return found

    monkeypatch.setattr(sources, "collect", swapped_meanwhile)
    Atlas(rec, None).backfill(NOW)
    assert b"invented outside turn" not in every_raw(rec) and held(rec) == {}


@pytest.mark.parametrize("utf8", [True, False], ids=["utf8_home", "non_utf8_home"])
@pytest.mark.parametrize("swapped_back", [False, True])
def test_a_database_swapped_for_a_link_before_it_is_opened_is_not_read(tmp_path, throwaway_home, monkeypatch,
                                                                        swapped_back, utf8):
    throwaway_home = throwaway_home if utf8 else non_utf8_home(tmp_path, monkeypatch)
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented inside turn")])
    other = hermes(throwaway_home / "outside-store", [("other", "cli", None, None, "invented private task")],
                   [("other", "user", "invented outside turn")])
    rec = record(tmp_path, HM)
    real = sqlite3.connect

    def swapped_meanwhile(database, *args, **kwargs):
        if not db.is_symlink():
            db.rename(db.with_name("saved.db"))
            db.symlink_to(other)
        con = real(database, *args, **kwargs)
        if swapped_back:
            con.execute("SELECT 1 FROM sqlite_master").fetchall()
            db.unlink()
            db.with_name("saved.db").rename(db)
        return con

    with monkeypatch.context() as patched:
        patched.setattr(_adapters.sqlite3, "connect", swapped_meanwhile)
        Atlas(rec, None).backfill(NOW)
    assert b"invented outside turn" not in every_raw(rec) and held(rec) == {}


@pytest.mark.parametrize("level", ["project", "store", "root"])
def test_a_store_folder_or_one_above_it_swapped_for_a_link_after_listing_is_not_read(tmp_path, throwaway_home,
                                                                                      monkeypatch, level):
    chat = claude(throwaway_home, "chat", "invented inside turn")
    claude(throwaway_home, "kept", "invented kept turn", cwd=str(throwaway_home / "other"))
    rollout(throwaway_home / ".codex", "thread", ["invented codex turn"])
    folder = {"project": chat.parent, "store": chat.parent.parent, "root": chat.parent.parent.parent}[level]
    outside = throwaway_home / "outside" / folder.name
    shutil.copytree(folder, outside)
    for transcript in outside.rglob("*.jsonl"):
        transcript.write_bytes(transcript.read_bytes().replace(b"invented", b"invented outside"))
    rec = record(tmp_path, CC, CX)
    real = sources.collect

    def swapped_meanwhile(*args, **kwargs):
        found = real(*args, **kwargs)
        folder.rename(folder.with_name("saved"))
        folder.symlink_to(outside, target_is_directory=True)
        return found

    monkeypatch.setattr(sources, "collect", swapped_meanwhile)
    Atlas(rec, None).backfill(NOW)
    assert b"invented outside" not in every_raw(rec)
    assert set(held(rec)) == {"thread", "kept"} if level == "project" else {"thread"}


@pytest.mark.parametrize("utf8", [True, False], ids=["utf8_home", "non_utf8_home"])
@pytest.mark.parametrize("by_descriptor", [True, False])
def test_a_database_swapped_for_a_link_while_it_is_opened_and_put_back_is_not_read(tmp_path, throwaway_home,
                                                                                   monkeypatch, by_descriptor, utf8):
    throwaway_home = throwaway_home if utf8 else non_utf8_home(tmp_path, monkeypatch)
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented inside turn")])
    other = hermes(throwaway_home / "outside-store", [("other", "cli", None, None, "invented private task")],
                   [("other", "user", "invented outside turn")])
    saved, moved = db.with_name("saved.db"), other.with_name("moved.db")
    rec = record(tmp_path, HM)
    real = sqlite3.connect

    def swapped_while_opened(database, *args, **kwargs):
        """The store is a link to the other file while SQLite opens it. Then the store comes back,
        and the other file's name becomes a link to the store, so every name resolves into it."""
        if other.is_symlink():
            other.unlink()
            moved.rename(other)
        db.rename(saved)
        db.symlink_to(other)
        con = real(database, *args, **kwargs)
        con.execute("SELECT 1 FROM sqlite_master").fetchall()
        db.unlink()
        saved.rename(db)
        other.rename(moved)
        other.symlink_to(db)
        return con

    with monkeypatch.context() as patched:
        patched.setattr(_adapters.sqlite3, "connect", swapped_while_opened)
        if not by_descriptor:
            patched.setattr(_adapters, "_DESCRIPTORS", "/no-such-folder", raising=False)
        Atlas(rec, None).backfill(NOW)
    assert b"invented outside turn" not in every_raw(rec) and "other" not in held(rec)


def non_utf8_home(tmp_path, monkeypatch) -> Path:
    """A home folder whose name holds a byte that is not UTF-8, as under an older Linux locale."""
    home = tmp_path / os.fsdecode(b"h\xf6me")
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.mark.parametrize("pinned", [True, False], ids=["o_path", "stat_only"])
def test_stores_under_a_home_whose_name_is_not_utf8_are_read(tmp_path, monkeypatch, pinned):
    home = non_utf8_home(tmp_path, monkeypatch)
    claude(home, "parent", "check the export", cwd="/invented/work")
    claude(home, "child", "delegated check", cwd="/invented/work")
    t3(home, [("parent-thread", "parent", None), ("child-thread", "child", "parent-thread")])
    hm = hermes(home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented hermes turn")])
    devin_store(home / ".local/share/devin/cli", [("user", "invented devin turn", True, None, "m1")],
                cwd="/invented/work")
    rec = record(tmp_path, CC, DV, HM)
    if not pinned:
        monkeypatch.setattr(_adapters, "_PIN", None)
    Atlas(rec, None).backfill(NOW)
    with writing(hm, (HERMES_ROW, ["chat", "invented hermes wal turn", T + 5])):
        Atlas(rec, None).tick(NOW)
    assert list(held(rec)) == ["parent", f"brave-otter@{int(T)}", "chat"]
    assert texts(rec, "chat") == ["invented hermes turn", "invented hermes wal turn"]


def test_a_database_that_links_to_another_database_in_its_root_is_not_read(tmp_path, throwaway_home):
    other = hermes(throwaway_home / ".hermes/other", [("other", "cli", None, None, "invented private task")],
                   [("other", "user", "invented outside turn")])
    (throwaway_home / ".hermes/state.db").symlink_to(other)
    rec = record(tmp_path, HM)
    Atlas(rec, None).backfill(NOW)
    assert b"invented outside turn" not in every_raw(rec) and held(rec) == {}


WRITER = """
import json, sqlite3, sys
con = sqlite3.connect(sys.argv[1], isolation_level=None)
con.execute("PRAGMA journal_mode=wal")
con.execute("PRAGMA wal_autocheckpoint=0")
for statement, params in json.loads(sys.argv[2]):
    con.execute(statement, params)
print("ready", flush=True)
sys.stdin.read()
"""
HERMES_ROW = "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)"


@contextmanager
def writing(db, *statements, elsewhere=True):
    """A Harness that holds db open in WAL mode, with statements in its WAL and not yet in db: in
    another process, or in this one as when Atlas runs inside Hermes."""
    if not elsewhere:
        with closing(sqlite3.connect(db, isolation_level=None)) as con:
            con.execute("PRAGMA journal_mode=wal")
            con.execute("PRAGMA wal_autocheckpoint=0")
            for statement, params in statements:
                con.execute(statement, params)
            yield
        return
    child = subprocess.Popen([sys.executable, "-c", WRITER, str(db), json.dumps(statements)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline() == "ready\n"
        yield
    finally:
        child.stdin.close()
        child.wait(10)


@pytest.mark.parametrize("elsewhere", [True, False], ids=["another_process", "this_process"])
@pytest.mark.parametrize("listed", [True, False], ids=["descriptors_listed", "not_listed"])
@pytest.mark.parametrize("pinned", [True, False], ids=["o_path", "stat_only"])
def test_stores_whose_newest_rows_are_in_their_wal_are_read_while_their_harness_writes(
        tmp_path, throwaway_home, monkeypatch, elsewhere, listed, pinned):
    hm = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented hermes turn")])
    dv = devin_store(throwaway_home / ".local/share/devin/cli", [("user", "invented devin turn", True, None, "m1")])
    node = json.dumps({"role": "user", "content": "invented devin wal turn", "message_id": "m2",
                       "metadata": {"created_at": "2026-11-08T10:05:00.123456789Z", "is_user_input": True}})
    rec = record(tmp_path, DV, HM)
    with (writing(hm, (HERMES_ROW, ["chat", "invented hermes wal turn", T + 5]), elsewhere=elsewhere),
          writing(dv, ("INSERT INTO message_nodes (session_id, node_id, parent_node_id, chat_message, created_at) "
                       "VALUES ('brave-otter', 1, 0, ?, ?)", [node, int(T) + 5]), elsewhere=elsewhere),
          monkeypatch.context() as patched):
        if not listed:
            patched.setattr(_adapters, "_OPEN", "/no-such-folder")
        if not pinned:
            patched.setattr(_adapters, "_PIN", None)
        Atlas(rec, None).backfill(NOW)
    assert texts(rec, "chat") == ["invented hermes turn", "invented hermes wal turn"]
    assert texts(rec, f"brave-otter@{int(T)}") == ["invented devin turn", "invented devin wal turn"]


@pytest.mark.parametrize("sidecar", ["-wal", "-shm"])
@pytest.mark.parametrize("link", ["hard", "symbolic"])
def test_a_store_wal_or_index_that_links_to_another_database_is_not_read(tmp_path, throwaway_home, sidecar, link):
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented inside turn")])
    other = hermes(throwaway_home / "outside-store", [("chat", "cli", None, None, "invented task")],
                   [("chat", "user", "invented inside turn")])
    rec = record(tmp_path, HM)
    with (writing(db, (HERMES_ROW, ["chat", "invented inside wal turn", T + 5])),
          writing(other, (HERMES_ROW, ["chat", "invented outside turn", T + 5]))):
        name, target = db.with_name(db.name + sidecar), other.with_name(other.name + sidecar)
        before = target.read_bytes()
        name.unlink()
        if link == "hard":
            os.link(target, name)
        else:
            name.symlink_to(target)
        Atlas(rec, None).backfill(NOW)
        assert target.read_bytes() == before
    assert b"invented outside turn" not in every_raw(rec) and held(rec) == {}


@pytest.mark.parametrize("by_descriptor", [True, False])
@pytest.mark.parametrize("pinned", [True, False], ids=["o_path", "stat_only"])
def test_a_store_folder_swapped_for_a_link_while_sqlite_opens_its_wal_is_not_read(tmp_path, throwaway_home,
                                                                                  monkeypatch, by_descriptor, pinned):
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented inside turn")])
    other = hermes(throwaway_home / "outside/.hermes", [("chat", "cli", None, None, "invented task")],
                   [("chat", "user", "invented inside turn")])
    folder, saved = db.parent, db.parent.with_name("saved")
    rec = record(tmp_path, HM)
    real = sqlite3.connect

    def swapped_while_opened(database, *args, **kwargs):
        """SQLite opens the database, then the store folder is a link to the other folder while SQLite
        opens the WAL and its index beside it by name, then the folder comes back."""
        con = real(database, *args, **kwargs)
        folder.rename(saved)
        folder.symlink_to(other.parent, target_is_directory=True)
        con.execute("SELECT 1 FROM sqlite_master").fetchall()
        folder.unlink()
        saved.rename(folder)
        return con

    with (writing(db, (HERMES_ROW, ["chat", "invented inside wal turn", T + 5])),
          writing(other, (HERMES_ROW, ["chat", "invented outside turn", T + 5])),
          monkeypatch.context() as patched):
        patched.setattr(_adapters.sqlite3, "connect", swapped_while_opened)
        if not by_descriptor:
            patched.setattr(_adapters, "_DESCRIPTORS", "/no-such-folder", raising=False)
        if not pinned:
            patched.setattr(_adapters, "_PIN", None)
        Atlas(rec, None).backfill(NOW)
    assert b"invented outside turn" not in every_raw(rec) and held(rec) == {}


def test_reading_a_store_keeps_the_lock_its_harness_holds_in_this_process(throwaway_home):
    """Closing any descriptor of a file drops every POSIX lock the process holds on it. Hermes in this
    process holds its WAL database locked while it is open. Without that lock, an older SQLite in another
    process that closes the database last takes the WAL away, and Hermes's next writes are lost."""
    if not Path("/proc/locks").exists():
        pytest.skip("the system does not list its locks")
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented turn")])

    def locks() -> list[str]:
        mine = f" {os.getpid()} "
        return [line.split(":", 1)[1] for line in Path("/proc/locks").read_text().splitlines()
                if mine in line and line.split()[-3].endswith(f":{db.stat().st_ino}")]

    with closing(sqlite3.connect(db, isolation_level=None)) as own:
        own.execute("PRAGMA journal_mode=wal")
        own.execute(HERMES_ROW, ["chat", "invented wal turn", T + 5])
        held = locks()
        assert _adapters._rows(_adapters._database(db, db.parent), "SELECT count(*) FROM messages") == [(2,)]
        assert held and locks() == held


@pytest.mark.parametrize("writer", [None, True, False], ids=["no_wal", "another_process", "this_process"])
@pytest.mark.parametrize("pinned", [True, False], ids=["o_path", "stat_only"])
def test_a_store_is_read_while_another_thread_opens_a_file_as_sqlite_opens_it(tmp_path, throwaway_home, monkeypatch,
                                                                              caplog, writer, pinned):
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented turn")])
    rec = record(tmp_path, HM)
    real, others = sqlite3.connect, []

    def crowded(*args, **kwargs):
        others.append(os.open(tmp_path / "invented-gateway.log", os.O_CREAT | os.O_RDWR))
        return real(*args, **kwargs)

    live = nullcontext() if writer is None else writing(db, (HERMES_ROW, ["chat", "invented wal turn", T + 5]),
                                                        elsewhere=writer)
    with live, monkeypatch.context() as patched:
        patched.setattr(_adapters.sqlite3, "connect", crowded)
        if not pinned:
            patched.setattr(_adapters, "_PIN", None)
        Atlas(rec, None).backfill(NOW)
    for fd in others:
        os.close(fd)
    assert others and "chat" in held(rec) and "could not be read" not in caplog.text


@pytest.mark.parametrize("elsewhere", [True, False], ids=["another_process", "this_process"])
@pytest.mark.parametrize("pinned", [True, False], ids=["o_path", "stat_only"])
def test_a_store_whose_wal_takes_a_descriptor_another_thread_freed_is_read(
        throwaway_home, monkeypatch, elsewhere, pinned):
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented turn")])
    real, lower = sqlite3.connect, []

    def freed_before_the_wal_opens(*args, **kwargs):
        con = real(*args, **kwargs)
        while lower:
            os.close(lower.pop())
        return con

    with (writing(db, (HERMES_ROW, ["chat", "invented wal turn", T + 5]), elsewhere=elsewhere),
          monkeypatch.context() as patched):
        patched.setattr(_adapters.sqlite3, "connect", freed_before_the_wal_opens)
        if not pinned:
            patched.setattr(_adapters, "_PIN", None)
        lower.append(os.open(os.devnull, os.O_RDONLY))
        assert _adapters._transaction(_adapters._database(db, db.parent),
                                      lambda con: con.execute("SELECT count(*) FROM messages").fetchone()[0]) == 2
    assert not lower


def test_a_store_is_read_every_time_while_other_threads_keep_opening_and_closing_files(tmp_path, throwaway_home):
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented turn")])
    store, stop = _adapters._database(db, db.parent), Event()

    def churn(n):
        while not stop.is_set():
            os.close(os.open(tmp_path / f"invented-{n}.log", os.O_CREAT | os.O_RDWR))

    with (writing(db, (HERMES_ROW, ["chat", "invented wal turn", T + 5]), elsewhere=False),
          ThreadPoolExecutor(2) as pool):
        churning = [pool.submit(churn, n) for n in range(2)]
        try:
            refused = []
            for _ in range(200):
                try:
                    _adapters._transaction(store, lambda con: con.execute("SELECT count(*) FROM messages").fetchall())
                except _adapters.Refused as error:
                    refused.append(type(error).__name__)
        finally:
            stop.set()
        for done in churning:
            done.result()
    assert refused == []


@pytest.mark.parametrize("other_wal", [False, True], ids=["other_rollback", "other_wal"])
@pytest.mark.parametrize("writer", [None, True, False], ids=["no_wal", "another_process", "this_process"])
@pytest.mark.parametrize("pinned", [True, False], ids=["o_path", "stat_only"])
def test_a_store_is_read_while_another_thread_opens_another_roots_store(tmp_path, throwaway_home, monkeypatch,
                                                                       caplog, other_wal, writer, pinned):
    """Another thread opens a second Hermes root's `state.db`, its WAL and index each time Atlas
    connects to the store, as a gateway serving two profiles does."""
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented turn")])
    other = hermes(throwaway_home / "outside/.hermes", [("chat", "cli", None, None, "invented task")],
                   [("chat", "user", "invented other turn")])
    if other_wal:
        with closing(sqlite3.connect(other)) as con:
            con.execute("PRAGMA journal_mode=wal")
    rec = record(tmp_path, HM)
    real, others = sqlite3.connect, []

    def open_other():
        con = real(other, check_same_thread=False)
        con.execute("SELECT count(*) FROM messages").fetchall()
        return con

    def alongside(*args, **kwargs):
        with ThreadPoolExecutor(1) as pool:
            others.append(pool.submit(open_other).result())
        return real(*args, **kwargs)

    live = nullcontext() if writer is None else writing(db, (HERMES_ROW, ["chat", "invented wal turn", T + 5]),
                                                        elsewhere=writer)
    try:
        with live, monkeypatch.context() as patched:
            patched.setattr(_adapters.sqlite3, "connect", alongside)
            if not pinned:
                patched.setattr(_adapters, "_PIN", None)
            Atlas(rec, None).backfill(NOW)
    finally:
        for con in others:
            con.close()
    assert others and "chat" in held(rec) and "could not be read" not in caplog.text


def test_a_store_whose_other_connection_in_this_process_closes_while_atlas_reads_is_read(
        tmp_path, throwaway_home, monkeypatch):
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented hermes turn")])
    rec = record(tmp_path, HM)
    real, logs = sqlite3.connect, []
    with writing(db, (HERMES_ROW, ["chat", "invented hermes wal turn", T + 5])):
        local = real(db, isolation_level=None)
        local.execute("SELECT count(*) FROM messages").fetchall()

        def closed_once_atlas_connects(*args, **kwargs):
            """Hermes, in this process, closes its connection and opens a log, so SQLite gives Atlas's
            WAL the descriptor number Hermes's WAL had."""
            con = real(*args, **kwargs)
            if not logs:
                local.close()
                logs.append((tmp_path / "invented.log").open("w"))
            return con

        with monkeypatch.context() as patched:
            patched.setattr(_adapters.sqlite3, "connect", closed_once_atlas_connects)
            Atlas(rec, None).backfill(NOW)
        for log in logs:
            log.close()
    assert logs and texts(rec, "chat") == ["invented hermes turn", "invented hermes wal turn"]


def test_a_store_whose_wal_its_harness_is_removing_while_atlas_checks_it_is_read(tmp_path, throwaway_home,
                                                                                 monkeypatch):
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented hermes turn")])
    rec = record(tmp_path, HM)
    real = os.stat

    def removing(path, *args, **kwargs):
        """A Harness closing its last connection unlinks its WAL and index between the lookup of
        their names and the reading of their link count."""
        st = real(path, *args, **kwargs)
        if str(path).endswith(("-wal", "-shm")):
            return os.stat_result(st[:3] + (0,) + st[4:])
        return st

    with writing(db, (HERMES_ROW, ["chat", "invented hermes wal turn", T + 5])), monkeypatch.context() as patched:
        patched.setattr(_adapters.os, "stat", removing)
        Atlas(rec, None).backfill(NOW)
    assert texts(rec, "chat") == ["invented hermes turn", "invented hermes wal turn"]


@pytest.mark.parametrize("sidecar", ["-wal", "-shm"])
def test_a_store_without_a_wal_whose_wal_or_index_is_linked_while_atlas_reads_it_is_not_read(
        tmp_path, throwaway_home, sidecar):
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented turn")])
    elsewhere = tmp_path / "invented-other-file"
    elsewhere.write_text("invented")

    def linked_meanwhile(con):
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        count = con.execute("SELECT count(*) FROM messages").fetchone()[0]
        os.link(elsewhere, db.with_name(db.name + sidecar))
        return count

    with pytest.raises(PermissionError):
        _adapters._read_db(_adapters._database(db, db.parent), linked_meanwhile)


def test_a_database_read_under_a_second_hard_link_while_this_process_holds_the_first_is_not_read(tmp_path):
    """SQLite gives every name of one database file in a process the index of the name opened first,
    while each name has its own WAL, so a read pairs one name's index with another's WAL. SQLite
    fails that read while the WAL holds frames and reads it only once a checkpoint empties it."""
    db = tmp_path / "store.db"
    with closing(sqlite3.connect(db, isolation_level=None)) as first:
        first.execute("PRAGMA journal_mode=wal")
        first.execute("CREATE TABLE invented (value INTEGER)")
        first.execute("INSERT INTO invented VALUES (42)")
        first.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.link(db, tmp_path / "second.db")
        with pytest.raises(_adapters.SwappedWhileRead):
            _adapters._rows(_adapters._database(tmp_path / "second.db", tmp_path), "SELECT * FROM invented")


def test_a_store_refused_for_a_linked_index_is_logged_and_skipped_while_other_stores_are_read(
        tmp_path, throwaway_home, caplog):
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented hermes turn")])
    claude(throwaway_home, "kept", "invented claude turn")
    rec = record(tmp_path, HM, CC)
    with writing(db, (HERMES_ROW, ["chat", "invented hermes wal turn", T + 5])):
        os.link(db.with_name(db.name + "-shm"), tmp_path / "invented-linked-index")
        Atlas(rec, None).backfill(NOW)
    assert list(held(rec)) == ["kept"]
    assert "could not be read (LinkedElsewhere)" in caplog.text and "invented" not in caplog.text


def test_reading_a_live_store_costs_no_more_with_ten_thousand_descriptors_open(tmp_path, throwaway_home):
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if max(soft, hard) < 10200 and hard != resource.RLIM_INFINITY:
        pytest.skip("the system allows fewer than 10200 descriptors")
    db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented task")],
                [("chat", "user", "invented turn")])
    store = _adapters._database(db, db.parent)

    def elapsed():
        start = perf_counter()
        assert _adapters._read_db(store, lambda con: con.execute("SELECT count(*) FROM messages").fetchone()[0]) == 2
        return perf_counter() - start

    with writing(db, (HERMES_ROW, ["chat", "invented wal turn", T + 5]), elsewhere=False):
        resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, 10200), hard))
        seed, extra = os.open(db, os.O_RDONLY), []
        try:
            elapsed()
            alone = statistics.median(elapsed() for _ in range(7))
            extra = [os.dup(seed) for _ in range(10000)]
            crowded = statistics.median(elapsed() for _ in range(7))
        finally:
            for fd in [seed, *extra]:
                os.close(fd)
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    assert crowded - alone < 0.005


def test_stores_under_linked_home_and_data_folders_are_read(tmp_path, throwaway_home, monkeypatch):
    dotfiles, disk = tmp_path / "dotfiles", tmp_path / "disk"
    claude(dotfiles, "chat", "invented claude turn", cwd=str(throwaway_home / "work"))
    rollout(dotfiles / ".codex", "thread", ["invented codex turn"])
    devin_store(disk / "share/devin/cli", [("user", "invented devin turn", True, None, "m1")])
    hermes(dotfiles / ".hermes", [("hermes", "cli", None, None, "invented title")],
           [("hermes", "user", "invented hermes turn")])
    for name in (".claude", ".codex", ".hermes"):
        (throwaway_home / name).symlink_to(dotfiles / name, target_is_directory=True)
    (throwaway_home / ".local").mkdir()
    (throwaway_home / ".local/share").symlink_to(disk / "share", target_is_directory=True)
    linked = tmp_path / "linked-home"
    linked.symlink_to(throwaway_home, target_is_directory=True)
    monkeypatch.setenv("HOME", str(linked))
    rec = record(tmp_path, CC, CX, DV, HM)
    Atlas(rec, None).backfill(NOW)
    assert set(held(rec)) == {"chat", "thread", f"brave-otter@{int(T)}", "hermes"}


@contextmanager
def waits_at_most(seconds):
    def hung(*_):
        pytest.fail(f"Atlas waited on a store for {seconds} s")
    kept = signal.signal(signal.SIGALRM, hung)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, kept)


@pytest.mark.parametrize("after_listing", [False, True])
def test_a_fifo_named_as_a_store_database_is_skipped_not_waited_on(tmp_path, throwaway_home, monkeypatch, caplog,
                                                                   after_listing):
    claude(throwaway_home, "chat", "invented claude turn")
    db = hermes(throwaway_home / ".hermes", [("hermes", "cli", None, None, "invented title")],
                [("hermes", "user", "invented hermes turn")])
    rec = record(tmp_path, CC, HM)
    real = sources.collect

    def fifo():
        db.unlink()
        os.mkfifo(db)

    def swapped_meanwhile(*args, **kwargs):
        found = real(*args, **kwargs)
        fifo()
        return found

    if after_listing:
        monkeypatch.setattr(sources, "collect", swapped_meanwhile)
    else:
        fifo()
    with waits_at_most(10):
        Atlas(rec, None).backfill(NOW)
    assert set(held(rec)) == {"chat"} and "skipped" in caplog.text


def test_store_roots_atlas_may_enter_but_not_list_are_read(tmp_path, throwaway_home):
    claude(throwaway_home, "chat", "invented claude turn")
    devin_store(throwaway_home / ".local/share/devin/cli", [("user", "invented devin turn", True, None, "m1")])
    hermes(throwaway_home / ".hermes", [("hermes", "cli", None, None, "invented title")],
           [("hermes", "user", "invented hermes turn")])
    t3(throwaway_home, [("thread", "chat", None)])
    shut = [throwaway_home / name for name in (".local/share/devin/cli", ".hermes", ".t3", ".t3/userdata")]
    rec = record(tmp_path, CC, DV, HM)
    for folder in shut:
        folder.chmod(0o311)
    try:
        Atlas(rec, None).backfill(NOW)
        found = {f.harness: f.sessions for f in sources.probe(rec.snapshot().settings())}
    finally:
        for folder in shut:
            folder.chmod(0o755)
    assert set(held(rec)) == {"chat", f"brave-otter@{int(T)}", "hermes"}
    assert found == {CC: 1, DV: 1, HM: 1, "T3 Code": 1}


def test_probe_logs_and_leaves_out_a_store_it_cannot_read(tmp_path, throwaway_home, caplog):
    claude(throwaway_home, "chat", "invented claude turn")
    userdata = t3(throwaway_home, [("thread", "chat", None)]).parent
    moved = throwaway_home / "disk/userdata"
    moved.parent.mkdir()
    userdata.rename(moved)
    userdata.symlink_to(moved, target_is_directory=True)
    found = {f.harness: f.sessions for f in sources.probe(record(tmp_path, CC).snapshot().settings())}
    assert found == {CC: 1} and "Atlas skipped T3 Code" in caplog.text


def test_a_store_file_whose_status_cannot_be_read_is_closed(throwaway_home, monkeypatch):
    path = claude(throwaway_home, "chat", "invented claude turn")
    store = throwaway_home / ".claude/projects"
    (f,) = _adapters._files(store, "*/*.jsonl", store, _adapters._within(store))
    target, real_open, real_fstat = path.stat(), os.open, os.fstat
    opened = []

    def watched_open(*args, **kwargs):
        opened.append(real_open(*args, **kwargs))
        return opened[-1]

    def failing_fstat(fd):
        st = real_fstat(fd)
        if os.path.samestat(st, target):
            raise OSError(errno.EIO, "invented status failure")
        return st

    def is_open(fd):
        try:
            return os.path.samestat(real_fstat(fd), target)
        except OSError:
            return False

    with monkeypatch.context() as patched:
        patched.setattr(os, "open", watched_open)
        patched.setattr(os, "fstat", failing_fstat)
        for _ in range(3):
            with pytest.raises(OSError):
                _adapters._slurp(f)
    assert not any(map(is_open, opened))


def two_turns(home, harness) -> tuple[Path, str]:
    """A Session whose store holds two turns, and its native key."""
    if harness is CC:
        return claude(home, "chat", "invented first turn", "invented second turn"), "chat"
    if harness is CX:
        return rollout(home / ".codex", "chat", ["invented first turn", "invented second turn"]), "chat"
    if harness is DV:
        return devin_store(home / ".local/share/devin/cli", [("user", "invented first turn", True, None, "m1"),
                                                             ("user", "invented second turn", True, None, "m2")]), (
            f"brave-otter@{int(T)}")
    return hermes(home / ".hermes", [("chat", "cli", None, None, "invented title")],
                  [("chat", "user", "invented first turn"), ("chat", "user", "invented second turn")]), "chat"


def take_back(path, harness, rewrite) -> None:
    """The store drops its second turn, or rewrites it in place."""
    if harness in (CC, CX):
        lines = path.read_text().splitlines(keepends=True)
        last = lines[-1].replace("invented second turn", "invented rewrite") if rewrite else ""
        path.write_text("".join(lines[:-1]) + last)
    elif harness is DV:
        run(path, "UPDATE message_nodes SET chat_message = REPLACE(chat_message, 'invented second turn', "
                  "'invented rewrite') WHERE node_id = 1" if rewrite else "DELETE FROM message_nodes WHERE node_id = 1")
    else:
        run(path, "UPDATE messages SET content = 'invented rewrite' WHERE id = 2" if rewrite
            else "DELETE FROM messages WHERE id = 2")


@pytest.mark.parametrize("harness", [CC, CX, DV, HM], ids=["claude_code", "codex", "devin", "hermes"])
@pytest.mark.parametrize("rewrite", [False, True], ids=["dropped", "rewritten"])
def test_a_turn_its_store_drops_or_rewrites_stays_readable(tmp_path, throwaway_home, harness, rewrite):
    path, native = two_turns(throwaway_home, harness)
    rec = record(tmp_path, harness)
    Atlas(rec, None).backfill(NOW)
    raw = every_raw(rec)
    take_back(path, harness, rewrite)
    Atlas(rec, None).tick(NOW)
    rewritten = ["invented rewrite"] if rewrite else []
    assert texts(rec, native) == ["invented first turn", "invented second turn", *rewritten]
    assert every_raw(rec).startswith(raw)


@pytest.mark.parametrize("undo", ["DELETE FROM messages WHERE id >= 3",
                                  "UPDATE messages SET active = 0 WHERE id >= 3"], ids=["deleted", "archived"])
def test_a_hermes_undo_keeps_the_turns_it_took_back_readable(tmp_path, throwaway_home, undo):
    db = hermes(throwaway_home / ".hermes", [("s1", "telegram", None, None, "shelves")],
                [("s1", "user", "plan the shelves"), ("s1", "assistant", "three shelves"),
                 ("s1", "user", "make it four"), ("s1", "assistant", "four shelves")])
    rec = record(tmp_path, HM)
    Atlas(rec, None).backfill(NOW)
    before, raw = held(rec)["s1"], every_raw(rec)
    run(db, undo)
    Atlas(rec, None).tick(NOW)
    assert held(rec)["s1"] == before and every_raw(rec) == raw


def retry(db, sid, at) -> None:
    """What Hermes v0.23.0's /retry writes (replace_messages with active_only): the live rows go,
    the history before the last owner message comes back under new row ids, then that message
    again and a new answer."""
    with closing(sqlite3.connect(db)) as con:
        rows = con.execute("SELECT role, content, timestamp FROM messages WHERE session_id = ? AND active = 1 "
                           "ORDER BY id", (sid,)).fetchall()
        last = max(n for n, (role, _, _) in enumerate(rows) if role == "user")
        con.execute("DELETE FROM messages WHERE session_id = ? AND active = 1", (sid,))
        con.executemany("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                        [(sid, *row) for row in rows[:last]] + [(sid, "user", rows[last][1], at),
                                                               (sid, "assistant", "invented new answer", at + 1)])
        con.commit()


def test_a_hermes_retry_adds_only_its_new_rows(tmp_path, throwaway_home):
    db = hermes(throwaway_home / ".hermes", [("chat", "telegram", None, None, "shelves")],
                [("chat", "user" if n % 2 == 0 else "assistant", f"invented line {n}") for n in range(20)])
    rec = record(tmp_path, HM)
    Atlas(rec, None).tick(NOW)
    for n in range(3):
        retry(db, "chat", T + 100 + 10 * n)
        Atlas(rec, None).tick(NOW)
    rows = [line for line in every_raw(rec).splitlines() if line.startswith(b'{"messages"')]
    assert len(rows) == 20 + 3 * 2
    assert texts(rec, "chat") == [f"invented line {n}" for n in range(20)] + [
        "invented line 18", "invented new answer"] * 3


def test_codex_compressing_a_rollout_in_place_repeats_no_turn(tmp_path, throwaway_home):
    plain = rollout(throwaway_home / ".codex", "thread-1", [f"use the key {KEY} for the sandbox", "then rerun it"],
                    ["I set Authorization: Bearer invented0123456789abcdef"])
    rec = record(tmp_path, CX)
    Atlas(rec, None).backfill(NOW)
    before, raw = held(rec)["thread-1"], every_raw(rec)
    plain.with_name(plain.name + ".zst").write_bytes(zstandard.ZstdCompressor().compress(plain.read_bytes()))
    plain.unlink()
    Atlas(rec, None).tick(NOW)
    assert held(rec)["thread-1"] == before and every_raw(rec) == raw


def test_two_roots_taking_turns_as_the_larger_copy_repeat_no_turn(tmp_path, throwaway_home):
    said = ["fix the invoice export", f"use the key {KEY} for the sandbox"]
    orca = tmp_path / "orca"
    rollout(throwaway_home / ".codex", "thread-1", said + ["a"])
    rollout(orca, "thread-1", said)
    rec = record(tmp_path, CX, roots=(orca,))
    Atlas(rec, None).backfill(NOW)
    for n in range(1, 4):
        rollout(orca if n % 2 else throwaway_home / ".codex", "thread-1", said + ["a"] * (2 * n))
        Atlas(rec, None).tick(NOW)
    assert texts(rec, "thread-1") == ["fix the invoice export", "use the key [secret: api key] for the sandbox",
                                      *["a"] * 6]


@pytest.mark.parametrize("same", [False, True], ids=["another message", "the message copied"])
def test_devin_messages_of_one_text_in_two_copies_are_told_apart_by_their_id(tmp_path, throwaway_home, same):
    devin_store(throwaway_home / ".local/share/devin/cli", [("user", "invented repeated turn", True, None, "m1")])
    rec = record(tmp_path, DV, roots=(throwaway_home / "devin-two",))
    Atlas(rec, None).backfill(NOW)
    devin_store(throwaway_home / "devin-two", [("user", "invented other turn", True, None, "m0"),
                                               ("user", "invented repeated turn", True, None, "m1" if same else "m2")],
                title="invented longer title, so this copy holds more")
    Atlas(rec, None).tick(NOW)
    assert list(held(rec).values()) == [[("owner", "invented repeated turn"), ("owner", "invented other turn"),
                                         *[("owner", "invented repeated turn")] * (0 if same else 1)]]


def test_a_devin_owner_message_copied_after_an_injected_copy_is_the_owners(tmp_path, throwaway_home):
    devin_store(throwaway_home / ".local/share/devin/cli", [("user", "invented owner request", False, None, "shared"),
                                                            ("user", "invented owner request", True, None, "shared"),
                                                            ("user", "invented later request", True, None, "later")])
    rec = record(tmp_path, DV)
    Atlas(rec, None).backfill(NOW)
    assert list(held(rec).values()) == [[("owner", "invented owner request"), ("owner", "invented later request")]]


def test_a_devin_session_whose_usage_changes_copies_its_row_once(tmp_path, throwaway_home):
    db = devin_store(throwaway_home / ".local/share/devin/cli", [("user", "check the gpx gaps", True, None, "m0")])
    rec = record(tmp_path, DV)
    Atlas(rec, None).backfill(NOW)
    for n in range(1, 4):
        run(db, "UPDATE sessions SET cogs_json = ?, last_activity_at = ?", json.dumps([{"invented calls": n}]), n)
        assert Atlas(rec, None).tick(NOW).collected == 0
        run(db, "INSERT INTO message_nodes (session_id, node_id, parent_node_id, chat_message, created_at) "
                "VALUES ('brave-otter', ?, ?, ?, ?)", n, n - 1, json.dumps({
                    "role": "assistant", "content": f"invented reply {n}", "message_id": f"m{n}",
                    "metadata": {"created_at": NOW.isoformat()}}), int(T) + n)
        Atlas(rec, None).tick(NOW)
    lines = every_raw(rec).splitlines()
    assert sum(line.startswith(b'{"sessions"') for line in lines) == 1
    assert sum(line.startswith(b'{"message_nodes"') for line in lines) == 4


def parent_and_child(tmp_path, home):
    """A Claude Code Session whose agent started a Codex thread through T3 Code."""
    parent = claude(home, "parent", "invented owner task: plan the export")
    child = rollout(home / ".codex", "thread-c", ["invented delegated task"], ["invented child reply"])
    db = t3(home, [("parent-thread", "parent", None)])
    run(db, "INSERT INTO orchestration_v2_projection_threads VALUES ('child-thread', ?, NULL, ?)", NOW.isoformat(),
        json.dumps({"lineage": {"parentThreadId": "parent-thread", "relationshipToParent": "subagent"}}))
    run(db, "INSERT INTO orchestration_v2_projection_provider_threads VALUES ('child-thread', ?)",
        json.dumps({"nativeThreadRef": {"driver": "codex", "nativeId": "thread-c"}}))
    rec = record(tmp_path, CC, CX)
    Atlas(rec, None).backfill(NOW)
    assert list(held(rec)) == ["parent"]
    return parent, child, db, rec


def resume(child: Path, text: str) -> None:
    last = json.loads(child.read_text().splitlines()[-1])
    with child.open("a") as fh:
        item = {"type": "UserMessage", "content": [{"type": "text", "text": text}]}
        fh.write(json.dumps({**last, "timestamp": (NOW + timedelta(hours=1)).isoformat(),
                             "payload": {"type": "item_completed", "item": item}}) + "\n")


@pytest.mark.parametrize("t3_pruned", [False, True], ids=["T3 Code keeps the lineage", "T3 Code pruned it"])
def test_a_folded_child_that_grows_after_its_parent_is_cleaned_up_stays_folded(tmp_path, throwaway_home, t3_pruned):
    parent, child, db, rec = parent_and_child(tmp_path, throwaway_home)
    parent.unlink()
    if t3_pruned:
        run(db, "DELETE FROM orchestration_v2_projection_threads")
        run(db, "DELETE FROM orchestration_v2_projection_provider_threads")
    resume(child, "invented later turn in the codex thread")
    Atlas(rec, None).tick(NOW)
    assert list(held(rec)) == ["parent"]
    assert ("agent", "invented later turn in the codex thread") in held(rec)["parent"]


def test_a_folded_child_compressed_in_place_after_its_parent_is_cleaned_up_stays_folded(tmp_path, throwaway_home):
    parent, child, db, rec = parent_and_child(tmp_path, throwaway_home)
    before = held(rec)
    parent.unlink()
    run(db, "DELETE FROM orchestration_v2_projection_threads")
    child.with_name(child.name + ".zst").write_bytes(zstandard.ZstdCompressor().compress(child.read_bytes()))
    child.unlink()
    Atlas(rec, None).tick(NOW)
    assert held(rec) == before


@pytest.mark.parametrize("collect", ["tick", "backfill"])
def test_forgets_of_other_sessions_during_every_commit_do_not_stop_a_collect(tmp_path, throwaway_home, monkeypatch,
                                                                             collect):
    claude(throwaway_home, "healthy", "invented healthy turn")
    rec = record(tmp_path, CC)
    real, forgets = rec.commit, []

    def others_forgotten_meanwhile(cs):
        if any(op.path.endswith("session.md") for op in cs._ops):
            forgets.append(1)
            forget(rec, CC, f"invented-other-{len(forgets)}")
        return real(cs)

    monkeypatch.setattr(rec, "commit", others_forgotten_meanwhile)
    Atlas(rec, None).tick(NOW) if collect == "tick" else Atlas(rec, None).backfill(NOW)
    assert list(held(rec)) == ["healthy"]


@pytest.mark.parametrize("collect", ["tick", "backfill"])
def test_a_collect_the_record_keeps_refusing_logs_and_raises_nothing(tmp_path, throwaway_home, monkeypatch, caplog,
                                                                     collect):
    from atlas.record import Conflict
    claude(throwaway_home, "chat", "invented turn")
    rec = record(tmp_path, CC)
    real = rec.commit

    def refused(cs):
        if any(op.path.startswith("evidence/") for op in cs._ops):
            raise Conflict("evidence")
        return real(cs)

    monkeypatch.setattr(rec, "commit", refused)
    Atlas(rec, None).tick(NOW) if collect == "tick" else Atlas(rec, None).backfill(NOW)
    assert held(rec) == {} and "next collect" in caplog.text


def test_two_sessions_whose_ids_once_shared_eight_characters_are_two_and_forgotten_alone(tmp_path, throwaway_home):
    first, second = "00000000-0000-4000-8000-000000018f1b", "00000000-0000-4000-8000-0000001032ae"
    claude(throwaway_home, first, "invented first task")
    claude(throwaway_home, second, "invented second task")
    rec = record(tmp_path, CC)
    Atlas(rec, None).backfill(NOW)
    assert set(held(rec)) == {first, second}
    forget(rec, CC, first)
    Atlas(rec, None).tick(NOW)
    assert held(rec) == {second: [("owner", "invented second task")]}


@pytest.mark.parametrize("change", ["copied", "rewritten"], ids=["a later copy gains one", "rewritten in place"])
def test_a_devin_message_whose_extensions_change_is_one_turn_and_one_line(tmp_path, throwaway_home, change):
    db = devin_store(throwaway_home / ".local/share/devin/cli", [("user", "invented owner request", True, None, "m1"),
                                                                 ("assistant", "invented reply", None, None, "m2")])
    rec = record(tmp_path, DV)
    Atlas(rec, None).backfill(NOW)
    before = every_raw(rec)
    with closing(sqlite3.connect(db)) as con:
        chat, created = con.execute("SELECT chat_message, created_at FROM message_nodes WHERE node_id = 1").fetchone()
    reply = json.loads(chat)
    reply["metadata"]["extensions"] = ["chisel/acp-content-blocks"]
    if change == "copied":
        run(db, "INSERT INTO message_nodes (session_id, node_id, parent_node_id, chat_message, created_at) "
                "VALUES ('brave-otter', 2, 1, ?, ?)", json.dumps(reply), created)
    else:
        run(db, "UPDATE message_nodes SET chat_message = ? WHERE node_id = 1", json.dumps(reply))
    Atlas(rec, None).tick(NOW)
    assert list(held(rec).values()) == [[("owner", "invented owner request"), ("agent", "invented reply")]]
    assert every_raw(rec) == before


@pytest.mark.parametrize("t3_pruned", [False, True], ids=["T3 Code keeps the lineage", "T3 Code pruned it"])
def test_a_forget_of_the_session_a_child_is_held_in_during_its_put_is_not_undone(tmp_path, throwaway_home,
                                                                                monkeypatch, t3_pruned):
    parent, child, db, rec = parent_and_child(tmp_path, throwaway_home)
    parent.unlink()
    if t3_pruned:
        run(db, "DELETE FROM orchestration_v2_projection_threads")
        run(db, "DELETE FROM orchestration_v2_projection_provider_threads")
    resume(child, "invented later turn in the codex thread")
    real, forgot = app._put_sessions, []

    def forgotten_meanwhile(snap, batch, *blocked):
        if not forgot:
            forgot.append(1)
            forget(rec, CC, "parent")
        return real(snap, batch, *blocked)

    monkeypatch.setattr(app, "_put_sessions", forgotten_meanwhile)
    for _ in range(1 if t3_pruned else 3):
        Atlas(rec, None).tick(NOW)
    assert held(rec) == {} and b"invented delegated task" not in every_raw(rec)


@pytest.mark.parametrize("grows", [False, True], ids=["unchanged", "grows"])
@pytest.mark.parametrize("correction", ["fork", "another parent"])
def test_t3_code_correcting_a_fold_puts_the_session_where_it_now_says(tmp_path, throwaway_home, correction, grows):
    parent, child, db, rec = parent_and_child(tmp_path, throwaway_home)
    if correction == "fork":
        run(db, "UPDATE orchestration_v2_projection_threads SET payload_json = ? WHERE thread_id = 'child-thread'",
            json.dumps({"lineage": {"parentThreadId": "parent-thread", "relationshipToParent": "fork"}}))
    else:
        claude(throwaway_home, "other", "invented other owner task")
        run(db, "INSERT INTO orchestration_v2_projection_threads VALUES ('other-thread', ?, NULL, '{}')",
            NOW.isoformat())
        run(db, "INSERT INTO orchestration_v2_projection_provider_threads VALUES ('other-thread', ?)",
            json.dumps({"nativeThreadRef": {"driver": "claudeAgent", "nativeId": "other"}}))
        link(db, "child-thread", "other-thread")
    if grows:
        resume(child, "invented later turn in the codex thread")
    Atlas(rec, None).tick(NOW)
    Atlas(rec, None).backfill(NOW)
    child_turns = [("owner", "invented delegated task"), ("agent", "invented child reply"),
                   *[("owner", "invented later turn in the codex thread")] * grows]
    expected = {"thread-c": child_turns} if correction == "fork" else {"other": [
        ("owner", "invented other owner task"), *[("agent", text) for _, text in child_turns]]}
    assert held(rec) == {"parent": [("owner", "invented owner task: plan the export")], **expected}
    assert every_raw(rec).count(b"invented delegated task") == 1


@pytest.mark.parametrize("harness", [DV, HM], ids=["devin", "hermes"])
def test_a_title_changed_back_reads_as_its_store_holds_it_now(tmp_path, throwaway_home, harness):
    if harness is DV:
        db = devin_store(throwaway_home / ".local/share/devin/cli", [("user", "invented owner request", True, None,
                                                                      "m1")], title="invented first title")
    else:
        db = hermes(throwaway_home / ".hermes", [("chat", "cli", None, None, "invented first title")],
                    [("chat", "user", "invented owner request")])
    rec = record(tmp_path, harness)
    Atlas(rec, None).backfill(NOW)
    for title in ("invented second title", "invented first title"):
        run(db, "UPDATE sessions SET title = ?", title)
        Atlas(rec, None).tick(NOW)
    assert [s.title for s in rec.snapshot().sessions()] == ["invented first title"]
    assert b"invented second title" in every_raw(rec)


CUT = b'{"type": "user", "message": {"content": "invented cut off'


def transcript(home, harness, *said) -> Path:
    return (claude(home, "chat", *said) if harness is CC
            else rollout(home / ".codex", "chat", list(said)))


def insert(path, at, line) -> None:
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join([*lines[:at], line, *lines[at:]]))


@pytest.mark.parametrize("harness", [CC, CX], ids=["claude-code", "codex"])
def test_a_transcript_with_a_line_that_does_not_parse_is_collected_without_it(tmp_path, throwaway_home, caplog,
                                                                             harness):
    chat = transcript(throwaway_home, harness, "invented first question", "invented second question")
    insert(chat, -1, CUT + b"\n")
    rec = record(tmp_path, harness)
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "chat") == ["invented first question", "invented second question"]
    assert every_raw(rec) == chat.read_bytes()
    assert f"Atlas left 1 unparseable line of one {harness.label} Session out of its turns" in caplog.text
    assert b"invented cut off" not in caplog.text.encode()


def test_a_claude_code_transcript_whose_first_line_does_not_parse_is_collected(tmp_path, throwaway_home):
    chat = transcript(throwaway_home, CC, "invented question")
    insert(chat, 0, b"\x00" * 64 + b"\n")
    rec = record(tmp_path, CC)
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "chat") == ["invented question"]
    assert every_raw(rec) == chat.read_bytes()


@pytest.mark.parametrize("harness", [CC, CX], ids=["claude-code", "codex"])
def test_a_line_fixed_after_a_tick_held_it_unparsed_is_collected(tmp_path, throwaway_home, harness):
    chat = transcript(throwaway_home, harness, "invented first question", "invented fixed question")
    good = chat.read_bytes().splitlines(keepends=True)
    chat.write_bytes(b"".join([*good[:-1], CUT + b"\n"]))
    rec = record(tmp_path, harness)
    Atlas(rec, None).tick(NOW)
    chat.write_bytes(b"".join(good))
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "chat") == ["invented first question", "invented fixed question"]
    assert every_raw(rec) == b"".join([*good[:-1], CUT + b"\n", good[-1]])


@pytest.mark.parametrize("harness", [CC, CX], ids=["claude-code", "codex"])
def test_a_last_line_still_being_written_waits_for_the_tick_that_reads_it_whole(tmp_path, throwaway_home, caplog,
                                                                              harness):
    chat = transcript(throwaway_home, harness, "invented first question", "invented later question")
    whole = chat.read_bytes()
    last = whole.splitlines(keepends=True)[-1]
    chat.write_bytes(whole[:-len(last) // 2])
    rec = record(tmp_path, harness)
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "chat") == ["invented first question"]
    assert every_raw(rec) == whole[:-len(last)]
    assert "unparseable" not in caplog.text
    chat.write_bytes(whole)
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "chat") == ["invented first question", "invented later question"]
    assert every_raw(rec) == whole


@pytest.mark.parametrize("harness", [CC, CX], ids=["claude-code", "codex"])
def test_a_line_glued_onto_the_fragment_a_crash_left_shows_its_turn(tmp_path, throwaway_home, caplog, harness):
    chat = transcript(throwaway_home, harness, "invented first question", "invented lost question")
    whole = chat.read_bytes().splitlines(keepends=True)
    fragment = b"".join(whole[:-1]) + whole[-1][:len(whole[-1]) // 2]
    chat.write_bytes(fragment)
    rec = record(tmp_path, harness)
    Atlas(rec, None).tick(NOW)
    chat.write_bytes(fragment + whole[-1].replace(b"invented lost question", b"invented resumed question"))
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "chat") == ["invented first question", "invented resumed question"]
    assert every_raw(rec) == chat.read_bytes()
    assert f"Atlas left 1 unparseable line of one {harness.label} Session out of its turns" in caplog.text


@pytest.mark.parametrize("harness", [CC, CX], ids=["claude-code", "codex"])
@pytest.mark.parametrize("ending", [b"\r\n", b"\r"], ids=["crlf", "bare-cr"])
def test_a_raw_copy_splits_lines_on_lf_only_and_keeps_their_bytes(tmp_path, throwaway_home, harness, ending):
    chat = transcript(throwaway_home, harness, "invented first question", "invented second question")
    lines = chat.read_bytes().splitlines()
    chat.write_bytes(b"".join(line + b"\r\n" for line in lines[:-1]) + CUT + ending + lines[-1] + b"\n")
    rec = record(tmp_path, harness)
    Atlas(rec, None).tick(NOW)
    assert every_raw(rec) == chat.read_bytes()
    assert texts(rec, "chat") == ["invented first question", "invented second question"]


@pytest.mark.parametrize("harness", [CC, CX], ids=["claude-code", "codex"])
def test_a_cut_line_the_harness_wrote_whole_again_is_no_longer_counted(tmp_path, throwaway_home, caplog, harness):
    chat = transcript(throwaway_home, harness, "invented first question", "invented fixed question")
    good = chat.read_bytes().splitlines(keepends=True)
    chat.write_bytes(b"".join([*good[:-1], good[-1][:len(good[-1]) // 2] + b"\n"]))
    rec = record(tmp_path, harness)
    Atlas(rec, None).tick(NOW)
    assert "Atlas left 1 unparseable line" in caplog.text
    chat.write_bytes(b"".join(good))
    caplog.clear()
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "chat") == ["invented first question", "invented fixed question"]
    assert "unparseable" not in caplog.text


def test_a_codex_rollout_whose_first_line_does_not_parse_is_logged_and_skipped(tmp_path, throwaway_home, caplog):
    insert(transcript(throwaway_home, CX, "invented question"), 0, CUT + b"\n")
    rec = record(tmp_path, CX)
    Atlas(rec, None).tick(NOW)
    assert rec.snapshot().sessions() == []
    assert "Atlas skipped one Codex Session it could not read" in caplog.text


@pytest.mark.parametrize("harness", [CC, CX], ids=["claude-code", "codex"])
def test_a_brace_heavy_cut_or_glued_line_is_parsed_a_bounded_number_of_times(tmp_path, throwaway_home, monkeypatch,
                                                                             harness):
    resumed = "invented resumed question" + " {x}" * 2000
    chat = transcript(throwaway_home, harness, "invented first question", resumed)
    whole = chat.read_bytes().splitlines(keepends=True)
    cut = whole[-1][:len(whole[-1]) // 2]
    chat.write_bytes(b"".join(whole[:-1]) + cut + b"}\n" + cut + whole[-1])
    real, parsed = _adapters._record, []
    monkeypatch.setattr(_adapters, "_record", lambda line: parsed.append(line) or real(line))
    rec = record(tmp_path, harness)
    Atlas(rec, None).tick(NOW)
    assert texts(rec, "chat") == ["invented first question", resumed]
    assert len(parsed) < 50


@pytest.mark.parametrize("line", [b"{\x00" * 2000, b"{\x00\x00\x00" * 2000,
                                  b"\xff\x00" + b"{\x00" * 2000 + b"}\x00" * 2000],
                         ids=["utf-16", "utf-32", "balanced-utf-16"])
def test_the_glued_search_decodes_a_line_of_nul_led_braces_a_bounded_number_of_times(monkeypatch, line):
    real, decoded = _adapters._record, []
    monkeypatch.setattr(_adapters, "_record", lambda tail: decoded.append(len(tail)) or real(tail))
    assert _adapters._glued(line) == (len(line), None)
    assert sum(decoded) <= 3 * len(line)


def test_the_glued_search_finds_the_shortest_tail_trying_every_brace_would():
    def every_brace(line):
        at = len(line)
        while (at := line.rfind(b"{", 1, at)) > 0:
            if (r := _adapters._record(line[at:])) is not None:
                return at, r
        return len(line), None

    texts = ['{"a":1}', '{"b":"}"}', '{"c":"\\""}', '{"d":[{}]}', '{"\u00e9":"\U0001f600"}', '{"s":"\\ud800"}', " { } "]
    pieces = ["{", "}", "[", "]", '"', "\\", ":", "1", " ", "\x00", "\ud800", "\u017d"]
    boms = {"utf-8": b"", "utf-16-le": codecs.BOM_UTF16_LE, "utf-16-be": codecs.BOM_UTF16_BE,
            "utf-32-le": codecs.BOM_UTF32_LE, "utf-32-be": codecs.BOM_UTF32_BE}
    objects = [bom * kept + t.encode(e, "surrogatepass") for e, bom in boms.items() for kept in (0, 1) for t in texts]
    atoms = [b"\x00", b"\x00" * 3, b"\xff", b"\xc3", b"\xed\xa0\x80", b"\xff" * 4, b"\r", b"\\",
             *(p.encode(e, "surrogatepass") for e in boms for p in pieces),
             *(cut for o in objects for cut in (o, o[:len(o) // 2], o[len(o) // 2:], o[1:]))]
    rng = random.Random(48)
    for _ in range(20000):
        line = b"".join(rng.choices(atoms, k=rng.randint(0, 10)))
        line += rng.choice(objects) * (rng.random() < 0.3) + b"\x00" * rng.choice([0, 0, 0, 1, 2, 3, 4])
        assert _adapters._glued(line) == every_brace(line), line


@pytest.mark.parametrize("first", ["backups", "reader"], ids=["main first", "subagent first"])
def test_turns_of_two_parts_in_one_minute_keep_their_order_when_one_part_is_read_again(tmp_path, throwaway_home,
                                                                                      first):
    said = {"backups": ("owner", "invented ask"), "reader": ("agent", "invented delegated read")}
    second = next(sid for sid in said if sid != first)
    db = hermes(throwaway_home / ".hermes", [("backups", "cli", None, None, "backups"),
                                             ("reader", "subagent", "backups", None, "reader")],
                [(sid, "user", said[sid][1]) for sid in (first, second)])
    run(db, "UPDATE messages SET timestamp = ? WHERE session_id = ?", T + 10, first)
    run(db, "UPDATE messages SET timestamp = ? WHERE session_id = ?", T + 50, second)
    rec = record(tmp_path, HM)
    Atlas(rec, None).backfill(NOW)
    run(db, "INSERT INTO messages (session_id, role, content, timestamp) VALUES ('backups', 'user', ?, ?)",
        "invented follow-up", T + 300)
    Atlas(rec, None).tick(NOW + timedelta(minutes=6))
    assert held(rec)["backups"] == [said[first], said[second], ("owner", "invented follow-up")]


def test_an_edited_turn_takes_no_same_role_turns_place_and_the_held_turns_keep_their_order(tmp_path, throwaway_home):
    db = hermes(throwaway_home / ".hermes", [("backups", "cli", None, None, "backups"),
                                             ("reader", "subagent", "backups", None, "reader")],
                [("backups", "user", "invented ask"), ("backups", "assistant", "invented answer A"),
                 ("reader", "user", "invented read B"), ("backups", "assistant", "invented answer C"),
                 ("reader", "assistant", "invented read D")])
    for row, seconds in enumerate((0, 10, 20, 30, 40), start=1):
        run(db, "UPDATE messages SET timestamp = ? WHERE id = ?", T + seconds, row)
    rec = record(tmp_path, HM)
    Atlas(rec, None).backfill(NOW)
    before = texts(rec, "backups")
    run(db, "UPDATE messages SET content = 'invented edited A' WHERE id = 2")
    Atlas(rec, None).tick(NOW + timedelta(minutes=2))
    after = texts(rec, "backups")
    assert after == ["invented ask", "invented answer A", "invented read B", "invented edited A", "invented answer C",
                     "invented read D"]
    assert [t for t in after if t != "invented edited A"] == before
    for n in range(3):
        run(db, "UPDATE sessions SET title = ? WHERE id = 'backups'", f"invented title {n}")
        Atlas(rec, None).tick(NOW + timedelta(minutes=3 + n))
        assert texts(rec, "backups") == after


def test_a_new_turn_read_with_held_ones_never_moves_them_past_a_part_not_read(tmp_path, throwaway_home):
    db = hermes(throwaway_home / ".hermes", [("backups", "cli", None, None, "backups"),
                                             ("reader", "subagent", "backups", None, "reader")],
                [("backups", "user", "invented a"), ("reader", "user", "invented read"),
                 ("backups", "user", "invented b")])
    for text, seconds in (("invented a", 10), ("invented read", 30), ("invented b", 50)):
        run(db, "UPDATE messages SET timestamp = ? WHERE content = ?", T + seconds, text)
    rec = record(tmp_path, HM)
    Atlas(rec, None).backfill(NOW)
    run(db, "INSERT INTO sessions (id, source, parent_session_id, started_at) VALUES ('writer', 'subagent', 'backups', ?)",
        T + 1)
    for sid, text, seconds in (("writer", "invented write", 2), ("backups", "invented n", 5),
                               ("backups", "invented follow-up", 300)):
        run(db, "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)", sid, text,
            T + seconds)
    Atlas(rec, None).tick(NOW + timedelta(minutes=6))
    after = texts(rec, "backups")
    assert after == ["invented n", "invented a", "invented read", "invented b", "invented write", "invented follow-up"]
    for sid, seconds in (("backups", 600), ("reader", 610), ("writer", 620)):
        run(db, "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)", sid,
            f"invented later {sid}", T + seconds)
    Atlas(rec, None).tick(NOW + timedelta(minutes=12))
    assert texts(rec, "backups")[:len(after)] == after


def test_a_row_edited_to_show_as_a_later_held_turn_keeps_the_held_order(tmp_path, throwaway_home):
    db = hermes(throwaway_home / ".hermes", [("backups", "cli", None, None, "backups"),
                                             ("reader", "subagent", "backups", None, "reader")],
                [("backups", "user", "invented ask"), ("backups", "assistant", "invented first"),
                 ("backups", "assistant", "invented ok"), ("backups", "user", "invented next"),
                 ("reader", "assistant", "invented read"), ("backups", "assistant", "invented ok")])
    for row, seconds in enumerate((0, 5, 10, 20, 30, 40), start=1):
        run(db, "UPDATE messages SET timestamp = ? WHERE id = ?", T + seconds, row)
    rec = record(tmp_path, HM)
    Atlas(rec, None).backfill(NOW)
    run(db, "UPDATE messages SET content = 'invented ok' WHERE id = 2")
    for n in range(3):
        Atlas(rec, None).tick(NOW + timedelta(minutes=2 + n))
        assert texts(rec, "backups") == ["invented ask", "invented first", "invented ok", "invented ok",
                                         "invented next", "invented read", "invented ok"]


@pytest.mark.parametrize("said, title", [
    ("x" * 70 + " ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8 then more", "x" * 70 + " "),
    ("x" * 64 + " password is Hunt3r!x9 then more", "x" * 64 + " password is "),
], ids=["key", "named-password"])
def test_a_title_cut_from_the_first_turn_keeps_no_part_of_a_secret(tmp_path, throwaway_home, said, title):
    claude(throwaway_home, "titled", said)
    rec = record(tmp_path, CC)
    Atlas(rec, None).tick(NOW)
    (s,) = rec.snapshot().sessions()
    assert s.title == title
