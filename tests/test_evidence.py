"""The evidence codec and ChangeSet.put_session: session.md plus verbatim raw/ parts."""
import errno
import hashlib
import io
import os
import random
import shutil
from collections import Counter
from functools import cache
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import zstandard

from atlas.model import (LOCAL, Forgotten, HarnessId, Inference, RawPart, SessionCard, SessionEvidence, Turn,
                         evidence_id, part_file, part_origin, skip_key)
from atlas.record import Blocked, Conflict, HeldPart, Record, _codec, _in_order, _paired, _txn
from test_commit import BERLIN, PROFILE, SETTINGS

T0 = datetime(2026, 11, 16, 23, 50, tzinfo=BERLIN)
SECRET = "sk-proj-" + "Ab3dE5gH7jK9mN1pQ3sT5vX7zB9dF1hJ"


def session(*turns: Turn, parts=("main",), native="thread-1") -> SessionEvidence:
    """parts: part keys, each copied from this Session's own store unit unless it is a T3 Code child's."""
    def origin(key):
        return part_origin(HarnessId.CODEX, key.split(":", 1)[1], "main") if key.startswith("t3-child:") else (
            part_origin(HarnessId.CODEX, native, key))
    return SessionEvidence(evidence_id(HarnessId.CODEX, LOCAL, native), HarnessId.CODEX, "T3 Code", LOCAL,
                           native, "invoice ids", T0, T0 + timedelta(minutes=30), "box", "~/work/payments-api",
                           "main", tuple(RawPart(part_file(key), "", f"~/.codex/{native}/{key}", f"10@{n}", origin(key))
                                         for n, key in enumerate(parts)), turns)


def put(root, s, raw, alone=False):
    record = Record.open(root)
    snap = record.snapshot()
    record.commit(snap.changes().put_session(s, raw, alone=alone))
    return record.snapshot()


def raw_file(root, s, name) -> bytes:
    return next(root.glob(f"evidence/codex/*/*/{s.id}/raw/{name}")).read_bytes()


def unzstd(data: bytes) -> bytes:
    return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data), read_across_frames=True).read()


def test_the_codec_round_trips_parts_flags_and_days():
    s = session(Turn("owner", T0, "**not a heading**\n\\backslash\n\nsecond paragraph", "main"),
                Turn("agent", T0 + timedelta(minutes=20), "after midnight", "subagent:abc"),
                Turn("owner", T0 + timedelta(minutes=21), "atlas, note it", "main", atlas_tool_turn=True),
                Turn("agent", None, "undated", "main"))
    text = _codec.render("session", s, BERLIN)
    assert b"**Codex \xc2\xb7 Tue 17 Nov 2026 00:10 \xc2\xb7 subagent:abc**" in text
    assert _codec.parse("session", text, BERLIN) == s


