"""Web-chat exports (#15): the parser seam, the inbox as a boundary, and the tick's import."""
import io
import json
import logging
import os
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
import zstandard

from atlas import app as app_module, filing, settings_rules
from atlas.app import Atlas
from atlas.model import (Actor, Area, Forgotten, HarnessId, HistoryLine, HistoryId, Inference, Kind, Membership, Pause,
                         Pursuit, PursuitId, ResumeNote, Status, skip_key)
from atlas.record import ImportLog, Record
from atlas.sources import webchat
from harness import exports, seeds, world

GPT, CAI = HarnessId.CHATGPT, HarnessId.CLAUDE_AI
NOW = world.EXPORTS_AT
SECRET = f"the staging db password is {world.PASTED_PASSWORD}"
YEAR_ONE = datetime(1, 1, 1, tzinfo=timezone.utc).timestamp()


def archive(members: dict[str, object]) -> zipfile.ZipFile:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
        for name, value in members.items():
            z.writestr(name, value if isinstance(value, (str, bytes)) else json.dumps(value))
    return zipfile.ZipFile(io.BytesIO(data.getvalue()))


def gpt(title: str) -> world.WorldSession:
    return next(s for s in world.EXPORTED[GPT] if s.title == title)


def conversation(c: webchat.Conversation):
    return webchat.session(c, lambda origin: None, seeds.AFTER_SETUP, NOW)


# ------------------------------------------------------------------ the parser seam

def test_a_chatgpt_export_reads_the_branch_that_ends_at_the_current_node():
    item = exports.chatgpt_conversation(gpt("knee physio exercises"))
    leaf = item["current_node"]
    item["mapping"]["edited"] = {"id": "edited", "parent": item["mapping"][leaf]["parent"], "children": [], "message": {
        "id": "edited", "author": {"role": "user"}, "create_time": 1.0, "content": {"content_type": "text",
                                                                                     "parts": ["an earlier edit"]}}}
    harness, (c,), broken, group, _ = webchat.parse_archive(archive({"conversations.json": [item]}))
    turns = conversation(c).evidence.turns
    assert (harness, broken, group) == (GPT, 0, False)
    assert [t.text for t in turns] == [t.text for t in gpt("knee physio exercises").turns()]
    assert [t.role for t in turns] == ["owner" if t.owner else "agent" for t in gpt("knee physio exercises").turns()]
    assert [t.at for t in turns] == [t.at for t in gpt("knee physio exercises").turns()]


def test_sharded_conversations_are_read_once_each_and_the_group_chats_are_never_opened(monkeypatch):
    sessions = world.EXPORTED[GPT]
    first = [exports.chatgpt_conversation(s) for s in sessions[:3]]
    second = [exports.chatgpt_conversation(s) for s in sessions[2:]]
    z = archive({"conversations-000.json": first, "conversations-001.json": second, "export_manifest.json": {},
                 "group_chats.json": [{"text": exports.GROUP_CHAT}]})
    opened, real = [], zipfile.ZipFile.open
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda self, name, *a, **k: (
        opened.append(getattr(name, "filename", name)), real(self, name, *a, **k))[1])
    harness, found, broken, group, skipped = webchat.parse_archive(z)
    assert {c.key for c in found} == {s.native for s in sessions} and len(found) == len(sessions)
    assert group and broken == 0 and skipped == 1
    assert sorted(opened) == ["conversations-000.json", "conversations-001.json"]


def test_a_claude_export_reads_iso_times_and_follows_parent_links_to_the_newest_leaf():
    s = world.EXPORTED[CAI][0]
    item = exports.claude_conversation(s)
    messages = item["chat_messages"]
    for n, m in enumerate(messages):
        m["parent_message_uuid"] = messages[n - 1]["uuid"] if n else "00000000-0000-4000-8000-000000000000"
    retried = {**messages[-1], "uuid": "retried", "text": "an answer replaced",
               "content": [{"type": "text", "text": "an answer replaced"}], "created_at": world.utc(s.start)}
    item["chat_messages"] = [*messages, retried]
    harness, (c,), _, _, _ = webchat.parse_archive(archive({"conversations.json": [item]}))
    turns = conversation(c).evidence.turns
    assert harness is CAI
    assert [t.text for t in turns] == [t.text for t in s.turns()]
    assert turns[0].at == s.start


