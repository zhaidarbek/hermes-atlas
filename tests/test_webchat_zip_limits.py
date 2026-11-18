"""The limits on an export zip that the audit found a way past: what importing it takes beyond
what parsing it takes, the zip64 end record, a shard nothing was read from, and a compression
method neither vendor writes. Every fixture is a megabyte or so, built here."""
import io
import json
import os
import struct
import tracemalloc
import zipfile
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from atlas.app import Atlas
from atlas.model import HarnessId, HarnessSetting
from atlas.record import Record
from atlas.sources import webchat
from test_commit import PROFILE, SETTINGS

NOW = datetime(2026, 11, 8, 10, tzinfo=timezone.utc)
T = NOW.timestamp()
LINES = 1 << 18
ABOUT = 1.25
"""How far past its charge an import may go and still be "about" it: one conversation measured 1.03 at most."""
APART = 1 << 18
"""What an import takes whatever the file holds: the record's own files and the import log."""


def conversation(text: str, id: str = "c1") -> dict:
    said = {"id": "m-" + id, "author": {"role": "user"}, "create_time": T - 86400,
            "content": {"content_type": "text", "parts": [text]}}
    return {"conversation_id": id, "title": id, "create_time": T - 86400, "update_time": T - 86400,
            "current_node": "a", "mapping": {"a": {"id": "a", "parent": None, "children": [], "message": said}}}


def record(tmp_path) -> Record:
    rec = Record.open(tmp_path / "atlas")
    rec.create(replace(SETTINGS, timezone="UTC", harnesses={
        HarnessId.CHATGPT: HarnessSetting(True, "export", ("~/atlas/inbox",), "owner")}), PROFILE)
    return rec


def settle(path) -> None:
    old = int((T - 3600) * 1e9)
    os.utime(path, ns=(old, old))


def drop(rec: Record, shards: dict[str, bytes], method: int = zipfile.ZIP_DEFLATED):
    path = rec.root / "inbox" / "export.zip"
    with zipfile.ZipFile(path, "w", method) as z:
        for name, data in shards.items():
            z.writestr(name, data)
    settle(path)
    return path


def packed(shard: bytes) -> zipfile.ZipFile:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("conversations.json", shard)
    return zipfile.ZipFile(data)


# ------------------------------------------------------------------ memory

@pytest.mark.parametrize("text", [
    "**\n" * LINES,                          # the audit's: every line is escaped in session.md
    "ab\n" * LINES,
    "x" + "\n" * (2 * LINES) + "x",
    "x" * (1 << 20),
    ("the quick brown fox jumps over the lazy dog " * 2 + "\n") * 12000,
    "x" * (1 << 20) + "€",
    "x" * (1 << 20) + "\U0001F600",
    "**\n" * LINES + "\U0001F600",
], ids=["starred lines", "short lines", "empty lines", "one line", "prose", "two-byte", "astral", "astral lines"])
def test_importing_one_large_conversation_takes_about_what_the_file_was_charged(tmp_path, text):
    rec = record(tmp_path)
    item = conversation(text)
    shard = json.dumps([item], ensure_ascii=False).encode()
    path = drop(rec, {"conversations.json": shard})
    charged = (webchat._cost(len(shard), sum(shard.count(t) for t in webchat._TOKENS), webchat._per_byte(shard))
               + webchat._put_cost(webchat.chatgpt(item)))

    tracemalloc.start()
    try:
        put = Atlas(rec, None)._import(NOW)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert put == 1 and not path.exists()
    assert peak <= ABOUT * charged + APART, f"{peak} bytes for a file charged {charged} ({peak / charged:.2f} times)"


def test_a_file_is_refused_when_putting_its_largest_conversation_would_pass_the_bound():
    lines = json.dumps([conversation("**\n" * LINES)]).encode()
    one = json.dumps([conversation("x" * (3 * LINES))]).encode()
    room = 8 << 20                                        # parsing either takes about 4 MiB of it

    assert len(webchat.parse_archive(packed(one), parsed=room).conversations) == 1
    with pytest.raises(webchat.TooLarge, match="put"):
        webchat.parse_archive(packed(lines), parsed=room)


# ------------------------------------------------------------------ the zip64 end record

ENTRY = struct.pack("<4s4B4HL2L5H2L", b"PK\x01\x02", 20, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
MANY = 200_000


def end64(entries: int, size: int, offset: int) -> bytes:
    return struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, entries, entries, size, offset)


def locator(offset: int) -> bytes:
    return struct.pack("<4sLQL", b"PK\x06\x07", 0, offset, 1)


END = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, len(ENTRY), 0, 0)


def refused_unbuilt(tmp_path, monkeypatch, data: bytes) -> str:
    """The failure Atlas read the archive as, having let zipfile build no directory from it."""
    inbox = tmp_path / "atlas" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "export.zip").write_bytes(data)
    settle(inbox / "export.zip")
    built: list[int] = []
    opened = zipfile.ZipFile.__init__

    def counting(self, *args, **kwargs):
        built.append(0)
        opened(self, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "__init__", counting)
    (name, stamp), = webchat.waiting(inbox, NOW)
    export = webchat.read(inbox, name, stamp, [HarnessId.CHATGPT])
    assert not built
    return export.failed


def test_a_zip64_record_is_read_whatever_the_end_record_states(tmp_path, monkeypatch):
    directory = ENTRY * MANY
    data = directory + end64(MANY, len(directory), 0) + locator(len(directory)) + END
    assert refused_unbuilt(tmp_path, monkeypatch, data) == "TooLarge"


def test_a_zip64_record_is_read_when_a_comment_pushes_its_locator_out_of_the_last_bytes(tmp_path, monkeypatch):
    directory = ENTRY * MANY
    comment = b"\0" * 0xFFFF
    end = END[:-2] + struct.pack("<H", len(comment)) + comment
    data = directory + end64(MANY, len(directory), 0) + locator(len(directory)) + end
    assert refused_unbuilt(tmp_path, monkeypatch, data) == "TooLarge"


def test_a_locator_that_names_another_record_than_the_one_before_it_is_refused(tmp_path, monkeypatch):
    """Some zipfile releases read the record the locator names, others the one right before the
    locator: a modest decoy at either place must not carry a large directory at the other."""
    directory = ENTRY * MANY
    decoy = end64(1, len(ENTRY), 0)
    named = decoy + directory + end64(MANY, len(directory), len(decoy)) + locator(0) + END
    assert refused_unbuilt(tmp_path, monkeypatch, named) == "NotAnExport"


def test_a_zip64_archive_as_zipfile_writes_it_passes_the_directory_check():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for n in range(0x10000 + 1):                      # past what the classic end record can count
            z.writestr(str(n), b"")
    assert data.getvalue()[-42:-38] == webchat._LOCATOR64
    webchat.directory(data, len(data.getvalue()))


# ------------------------------------------------------------------ a shard nothing was read from

def test_a_zip_with_a_shard_that_lists_no_conversations_is_kept_and_read_once(tmp_path, monkeypatch):
    rec = record(tmp_path)
    path = drop(rec, {"conversations-1.json": json.dumps([conversation("what first says", "first")]).encode(),
                      "conversations-2.json": json.dumps({"items": [conversation("what second says", "second")]}).encode()})
    atlas = Atlas(rec, None)

    assert atlas._import(NOW) == 1

    assert {s.native_key for s in rec.snapshot().sessions()} == {"first"}
    assert path.exists()
    (entry,) = rec.snapshot().imports()
    assert (entry.deleted, entry.failure, entry.broken) == (False, "Unreadable", 1)

    reads: list[str] = []
    read = webchat.read
    monkeypatch.setattr(webchat, "read", lambda *a, **k: reads.append(a[1]) or read(*a, **k))
    assert atlas._import(NOW) == 0
    assert not reads and path.exists()                    # the kept file costs the next tick no read


def test_a_shard_whose_object_holds_its_conversations_is_read(tmp_path):
    rec = record(tmp_path)
    path = drop(rec, {"conversations.json": json.dumps({"conversations": [conversation("hello", "held")]}).encode()})

    assert Atlas(rec, None)._import(NOW) == 1
    assert not path.exists()


# ------------------------------------------------------------------ compression

@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_a_shard_that_is_neither_stored_nor_deflated_is_not_inflated(tmp_path, monkeypatch, method):
    rec = record(tmp_path)
    path = drop(rec, {"conversations.json": json.dumps([conversation("hello")]).encode()}, method)
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda *a, **k: pytest.fail("the shard was opened"))

    assert Atlas(rec, None)._import(NOW) == 0

    (entry,) = rec.snapshot().imports()
    assert (entry.deleted, entry.failure) == (False, "NotAnExport") and path.exists()


def test_a_stored_shard_is_read(tmp_path):
    rec = record(tmp_path)
    path = drop(rec, {"conversations.json": json.dumps([conversation("hello")]).encode()}, zipfile.ZIP_STORED)

    assert Atlas(rec, None)._import(NOW) == 1
    assert not path.exists()