def test_secrets_are_masked_in_session_md_and_raw_stays_verbatim(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    s = session(Turn("owner", T0, f"try {SECRET}", "main"))
    raw = {"main.jsonl.zst": f'{{"text": "try {SECRET}"}}\n'.encode()}
    snap = put(tmp_path, s, raw)
    md = next(tmp_path.glob("evidence/codex/2026/11/*/session.md")).read_text()
    assert SECRET not in md and "try [secret: api key]" in md
    assert unzstd(raw_file(tmp_path, s, "main.jsonl.zst")) == raw["main.jsonl.zst"]
    assert snap.session(s.id).turns[0].text == "try [secret: api key]"


def test_a_part_grows_by_new_frames_and_a_copy_that_drops_a_line_conflicts(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    first = session(Turn("owner", T0, "one", "main"))
    put(tmp_path, first, {"main.jsonl.zst": b"line 1\n"})
    held = raw_file(tmp_path, first, "main.jsonl.zst")
    snap = put(tmp_path, first, {"main.jsonl.zst": b"line 1\nline 2\n"})
    grown = raw_file(tmp_path, first, "main.jsonl.zst")
    assert grown.startswith(held) and unzstd(grown) == b"line 1\nline 2\n"
    assert snap.session(first.id).parts[0].sha256 == hashlib.sha256(b"line 1\nline 2\n").hexdigest()
    assert snap.copy(part_origin(HarnessId.CODEX, "thread-1", "main")) == b"line 1\nline 2\n"
    cs = snap.changes()
    with pytest.raises(Conflict):
        cs.put_session(first, {"main.jsonl.zst": b"line 2 rewritten\nline 1\n"})
    assert cs.is_empty()


def test_putting_the_same_session_twice_changes_nothing(tmp_path):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    s = session(Turn("owner", T0 + timedelta(seconds=37), "one", "main"))
    head = put(tmp_path, s, {"main.jsonl.zst": b"x\n"}).revision.head
    assert put(tmp_path, s, {"main.jsonl.zst": b"x\n"}).revision.head == head


def test_parts_not_put_again_are_kept_with_their_turns(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    both = session(Turn("owner", T0, "one", "main"), Turn("agent", T0 + timedelta(minutes=1), "sub", "subagent:x"),
                   parts=("main", "subagent:x"))
    put(tmp_path, both, {"main.jsonl.zst": b"a\n", "subagent-x.jsonl.zst": b"b\n"})
    grown = replace(session(Turn("owner", T0, "one", "main"), Turn("owner", T0 + timedelta(minutes=5), "two", "main")),
                    last_at=T0 + timedelta(minutes=40))
    held = put(tmp_path, grown, {"main.jsonl.zst": b"a\nc\n"}).session(both.id)
    assert [p.name for p in held.parts] == ["main.jsonl.zst", "subagent-x.jsonl.zst"]
    assert [t.text for t in held.turns] == ["one", "sub", "two"]


def test_a_turn_read_with_its_held_twin_keeps_that_place_and_a_new_part_goes_after_its_minute(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    asked = Turn("owner", T0 + timedelta(seconds=50), "asked", "main")
    held = Turn("agent", T0 + timedelta(seconds=55), "held sub", "subagent:x")
    put(tmp_path, session(asked, held, parts=("main", "subagent:x")),
        {"main.jsonl.zst": b"a\n", "subagent-x.jsonl.zst": b"b\n"})
    both = session(Turn("agent", T0 + timedelta(seconds=10), "child", "t3-child:y"), asked,
                   parts=("main", "t3-child:y"))
    raw = {"main.jsonl.zst": b"a\n", "t3-child-y.jsonl.zst": b"c\n"}
    for _ in range(3):
        assert [t.text for t in put(tmp_path, both, raw).session(both.id).turns] == ["asked", "held sub", "child"]


def test_turns_session_md_shows_keep_their_order_when_every_part_is_read_again(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    first, second = (Turn("owner", T0 + timedelta(seconds=30), "M1", "main"),
                     Turn("agent", T0 + timedelta(seconds=50), "M2", "main"))
    late = Turn("agent", T0 + timedelta(seconds=5), "S1", "t3-child:y")
    put(tmp_path, session(first, second), {"main.jsonl.zst": b"a\n"})
    put(tmp_path, session(late, parts=("t3-child:y",)), {"t3-child-y.jsonl.zst": b"c\n"})
    every = session(late, first, second, parts=("main", "t3-child:y"))
    for _ in range(3):
        held = put(tmp_path, every, {"main.jsonl.zst": b"a\nb\n", "t3-child-y.jsonl.zst": b"c\nd\n"})
        assert [t.text for t in held.session(every.id).turns] == ["M1", "M2", "S1"]


@pytest.mark.parametrize("part", ["main", "subagent:invented-child"])
@pytest.mark.parametrize("undated", [False, True], ids=["same minute", "undated"])
def test_same_role_turns_in_one_minute_keep_their_places_when_one_part_is_read_again(tmp_path, part, undated):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    original = tuple(Turn("agent", None if undated else T0 + timedelta(seconds=n * 10), f"invented turn {n}",
                          "main" if n % 2 == 0 else "subagent:invented-child") for n in range(6))
    both = session(*original, parts=("main", "subagent:invented-child"))
    raw = {"main.jsonl.zst": b"invented main\n", "subagent-invented-child.jsonl.zst": b"invented child\n"}
    put(tmp_path, both, raw)
    read = session(*(t for t in original if t.part == part), parts=(part,))
    for _ in range(3):
        held = put(tmp_path, read, {p.name: raw[p.name] for p in read.parts})
        assert [t.text for t in held.session(both.id).turns] == [t.text for t in original]


def test_a_turn_carrying_a_secret_keeps_its_place_when_its_part_is_read_again(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    keyed = Turn("owner", T0 + timedelta(seconds=10), f"invented try {SECRET}", "main")
    sub = Turn("agent", T0 + timedelta(seconds=30), "invented sub", "subagent:invented-child")
    answer = Turn("agent", T0 + timedelta(seconds=50), "invented answer", "main")
    raw = {"main.jsonl.zst": b"invented main\n", "subagent-invented-child.jsonl.zst": b"invented child\n"}
    put(tmp_path, session(keyed, sub, answer, parts=("main", "subagent:invented-child")), raw)
    main = session(keyed, answer)
    for _ in range(3):
        held = put(tmp_path, main, {"main.jsonl.zst": raw["main.jsonl.zst"]}).session(main.id)
        assert [t.text for t in held.turns] == ["invented try [secret: api key]", "invented sub", "invented answer"]


def test_a_twin_read_after_a_held_turn_goes_in_after_the_held_turns_of_its_minute(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    ok = Turn("agent", T0 + timedelta(seconds=10), "invented ok", "main")
    sub = Turn("agent", T0 + timedelta(seconds=30), "invented sub", "subagent:invented-child")
    raw = {"main.jsonl.zst": b"invented main\n", "subagent-invented-child.jsonl.zst": b"invented child\n"}
    put(tmp_path, session(ok, sub, parts=("main", "subagent:invented-child")), raw)
    main = session(ok, replace(ok, at=T0 + timedelta(seconds=50)))
    held = put(tmp_path, main, {"main.jsonl.zst": b"invented main\ninvented again\n"}).session(main.id)
    assert [(t.part, t.text) for t in held.turns] == [("main", "invented ok"), ("subagent:invented-child", "invented sub"),
                                                     ("main", "invented ok")]


def test_pairing_finds_a_longest_common_subsequence():
    rng = random.Random(59)
    for _ in range(2000):
        held, read = ([rng.choice("abc") for _ in range(rng.randint(0, 7))] for _ in range(2))
        @cache
        def longest(i, j):
            if i == len(held) or j == len(read):
                return 0
            return longest(i + 1, j + 1) + 1 if held[i] == read[j] else max(longest(i + 1, j), longest(i, j + 1))
        pairs = _paired(held, read)
        assert len(pairs) == longest(0, 0) and all(held[a] == read[b] for a, b in pairs)
        assert all(a < c and b < d for (a, b), (c, d) in zip(pairs, pairs[1:]))


def test_turn_order_loses_no_turn_and_keeps_each_parts_and_the_held_order_once_and_for_all():
    rng = random.Random(59)
    parts = ("main", "subagent:invented-one", "subagent:invented-two")
    files = {part_file(p) for p in parts}
    def shown(turns):
        return _codec.shown(session(parts=parts), turns, BERLIN)
    def keys(turns):
        return [(t.part, t.role, t.text) for t in turns]
    for case in range(400):
        turns = sorted((Turn(rng.choice(("owner", "agent")),
                             None if rng.random() < 0.05 else T0 + timedelta(seconds=rng.randint(0, 150)),
                             f"invented {case} {n}", rng.choice(parts)) for n in range(rng.randint(1, 10))),
                       key=lambda t: (t.at is None, t.at or T0))
        first = [t for t in turns if rng.random() < 0.7]
        held = shown(_in_order((), first, shown(first), files))
        again = {f for f in files if rng.random() < 0.5} or {rng.choice(sorted(files))}
        read = [t for t in turns if part_file(t.part) in again]
        actual = _in_order(held, read, shown(read), again)
        kept = [t for t in held if part_file(t.part) not in again]
        assert Counter(keys(actual)) == Counter(keys(kept) + keys(read))
        for p in parts:
            assert keys(t for t in actual if t.part == p) == keys(t for t in [*kept, *read] if t.part == p)
        still = {t.text for t in held} & {t.text for t in actual}
        assert [t.text for t in actual if t.text in still] == [t.text for t in held if t.text in still]
        assert keys(_in_order(shown(actual), read, shown(read), again)) == keys(actual)


def test_the_record_holds_each_part_by_its_origin(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    s = session(Turn("owner", T0, "one", "main"), parts=("main", "t3-child:y"))
    held = put(tmp_path, s, {"main.jsonl.zst": b"a", "t3-child-y.jsonl.zst": b"c"}).held()
    assert held.get(part_origin(HarnessId.CODEX, "thread-1", "main")) == HeldPart(s.id, "main.jsonl.zst", "10@0")
    assert held.get(part_origin(HarnessId.CODEX, "y", "main")) == HeldPart(s.id, "t3-child-y.jsonl.zst", "10@1")
    assert held.get(part_origin(HarnessId.CODEX, "thread-2", "main")) is None
    assert [(h.id, h.interface) for h in held.sessions()] == [(s.id, "T3 Code")]


def test_a_part_put_again_shows_the_turns_put_once_whatever_session_md_masked(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    first = session(Turn("owner", T0, f"try {SECRET}", "main"))
    put(tmp_path, first, {"main.jsonl.zst": b"a\n"})
    copy = replace(session(Turn("owner", T0, f"try {SECRET}", "main"), Turn("owner", T0 + timedelta(minutes=9), "two",
                                                                          "main")),
                   parts=(replace(first.parts[0], source="~/orca/thread-1/main", fingerprint="20@0"),))
    held = put(tmp_path, copy, {"main.jsonl.zst": b"a\nb\n"}).session(first.id)
    assert [t.text for t in held.turns] == ["try [secret: api key]", "two"]
    assert [(p.source, p.fingerprint) for p in held.parts] == [("~/orca/thread-1/main", "20@0")]


def test_a_session_folded_in_moves_every_part_it_holds(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    child = session(Turn("owner", T0 + timedelta(minutes=2), "check the totals", "main"),
                    Turn("agent", T0 + timedelta(minutes=3), "a nested check", "t3-child:x"),
                    Turn("agent", T0 + timedelta(minutes=4), "a subagent's note", "subagent:s"),
                    parts=("main", "t3-child:x", "subagent:s"), native="child")
    put(tmp_path, child, {"main.jsonl.zst": b"child\n", "t3-child-x.jsonl.zst": b"x\n", "subagent-s.jsonl.zst": b"s\n"})
    top = session(Turn("owner", T0, "plan the release", "main"))
    record = Record.open(tmp_path)
    record.commit(record.snapshot().changes().put_session(top, {"main.jsonl.zst": b"top\n"}, {child.id: "t3-child:child"}))
    held = record.snapshot()
    assert [s.id for s in held.sessions()] == [top.id]
    assert [(t.role, t.text, t.part) for t in held.session(top.id).turns] == [
        ("owner", "plan the release", "main"), ("agent", "check the totals", "t3-child:child"),
        ("agent", "a nested check", "t3-child:x"), ("agent", "a subagent's note", "t3-child:child/subagent:s")]
    assert {p.name: unzstd(raw_file(tmp_path, top, p.name)) for p in held.session(top.id).parts} == {
        "main.jsonl.zst": b"top\n", "t3-child-child.jsonl.zst": b"child\n", "t3-child-x.jsonl.zst": b"x\n",
        "t3-child-child-subagent-s.jsonl.zst": b"s\n"}


@pytest.mark.parametrize("forgotten", ["thread-1", "child"])
def test_a_put_planned_before_a_forget_of_its_session_conflicts(tmp_path, forgotten):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    planned = record.snapshot().changes().put_session(
        session(Turn("owner", T0, "one", "main")), {"main.jsonl.zst": b"a"},
        skip_keys=frozenset({skip_key(HarnessId.CODEX, "child")}))
    record.commit(record.snapshot().changes().put_forgotten(Forgotten(((T0, "a Session"),), frozenset({
        skip_key(HarnessId.CODEX, forgotten)}))))
    with pytest.raises(Conflict):
        record.commit(planned)


def test_a_put_planned_before_a_forget_of_another_session_commits(tmp_path):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    s = session(Turn("owner", T0, "one", "main"))
    planned = record.snapshot().changes().put_session(s, {"main.jsonl.zst": b"a"})
    record.commit(record.snapshot().changes().put_forgotten(Forgotten(((T0, "a Session"),), frozenset({
        skip_key(HarnessId.CODEX, "thread-2")}))))
    record.commit(planned)
    assert [h.id for h in record.snapshot().held().sessions()] == [s.id]


def test_a_session_held_inside_another_is_put_there_when_it_is_read_alone(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    top = session(Turn("owner", T0, "plan the release", "main"),
                  Turn("agent", T0 + timedelta(minutes=2), "check", "t3-child:x"), parts=("main", "t3-child:x"))
    put(tmp_path, top, {"main.jsonl.zst": b"top\n", "t3-child-x.jsonl.zst": b"x\n"})
    alone = session(Turn("owner", T0 + timedelta(minutes=2), "check", "main"),
                    Turn("owner", T0 + timedelta(minutes=8), "check the totals too", "main"), native="x")
    held = put(tmp_path, alone, {"main.jsonl.zst": b"x\nmore\n"}, alone=True)
    assert [h.id for h in held.held().sessions()] == [top.id]
    assert [(t.role, t.text, t.part) for t in held.session(top.id).turns] == [
        ("owner", "plan the release", "main"), ("agent", "check", "t3-child:x"),
        ("agent", "check the totals too", "t3-child:x")]
    assert held.session(top.id).title == top.title
    assert unzstd(raw_file(tmp_path, top, "t3-child-x.jsonl.zst")) == b"x\nmore\n"


def test_a_session_read_alone_whose_home_is_gone_conflicts(tmp_path):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    alone = session(Turn("owner", T0, "check", "main"), native="x")
    with pytest.raises(Conflict):
        record.snapshot().changes().put_session(alone, {"main.jsonl.zst": b"x\n"}, alone=True)


def test_a_session_put_as_its_own_leaves_the_session_that_held_it_folded(tmp_path):
    Record.open(tmp_path).create(SETTINGS, PROFILE)
    top = session(Turn("owner", T0, "plan the release", "main"),
                  Turn("agent", T0 + timedelta(minutes=40), "check", "t3-child:x"), parts=("main", "t3-child:x"))
    put(tmp_path, top, {"main.jsonl.zst": b"top\n", "t3-child-x.jsonl.zst": b"x\n"})
    fork = session(Turn("owner", T0 + timedelta(minutes=40), "check", "main"),
                   Turn("owner", T0 + timedelta(minutes=48), "check the totals too", "main"), native="x")
    held = put(tmp_path, fork, {"main.jsonl.zst": b"x\nmore\n"})
    assert {s.id: [(t.role, t.text) for t in s.turns] for s in held.sessions()} == {
        top.id: [("owner", "plan the release")],
        fork.id: [("owner", "check"), ("owner", "check the totals too")]}
    assert [p.name for p in held.session(top.id).parts] == ["main.jsonl.zst"]
    assert held.session(top.id).last_at == T0
    assert not list(tmp_path.glob(f"evidence/codex/*/*/{top.id}/raw/t3-child-x.jsonl.zst"))
    assert unzstd(raw_file(tmp_path, fork, "main.jsonl.zst")) == b"x\nmore\n"


def test_a_put_that_conflicts_leaves_the_others_of_its_changeset(tmp_path):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    kept = session(Turn("owner", T0, "one", "main"))
    put(tmp_path, kept, {"main.jsonl.zst": b"a\n"})
    other = session(Turn("owner", T0, "two", "main"), native="thread-2")
    cs = record.snapshot().changes().put_session(other, {"main.jsonl.zst": b"b\n"})
    with pytest.raises(Conflict):
        cs.put_session(kept, {"main.jsonl.zst": b"c\n"})
    record.commit(cs)
    held = {s.id: [t.text for t in s.turns] for s in record.snapshot().sessions()}
    assert held == {kept.id: ["one"], other.id: ["two"]}


def test_a_put_that_conflicts_after_folding_a_session_in_leaves_the_folded_session(tmp_path):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    folded = carded(tmp_path)
    top = session(Turn("owner", T0, "plan the release", "main"), native="top")
    put(tmp_path, top, {"main.jsonl.zst": b"top\n"})
    cs = record.snapshot().changes()
    with pytest.raises(Conflict):
        cs.put_session(top, {"main.jsonl.zst": b"rewritten\n"}, {folded.id: "t3-child:invented"})
    other = session(Turn("owner", T0, "two", "main"), native="thread-2")
    record.commit(cs.put_session(other, {"main.jsonl.zst": b"b\n"}))
    assert sorted(s.native_key for s in record.snapshot().sessions()) == ["thread-1", "thread-2", "top"]
    assert record.snapshot().card(folded.id) is not None


@pytest.mark.parametrize("where", ["folder", "raw", "mirror"])
def test_a_commit_a_link_stands_in_the_way_of_names_the_session_folder_a_later_put_avoids(tmp_path, where):
    root, outside = tmp_path / "record", tmp_path / "outside"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    s = carded(root)
    folder = next(root.glob(f"evidence/codex/*/*/{s.id}"))
    stand_in({"folder": folder, "raw": folder / "raw",
              "mirror": root / ".atlas/last-written" / folder.relative_to(root)}[where], outside)
    kept = files_in(outside)
    with pytest.raises(Blocked) as blocked:
        record.commit(grown(record, s))
    assert blocked.value.args == (f"{folder.relative_to(root)}/",)
    cs = record.snapshot().changes().avoid(*blocked.value.args)
    with pytest.raises(Blocked):
        cs.put_session(session(Turn("owner", T0, "one grown", "main")), {"main.jsonl.zst": b"a\nb\n"})
    other = session(Turn("owner", T0, "two", "main"), native="thread-2")
    record.commit(cs.put_session(other, {"main.jsonl.zst": b"b\n"}))
    assert files_in(outside) == kept
    assert other.id in {h.id for h in record.snapshot().held().sessions()}


def test_removed_evidence_leaves_no_file_or_folder(tmp_path):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    s = session(Turn("owner", T0, "one", "main"))
    snap = put(tmp_path, s, {"main.jsonl.zst": b"a"})
    record.commit(snap.changes().remove_evidence(s.id))
    assert not list(tmp_path.glob("evidence/**/*"))
    assert record.snapshot().sessions() == []


@pytest.mark.parametrize("stray", [".DS_Store", "card.md~", "raw/.DS_Store", "notes/invented.txt"])
def test_a_removal_deletes_a_file_atlas_did_not_write_in_the_folder(tmp_path, stray):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    gone = carded(tmp_path)
    folder = next(tmp_path.glob(f"evidence/codex/*/*/{gone.id}"))
    (folder / stray).parent.mkdir(exist_ok=True)
    (folder / stray).write_bytes(b"invented stray bytes")
    remove = record.snapshot().changes().remove_evidence(gone.id)
    (folder / stray).write_bytes(b"invented bytes saved after the plan")
    record.commit(remove)
    assert [p for p in tmp_path.rglob("*") if gone.id in str(p)] == []


@pytest.mark.parametrize("to_folder", [True, False], ids=["folder", "file"])
def test_a_removal_never_deletes_what_a_link_in_the_folder_points_to(tmp_path, to_folder):
    root, outside = tmp_path / "record", tmp_path / "outside"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    gone = carded(root)
    outside.mkdir()
    (outside / "kept.txt").write_bytes(b"invented")
    target = outside if to_folder else outside / "kept.txt"
    next(root.glob(f"evidence/codex/*/*/{gone.id}")).joinpath("link").symlink_to(target, target_is_directory=to_folder)
    record.commit(record.snapshot().changes().remove_evidence(gone.id))
    assert (outside / "kept.txt").read_bytes() == b"invented"
    assert record.snapshot().sessions() == []


def removal(record, gone, fold):
    """A Forget of `gone`, or a put of a new Session that folds it in."""
    cs = record.snapshot().changes()
    if not fold:
        return cs.remove_evidence(gone.id)
    top = session(Turn("owner", T0, "plan the release", "main"), native="top")
    return cs.put_session(top, {"main.jsonl.zst": b"top\n"}, {gone.id: "t3-child:invented"})


def files_in(folder):
    return {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("fold", [False, True], ids=["forget", "fold"])
@pytest.mark.parametrize("planned", [False, True], ids=["linked-before-the-plan", "linked-after-it"])
@pytest.mark.parametrize("where", ["folder", "raw", "mirror"])
def test_a_removal_never_reaches_through_a_link_standing_for_a_folder(tmp_path, where, planned, fold):
    root, outside = tmp_path / "record", tmp_path / "outside"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    gone = carded(root)
    folder = next(root.glob(f"evidence/codex/*/*/{gone.id}"))
    moved = {"folder": folder, "raw": folder / "raw", "mirror": root / ".atlas/last-written" / folder.relative_to(root)}
    cs = removal(record, gone, fold) if planned else None
    moved[where].rename(outside)
    moved[where].symlink_to(outside, target_is_directory=True)
    kept = files_in(outside)
    try:
        record.commit(cs or removal(record, gone, fold))
    except Conflict:
        assert where != "raw" or planned
    assert files_in(outside) == kept
    if where == "raw" and not planned:
        assert gone.id not in {h.id for h in record.snapshot().held().sessions()}


@pytest.mark.parametrize("fold", [False, True], ids=["forget", "fold"])
def test_a_removal_takes_away_a_link_named_like_a_raw_part_without_reading_what_it_points_to(tmp_path, fold):
    root, outside = tmp_path / "record", tmp_path / "outside.zst"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    gone = carded(root)
    part = next(root.glob(f"evidence/codex/*/*/{gone.id}/raw/main.jsonl.zst"))
    part.rename(outside)
    part.symlink_to(outside)
    outside.chmod(0)
    record.commit(removal(record, gone, fold))
    outside.chmod(0o600)
    assert unzstd(outside.read_bytes()) == b"a\n"
    assert gone.id not in {h.id for h in record.snapshot().held().sessions()}


def hand_edit(file) -> bytes:
    file.write_bytes(file.read_bytes() + b"\ninvented hand edit\n")
    return file.read_bytes()


def edit_while_staged(monkeypatch, file) -> list[bytes]:
    """Edits `file` by hand once the commit has compared it, as its transaction is staged."""
    edited, stage = [], _txn.stage

    def staged(*args, **kwargs):
        if not edited:
            edited.append(hand_edit(file))
        return stage(*args, **kwargs)
    monkeypatch.setattr(_txn, "stage", staged)
    return edited


@pytest.mark.parametrize("when", ["before-the-plan", "after-the-plan", "after-the-compare"])
def test_a_fold_keeps_a_session_whose_card_was_edited_by_hand_as_its_own(tmp_path, monkeypatch, caplog, when):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    gone = carded(tmp_path)
    card = next(tmp_path.glob(f"evidence/codex/*/*/{gone.id}/card.md"))
    edited = [hand_edit(card)] if when == "before-the-plan" else []
    cs = removal(record, gone, fold=True)
    if when == "after-the-plan":
        edited.append(hand_edit(card))
    if when != "before-the-plan":
        with monkeypatch.context() as m, pytest.raises(Conflict):
            if when == "after-the-compare":
                edited = edit_while_staged(m, card)
            record.commit(cs)
        assert not list((tmp_path / ".atlas/txn").iterdir())
        cs = removal(record, gone, fold=True)
    record.commit(cs)
    assert card.read_bytes() == edited[0]
    assert list(tmp_path.glob(f".atlas/last-written/evidence/codex/*/*/{gone.id}/card.md"))
    assert {s.native_key for s in record.snapshot().sessions()} == {"thread-1", "top"}
    assert "Atlas kept a folded Session as its own" in caplog.text and "invented" not in caplog.text


def test_a_fold_regenerates_a_session_md_edited_by_hand(tmp_path):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    gone = carded(tmp_path)
    hand_edit(next(tmp_path.glob(f"evidence/codex/*/*/{gone.id}/session.md")))
    record.commit(removal(record, gone, fold=True))
    assert [p for p in tmp_path.rglob("*") if gone.id in str(p)] == []
    assert [s.native_key for s in record.snapshot().sessions()] == ["top"]


def test_a_fold_a_crash_left_ready_is_dropped_when_its_card_is_edited_by_hand_before_the_replay(tmp_path, monkeypatch):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    gone = carded(tmp_path)
    card = next(tmp_path.glob(f"evidence/codex/*/*/{gone.id}/card.md"))

    def interrupted(root, txn):
        raise KeyboardInterrupt
    with monkeypatch.context() as m, pytest.raises(KeyboardInterrupt):
        m.setattr(_txn, "apply", interrupted)
        record.commit(removal(record, gone, fold=True))
    edited = hand_edit(card)
    reopened = Record.open(tmp_path)
    assert card.read_bytes() == edited
    assert [s.native_key for s in reopened.snapshot().sessions()] == ["thread-1"]


def card_for(record, s):
    record.commit(record.snapshot().changes().put_card(SessionCard(
        s.id, Inference("Invented intent.", (s.id,)), Inference("Invented outcome.", (s.id,)), None, None)))


@pytest.mark.parametrize("edited", [False, True], ids=["sealed", "edited-by-hand"])
def test_a_fold_deletes_a_card_written_after_its_plan_only_while_the_card_holds_its_seal(tmp_path, edited):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    gone = session(Turn("owner", T0, "one", "main"))
    put(tmp_path, gone, {"main.jsonl.zst": b"a\n"})
    cs = removal(record, gone, fold=True)
    card_for(record, gone)
    card = next(tmp_path.glob(f"evidence/codex/*/*/{gone.id}/card.md"))
    if edited:
        before = hand_edit(card)
        with pytest.raises(Conflict):
            record.commit(cs)
        assert card.read_bytes() == before
        assert [s.native_key for s in record.snapshot().sessions()] == ["thread-1"]
    else:
        record.commit(cs)
        assert [p for p in tmp_path.rglob("*") if gone.id in str(p)] == []
        assert [s.native_key for s in record.snapshot().sessions()] == ["top"]


def two_carded(root):
    """A record with two carded Sessions, and a put of a new Session that folds both in."""
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    one = carded(root)
    two = session(Turn("owner", T0, "two", "main"), native="thread-2")
    put(root, two, {"main.jsonl.zst": b"b\n"})
    card_for(record, two)
    top = session(Turn("owner", T0, "plan the release", "main"), native="top")
    return record, record.snapshot().changes().put_session(
        top, {"main.jsonl.zst": b"top\n"}, {one.id: "t3-child:one", two.id: "t3-child:two"})


def test_a_fold_a_crash_cut_short_after_its_first_card_went_is_finished_by_the_replay(tmp_path, monkeypatch):
    record, cs = two_carded(tmp_path)
    first, second = sorted(tmp_path.glob("evidence/codex/*/*/*/card.md"))
    unlink = _txn._unlink

    def cut_short(root, target):
        unlink(root, target)
        if target == str(first.relative_to(root)):
            raise KeyboardInterrupt
    with monkeypatch.context() as m, pytest.raises(KeyboardInterrupt):
        m.setattr(_txn, "_unlink", cut_short)
        record.commit(cs)
    assert not first.exists()
    hand_edit(second)
    reopened = Record.open(tmp_path)
    assert list(tmp_path.glob("**/card.md")) == []
    assert [s.native_key for s in reopened.snapshot().sessions()] == ["top"]
    assert not list((tmp_path / ".atlas/txn").iterdir())


@pytest.mark.parametrize("name", ["session.md", "card.md"])
@pytest.mark.parametrize("when", ["before-the-plan", "after-the-compare"])
def test_a_forget_removes_a_session_md_or_card_md_edited_by_hand(tmp_path, monkeypatch, name, when):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    gone = carded(tmp_path)
    file = next(tmp_path.glob(f"evidence/codex/*/*/{gone.id}")) / name
    if when == "before-the-plan":
        hand_edit(file)
    cs = removal(record, gone, fold=False)
    if when == "after-the-compare":
        edit_while_staged(monkeypatch, file)
    record.commit(cs)
    assert [p for p in tmp_path.rglob("*") if gone.id in str(p)] == []
    assert record.snapshot().sessions() == []


def test_a_removal_leaves_a_file_whose_folder_became_a_link_after_staging(tmp_path, monkeypatch, caplog):
    root, outside = tmp_path / "record", tmp_path / "outside"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    gone = carded(root)
    nested = next(root.glob(f"evidence/codex/*/*/{gone.id}")) / "notes"
    nested.mkdir()
    (nested / "invented.txt").write_bytes(b"invented stray bytes")
    outside.mkdir()
    (outside / "invented.txt").write_bytes(b"invented outside bytes")
    remove = record.snapshot().changes().remove_evidence(gone.id)
    stage = _txn.stage

    def swapped(root, files, *guards):
        txn = stage(root, files, *guards)
        nested.rename(tmp_path / "moved")
        nested.symlink_to(outside, target_is_directory=True)
        return txn

    monkeypatch.setattr(_txn, "stage", swapped)
    record.commit(remove)
    assert (outside / "invented.txt").read_bytes() == b"invented outside bytes"
    assert "a link stands where its folder was" in caplog.text


def test_a_removal_never_opens_a_folder_a_link_took_after_staging(tmp_path, monkeypatch):
    root, outside = tmp_path / "record", tmp_path / "outside"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    gone = carded(root)
    nested = next(root.glob(f"evidence/codex/*/*/{gone.id}")) / "notes"
    nested.mkdir()
    (nested / "invented.txt").write_bytes(b"invented stray bytes")
    outside.mkdir()
    remove = record.snapshot().changes().remove_evidence(gone.id)
    stage = _txn.stage

    def swapped(root, files, *guards):
        txn = stage(root, files, *guards)
        nested.rename(tmp_path / "moved")
        nested.symlink_to(outside, target_is_directory=True)
        outside.chmod(0o111)
        return txn

    monkeypatch.setattr(_txn, "stage", swapped)
    try:
        record.commit(remove)
        Record.open(root)
    finally:
        outside.chmod(0o700)
    assert not list(root.glob(".atlas/txn/*.ready"))


@pytest.mark.parametrize("fold", [False, True], ids=["forget", "fold"])
@pytest.mark.parametrize("tree", ["evidence", ".atlas/last-written/evidence"])
def test_a_prune_never_reaches_another_sessions_folder_through_a_link_taken_after_staging(tmp_path, monkeypatch, tree,
                                                                                         fold):
    root = tmp_path / "record"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    gone, other = carded(root), session(Turn("owner", T0, "two", "main"), native="thread-2")
    put(root, other, {"main.jsonl.zst": b"a\n"})
    nested = next(root.glob(f"{tree}/codex/*/*/{gone.id}")) / "notes"
    (nested / "empty").mkdir(parents=True)
    (nested / "empty/invented.txt").write_bytes(b"invented stray bytes")
    kept = nested.parent.parent / other.id / "notes"
    (kept / "empty").mkdir(parents=True)
    cs = removal(record, gone, fold)
    stage = _txn.stage

    def swapped(root, files, *guards):
        txn = stage(root, files, *guards)
        nested.rename(tmp_path / "moved")
        nested.symlink_to(kept, target_is_directory=True)
        return txn

    monkeypatch.setattr(_txn, "stage", swapped)
    record.commit(cs)
    assert (kept / "empty").is_dir()


def grown(record, s):
    """A commit that writes session.md and the raw part of `s`, and its card and the card's mirror."""
    cs = record.snapshot().changes().put_session(session(Turn("owner", T0, "one grown", "main")),
                                                 {"main.jsonl.zst": b"a\nb\n"})
    return cs.put_card(SessionCard(s.id, Inference("Invented grown intent.", (s.id,)),
                                   Inference("Invented outcome.", (s.id,)), None, None))


def staging(monkeypatch) -> dict[str, bytes | None]:
    """Collects the files each commit stages."""
    files, stage = {}, _txn.stage

    def staged(root, changes, *guards):
        files.update(changes)
        return stage(root, changes, *guards)

    monkeypatch.setattr(_txn, "stage", staged)
    return files


def unwritten(root, files) -> list[str]:
    """The files of a commit that do not hold what it writes."""
    return [path for path, data in files.items() if data is not None and (root / path).read_bytes() != data]


def stand_in(folder, outside, node="link"):
    """Moves `folder` to `outside` and puts a link to it, or a file, in its place."""
    folder.rename(outside)
    if node == "link":
        folder.symlink_to(outside, target_is_directory=True)
    else:
        folder.write_bytes(b"invented stand-in")


def put_back(folder, outside):
    folder.unlink()
    outside.rename(folder)


def swap_on_write(monkeypatch, folder, swap):
    """Calls `swap` once, right before an apply moves the first file into `folder`."""
    replace_below, swaps = _txn._replace_below, []

    def replacing(root, staged, target):
        if not swaps and (root / target).is_relative_to(folder):
            swaps.append(target)
            swap()
        return replace_below(root, staged, target)

    monkeypatch.setattr(_txn, "_replace_below", replacing)


@pytest.mark.parametrize("crash", [False, True], ids=["applied", "replayed-after-a-crash"])
@pytest.mark.parametrize("node", ["link", "file"])
@pytest.mark.parametrize("where", ["folder", "raw", "mirror"])
def test_a_commit_a_link_takes_a_folder_of_while_it_applies_waits_for_it_to_go_and_never_writes_through_it(
        tmp_path, monkeypatch, caplog, where, node, crash):
    root, outside = tmp_path / "record", tmp_path / "outside"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    s = carded(root)
    folder = next(root.glob(f"evidence/codex/*/*/{s.id}"))
    linked = {"folder": folder, "raw": folder / "raw",
              "mirror": root / ".atlas/last-written" / folder.relative_to(root)}[where]
    kept, head = files_in(linked), record.snapshot().revision.head
    cs = grown(record, s)
    fsync, synced = os.fsync, []

    def swap():
        stand_in(linked, outside, node)
        if crash:
            raise KeyboardInterrupt

    with monkeypatch.context() as m:
        files = staging(m)
        swap_on_write(m, linked, swap)
        m.setattr(_txn.os, "fsync", lambda fd: (synced.append(inode(fd)), fsync(fd)))
        with pytest.raises(KeyboardInterrupt if crash else Conflict):
            record.commit(cs)
        Record.open(root)
    assert files_in(outside) == kept
    assert inode(outside) not in synced
    assert len(list(root.glob(".atlas/txn/*.ready"))) == 1
    assert "Atlas left a transaction ready: a link stands where a folder it writes in was" in caplog.text
    put_back(linked, outside)
    assert Record.open(root).snapshot().revision.head == head + 1
    assert unwritten(root, files) == []
    assert not list(root.glob(".atlas/txn/*"))


def test_a_replay_keeps_what_a_crash_left_unwritten_until_a_link_in_its_way_is_gone(tmp_path, monkeypatch):
    root, outside = tmp_path / "record", tmp_path / "outside"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    s = carded(root)
    raw = next(root.glob(f"evidence/codex/*/*/{s.id}/raw"))
    cs = grown(record, s)

    def crash():
        raise KeyboardInterrupt

    with monkeypatch.context() as m, pytest.raises(KeyboardInterrupt):
        files = staging(m)
        swap_on_write(m, raw, crash)
        record.commit(cs)
    stand_in(raw, outside)
    Record.open(root)
    assert unwritten(root, files) == [str(raw.relative_to(root) / "main.jsonl.zst")]
    put_back(raw, outside)
    Record.open(root)
    assert unwritten(root, files) == []


def test_a_fold_a_link_holds_up_keeps_the_folded_session_until_its_parts_reach_the_session_it_folds_into(
        tmp_path, monkeypatch, caplog):
    root, outside = tmp_path / "record", tmp_path / "outside"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    gone = carded(root)
    child = next(root.glob(f"evidence/codex/*/*/{gone.id}"))
    kept = files_in(child)
    top = child.parent / evidence_id(HarnessId.CODEX, LOCAL, "top") / "raw"

    def swap():
        top.parent.mkdir()
        outside.mkdir()
        top.symlink_to(outside, target_is_directory=True)

    with monkeypatch.context() as m, pytest.raises(Conflict):
        swap_on_write(m, top, swap)
        record.commit(removal(record, gone, fold=True))
    assert files_in(child) == kept and files_in(outside) == {}
    with pytest.raises(Conflict):
        record.commit(record.snapshot().changes().remove_evidence(gone.id))
    two = session(Turn("owner", T0, "two", "main"), native="thread-2")
    record.commit(record.snapshot().changes().put_session(two, {"main.jsonl.zst": b"b\n"}))
    assert caplog.text.count("Atlas left a transaction ready") == 1
    top.unlink()
    Record.open(root)
    assert not child.exists()
    assert sorted(s.native_key for s in record.snapshot().sessions()) == ["thread-2", "top"]
    assert sorted(path.name for path in top.iterdir()) == ["main.jsonl.zst", "t3-child-invented.jsonl.zst"]


def swap_on_open(monkeypatch, target, swap):
    """Calls `swap` once, right after an apply opens the folder it moves `target` into."""
    below, swaps = _txn.below, []

    def opening(root, path, make=False):
        opened = below(root, path, make)
        if make and not swaps and root / path == target:
            swaps.append(path)
            swap()
        return opened

    monkeypatch.setattr(_txn, "below", opening)


def held_up(record, cs, replayed):
    """Commits `cs`, which fails with Conflict, or when `replayed` lets a crash cut it short right
    before its apply and opens the record, which replays it and returns."""
    if not replayed:
        with pytest.raises(Conflict):
            record.commit(cs)
        return

    def interrupted(root, txn):
        raise KeyboardInterrupt
    with pytest.MonkeyPatch.context() as m, pytest.raises(KeyboardInterrupt):
        m.setattr(_txn, "apply", interrupted)
        record.commit(cs)
    Record.open(record.root)


@pytest.mark.parametrize("replayed", [False, True], ids=["applied", "replayed-after-a-crash"])
@pytest.mark.parametrize("where", ["folder", "raw", "mirror"])
def test_a_folder_a_link_takes_the_place_of_once_an_apply_opened_it_keeps_none_of_the_commits_bytes(
        tmp_path, monkeypatch, where, replayed):
    root, outside = tmp_path / "record", tmp_path / "outside"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    s = carded(root)
    folder = next(root.glob(f"evidence/codex/*/*/{s.id}"))
    linked, name = {"folder": (folder, "card.md"), "raw": (folder / "raw", "main.jsonl.zst"),
                    "mirror": (root / ".atlas/last-written" / folder.relative_to(root), "card.md")}[where]
    kept, head = files_in(linked), record.snapshot().revision.head
    with monkeypatch.context() as m:
        files = staging(m)
        swap_on_open(m, linked / name, lambda: stand_in(linked, outside))
        held_up(record, grown(record, s), replayed)
    assert files_in(outside).items() <= kept.items()
    assert len(list(root.glob(".atlas/txn/*.ready"))) == 1
    put_back(linked, outside)
    assert Record.open(root).snapshot().revision.head == head + 1
    assert unwritten(root, files) == []


@pytest.mark.parametrize("swapped", ["raw/t3-child-invented.jsonl.zst", "session.md"],
                         ids=["once-its-last-part-is-open", "once-every-part-is-written"])
def test_a_fold_keeps_the_folded_session_while_a_part_it_wrote_is_behind_a_link(tmp_path, monkeypatch, swapped):
    root, outside = tmp_path / "record", tmp_path / "outside"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    gone = carded(root)
    child = next(root.glob(f"evidence/codex/*/*/{gone.id}"))
    kept = files_in(child)
    top = child.parent / evidence_id(HarnessId.CODEX, LOCAL, "top")
    with monkeypatch.context() as m, pytest.raises(Conflict):
        swap_on_open(m, top / swapped, lambda: stand_in(top / "raw", outside))
        record.commit(removal(record, gone, fold=True))
    Record.open(root)
    assert files_in(child) == kept
    put_back(top / "raw", outside)
    Record.open(root)
    assert not child.exists()
    assert sorted(path.name for path in (top / "raw").iterdir()) == ["main.jsonl.zst", "t3-child-invented.jsonl.zst"]


def take_place(folder, outside):
    """Puts a link to `outside` in the place of the empty `folder`."""
    folder.rmdir()
    folder.symlink_to(outside, target_is_directory=True)


@pytest.mark.parametrize("replayed", [False, True], ids=["applied", "replayed-after-a-crash"])
def test_a_folder_an_apply_made_and_a_link_took_the_place_of_once_open_holds_the_commit_up(
        tmp_path, monkeypatch, replayed):
    root, outside = tmp_path / "record", tmp_path / "outside"
    outside.mkdir()
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    month = next(root.glob(f"evidence/codex/*/*/{carded(root).id}")).parent
    two = session(Turn("owner", T0, "two", "main"), native="thread-2")
    raw = month / two.id / "raw"
    with monkeypatch.context() as m:
        files = staging(m)
        swap_on_open(m, raw / "main.jsonl.zst", lambda: take_place(raw, outside))
        held_up(record, record.snapshot().changes().put_session(two, {"main.jsonl.zst": b"b\n"}), replayed)
    assert files_in(outside) == {}
    assert len(list(root.glob(".atlas/txn/*.ready"))) == 1
    raw.unlink()
    Record.open(root)
    assert unwritten(root, files) == []


def test_a_record_logs_once_that_commits_wait_however_many_links_hold_them_up(tmp_path, monkeypatch, caplog):
    root, outside = tmp_path / "record", tmp_path / "outside"
    outside.mkdir()
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    month = next(root.glob(f"evidence/codex/*/*/{carded(root).id}")).parent
    for native in ("thread-2", "thread-3"):
        s = session(Turn("owner", T0, native, "main"), native=native)
        raw = month / s.id / "raw"
        with monkeypatch.context() as m, pytest.raises(Conflict):
            swap_on_open(m, raw / "main.jsonl.zst", lambda: take_place(raw, outside))
            record.commit(record.snapshot().changes().put_session(s, {"main.jsonl.zst": b"a\n"}))
        record.commit(record.snapshot().changes())
    assert len(list(root.glob(".atlas/txn/*.ready"))) == 2
    assert caplog.text.count("Atlas left a transaction ready") == 1


@pytest.mark.parametrize("moved", ["record", "evidence", "evidence/codex", ".atlas/last-written"])
def test_a_record_or_folder_above_a_session_moved_and_linked_back_keeps_taking_writes(tmp_path, moved):
    root = tmp_path / "record"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    s = carded(root)
    folder = root if moved == "record" else root / moved
    folder.rename(tmp_path / "moved")
    folder.symlink_to(tmp_path / "moved", target_is_directory=True)
    record.commit(grown(record, s))
    snap = record.snapshot()
    assert snap.session(s.id).turns[0].text == "one grown"
    assert snap.card(s.id).intent.text == "Invented grown intent."


def test_an_apply_syncs_each_folder_it_wrote_in_once(tmp_path, monkeypatch):
    root = tmp_path / "record"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    s = carded(root)
    cs = grown(record, s)
    apply, fsync, synced = _txn.apply, os.fsync, []

    def applying(*args):
        monkeypatch.setattr(_txn.os, "fsync", lambda fd: (synced.append(inode(fd)), fsync(fd)))
        apply(*args)

    monkeypatch.setattr(_txn, "apply", applying)
    record.commit(cs)
    folder = next(root.glob(f"evidence/codex/*/*/{s.id}"))
    written = {folder, folder / "raw", root / ".atlas/last-written" / folder.relative_to(root), root / ".atlas"}
    assert sorted(synced) == sorted(inode(where) for where in written)


@pytest.mark.parametrize("name", ["card.md", "raw/main.jsonl.zst"])
def test_a_removal_compares_a_file_its_snapshot_read_also_when_the_file_was_gone_at_the_plan(tmp_path, name):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    gone = carded(tmp_path)
    snap = record.snapshot()
    snap.card(gone.id)
    snap.copy(gone.parts[0].origin)
    file = next(tmp_path.glob(f"evidence/codex/*/*/{gone.id}")) / name
    read = file.read_bytes()
    file.unlink()
    remove = snap.changes().remove_evidence(gone.id)
    file.write_bytes(read + b"invented bytes saved after the plan")
    with pytest.raises(Conflict):
        record.commit(remove)


@pytest.mark.parametrize("crash", [False, True], ids=["committed", "crashed-before-the-prune"])
def test_a_removed_carded_session_leaves_no_folder_named_by_its_id_in_either_tree(tmp_path, monkeypatch, crash):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    gone, kept = session(Turn("owner", T0, "one", "main")), session(Turn("owner", T0, "two", "main"), native="thread-2")
    for s in (gone, kept):
        put(tmp_path, s, {"main.jsonl.zst": b"a\n"})
        card = SessionCard(s.id, Inference("Invented intent.", (s.id,)), Inference("Invented outcome.", (s.id,)),
                           None, None)
        record.commit(record.snapshot().changes().put_card(card))
    assert list(tmp_path.glob(f".atlas/last-written/evidence/codex/*/*/{gone.id}/card.md"))
    remove = record.snapshot().changes().remove_evidence(gone.id)
    if crash:
        def interrupted(folder, top):
            raise KeyboardInterrupt
        with monkeypatch.context() as m, pytest.raises(KeyboardInterrupt):
            m.setattr(_txn, "_prune", interrupted)
            record.commit(remove)
        Record.open(tmp_path)
    else:
        record.commit(remove)
    assert [p for p in tmp_path.rglob("*") if gone.id in str(p)] == []
    for top in ("evidence", ".atlas/last-written/evidence"):
        assert list(tmp_path.glob(f"{top}/codex/*/*/{kept.id}/card.md"))


def carded(root):
    record = Record.open(root)
    s = session(Turn("owner", T0, "one", "main"))
    put(root, s, {"main.jsonl.zst": b"a\n"})
    card_for(record, s)
    return s


@pytest.mark.parametrize("tree", ["evidence", ".atlas/last-written/evidence"])
@pytest.mark.parametrize("cut", [1, 2, 3, 4])
def test_an_open_finishes_a_prune_cut_short_after_any_rmdir(tmp_path, monkeypatch, tree, cut):
    record = Record.open(tmp_path)
    record.create(SETTINGS, PROFILE)
    gone = carded(tmp_path)
    folders = {(f.stat().st_dev, f.stat().st_ino) for f in [tmp_path / tree, *(tmp_path / tree).rglob("*")]}
    removed = []
    real_rmdir = _txn.os.rmdir

    def rmdir(name, dir_fd):
        real_rmdir(name, dir_fd=dir_fd)
        if (os.fstat(dir_fd).st_dev, os.fstat(dir_fd).st_ino) in folders:
            removed.append(name)
        if len(removed) == cut:
            raise KeyboardInterrupt
    with monkeypatch.context() as m, pytest.raises(KeyboardInterrupt):
        m.setattr(_txn.os, "rmdir", rmdir)
        record.commit(record.snapshot().changes().remove_evidence(gone.id))
    Record.open(tmp_path)
    assert not list(tmp_path.glob(".atlas/txn/*.ready"))
    for top in ("evidence", ".atlas/last-written/evidence"):
        assert list((tmp_path / top).rglob("*")) == []


@pytest.mark.parametrize("top", ["evidence", ".atlas/last-written/evidence"])
def test_a_prune_never_removes_a_folder_through_a_symlink(tmp_path, top):
    root = tmp_path / "record"
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    gone = carded(root)
    harness, outside = root / top / "codex", tmp_path / "outside"
    harness.rename(outside)
    harness.symlink_to(outside, target_is_directory=True)
    record.commit(record.snapshot().changes().remove_evidence(gone.id))
    assert (outside / "2026/11").is_dir()
    assert harness.is_symlink()


def left_by_an_older_version(root, monkeypatch):
    """Removes one of two carded Sessions the way versions before the mirror prune did, then forgets
    the sweep ran. Returns (removed, kept)."""
    record = Record.open(root)
    record.create(SETTINGS, PROFILE)
    gone, kept = carded(root), session(Turn("owner", T0, "two", "main"), native="thread-2")
    put(root, kept, {"main.jsonl.zst": b"a\n"})
    card = SessionCard(kept.id, Inference("Invented intent.", (kept.id,)), Inference("Invented outcome.", (kept.id,)),
                       None, None)
    record.commit(record.snapshot().changes().put_card(card))
    with monkeypatch.context() as m:
        m.setattr(_txn, "PRUNED", ("evidence",))
        record.commit(record.snapshot().changes().remove_evidence(gone.id))
    (root / ".atlas/swept").unlink(missing_ok=True)
    assert list(root.glob(f".atlas/last-written/evidence/codex/2026/11/{gone.id}"))
    return gone, kept


def test_an_open_sweeps_the_empty_mirror_folders_an_older_version_left(tmp_path, monkeypatch):
    gone, kept = left_by_an_older_version(tmp_path, monkeypatch)
    Record.open(tmp_path)
    assert [p for p in tmp_path.rglob("*") if gone.id in str(p)] == []
    for top in ("evidence", ".atlas/last-written/evidence"):
        assert list(tmp_path.glob(f"{top}/codex/2026/11/{kept.id}/card.md"))


def test_an_open_after_a_crash_mid_sweep_finishes_it(tmp_path, monkeypatch):
    gone, _ = left_by_an_older_version(tmp_path, monkeypatch)
    for leftover in ("evidence/claude_code/2026/10/cc-x", ".atlas/last-written/evidence/hermes/2026/09/hm-y"):
        (tmp_path / leftover).mkdir(parents=True)
    real_rmdir = _txn.os.rmdir

    def rmdir(name, dir_fd):
        real_rmdir(name, dir_fd=dir_fd)
        raise KeyboardInterrupt
    with monkeypatch.context() as m, pytest.raises(KeyboardInterrupt):
        m.setattr(_txn.os, "rmdir", rmdir)
        Record.open(tmp_path)
    assert not (tmp_path / ".atlas/swept").exists()
    Record.open(tmp_path)
    assert [p for p in tmp_path.rglob("*") if gone.id in str(p) or p.name in ("cc-x", "hm-y")] == []
    assert sorted(p.name for p in (tmp_path / "evidence").iterdir()) == ["codex"]
    assert sorted(p.name for p in (tmp_path / ".atlas/last-written/evidence").iterdir()) == ["codex"]


def inode(where) -> tuple[int, int]:
    """The folder a path or an open descriptor names."""
    found = os.fstat(where) if isinstance(where, int) else os.stat(where)
    return found.st_dev, found.st_ino


def walks(root, monkeypatch) -> list[int]:
    """Collects each time a sweep lists a top folder, which it opens by descriptor."""
    tops = {inode(root / "evidence"), inode(root / ".atlas/last-written/evidence")}
    listed, real_scandir = [], os.scandir

    def scandir(path="."):
        if isinstance(path, int) and inode(path) in tops:
            listed.append(path)
        return real_scandir(path)
    monkeypatch.setattr(_txn.os, "scandir", scandir)
    return listed


def test_the_sweep_runs_once(tmp_path, monkeypatch):
    left_by_an_older_version(tmp_path, monkeypatch)
    Record.open(tmp_path)
    listed = walks(tmp_path, monkeypatch)
    Record.open(tmp_path)
    carded(tmp_path)
    assert listed == []


def test_a_record_that_failed_to_sweep_tries_again_only_on_the_next_open(tmp_path, monkeypatch):
    left_by_an_older_version(tmp_path, monkeypatch)
    month = tmp_path / "evidence/codex/2026/11"
    month.chmod(0o555)
    try:
        record = Record.open(tmp_path)
        listed = walks(tmp_path, monkeypatch)
        record.commit(record.snapshot().changes())
        record.cache_put("invented-key", b"{}", ())
        assert listed == []
        Record.open(tmp_path)
        assert listed
    finally:
        month.chmod(0o755)
    assert not (tmp_path / ".atlas/swept").exists()


def unwritable_machinery(root, monkeypatch):
    (root / ".atlas").chmod(0o555)
    return lambda: (root / ".atlas").chmod(0o755)


def read_only_marker(root, monkeypatch):
    def write_synced(path, data):
        raise OSError(errno.EROFS, "invented read-only filesystem")
    monkeypatch.setattr(_txn, "write_synced", write_synced)
    return lambda: None


def unreadable_top(root, monkeypatch):
    (root / "evidence").chmod(0)
    return lambda: (root / "evidence").chmod(0o755)


def failing_fsync(root, monkeypatch):
    def fsync(fd):
        raise OSError(errno.EIO, "invented sync failure")
    monkeypatch.setattr(_txn.os, "fsync", fsync)
    return lambda: None


@pytest.mark.parametrize("breakage", [unwritable_machinery, read_only_marker, unreadable_top, failing_fsync])
@pytest.mark.parametrize("operation", ["open", "commit"])
def test_a_sweep_that_cannot_finish_never_stops_an_open_or_commit(tmp_path, monkeypatch, breakage, operation):
    gone, _ = left_by_an_older_version(tmp_path, monkeypatch)
    record = Record(tmp_path)
    changes = record.snapshot().changes()
    with monkeypatch.context() as m:
        mend = breakage(tmp_path, m)
        try:
            Record.open(tmp_path) if operation == "open" else record.commit(changes)
        finally:
            mend()
    assert not (tmp_path / ".atlas/swept").exists()
    Record.open(tmp_path)
    assert [p for p in tmp_path.rglob("*") if gone.id in str(p)] == []
    assert (tmp_path / ".atlas/swept").exists()


@pytest.mark.parametrize("top", ["evidence", ".atlas/last-written/evidence"])
def test_the_sweep_passes_over_a_top_that_is_a_looping_symlink(tmp_path, monkeypatch, top):
    left_by_an_older_version(tmp_path, monkeypatch)
    shutil.rmtree(tmp_path / top)
    (tmp_path / top).symlink_to("evidence", target_is_directory=True)
    Record.open(tmp_path)
    assert (tmp_path / top).is_symlink()
    assert (tmp_path / ".atlas/swept").exists()


def test_a_symlink_to_a_folder_it_cannot_read_never_keeps_the_sweep_pending(tmp_path, monkeypatch):
    left_by_an_older_version(tmp_path, monkeypatch)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    (outside / "cx-empty").mkdir(parents=True)
    (tmp_path / "evidence/chatgpt").symlink_to(outside, target_is_directory=True)
    outside.chmod(0)
    try:
        Record.open(tmp_path)
    finally:
        outside.chmod(0o755)
    assert (tmp_path / ".atlas/swept").exists()
    assert (outside / "cx-empty").is_dir()


def test_a_retry_syncs_a_folder_whose_sync_failed_after_its_empty_folder_went(tmp_path, monkeypatch):
    gone, _ = left_by_an_older_version(tmp_path, monkeypatch)
    month = tmp_path / ".atlas/last-written/evidence/codex/2026/11"
    real_fsync, synced = os.fsync, []

    def the_month(fd):
        return inode(fd) == inode(month)

    def failing(fd):
        if the_month(fd):
            raise OSError(errno.EIO, "invented sync failure")
        real_fsync(fd)
    with monkeypatch.context() as m:
        m.setattr(_txn.os, "fsync", failing)
        Record.open(tmp_path)
    assert not (month / gone.id).exists()
    assert not (tmp_path / ".atlas/swept").exists()
    monkeypatch.setattr(_txn.os, "fsync", lambda fd: (synced.append(the_month(fd)), real_fsync(fd)))
    Record.open(tmp_path)
    assert (tmp_path / ".atlas/swept").exists()
    assert any(synced)


def test_a_first_sweep_syncs_only_the_folders_it_emptied(tmp_path, monkeypatch):
    gone, _ = left_by_an_older_version(tmp_path, monkeypatch)
    folders = {p: inode(p) for top in ("evidence", ".atlas/last-written/evidence")
               for p in (tmp_path / top, *(tmp_path / top).rglob("*")) if p.is_dir()}
    real_fsync, synced = os.fsync, set()

    def fsync(fd):
        synced.add(inode(fd))
        real_fsync(fd)
    monkeypatch.setattr(_txn.os, "fsync", fsync)
    Record.open(tmp_path)
    assert {p for p, found in folders.items() if found in synced} == {p.parent for p in folders if p.name == gone.id}


def test_the_sweep_never_follows_a_folder_above_a_top_swapped_for_a_symlink_once_it_looked(tmp_path, monkeypatch):
    root = tmp_path / "record"
    left_by_an_older_version(root, monkeypatch)
    mirror = root / ".atlas/last-written"
    outside = tmp_path / "outside"
    (outside / "evidence/cx-empty").mkdir(parents=True)
    mirror_inode = inode(mirror)

    def swap_once_looked_at(real):
        def look(path, *args, dir_fd=None, **kwargs):
            seen = real(path, *args, dir_fd=dir_fd, **kwargs)
            below = dir_fd is not None and path == "evidence" and inode(dir_fd) == mirror_inode
            if not os.path.islink(mirror) and (str(path) == str(mirror / "evidence") or below):
                mirror.rename(root / ".atlas/last-written-before")
                mirror.symlink_to(outside, target_is_directory=True)
            return seen
        return look
    monkeypatch.setattr(_txn.os, "open", swap_once_looked_at(os.open))
    monkeypatch.setattr(_txn.os, "stat", swap_once_looked_at(os.stat))
    Record.open(root)
    assert mirror.is_symlink()
    assert (outside / "evidence/cx-empty").is_dir()


@pytest.mark.parametrize("top", ["evidence", ".atlas/last-written/evidence"])
def test_the_sweep_keeps_every_folder_holding_anything_and_never_goes_through_a_symlink(tmp_path, monkeypatch, top):
    root = tmp_path / "record"
    left_by_an_older_version(root, monkeypatch)
    outside = tmp_path / "outside"
    (outside / "2026/11/cx-empty").mkdir(parents=True)
    (root / top / "chatgpt").symlink_to(outside, target_is_directory=True)
    (root / top / "hermes/2026/11/hm-dotfile").mkdir(parents=True)
    (root / top / "hermes/2026/11/hm-dotfile/.keep").write_bytes(b"")
    Record.open(root)
    assert (outside / "2026/11/cx-empty").is_dir()
    assert (root / top / "chatgpt").is_symlink()
    assert (root / top / "hermes/2026/11/hm-dotfile/.keep").exists()


@pytest.mark.parametrize("top", ["evidence", ".atlas/last-written/evidence"])
def test_the_sweep_skips_a_top_that_is_a_symlink(tmp_path, monkeypatch, top):
    root = tmp_path / "record"
    left_by_an_older_version(root, monkeypatch)
    outside = tmp_path / "outside"
    (root / top).rename(outside)
    (outside / "codex/2026/10/cx-empty").mkdir(parents=True)
    (root / top).symlink_to(outside, target_is_directory=True)
    Record.open(root)
    assert (outside / "codex/2026/10/cx-empty").is_dir()


def test_a_sweep_a_folder_denied_is_not_marked_done_and_the_next_open_finishes_it(tmp_path, monkeypatch):
    gone, _ = left_by_an_older_version(tmp_path, monkeypatch)
    month = tmp_path / ".atlas/last-written/evidence/codex/2026/11"
    month.chmod(0o555)
    try:
        Record.open(tmp_path)
    finally:
        month.chmod(0o755)
    assert (month / gone.id).is_dir()
    assert not (tmp_path / ".atlas/swept").exists()
    Record.open(tmp_path)
    assert [p for p in tmp_path.rglob("*") if gone.id in str(p)] == []
    assert (tmp_path / ".atlas/swept").exists()


def test_a_top_it_cannot_open_never_keeps_the_later_opens_syncing_every_folder(tmp_path, monkeypatch):
    left_by_an_older_version(tmp_path, monkeypatch)
    unreadable_top(tmp_path, monkeypatch)
    try:
        Record.open(tmp_path)
    finally:
        (tmp_path / "evidence").chmod(0o755)
    assert not (tmp_path / ".atlas/sweeping").exists()
    assert not (tmp_path / ".atlas/swept").exists()
    real_fsync, synced = os.fsync, []
    monkeypatch.setattr(_txn.os, "fsync", lambda fd: (synced.append(inode(fd)), real_fsync(fd)))
    Record.open(tmp_path)
    assert (tmp_path / ".atlas/swept").exists()
    folders = {inode(p) for p in (tmp_path / "evidence", *(tmp_path / "evidence").rglob("*")) if p.is_dir()}
    assert folders.isdisjoint(synced)


def test_a_marked_open_drops_the_note_a_crash_left_after_the_marker(tmp_path, monkeypatch):
    left_by_an_older_version(tmp_path, monkeypatch)
    Record.open(tmp_path)
    (tmp_path / ".atlas/sweeping").write_bytes(b"")
    Record.open(tmp_path)
    assert (tmp_path / ".atlas/swept").exists()
    assert not (tmp_path / ".atlas/sweeping").exists()


def test_a_retry_that_cannot_open_a_top_keeps_the_note_for_the_removals_it_left_unsynced(tmp_path, monkeypatch):
    gone, _ = left_by_an_older_version(tmp_path, monkeypatch)
    mirror = tmp_path / ".atlas/last-written/evidence"
    month = mirror / "codex/2026/11"
    real_fsync, synced = os.fsync, []

    def failing(fd):
        if inode(fd) == inode(month):
            raise OSError(errno.EIO, "invented sync failure")
        real_fsync(fd)
    with monkeypatch.context() as m:
        m.setattr(_txn.os, "fsync", failing)
        Record.open(tmp_path)
    assert not (month / gone.id).exists()
    mirror.chmod(0)
    try:
        Record.open(tmp_path)
    finally:
        mirror.chmod(0o755)
    assert (tmp_path / ".atlas/sweeping").exists()
    monkeypatch.setattr(_txn.os, "fsync", lambda fd: (synced.append(inode(fd)), real_fsync(fd)))
    Record.open(tmp_path)
    assert inode(month) in synced
    assert (tmp_path / ".atlas/swept").exists()
    assert not (tmp_path / ".atlas/sweeping").exists()


def test_a_retry_syncs_the_note_before_its_first_removal_so_a_power_loss_cannot_drop_it(tmp_path, monkeypatch):
    gone, _ = left_by_an_older_version(tmp_path, monkeypatch)
    machinery, month = tmp_path / ".atlas", tmp_path / ".atlas/last-written/evidence/codex/2026/11"
    real_fsync, real_rmdir = os.fsync, os.rmdir
    note_durable, synced = False, []

    def failing(fd):
        if inode(fd) == inode(machinery):
            raise OSError(errno.EIO, "invented sync failure")
        real_fsync(fd)
    with monkeypatch.context() as m:
        m.setattr(_txn.os, "fsync", failing)
        Record.open(tmp_path)
    assert (machinery / "sweeping").exists()
    assert (month / gone.id).exists()

    def tracking(fd):
        nonlocal note_durable
        note_durable = note_durable or inode(fd) == inode(machinery)
        real_fsync(fd)

    def crashing(name, *, dir_fd):
        real_rmdir(name, dir_fd=dir_fd)
        raise KeyboardInterrupt
    with monkeypatch.context() as m, pytest.raises(KeyboardInterrupt):
        m.setattr(_txn.os, "fsync", tracking)
        m.setattr(_txn.os, "rmdir", crashing)
        Record.open(tmp_path)
    if not note_durable:
        (machinery / "sweeping").unlink()
    monkeypatch.setattr(_txn.os, "fsync", lambda fd: (synced.append(inode(fd)), real_fsync(fd)))
    Record.open(tmp_path)
    assert inode(month) in synced
    assert (machinery / "swept").exists()
    assert not (machinery / "sweeping").exists()


def test_a_turn_keeps_its_seconds_and_in_the_repeated_hour_its_offset():
    first = datetime(2026, 10, 25, 2, 52, 10, tzinfo=BERLIN)
    second = datetime(2026, 10, 25, 2, 5, 30, fold=1, tzinfo=BERLIN)
    s = replace(session(Turn("owner", first, "before the clocks went back", "main"),
                        Turn("agent", second, "after the clocks went back", "main"),
                        Turn("owner", datetime(2026, 10, 25, 9, 0, tzinfo=BERLIN), "later", "main")),
                started=first, last_at=first + timedelta(hours=7))
    text = _codec.render("session", s, BERLIN).decode()
    assert "**You · 02:52:10+02:00**" in text
    assert "**Codex · 02:05:30+01:00**" in text
    assert "**You · 09:00**" in text
    read = _codec.parse("session", text.encode(), BERLIN)
    assert [t.at.timestamp() for t in read.turns] == [t.at.timestamp() for t in s.turns]