def test_epoch_and_iso_times_and_an_undated_message_placed_between_its_neighbours():
    s = gpt("sourdough starter ratio")
    item = exports.chatgpt_conversation(s, undated=True)
    (c,) = webchat.parse_archive(archive({"conversations.json": [item]})).conversations
    turns = conversation(c).evidence.turns
    said = s.turns()
    assert turns[2].at_interpolated and said[1].at < turns[2].at < said[3].at
    assert not any(t.at_interpolated for n, t in enumerate(turns) if n != 2)
    epoch = datetime(2026, 11, 16, 18, 10, tzinfo=timezone.utc).timestamp()
    assert webchat._time("2026-11-16T18:10:00.000Z") == webchat._time(epoch) == webchat._time(epoch * 1000)
    assert webchat._time("not a time") is None and webchat._time(True) is None


def test_a_malformed_conversation_is_counted_and_the_others_are_read():
    good = exports.chatgpt_conversation(gpt("birthday gift ideas"))
    bad = {**exports.chatgpt_conversation(gpt("translate a lease clause")), "mapping": {"x": {"parent": 7, "message": {"author": 3}}}}
    harness, found, broken, _, _ = webchat.parse_archive(archive({"conversations.json": [good, bad, "nonsense"]}))
    assert [c.key for c in found] == [good["id"]] and broken == 1


@pytest.mark.parametrize("members, error", [
    ({"conversations.json": "{not json"}, "JSONDecodeError"),
    ({"user.json": "{}"}, "NotAnExport"),
    ({"conversations.json": {"a": 1}}, None),
])
def test_a_file_that_cannot_be_read_is_reported_by_its_failure_type_only(tmp_path, members, error):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    path = inbox / "export.zip"
    path.write_bytes(_bytes(members))
    read = _read_one(inbox)
    assert read.failed == error and read.conversations == ()


def test_a_shard_past_its_cap_or_inflating_past_what_it_declares_is_refused(tmp_path, monkeypatch):
    zeros = [{"id": "x", "mapping": {}, "pad": "0" * (3 << 20)}]
    monkeypatch.setattr(webchat, "MAX_SHARD", 1 << 20)
    with pytest.raises(webchat.TooLarge):
        webchat.parse_archive(archive({"conversations.json": zeros}))
    z = archive({"conversations.json": zeros})
    z.getinfo("conversations.json").file_size = 1000
    with pytest.raises(Exception):
        webchat._inflate(z, z.getinfo("conversations.json"), 10 << 20)


def test_more_conversations_than_the_cap_fail_the_file(monkeypatch):
    monkeypatch.setattr(webchat, "MAX_CONVERSATIONS", 2)
    items = [exports.chatgpt_conversation(s) for s in world.EXPORTED[GPT][:3]]
    with pytest.raises(webchat.TooLarge):
        webchat.parse_archive(archive({"conversations.json": items}))


# ------------------------------------------------------------------ the inbox

