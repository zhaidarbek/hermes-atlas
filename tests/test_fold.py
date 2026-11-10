"""sources.resolve, the pure fold: native units in, one Session per owner conversation out."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.model import LOCAL, HarnessId, RawPart, SessionEvidence, Turn, evidence_id, part_file, skip_key
from atlas.sources import Lineage, NativeSession, resolve

CC, CX, DV = HarnessId.CLAUDE_CODE, HarnessId.CODEX, HarnessId.DEVIN
T0 = datetime(2026, 11, 13, 17, 40, tzinfo=timezone.utc)


def native(harness, id, minutes=0, parent=None, interface=None, subagents=(), key=None) -> NativeSession:
    start = T0 + timedelta(minutes=minutes)
    keys = ["main", *(f"subagent:{name}" for name in subagents)]
    turns = (Turn("owner", start, f"ask {id}", "main"), Turn("agent", start + timedelta(minutes=1), f"done {id}", "main"),
             *(Turn("agent", start + timedelta(minutes=2), f"sub {k}", k) for k in keys[1:]))
    evidence = SessionEvidence(evidence_id(harness, LOCAL, key or id), harness, interface, LOCAL, key or id,
                               f"title {id}", start, start + timedelta(minutes=2), "box", "~/work", None,
                               tuple(RawPart(part_file(k), "", f"~/{id}/{k}", f"1@{minutes}") for k in keys), turns)
    return NativeSession(harness, id, parent, interface, {k: f"{id}:{k}".encode() for k in keys}, evidence)


def only(sessions):
    assert len(sessions) == 1
    return sessions[0]


@pytest.mark.parametrize("child_harness", [CC, CX, DV])
def test_a_t3_child_folds_into_the_session_that_started_it(child_harness):
    parent, child = native(CC, "parent"), native(child_harness, "child", minutes=5)
    lineage = [Lineage((CC, "parent"), None, "T3 Code"), Lineage((child_harness, "child"), None, "T3 Code"),
               Lineage((child_harness, "child"), (CC, "parent"), "T3 Code")]
    folded = only(resolve([child, parent], lineage))
    assert folded.evidence.id == parent.evidence.id
    assert folded.evidence.interface == "T3 Code"
    assert [p.name for p in folded.evidence.parts] == ["main.jsonl.zst", "t3-child-child.jsonl.zst"]
    assert folded.raw["t3-child-child.jsonl.zst"] == b"child:main"
    assert [(t.role, t.part) for t in folded.evidence.turns if t.text.endswith("child")] == [
        ("agent", "t3-child:child"), ("agent", "t3-child:child")]
    assert folded.replaces == {child.evidence.id: "t3-child:child"}
    assert folded.folded == 1
    assert folded.evidence.last_at == child.evidence.last_at
    assert folded.skip_keys == {skip_key(CC, "parent"), skip_key(child_harness, "child")}


def test_subagents_fold_and_are_counted():
    parent = native(CX, "parent", subagents=("a",))
    child = native(CX, "spawned", minutes=3, parent="parent")
    grandchild = native(CX, "spawned-again", minutes=4, parent="spawned")
    folded = only(resolve([grandchild, child, parent], []))
    assert folded.folded == 3
    assert folded.replaces == {}
    assert {p.name for p in folded.evidence.parts} == {
        "main.jsonl.zst", "subagent-a.jsonl.zst", "subagent-spawned.jsonl.zst", "subagent-spawned-again.jsonl.zst"}
    assert [t.text for t in folded.evidence.turns if t.role == "owner"] == ["ask parent"]


def test_a_t3_childs_own_subagents_come_along():
    folded = only(resolve([native(CC, "parent"), native(CC, "child", subagents=("x",))],
                          [Lineage((CC, "child"), (CC, "parent"), "T3 Code")]))
    assert {p.name for p in folded.evidence.parts} == {
        "main.jsonl.zst", "t3-child-child.jsonl.zst", "t3-child-child-subagent-x.jsonl.zst"}
    assert folded.folded == 2


def test_a_family_whose_starting_session_was_not_read_is_left_out():
    assert resolve([native(CX, "orphan", parent="gone")], []) == []


def test_a_lineage_cycle_ends():
    a, b = native(CC, "a"), native(CC, "b", minutes=1)
    lineage = [Lineage((CC, "a"), (CC, "b"), "T3 Code"), Lineage((CC, "b"), (CC, "a"), "T3 Code")]
    assert len(resolve([a, b], lineage)) == 1


def test_a_session_through_an_interface_counts_once_under_its_harness():
    folded = resolve([native(CX, "x"), native(CC, "y", minutes=1)], [Lineage((CX, "x"), None, "T3 Code")])
    assert [(c.evidence.harness, c.evidence.interface) for c in folded] == [(CX, "T3 Code"), (CC, None)]


def test_two_sessions_that_share_a_devin_slug_stay_two():
    first = native(DV, "slug", key="slug@2026-07-15T14:00:00Z")
    again = native(DV, "slug", 5, key="slug@2026-11-01T09:00:00Z")
    assert [c.evidence.native_key for c in resolve([first, again], [])] == [first.evidence.native_key,
                                                                             again.evidence.native_key]