def test_the_inbox_lists_only_settled_regular_zip_files_and_follows_no_link(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.zip").write_bytes(b"zip")
    os.utime(inbox / "a.zip", (NOW.timestamp() - 600,) * 2)
    (inbox / "fresh.zip").write_bytes(b"zip")
    os.utime(inbox / "fresh.zip", (NOW.timestamp() - 10,) * 2)
    (inbox / "folder.zip").mkdir()
    (tmp_path / "elsewhere.zip").write_bytes(b"zip")
    (inbox / "linked.zip").symlink_to(tmp_path / "elsewhere.zip")
    (inbox / "notes.txt").write_text("x")
    assert [name for name, _ in webchat.waiting(inbox, NOW)] == ["a.zip"]
    linked = tmp_path / "linked-inbox"
    linked.symlink_to(inbox, target_is_directory=True)
    assert webchat.waiting(linked, NOW) == []
    assert not webchat.release(linked, "a.zip", webchat.waiting(inbox, NOW)[0][1])


def test_release_deletes_only_the_file_atlas_read(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    path = inbox / "export.zip"
    path.write_bytes(b"one")
    os.utime(path, (NOW.timestamp() - 600,) * 2)
    ((name, stamp),) = webchat.waiting(inbox, NOW)
    path.write_bytes(b"swapped in")
    assert not webchat.release(inbox, name, stamp) and path.exists()
    path.unlink()
    path.symlink_to(tmp_path / "elsewhere")
    assert not webchat.release(inbox, name, stamp) and path.is_symlink()


# ------------------------------------------------------------------ the tick

@pytest.fixture
def home(tmp_path):
    from harness.home import AtlasHome
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(NOW)
        seeds.web_chats_added(seeds.after_scene_6)(home.record_root)
        yield home


def tick(home, minutes: int = 0):
    return Atlas.open(home.record_root).tick(NOW + timedelta(minutes=minutes))


def drop(home, sessions, name="chatgpt-export.zip", harness=GPT):
    build = exports.chatgpt_zip if harness is GPT else exports.claude_zip
    return build(home.record_root / "inbox" / name, sessions, NOW - timedelta(hours=1))


def held(home, harness=GPT):
    return {s.id for s in Record.open(home.record_root).snapshot().sessions() if s.harness is harness}


def test_an_export_is_imported_masked_then_deleted_and_logged_without_its_name(home, caplog):
    secret = replace(gpt("translate a lease clause"), pasted=SECRET)
    path = drop(home, (secret, gpt("birthday gift ideas")))
    assert tick(home).collected == 2
    snap = Record.open(home.record_root).snapshot()
    assert held(home) == {seeds.evidence_id(secret), seeds.evidence_id(gpt("birthday gift ideas"))}
    folder = next(home.record_root.glob(f"evidence/chatgpt/*/*/{seeds.evidence_id(secret)}"))
    assert world.PASTED_PASSWORD not in (folder / "session.md").read_text()
    raw = zstandard.ZstdDecompressor().decompressobj().decompress((folder / "raw" / "main.jsonl.zst").read_bytes())
    assert world.PASTED_PASSWORD.encode() in raw and exports.GROUP_CHAT.encode() not in raw
    assert not path.exists()
    (entry,) = snap.imports()
    assert (entry.harness, entry.sessions, entry.deleted, entry.failure) == (GPT, 2, True, None)
    assert "chatgpt-export" not in (home.record_root / ".atlas" / "imports.json").read_text()
    assert "chatgpt-export" not in caplog.text


def test_the_same_export_dropped_again_changes_no_session_and_a_forgotten_one_never_returns(home):
    knee, other = gpt("knee physio exercises"), gpt("birthday gift ideas")
    drop(home, (knee, other))
    tick(home)
    record = Record.open(home.record_root)
    snap = record.snapshot()
    record.commit(snap.changes().remove_evidence(seeds.evidence_id(knee)).put_forgotten(Forgotten(
        ((NOW, "a Session"),), frozenset({skip_key(GPT, knee.native_key)}))))
    folder = next(home.record_root.glob(f"evidence/chatgpt/*/*/{seeds.evidence_id(other)}"))
    before = (folder / "session.md").read_bytes()
    longer = replace(knee, minutes=knee.minutes + 30)
    path = drop(home, (longer, other), name="newer.zip")
    assert tick(home, 10).collected == 0
    assert held(home) == {seeds.evidence_id(other)} and (folder / "session.md").read_bytes() == before
    assert not path.exists()


def test_a_later_export_adds_the_messages_the_copy_lacks_and_keeps_those_it_dropped(home):
    s = gpt("birthday gift ideas")
    drop(home, (s,))
    tick(home)
    later = exports.chatgpt_conversation(replace(s, minutes=s.minutes + 20))
    first_id = next(n for n, v in later["mapping"].items() if v["message"] and v["message"]["author"]["role"] == "user")
    later["mapping"][first_id]["message"]["content"]["parts"] = ["the owner deleted this one in the service"]
    later["mapping"][first_id]["message"]["id"] = "replaced"
    exports._zip(home.record_root / "inbox" / "later.zip", {"conversations.json": [later]}, NOW)
    tick(home, 70)
    texts = [t.text for t in Record.open(home.record_root).snapshot().session(seeds.evidence_id(s)).turns]
    assert s.title in texts and "the owner deleted this one in the service" in texts


def test_a_failed_file_is_kept_reported_once_and_the_tick_goes_on(home, caplog):
    bad = home.record_root / "inbox" / "mine.zip"
    bad.write_bytes(b"not a zip at all")
    os.utime(bad, (NOW.timestamp() - 600,) * 2)
    good = drop(home, (gpt("birthday gift ideas"),))
    with caplog.at_level(logging.WARNING):
        report = tick(home)
    assert report.collected == 1 and bad.exists() and not good.exists()
    entries = Record.open(home.record_root).snapshot().imports()
    assert [(e.harness, e.deleted, e.failure) for e in entries] == [(GPT, True, None), (None, False, "NotAnExport")]
    assert "mine.zip" not in caplog.text and "NotAnExport" in caplog.text
    tick(home, 10)
    assert len(Record.open(home.record_root).snapshot().imports()) == 2
    shown = settings_rules.added(Record.open(home.record_root).snapshot().settings(),
                                 Record.open(home.record_root).snapshot().imports())
    assert shown[0] == ("ChatGPT", "an export in ~/atlas/inbox could not be read, so it stays there")


def test_a_file_that_failed_in_part_keeps_what_it_read_and_stays(home):
    good = exports.chatgpt_conversation(gpt("birthday gift ideas"))
    path = exports._zip(home.record_root / "inbox" / "part.zip", {"conversations.json": [good, {"mapping": {}}]},
                        NOW - timedelta(hours=1))
    tick(home)
    assert held(home) == {seeds.evidence_id(gpt("birthday gift ideas"))} and path.exists()
    assert Record.open(home.record_root).snapshot().imports()[-1].failure == "Unreadable"


def test_an_export_larger_than_the_budget_is_imported_over_ticks_and_deleted_at_the_end(home, monkeypatch):
    monkeypatch.setattr(app_module, "IMPORT_BUDGET", 2)
    path = drop(home, world.EXPORTED[GPT])
    counts = []
    for n in range(4):
        counts.append(tick(home, 10 * n).collected)
    assert counts == [2, 2, 2, 0] and not path.exists()
    assert len(held(home)) == len(world.EXPORTED[GPT])


def test_a_file_is_kept_when_the_commit_that_holds_its_sessions_fails(home, monkeypatch):
    path = drop(home, (gpt("birthday gift ideas"),))
    from atlas.record import Conflict
    real = Record.commit

    def failing(self, cs):
        if any(op.path.startswith("evidence/") for op in cs._ops):
            raise Conflict("raced")
        return real(self, cs)
    monkeypatch.setattr(Record, "commit", failing)
    tick(home)
    assert path.exists() and held(home) == set()


def test_an_export_of_a_web_chat_not_added_stays_untouched(home):
    record = Record.open(home.record_root)
    snap = record.snapshot()
    record.commit(snap.changes().put_settings(replace(snap.settings(), harnesses={GPT: seeds.WEB_CHATS[GPT]})))
    path = drop(home, world.EXPORTED[CAI], name="claude.zip", harness=CAI)
    assert tick(home).collected == 0 and path.exists() and held(home, CAI) == set()
    (entry,) = Record.open(home.record_root).snapshot().imports()
    assert (entry.harness, entry.failure, entry.deleted) == (CAI, "NotAdded", False)
    read = []
    real = webchat.read
    with pytest.MonkeyPatch.context() as m:
        m.setattr(webchat, "read", lambda *a: read.append(1) or real(*a))
        tick(home, 10)
        assert read == []
        snap = Record.open(home.record_root).snapshot()
        assert settings_rules.added(snap.settings(), snap.imports())[0][1].startswith("waiting for an export")
        record = Record.open(home.record_root)
        record.commit(snap.changes().put_settings(replace(snap.settings(), harnesses=dict(seeds.WEB_CHATS))))
        assert tick(home, 20).collected == len(world.EXPORTED[CAI]) and read == [1] and not path.exists()


def test_imports_log_holds_no_text_so_forget_leaves_it_and_the_sweep_ignores_it(home):
    s = gpt("birthday gift ideas")
    drop(home, (s,))
    tick(home)
    log = (home.record_root / ".atlas" / "imports.json").read_text()
    assert set(json.loads(log)[0]) == {"harness", "at", "sessions", "deleted", "failure", "file", "broken", "skipped"}
    assert s.title not in log and s.native not in log
    record = Record.open(home.record_root)
    snap = record.snapshot()
    record.commit(snap.changes().remove_evidence(seeds.evidence_id(s)).put_forgotten(Forgotten(
        ((NOW, "a Session"),), frozenset({skip_key(GPT, s.native_key)}))))
    assert not Record.open(home.record_root).reconcile()
    assert (home.record_root / ".atlas" / "imports.json").read_text() == log


# ------------------------------------------------------------------ filing and the Settings line

def test_web_chat_sessions_wait_behind_the_others_so_an_export_never_holds_back_today():
    class Snap:
        def __init__(self, sessions):
            self._s = sessions

        def holds_pursuits(self): return True
        def filing_retries(self): return {}
        def pursuits(self): return []
        def held(self): return type("H", (), {"sessions": lambda _: [type("S", (), {"id": s.id})() for s in self._s]})()
        def sessions(self): return self._s
        def card(self, id): return None
        def settings(self): return replace(seeds.AFTER_SETUP, harnesses={**seeds.AFTER_SETUP.harnesses, **seeds.WEB_CHATS})

    old = seeds._evidence(gpt("sourdough starter ratio"))
    today = seeds._evidence(world.by_title("test_retry one worker"))
    assert filing.queue(Snap([old, today]), NOW) == [today.id, old.id]


def _created(created, sessions, history_actor=Actor.ATLAS, focus=False):
    return Pursuit(PursuitId("p-k1"), "Knee rehab plan", Kind.PROBLEM, Area("health"), Status.ACTIVE, focus, created, None,
                   ResumeNote((), (), (), (), ()), None, (), (), tuple(sessions), (),
                   (HistoryLine(HistoryId("h-0001"), NOW, history_actor, Inference("Created from 1 Session", ("gpt-0",))),))


@pytest.mark.parametrize("days_old, count, actor, focus, proposed", [
    (0, 3, Actor.ATLAS, False, True),
    (0, 2, Actor.ATLAS, False, False),
    (8, 3, Actor.ATLAS, False, False),
    (0, 3, Actor.OWNER, False, False),
    (0, 3, Actor.ATLAS, True, False),
])
def test_a_new_pursuit_worth_pushing_gets_one_focus_proposal(days_old, count, actor, focus, proposed):
    members = [Membership(f"gpt-{n}", NOW - timedelta(days=n), True) for n in range(count)]
    p = filing._offered(_created(NOW.date() - timedelta(days=days_old), members, actor, focus), NOW, NOW.date())
    assert bool(p.proposals) is proposed
    if proposed:
        assert filing._offered(p, NOW, NOW.date()) == p


def test_the_settings_line_says_what_the_last_export_gave():
    s = replace(seeds.AFTER_SETUP, harnesses=dict(seeds.WEB_CHATS))
    assert settings_rules.added(s) == [("ChatGPT", "waiting for an export in ~/atlas/inbox"),
                                       ("claude.ai", "waiting for an export in ~/atlas/inbox")]
    log = (ImportLog(GPT, NOW, 143, True, None, "1:2:3:4"),)
    assert settings_rules.added(s, log)[0] == (
        "ChatGPT", "read 143 Sessions from the export of Wed 18 Nov and deleted it; waiting for the next one in ~/atlas/inbox")


def _bytes(members) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name, value in members.items():
            z.writestr(name, value if isinstance(value, str) else json.dumps(value))
    return data.getvalue()


def _read_one(inbox):
    path = next(inbox.iterdir())
    os.utime(path, (NOW.timestamp() - 600,) * 2)
    ((name, stamp),) = webchat.waiting(inbox, NOW)
    return webchat.read(inbox, name, stamp, (GPT, CAI))


# ------------------------------------------------------------------ hostile files: memory, directory, times

def _shapes() -> dict[str, bytes]:
    """The shapes json builds most from a byte, made at run time and kept small for CI."""
    n = 50_000
    chat = [exports.chatgpt_conversation(s) for s in world.EXPORTED[GPT]] * 40
    return {
        "nested empty lists": b"[" + b",".join([b"[]"] * n) + b"]",
        "empty dicts": b"[" + b",".join([b"{}"] * n) + b"]",
        "lists of dicts": b"[" + b",".join([b'[{"a":[]}]'] * n) + b"]",
        "distinct keys": b"{" + b",".join(b'"k%d":0' % i for i in range(n)) + b"}",
        "short strings": b"[" + b",".join(b'"%02x"' % (i % 256) for i in range(n)) + b"]",
        "floats": b"[" + b",".join([b"1.5"] * n) + b"]",
        "deep": b"[" * 900 + b"]" * 900,
        "astral escape": b'["' + b"a" * n * 10 + b'\\ud83d\\ude00"]',
        "astral raw": ('["' + "a" * n * 10 + "\U0001F600" + '"]').encode(),
        "wide escape": b'["' + b"a" * n * 10 + b'\\u4e2d"]',
        "plain string": b'["' + b"a" * n * 10 + b'"]',
        "latin escape": b'["' + b"a" * n * 10 + b'\\u00e9"]',
        "a ChatGPT export": json.dumps(chat).encode(),
        "a ChatGPT export, raw text": json.dumps(chat, ensure_ascii=False).encode(),
    }


@pytest.mark.parametrize("shape", list(_shapes()))
def test_the_parse_bound_is_never_less_than_what_json_builds(shape):
    import tracemalloc
    data = _shapes()[shape]
    z = archive({"conversations.json": data})
    inflated, cost = webchat._inflate(z, z.getinfo("conversations.json"), len(data), 1 << 40)
    tracemalloc.start()
    built = json.loads(inflated)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    del built
    assert peak <= cost


def test_a_shard_that_would_parse_past_the_bound_is_refused_before_json_builds_it(monkeypatch):
    import tracemalloc
    bomb = b"[" + b",".join([b"[]"] * 2_000_000) + b"]"
    z = archive({"conversations.json": bomb})
    monkeypatch.setattr(webchat.json, "loads", lambda *a, **k: pytest.fail("json built the bomb"))
    tracemalloc.start()
    with pytest.raises(webchat.TooLarge):
        webchat.parse_archive(z, parsed=32 << 20)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 32 << 20


def _many_entries(path, count: int, zip64: bool = False):
    with pytest.MonkeyPatch.context() as m:
        if zip64:
            m.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("conversations.json", "[]")
            for n in range(count):
                z.writestr(f"file-{n}.dat", b"")
    if zip64:
        data = bytearray(path.read_bytes())
        end = data.rfind(b"PK\x05\x06")
        data[end + 8:end + 12] = b"\xff\xff\xff\xff"
        path.write_bytes(bytes(data))
    return path


@pytest.mark.parametrize("zip64", [False, True])
def test_a_directory_past_its_cap_is_refused_from_the_end_record_before_zipfile_reads_it(tmp_path, monkeypatch, zip64):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _many_entries(inbox / "export.zip", 50, zip64)
    monkeypatch.setattr(webchat, "MAX_ENTRIES", 10)
    monkeypatch.setattr(webchat.zipfile, "ZipFile", lambda *a, **k: pytest.fail("zipfile read the directory"))
    assert _read_one(inbox).failed == "TooLarge"
    monkeypatch.setattr(webchat, "MAX_ENTRIES", 100)
    monkeypatch.setattr(webchat, "MAX_DIRECTORY", 100)
    assert _read_one(inbox).failed == "TooLarge"


def test_a_file_that_would_parse_past_the_bound_is_noted_once_and_not_read_again_until_it_changes(home, monkeypatch):
    monkeypatch.setattr(webchat, "MAX_PARSED", 1 << 20)
    path = exports._zip(home.record_root / "inbox" / "bomb.zip",
                        {"conversations.json": "[" + ",".join(["[]"] * 400_000) + "]"}, NOW - timedelta(hours=1))
    good = drop(home, (gpt("birthday gift ideas"),))
    assert tick(home).collected == 1 and path.exists() and not good.exists()
    entries = Record.open(home.record_root).snapshot().imports()
    assert [(e.harness, e.deleted, e.failure) for e in entries] == [(None, False, "TooLarge"), (GPT, True, None)]
    read = []
    real = webchat.read
    monkeypatch.setattr(webchat, "read", lambda *a: read.append(1) or real(*a))
    tick(home, 10)
    assert read == [] and len(Record.open(home.record_root).snapshot().imports()) == 2
    os.utime(path, (NOW.timestamp() - 300,) * 2)
    tick(home, 20)
    assert read == [1]


def test_a_conversation_the_record_cannot_hold_is_broken_and_the_rest_of_the_inbox_imports(home, monkeypatch):
    good, other = gpt("birthday gift ideas"), gpt("translate a lease clause")
    surrogate = {**exports.chatgpt_conversation(gpt("sourdough starter ratio")), "title": "a cut emoji \ud83d"}
    undated = exports.chatgpt_conversation(gpt("knee physio exercises"))
    for node in undated["mapping"].values():
        if node["message"]:
            node["message"]["create_time"] = float("nan")
    undated.update(create_time=None, update_time=10 ** 30)
    part = exports._zip(home.record_root / "inbox" / "part.zip", {"conversations.json": [
        surrogate, exports.chatgpt_conversation(good), undated]}, NOW - timedelta(hours=2))
    second = drop(home, (other,))
    assert tick(home).collected == 2
    assert held(home) == {seeds.evidence_id(good), seeds.evidence_id(other)}
    assert part.exists() and not second.exists()
    entry = Record.open(home.record_root).snapshot().imports()[0]
    assert (entry.harness, entry.sessions, entry.broken, entry.deleted, entry.failure) == (
        GPT, 1, 2, False, "UnicodeEncodeError")
    snap = Record.open(home.record_root).snapshot()
    assert settings_rules.added(snap.settings(), snap.imports())[0] == ("ChatGPT", (
        "read 1 Session from the export of Wed 18 Nov; part of it could not be read, so it stays in ~/atlas/inbox"))
    read = []
    real = webchat.read
    monkeypatch.setattr(webchat, "read", lambda *a: read.append(1) or real(*a))
    before = len(snap.sessions())
    tick(home, 10)
    assert read == [] and len(Record.open(home.record_root).snapshot().sessions()) == before
    monkeypatch.setattr(webchat, "read", real)
    os.utime(part, (NOW.timestamp() - 300,) * 2)
    assert tick(home, 20).collected == 0 and len(Record.open(home.record_root).snapshot().sessions()) == before


@pytest.mark.parametrize("at, kept", [
    (datetime(1, 1, 1, tzinfo=timezone.utc).timestamp(), False),
    (datetime(2014, 12, 31, tzinfo=timezone.utc).timestamp(), False),
    ((NOW + timedelta(days=3)).timestamp(), False),
    ((NOW - timedelta(days=400)).timestamp(), True),
])
def test_a_time_outside_the_sane_range_is_no_time_for_its_turn(at, kept):
    item = exports.chatgpt_conversation(gpt("sourdough starter ratio"))
    nodes = [n for n in item["mapping"].values() if n["message"] and n["message"]["author"]["role"] != "system"]
    nodes[1]["message"]["create_time"] = at
    (c,) = webchat.parse_archive(archive({"conversations.json": [item]})).conversations
    turn = conversation(c).evidence.turns[1]
    assert (turn.at.timestamp() == at) is kept and turn.at_interpolated is not kept


def test_a_conversation_with_no_time_in_range_is_broken():
    item = exports.chatgpt_conversation(gpt("sourdough starter ratio"))
    for node in item["mapping"].values():
        if node["message"]:
            node["message"]["create_time"] = YEAR_ONE
    item.update(create_time=YEAR_ONE, update_time=YEAR_ONE)
    (c,) = webchat.parse_archive(archive({"conversations.json": [item]})).conversations
    with pytest.raises(ValueError):
        conversation(c)


def test_a_session_dated_before_time_zones_reads_back_and_the_journal_goes_on(home, caplog):
    drop(home, (gpt("birthday gift ideas"),))
    tick(home)
    record = Record.open(home.record_root)
    s = next(s for s in record.snapshot().sessions() if s.harness is GPT)
    ancient = datetime(1, 1, 1, 0, 53, 28, tzinfo=timezone.utc)
    bad = replace(s, id=seeds.evidence_id(gpt("translate a lease clause")), native_key="other", started=ancient,
                  last_at=ancient, parts=(), turns=tuple(replace(t, at=ancient) for t in s.turns))
    record.commit(record.snapshot().changes().put_session(bad, {}))
    snap = Record.open(home.record_root).snapshot()
    assert {x.id for x in snap.sessions(since=NOW - timedelta(days=30))} == {s.id}
    with caplog.at_level(logging.WARNING):
        tick(home, 10)
    assert "skipped the Journal" not in caplog.text


@pytest.mark.parametrize("line", ["last_at: not a time", "started: 0001-01-01T00:53:28+00:53:28 and more"])
def test_a_session_file_whose_time_cannot_be_read_is_left_out_of_the_list_logged(home, caplog, line):
    drop(home, (gpt("birthday gift ideas"), gpt("translate a lease clause")))
    tick(home)
    path, other = sorted(home.record_root.glob("evidence/chatgpt/*/*/*/session.md"))
    field = line.split(":")[0]
    path.write_text("\n".join(line if row.startswith(field + ":") else row for row in path.read_text().split("\n")))
    with caplog.at_level(logging.WARNING):
        snap = Record.open(home.record_root).snapshot()
        assert [x.id for x in snap.sessions(since=NOW - timedelta(days=30))] == [other.parent.name]
        assert [x.id for x in snap.sessions()] == [other.parent.name]
    assert "could not read (" in caplog.text and "birthday" not in caplog.text


def test_conversations_held_twice_or_with_no_text_are_counted_as_skipped_and_the_file_goes(home):
    s = gpt("birthday gift ideas")
    first = exports.chatgpt_conversation(s)
    again = {**exports.chatgpt_conversation(s), "update_time": first["update_time"] + 60}
    empty = {**exports.chatgpt_conversation(gpt("translate a lease clause")), "mapping": {}}
    path = exports._zip(home.record_root / "inbox" / "dupes.zip", {"conversations.json": [first, again, empty]},
                        NOW - timedelta(hours=1))
    assert tick(home).collected == 1 and not path.exists()
    (entry,) = Record.open(home.record_root).snapshot().imports()
    assert (entry.sessions, entry.skipped, entry.broken, entry.deleted) == (1, 2, 0, True)


def test_failing_files_past_the_files_a_tick_reads_are_each_read_once_and_leave_the_log_with_the_file(home, monkeypatch):
    monkeypatch.setattr(app_module, "IMPORT_FILES", 3)
    bad = []
    for n in range(8):
        path = home.record_root / "inbox" / f"bad-{n}.zip"
        path.write_bytes(b"not a zip %d" % n)
        os.utime(path, (NOW.timestamp() - 600 + n,) * 2)
        bad.append(path)
    read = []
    real = webchat.read
    monkeypatch.setattr(webchat, "read", lambda *a: read.append(1) or real(*a))
    for n in range(4):
        tick(home, 10 * n)
    assert len(read) == 8 and len(Record.open(home.record_root).snapshot().imports()) == 8
    bad[0].unlink()
    tick(home, 50)
    assert len(read) == 8 and len(Record.open(home.record_root).snapshot().imports()) == 7


def _filing(home):
    from atlas.librarian import HostLlm
    from harness.fake_llm import FakeLlm
    llm = FakeLlm({"atlas_file": lambda call: {
        "intent": "Plan a gift.", "outcome": "Listed ideas.", "pursuit": "p-4k7q", "new_pursuit": None,
        "one_off_area": None, "rule": None, "advances": True, "left_off": [], "step_done": None, "next_step": None,
        "kind": None, "ending": None}})
    return llm, Atlas.open(home.record_root, llm=HostLlm(type("Ctx", (), {"llm": llm})()))


def _filed_calls(llm) -> int:
    return sum(c["task"] == "atlas_file" for c in llm.calls)


def test_web_chat_sessions_ending_between_whole_minutes_leave_the_filing_queue_once_filed(home):
    base = gpt("birthday gift ideas")
    flood = tuple(replace(base, start=base.start - timedelta(days=2) + timedelta(minutes=n, seconds=37))
                  for n in range(45))
    drop(home, flood)
    tick(home)
    queued = len(filing.queue(Record.open(home.record_root).snapshot(), NOW))
    assert queued >= len(flood)
    llm, atlas = _filing(home)
    for n in range(1, 4):
        atlas.tick(NOW + timedelta(minutes=10 * n))
        left = len(filing.queue(Record.open(home.record_root).snapshot(), NOW + timedelta(minutes=10 * n)))
        assert left == max(queued - filing.BUDGET * n, 0)
    calls = _filed_calls(llm)
    assert calls == queued
    atlas.tick(NOW + timedelta(minutes=40))
    assert _filed_calls(llm) == calls


def test_an_export_dropped_while_paused_is_imported_filed_and_shown_in_settings_with_no_brief_sent(home):
    record = Record.open(home.record_root)
    record.commit(record.snapshot().changes().put_pauses((Pause(NOW - timedelta(days=1), NOW + timedelta(days=2), ""),)))
    gift = gpt("birthday gift ideas")
    path = drop(home, (gift,))
    _, atlas = _filing(home)
    report = atlas.tick(NOW)
    assert (report.collected, report.filed, report.sent) == (1, 1, ())
    assert not path.exists()
    snap = Record.open(home.record_root).snapshot()
    assert seeds.evidence_id(gift) in {m.evidence for m in snap.pursuit("p-4k7q").sessions}
    assert settings_rules.added(snap.settings(), snap.imports())[0] == (
        "ChatGPT", "read 1 Session from the export of Wed 18 Nov and deleted it; waiting for the next one in ~/atlas/inbox")
