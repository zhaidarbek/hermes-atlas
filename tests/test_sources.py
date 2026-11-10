"""Each adapter's units and reads against fixture stores, and sources.collect over them."""
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import replace
from datetime import timedelta

import pytest

from atlas import sources
from atlas.model import HarnessId, HarnessSetting, InterfaceSetting, Sent, part_origin, skip_key
from atlas.record import Held, HeldPart
from atlas.sources._handoff import live_ask
from harness import world
from harness.stores import DEFAULT_ROOTS, T3, write_store
from test_probe import BERLIN

CC, CX, DV, HM = HarnessId.CLAUDE_CODE, HarnessId.CODEX, HarnessId.DEVIN, HarnessId.HERMES
NOW = world.SETUP_AT
ALL = replace(BERLIN, harnesses={h: HarnessSetting(True, "store", (), "probe") for h in (CC, CX, DV, HM)})


@pytest.fixture
def stores(throwaway_home):
    for store in (CC, CX, DV, HM, T3):
        write_store(store, throwaway_home / DEFAULT_ROOTS[store], throwaway_home, NOW)
    return throwaway_home


def natives(harness, sent=Sent(), settings=ALL):
    adapter = sources.ADAPTERS[harness]
    return {n.evidence.title: n for u in adapter.units(settings, sent) if (n := adapter.read(settings, u, sent))}


def collected(settings=ALL, skip=frozenset(), sent=Sent(), held=Held()):
    pending, imports = sources.collect(settings, held, skip, sent, None)
    assert imports == []
    return {c.evidence.title: c for p in pending for c in p.read(sent).sessions(lambda origin: None, sent)}


def holding(found) -> Held:
    """What the record holds once it has committed these Sessions."""
    return Held({p.origin: HeldPart(c.evidence.id, p.name, p.fingerprint) for c in found for p in c.evidence.parts})


@pytest.mark.parametrize("harness", [CC, CX, DV, HM])
def test_each_adapter_reads_every_session_of_its_store(stores, harness):
    mine = [s for s in world.visible(NOW) if s.harness is harness and s.source != "cron"]
    read = natives(harness)
    assert set(read) == {s.title for s in mine}
    for s in mine:
        n = read[s.title]
        assert n.evidence.native_key == s.native_key
        assert [(t.role, t.text) for t in n.evidence.turns if t.part == "main"] == [
            ("owner" if t.owner else "agent", t.text) for t in sorted(s.turns(), key=lambda t: t.at) if t.at <= NOW]
        assert n.evidence.cwd == s.cwd
        assert all(n.raw[k] for k in n.raw)


@pytest.mark.parametrize("harness", [CC, CX, DV, HM])
def test_a_unit_lists_the_fingerprints_its_read_records(stores, harness):
    adapter = sources.ADAPTERS[harness]
    for unit in adapter.units(ALL, Sent()):
        n = adapter.read(ALL, unit, Sent())
        if n is not None:
            assert {p.source: p.fingerprint for p in n.evidence.parts} == dict(unit.parts.values())


def test_claude_code_tells_owner_turns_from_injected_ones(stores):
    transcript = next((stores / ".claude/projects").glob(f"*/{world.by_title('pi-hole dns loop').native}.jsonl"))
    first = json.loads(transcript.read_text().splitlines()[0])
    injected = [{**first, "isMeta": True, "message": {"role": "user", "content": "caveat"}},
                {**first, "toolUseResult": {}, "message": {"role": "user", "content": [
                    {"type": "tool_result", "content": "ok"}]}},
                {**first, "promptSource": "system", "message": {"role": "user", "content": "scheduled"}},
                {**first, "message": {"role": "user", "content": [{"type": "text", "text": "and this"}]}}]
    with transcript.open("a") as fh:
        fh.writelines(json.dumps({**r, "uuid": f"injected-{n}"}) + "\n" for n, r in enumerate(injected))
    owner = [t.text for t in natives(CC)["pi-hole dns loop"].evidence.turns if t.role == "owner"]
    assert owner == ["pi-hole dns loop", "and this", "Go on."]


def test_claude_code_takes_a_human_origin_for_the_owners_turn(stores):
    transcript = next((stores / ".claude/projects").glob(f"*/{world.by_title('pi-hole dns loop').native}.jsonl"))
    first = json.loads(transcript.read_text().splitlines()[0])
    later = [{**first, "origin": {"kind": "human"}, "promptSource": "typed",
              "message": {"role": "user", "content": "typed in the new shape"}},
             {**first, "origin": {"kind": "task-notification", "producer": "bash"}, "promptSource": "system",
              "message": {"role": "user", "content": "a background task finished"}}]
    with transcript.open("a") as fh:
        fh.writelines(json.dumps({**r, "uuid": f"later-{n}"}) + "\n" for n, r in enumerate(later))
    owner = [t.text for t in natives(CC)["pi-hole dns loop"].evidence.turns if t.role == "owner"]
    assert owner == ["pi-hole dns loop", "typed in the new shape", "Go on."]


def test_codex_takes_owner_turns_from_user_message_events_only(stores):
    rollout = next((stores / ".codex/sessions").glob(f"**/rollout-*-{world.by_title('gpx tunnel gaps').native}.jsonl"))
    stamp = json.loads(rollout.read_text().splitlines()[1])["timestamp"]
    injected = [{"timestamp": stamp, "type": "response_item", "payload": {
                    "type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment>"}]}},
                {"timestamp": stamp, "type": "event_msg", "payload": {"type": "item_completed", "item": {
                    "type": "Reasoning", "text": "thinking"}}}]
    with rollout.open("a") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in injected)
    turns = natives(CX)["gpx tunnel gaps"].evidence.turns
    assert [t.text for t in turns if t.role == "owner"] == ["gpx tunnel gaps", "Go on."]
    assert "thinking" not in {t.text for t in turns}


def test_devin_handoffs_injected_rows_and_compaction_copies_are_not_owner_turns(stores):
    with closing(sqlite3.connect(stores / ".local/share/devin/cli/sessions.db")) as con:
        sid = world.by_title("dns forwarding notes").native
        first = json.loads(con.execute("SELECT chat_message FROM message_nodes WHERE session_id = ? AND node_id = 1",
                                       (sid,)).fetchone()[0])
        later = [{**first, "content": "handed over", "message_id": "handoff",
                  "metadata": {**first["metadata"], "extensions": {"subagent/handoff": {}}}},
                 {**first, "content": "<system-reminder>", "message_id": "injected",
                  "metadata": {**first["metadata"], "is_user_input": None}},
                 first]
        con.executemany("INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) VALUES (?, ?, ?, ?)",
                        [(sid, 100 + n, json.dumps(m), int(NOW.timestamp())) for n, m in enumerate(later)])
        con.commit()
    turns = natives(DV)["dns forwarding notes"].evidence.turns
    assert [t.text for t in turns if t.role == "owner"] == ["dns forwarding notes", "Go on."]


def test_claude_code_subagents_are_parts_with_agent_turns(stores):
    n = natives(CC)["retry flake"]
    assert len(n.raw) == 3 and n.parent_native_id is None
    assert {t.role for t in n.evidence.turns if t.part != "main"} == {"agent"}


def test_t3_code_markers_note_the_interface(stores):
    read = {**natives(CC), **natives(CX)}
    noted = {t for t, n in read.items() if n.interface == "T3 Code"}
    assert noted == {s.title for s in world.visible(NOW) if s.interface}


def test_codex_thread_spawn_names_its_parent_and_guardian_review_is_dropped(stores):
    read = natives(CX)
    assert read["check redis config"].parent_native_id == world.by_title("redis db per worker").native
    rollout = next((stores / ".codex/sessions").glob("**/rollout-*.jsonl"))
    lines = rollout.read_text().splitlines()
    meta = json.loads(lines[0])
    meta["payload"] = {**meta["payload"], "id": "guardian", "source": {"subagent": "guardian_review"}}
    rollout.with_name("rollout-2026-11-14T09-00-00-guardian.jsonl").write_text(
        "\n".join([json.dumps(meta), *lines[1:]]) + "\n")
    assert "guardian" not in {n.native_id for n in natives(CX).values()}
    assert "guardian" not in {c.evidence.native_key for c in collected().values()}


def test_a_second_codex_home_counts_once_and_the_larger_copy_wins(stores, tmp_path):
    write_store(CX, tmp_path / "orca", stores, NOW)
    native = world.by_title("gpx tunnel gaps").native
    grown = next((tmp_path / "orca/sessions").glob(f"**/rollout-*-{native}.jsonl"))
    with grown.open("a") as fh:
        fh.write(json.dumps({"timestamp": world.utc(NOW), "type": "event_msg", "payload": {
            "type": "item_completed", "item": {"type": "UserMessage", "text": "and the tunnels"}}}) + "\n")
    orca = HarnessSetting(True, "store", (str(tmp_path / "orca"),), "owner")
    settings = replace(ALL, harnesses={**ALL.harnesses, CX: orca})
    units = sources.ADAPTERS[CX].units(settings, Sent())
    assert len(units) == len({u.native_key for u in units}) == len(natives(CX))
    chosen = {u.native_id: u.parts["main"][0] for u in units}
    assert chosen[native] == str(grown) and all(s.startswith("~/.codex/") for n, s in chosen.items() if n != native)


def test_collect_reads_only_sessions_with_a_part_not_held_as_it_is_now(stores):
    held = holding(collected().values())
    assert collected(held=held) == {}
    with closing(sqlite3.connect(stores / ".hermes/state.db")) as con:
        sid = world.by_title("shelf bracket sizes").native
        con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', 'more', ?)",
                    (sid, NOW.timestamp()))
        con.commit()
    assert set(collected(held=held)) == {"shelf bracket sizes"}


def test_hermes_drops_rows_hermes_send_mirrored(stores):
    brief = "Atlas · Daily Brief · Monday 16 November\n\n1. Payments CI flakes"
    with closing(sqlite3.connect(stores / ".hermes/state.db")) as con:
        sid = world.by_title("shelf bracket sizes").native
        con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                    (sid, brief + "\n", NOW.timestamp()))
        con.commit()
    kept = natives(HM, sent=Sent(frozenset({brief})))["shelf bracket sizes"]
    assert brief not in {t.text for t in kept.evidence.turns}
    assert brief.encode() not in kept.raw["main"]
    assert brief in {t.text for t in natives(HM)["shelf bracket sizes"].evidence.turns}


MASKED_BRIEF = "Atlas · Daily Brief · Monday 16 November\n\n1. Rotate [secret: github token]"


@pytest.mark.parametrize("sent, row, scrubbed", [
    (frozenset({"Atlas · Daily Brief · Monday 16 November\n\n1. Payments CI flakes"}), None, False),
    (frozenset({MASKED_BRIEF}), None, False),
    (frozenset({MASKED_BRIEF}), "Atlas · Daily Brief · Monday 16 November\n\n1. Rotate ghp_" + "a1" * 18, True)])
def test_hermes_scrubs_a_row_only_when_it_could_be_a_masked_sent_text(stores, monkeypatch, sent, row, scrubbed):
    if row is not None:
        with closing(sqlite3.connect(stores / ".hermes/state.db")) as con:
            con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                        (world.by_title("shelf bracket sizes").native, row, NOW.timestamp()))
            con.commit()
    with closing(sqlite3.connect(stores / ".hermes/state.db")) as con:
        rows = {text for (text,) in con.execute("SELECT content FROM messages WHERE role = 'assistant'")}
    calls = []
    scrub = sources._adapters.privacy.scrub
    monkeypatch.setattr(sources._adapters.privacy, "scrub", lambda text: calls.append(text) or scrub(text))
    found = collected(sent=Sent(sent, sent))
    assert set(calls) & rows == ({row} if scrubbed else set())
    assert row is None or row not in {t.text for t in found["shelf bracket sizes"].evidence.turns}


def test_hermes_keeps_an_agent_row_that_differs_from_a_text_sent_as_stored_only_by_its_secret(stores, monkeypatch):
    agent = MASKED_BRIEF.replace("[secret: github token]", "ghp_" + "b2" * 18)
    with closing(sqlite3.connect(stores / ".hermes/state.db")) as con:
        con.executemany("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                        [(world.by_title("shelf bracket sizes").native, text, NOW.timestamp() + i)
                         for i, text in enumerate([MASKED_BRIEF, agent])])
        con.commit()
    calls = []
    scrub = sources._adapters.privacy.scrub
    monkeypatch.setattr(sources._adapters.privacy, "scrub", lambda text: calls.append(text) or scrub(text))
    found = collected(sent=Sent(frozenset({MASKED_BRIEF})))
    turns = [t.text for t in found["shelf bracket sizes"].evidence.turns]
    assert agent in turns and MASKED_BRIEF not in turns
    assert agent not in calls


def test_hermes_marks_an_exchange_that_called_atlas_tools(stores):
    with closing(sqlite3.connect(stores / ".hermes/state.db")) as con:
        sid = world.by_title("shelf bracket sizes").native
        at = NOW.timestamp()
        con.executemany("INSERT INTO messages (session_id, role, content, tool_name, timestamp) VALUES (?, ?, ?, ?, ?)",
                        [(sid, "user", "atlas, what is next?", None, at), (sid, "tool", "{}", "atlas_read", at + 1),
                         (sid, "assistant", "Payments CI flakes.", None, at + 2)])
        con.commit()
    turns = natives(HM)["shelf bracket sizes"].evidence.turns
    assert [t.text for t in turns if t.atlas_tool_turn] == ["atlas, what is next?", "Payments CI flakes."]


def test_hermes_continues_a_session_across_a_compression_split(stores):
    db = stores / ".hermes/state.db"
    with closing(sqlite3.connect(db)) as con:
        sid = world.by_title("shelf bracket sizes").native
        con.execute("UPDATE sessions SET end_reason = 'compression' WHERE id = ?", (sid,))
        con.execute("INSERT INTO sessions (id, source, parent_session_id, started_at) VALUES ('later', 'telegram', ?, ?)",
                    (sid, NOW.timestamp()))
        con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('later', 'user', 'again', ?)",
                    (NOW.timestamp(),))
        con.commit()
    read = natives(HM)
    n = read["shelf bracket sizes"]
    assert set(n.raw) == {"main", "continued:later"}
    assert [t.text for t in n.evidence.turns if t.role == "owner"][-1] == "again"
    assert len(read) == len([s for s in world.visible(NOW) if s.harness is HM and s.source != "cron"])


def test_collect_folds_and_skips_cron(stores):
    got = collected()
    assert set(got) == {s.title for s in world.visible(NOW) if s.is_owner_session}
    assert sum(c.folded for c in got.values()) == world.folded(NOW)
    assert got["summarise backup logs"].folded == 1
    assert "nightly digest" not in got


def _add_t3_child(home, parent_title):
    """A T3 Code subagent thread whose native Session is a top-level Claude Code transcript."""
    parent = world.by_title(parent_title)
    child = world.WorldSession(CC, "delegated check", parent.cwd, parent.start + timedelta(minutes=5), 5)
    db = home / ".t3/userdata/statev2.sqlite"
    with closing(sqlite3.connect(db)) as con:
        con.execute("INSERT INTO orchestration_v2_projection_threads (thread_id, payload_json) VALUES (?, ?)",
                    ("child-thread", json.dumps({"lineage": {"parentThreadId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"t3:{parent.native}")),
                                                             "relationshipToParent": "subagent"}})))
        con.execute("INSERT INTO orchestration_v2_projection_provider_threads VALUES (?, ?)", ("child-thread", json.dumps(
            {"nativeThreadRef": {"driver": "claudeAgent", "nativeId": child.native}})))
        con.commit()
    folder = next((home / ".claude/projects").glob(f"*/{world.by_title('pi-hole dns loop').native}.jsonl")).parent
    line = {"type": "user", "sessionId": child.native, "timestamp": world.utc(child.start), "cwd": str(home),
            "message": {"role": "user", "content": "check the export"}}
    (folder / f"{child.native}.jsonl").write_text(json.dumps(line) + "\n")
    return child


def test_a_t3_child_folds_into_its_parent_across_harnesses(stores):
    child = _add_t3_child(stores, "pdf vat rounding")
    got = collected()
    assert "check the export" not in got
    parent = got["pdf vat rounding"]
    assert parent.folded == 1 and parent.evidence.interface == "T3 Code"
    assert [t.role for t in parent.evidence.turns if t.part == f"t3-child:{child.native}"] == ["agent"]


def test_a_changed_child_brings_its_starting_session(stores):
    child = _add_t3_child(stores, "pdf vat rounding")
    held = holding(collected().values())
    transcript = next((stores / ".claude/projects").glob(f"*/{child.native}.jsonl"))
    with transcript.open("a") as fh:
        fh.write(json.dumps({"type": "user", "sessionId": child.native, "timestamp": world.utc(NOW),
                             "message": {"role": "user", "content": "and the totals"}}) + "\n")
    got = collected(held=held)
    assert set(got) == {"pdf vat rounding"}
    assert got["pdf vat rounding"].folded == 1


def test_forgotten_and_excluded_sessions_are_not_collected(stores):
    forgotten = world.by_title("pi-hole dns loop")
    settings = replace(ALL, exclusions=("~/home/greenhouse",))
    got = collected(settings, skip=frozenset({skip_key(forgotten.harness, forgotten.native_key)}))
    assert forgotten.title not in got
    assert not any(c.evidence.cwd.startswith("~/home/greenhouse") for c in got.values())
    assert "restic nightly backups" in got


def test_a_disabled_or_unreadable_store_is_skipped_and_the_rest_collected(stores, caplog):
    (stores / ".hermes/state.db").write_bytes(b"not a database")
    settings = replace(ALL, harnesses={**ALL.harnesses, DV: HarnessSetting(False, "store", (), "probe")})
    got = collected(settings)
    assert {c.evidence.harness for c in got.values()} == {CC, CX}
    assert "Hermes" in caplog.text and "could not be read" in caplog.text


def test_an_unreadable_session_is_skipped_alone(stores, caplog):
    transcript = next((stores / ".claude/projects").glob(f"*/{world.by_title('pi-hole dns loop').native}.jsonl"))
    transcript.write_text("{not json\n")
    got = collected()
    assert "pi-hole dns loop" not in got and "keyboard matrix scan" in got
    assert "skipped one Claude Code Session" in caplog.text


def test_missing_stores_collect_nothing(throwaway_home):
    assert sources.collect(ALL, Held(), frozenset(), Sent(), None) == ([], [])


def test_t3_lineage_reads_a_configured_root(stores, tmp_path):
    (stores / ".t3").rename(tmp_path / "t3")
    settings = replace(ALL, interfaces={"t3_code": InterfaceSetting("owner", str(tmp_path / "t3"))})
    assert {c.evidence.title for c in collected(settings).values() if c.evidence.interface} == {
        s.title for s in world.via_interface(NOW)}


@pytest.mark.parametrize("harness", [CC, CX, DV, HM])
def test_a_part_read_again_adds_to_the_held_copy_only_the_lines_it_lacks(stores, harness):
    adapter = sources.ADAPTERS[harness]
    unit = next(u for u in adapter.units(ALL, Sent()) if adapter.read(ALL, u, Sent()) is not None)
    first = adapter.read(ALL, unit, Sent())
    copies = {p.origin: first.raw[k] for k, p in zip(first.raw, first.evidence.parts)}
    again = adapter.parse(ALL, adapter.fetch(unit, Sent()), copies.get, Sent())
    assert again.raw == first.raw and again.evidence.turns == first.evidence.turns
    held = {origin: copy + b'{"invented": "a line the store no longer holds"}\n' for origin, copy in copies.items()}
    kept = adapter.parse(ALL, adapter.fetch(unit, Sent()), held.get, Sent())
    assert [kept.raw[k] for k in first.raw] == list(held.values())


def test_identities_are_the_ones_each_harness_gives_a_line():
    cc, cx, dv, hm = (sources.ADAPTERS[h] for h in (CC, CX, DV, HM))
    line = {"type": "user", "uuid": "u-1", "timestamp": "2026-11-08T10:00:00Z", "message": {"content": "invented"}}
    assert cc.identity(line) == cc.identity({**line, "message": {"content": "rewritten"}})
    later = {**line, "uuid": None, "timestamp": "2026-11-08T10:09:00Z"}
    assert cc.identity({**line, "uuid": None}) != cc.identity(later)
    event = {"timestamp": "2026-11-08T10:00:00Z", "type": "event_msg", "payload": {"type": "item_completed"}}
    assert cx.identity(event) == cx.identity(json.loads(json.dumps(event, indent=2)))
    session = {"id": "brave-otter", "created_at": 1, "working_directory": "/w", "title": "invented", "cogs_json": "[]"}
    assert dv.identity({"sessions": session}) == dv.identity({"sessions": {**session, "cogs_json": "[1]",
                                                                           "last_activity_at": 9}})
    assert dv.identity({"sessions": session}) != dv.identity({"sessions": {**session, "title": "renamed"}})
    message = {"role": "user", "content": "invented", "message_id": "m1", "metadata": {"is_user_input": True}}
    node = {"session_id": "brave-otter", "node_id": 1, "chat_message": json.dumps(message), "created_at": 1}
    copied = {**node, "node_id": 9, "created_at": 5, "chat_message": json.dumps(
        {**message, "metadata": {"is_user_input": True, "metrics": {"invented": 1}}})}
    assert dv.identity({"message_nodes": node}) == dv.identity({"message_nodes": copied})
    for meta, same in (({"is_user_input": True, "extensions": ["chisel/acp-content-blocks"]}, True),
                       ({"is_user_input": True, "extensions": {}}, True),
                       ({"is_user_input": True, "extensions": {"subagent/handoff": {}}}, False),
                       ({"is_user_input": False}, False)):
        again = {**node, "chat_message": json.dumps({**message, "metadata": meta})}
        assert (dv.identity({"message_nodes": node}) == dv.identity({"message_nodes": again})) is same
    row = {"id": 3, "session_id": "s", "role": "assistant", "content": "invented", "timestamp": 1.5, "active": 1,
           "tool_calls": json.dumps([{"id": "call_1", "function": {"arguments": "{\"long\": 1}"}}])}
    retried = {**row, "id": 40, "active": 0, "compacted": 1,
               "tool_calls": json.dumps([{"id": "call_1", "function": {"arguments": "{}"}}])}
    assert hm.identity({"messages": row}) == hm.identity({"messages": retried})
    assert hm.identity({"messages": row}) != hm.identity({"messages": {**row, "timestamp": 2.5}})
    ask = {**row, "role": "user", "content": "invented ask", "tool_calls": None}
    handoff = {**ask, "id": 41, "content": HANDOFF}
    assert hm.identity({"messages": handoff}) == hm.identity({"messages": ask})
    assert hm.identity({"messages": {**handoff, "display_kind": "hidden"}}) == hm.identity({"messages": ask})
    assert hm.identity({"messages": {**handoff, "display_kind": "steer"}}) != hm.identity({"messages": ask})
    assert hm.identity({"messages": {**ask, "message_uid": "u1", "absorbed_message_uids": "[]"}}) == hm.identity({"messages": ask})
    assert hm.identity({"messages": {**ask, "absorbed_message_uids": '["u2"]'}}) != hm.identity({"messages": ask})


SUMMARY = "[CONTEXT SUMMARY]: invented summary"
END = "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
DELIMITER = "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]"
PRIOR = "[PRIOR CONTEXT — for reference only; not a new message]"
HANDOFF = f"{SUMMARY}\n\n{END}\n\ninvented ask"


@pytest.mark.parametrize("content, kind, ask", [
    (HANDOFF, None, "invented ask"),
    (f"{PRIOR}\ninvented ask\n\n{DELIMITER}\n\n{SUMMARY}\n\n{END}", None, "invented ask"),
    (f"invented ask\n\n{DELIMITER}\n\n{SUMMARY}\n\n{END}", "hidden", "invented ask"),
    ("\x00json:" + json.dumps([{"type": "text", "text": f"{SUMMARY}\n\n{END}\n\n"},
                               {"type": "image_url", "image_url": {"url": "invented"}}]),
     None, "\x00json:" + json.dumps([{"type": "image_url", "image_url": {"url": "invented"}}])),
    (HANDOFF, "notice", None),
    (f"{SUMMARY}\n\n{END}", None, None),
    (f"{SUMMARY}\n\n{END}\n\n[IMPORTANT: Background process 7 finished]", None, None),
    (f"{SUMMARY}\n\n{END}\n\n{SUMMARY}", None, None),
    ("invented ask", None, None),
    (f"[CONTEXT SUMMARY] invented summary\n\n{END}\n\ninvented ask", None, None),
], ids=["after-end-marker", "before-delimiter", "hidden-kind", "content-parts", "other-kind", "summary-alone",
        "synthetic-ask", "summary-as-ask", "plain-row", "no-known-prefix"])
def test_a_hermes_user_handoff_row_carries_the_live_ask_hermes_keys_it_by(content, kind, ask):
    assert live_ask(content, kind) == ask


def _with_handoff(db, sid: str, at: float) -> None:
    with closing(sqlite3.connect(db)) as con:
        con.executemany("INSERT INTO messages (session_id, role, content, timestamp, active, compacted) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [(sid, "user", "invented ask", at, 0, 1), (sid, "assistant", "invented answer", at + 1, 0, 1),
                         (sid, "user", HANDOFF, at, 1, 0), (sid, "assistant", "invented answer", at + 1, 1, 0)])
        con.commit()


def test_hermes_shows_a_user_handoff_row_and_its_ask_as_one_turn(stores):
    _with_handoff(stores / ".hermes/state.db", world.by_title("shelf bracket sizes").native, NOW.timestamp())
    turns = [(t.role, t.text) for t in natives(HM)["shelf bracket sizes"].evidence.turns]
    assert turns[-2:] == [("owner", "invented ask"), ("agent", "invented answer")]
    assert [text for _, text in turns].count("invented ask") == 1
    assert not any(SUMMARY in text for _, text in turns)


def test_a_copy_held_under_the_old_hermes_key_gains_no_line_or_turn(stores):
    adapter, sid = sources.ADAPTERS[HM], world.by_title("shelf bracket sizes").native
    _with_handoff(stores / ".hermes/state.db", sid, NOW.timestamp())
    unit = next(u for u in adapter.units(ALL, Sent()) if u.native_id == sid)
    fetched = adapter.fetch(unit, Sent())
    data = fetched.parts["main"][2]
    assert data.count(b"invented ask") == 2
    held = {part_origin(HM, unit.native_key, "main"): data}
    again = adapter.parse(ALL, fetched, held.get, Sent())
    assert again.raw["main"] == data
    assert [t.text for t in again.evidence.turns].count("invented ask") == 1
    assert not any(SUMMARY in t.text for t in again.evidence.turns)
    fresh = adapter.read(ALL, unit, Sent())
    assert fresh.raw["main"].count(b"invented ask") == 1
    assert fresh.evidence.turns == again.evidence.turns


CALLS = [{"id": f"call_{c}", "type": "function", "function": {"name": "terminal", "arguments": "{}"}} for c in "ab"]
NEXT = [("user", "invented next ask", "u8", None, {}), ("assistant", "invented next answer", "u9", None, {})]
RETRIED = {
    "user;user": ([("user", "invented first ask", "u1", None, {}), ("user", "invented second ask", "u2", None, {}),
                   ("assistant", "invented answer", "u3", None, {}), *NEXT],
                  [("user", "invented first ask\n\ninvented second ask", "u1", ["u2"], {}),
                   ("assistant", "invented answer", "u3", None, {})]),
    "user;user with whitespace": ([("user", "invented first ask \n", "u1", None, {}),
                                   ("user", "invented second ask\n\n", "u2", None, {}),
                                   ("assistant", "invented answer", "u3", None, {}), *NEXT],
                                  [("user", "invented first ask\n\ninvented second ask", "u1", ["u2"], {}),
                                   ("assistant", "invented answer", "u3", None, {})]),
    "assistant;assistant": ([("user", "invented ask", "u1", None, {}),
                             ("assistant", "invented first half", "u2", None, {}),
                             ("assistant", "invented second half", "u3", None, {}), *NEXT],
                            [("user", "invented ask", "u1", None, {}),
                             ("assistant", "invented first half\ninvented second half", "u2", ["u3"], {})]),
    "unanswered call": ([("user", "invented ask", "u1", None, {}),
                         ("assistant", "invented look", "u2", None, {"tool_calls": json.dumps(CALLS)}),
                         ("tool", "invented output", "u3", None, {"tool_call_id": "call_a", "tool_name": "terminal"}),
                         ("assistant", "invented reply", "u4", None, {}), *NEXT],
                        [("user", "invented ask", "u1", None, {}),
                         ("assistant", "invented look", "u2", None, {"tool_calls": json.dumps(CALLS[:1])}),
                         ("tool", "invented output", "u3", None, {"tool_call_id": "call_a", "tool_name": "terminal"}),
                         ("assistant", "invented reply", "u4", None, {})]),
    "rewrite of rows stored with whitespace": ([("user", "invented ask\n", "u1", None, {}),
                                                ("assistant", "invented answer\n\n", "u2", None, {}), *NEXT],
                                               [("user", "invented ask", "u1", None, {}),
                                                ("assistant", "invented answer", "u2", None, {})]),
    "unanswered call stored with whitespace": ([("user", "invented ask", "u1", None, {}),
                                                ("assistant", " invented look\n", "u2", None, {"tool_calls": json.dumps(CALLS)}),
                                                ("tool", "invented output", "u3", None, {"tool_call_id": "call_a", "tool_name": "terminal"}),
                                                ("assistant", "invented reply", "u4", None, {}), *NEXT],
                                               [("user", "invented ask", "u1", None, {}),
                                                ("assistant", "invented look", "u2", None, {"tool_calls": json.dumps(CALLS[:1])}),
                                                ("tool", "invented output", "u3", None, {"tool_call_id": "call_a", "tool_name": "terminal"}),
                                                ("assistant", "invented reply", "u4", None, {})]),
}


def _chat(db, sid: str, at: float, rows) -> None:
    """The chat's rows as Hermes v0.23.0 stores them: a /retry deletes the session's active rows
    and inserts what its alternation repair kept, each under the message_uid it had."""
    with closing(sqlite3.connect(db)) as con:
        for column in ("message_uid", "absorbed_message_uids"):
            if column not in {c[1] for c in con.execute("PRAGMA table_info(messages)")}:
                con.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")
        con.execute("DELETE FROM messages WHERE session_id = ? AND message_uid IS NOT NULL", (sid,))
        for role, content, uid, absorbed, extra in rows:
            when = at + int(uid[1:])
            con.execute("INSERT INTO messages (session_id, role, content, timestamp, message_uid, absorbed_message_uids, "
                        "tool_calls, tool_call_id, tool_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (sid, role, content, when, f"{sid}-{uid}", absorbed and json.dumps([f"{sid}-{u}" for u in absorbed]),
                         extra.get("tool_calls"), extra.get("tool_call_id"), extra.get("tool_name")))
        con.commit()


def _invented(turns) -> list[tuple[str, str]]:
    return [(t.role, t.text) for t in turns if t.text.startswith("invented")]


@pytest.mark.parametrize("before, after", RETRIED.values(), ids=RETRIED)
def test_a_row_hermes_retry_repaired_shows_its_words_once(stores, before, after):
    adapter, sid = sources.ADAPTERS[HM], world.by_title("shelf bracket sizes").native
    db = stores / ".hermes/state.db"
    _chat(db, sid, NOW.timestamp(), before)
    unit = next(u for u in adapter.units(ALL, Sent()) if u.native_id == sid)
    first = adapter.read(ALL, unit, Sent())
    said = [(("owner" if role == "user" else "agent"), text.strip()) for role, text, *_ in before if role != "tool"]
    assert _invented(first.evidence.turns) == said
    _chat(db, sid, NOW.timestamp(), after)
    unit = next(u for u in adapter.units(ALL, Sent()) if u.native_id == sid)
    held = {part_origin(HM, unit.native_key, "main"): first.raw["main"]}
    retried = adapter.parse(ALL, adapter.fetch(unit, Sent()), held.get, Sent())
    assert retried.raw["main"].startswith(first.raw["main"])
    assert _invented(retried.evidence.turns) == said
    held = {part_origin(HM, unit.native_key, "main"): retried.raw["main"]}
    again = adapter.parse(ALL, adapter.fetch(unit, Sent()), held.get, Sent())
    assert again.raw["main"] == retried.raw["main"]
    assert again.evidence.turns == retried.evidence.turns


def test_a_repaired_row_whose_rows_atlas_never_held_is_a_turn(stores):
    sid = world.by_title("shelf bracket sizes").native
    _chat(stores / ".hermes/state.db", sid, NOW.timestamp(), RETRIED["user;user"][1])
    turns = _invented(natives(HM)["shelf bracket sizes"].evidence.turns)
    assert turns == [("owner", "invented first ask\n\ninvented second ask"), ("agent", "invented answer")]


def test_a_row_under_a_held_uid_with_other_words_is_a_turn(stores):
    adapter, sid = sources.ADAPTERS[HM], world.by_title("shelf bracket sizes").native
    db = stores / ".hermes/state.db"
    before, _ = RETRIED["user;user"]
    _chat(db, sid, NOW.timestamp(), before)
    unit = next(u for u in adapter.units(ALL, Sent()) if u.native_id == sid)
    held = {part_origin(HM, unit.native_key, "main"): adapter.read(ALL, unit, Sent()).raw["main"]}
    _chat(db, sid, NOW.timestamp(), [("user", "invented first ask\n\ninvented other ask", "u1", ["u2"], {})])
    unit = next(u for u in adapter.units(ALL, Sent()) if u.native_id == sid)
    edited = adapter.parse(ALL, adapter.fetch(unit, Sent()), held.get, Sent())
    assert ("owner", "invented first ask\n\ninvented other ask") in _invented(edited.evidence.turns)


def _reread(sid: str, previous=None, sent=Sent()):
    """The Hermes Session as its store holds it now, read onto the raw copies `previous` holds."""
    adapter = sources.ADAPTERS[HM]
    unit = next(u for u in adapter.units(ALL, sent) if u.native_id == sid)
    held = {part_origin(HM, unit.native_key, k): data for k, data in (previous.raw if previous else {}).items()}
    return adapter.parse(ALL, adapter.fetch(unit, sent), held.get, sent)


@pytest.mark.parametrize("edited", [
    [("user", "invented code:\nif invented_flag:\n    invented_act()\ninvented_stop()", "u3", None, {})],
    [("user", "invented first ask\ninvented second ask", "u1", ["u2"], {})],
], ids=["indentation", "join Hermes does not make"])
def test_a_row_whose_words_differ_from_its_held_uids_only_in_whitespace_is_a_turn(stores, edited):
    sid = world.by_title("shelf bracket sizes").native
    db = stores / ".hermes/state.db"
    _chat(db, sid, NOW.timestamp(), [
        ("user", "invented code:\nif invented_flag:\n    invented_act()\n    invented_stop()", "u3", None, {}),
        ("user", "invented first ask", "u1", None, {}), ("user", "invented second ask", "u2", None, {})])
    first = _reread(sid)
    _chat(db, sid, NOW.timestamp(), edited)
    assert ("owner", edited[0][1]) in _invented(_reread(sid, first).evidence.turns)


def test_a_repair_of_an_edited_row_shows_no_words_twice(stores):
    sid = world.by_title("shelf bracket sizes").native
    db = stores / ".hermes/state.db"
    asks = [("user", "invented first ask", "u1", None, {}), ("user", "invented second ask", "u2", None, {})]
    _chat(db, sid, NOW.timestamp(), asks)
    first = _reread(sid)
    _chat(db, sid, NOW.timestamp(), [("user", "invented edited ask", "u1", None, {}), asks[1]])
    edited = _reread(sid, first)
    _chat(db, sid, NOW.timestamp(), [("user", "invented edited ask\n\ninvented second ask", "u1", ["u2"], {})])
    assert _invented(_reread(sid, edited).evidence.turns) == _invented(edited.evidence.turns) == [
        ("owner", "invented first ask"), ("owner", "invented edited ask"), ("owner", "invented second ask")]


def test_a_later_repair_of_a_repaired_row_atlas_showed_shows_no_words_twice(stores):
    sid = world.by_title("shelf bracket sizes").native
    db = stores / ".hermes/state.db"
    _chat(db, sid, NOW.timestamp(), [("user", "invented first ask\n\ninvented second ask", "u1", ["u2"], {}),
                                     ("user", "invented third ask", "u3", None, {})])
    first = _reread(sid)
    _chat(db, sid, NOW.timestamp(), [
        ("user", "invented first ask\n\ninvented second ask\n\ninvented third ask", "u1", ["u2", "u3"], {})])
    assert _invented(_reread(sid, first).evidence.turns) == _invented(first.evidence.turns) == [
        ("owner", "invented first ask\n\ninvented second ask"), ("owner", "invented third ask")]


def test_a_continued_part_whose_row_shares_a_uid_with_an_earlier_part_is_a_turn(stores):
    sid, child = world.by_title("shelf bracket sizes").native, "20261116_094000_c0ffee"
    db = stores / ".hermes/state.db"
    _chat(db, sid, NOW.timestamp(), [("user", "invented repeated ask", "u1", None, {})])
    with closing(sqlite3.connect(db)) as con:
        con.execute("UPDATE sessions SET end_reason = 'compression' WHERE id = ?", (sid,))
        con.execute("INSERT INTO sessions (id, source, parent_session_id, started_at) VALUES (?, 'telegram', ?, ?)",
                    (child, sid, NOW.timestamp() + 100))
        con.commit()
    _chat(db, child, NOW.timestamp() + 100, [("user", "invented repeated ask", "u1", None, {})])
    with closing(sqlite3.connect(db)) as con:
        con.execute("UPDATE messages SET message_uid = ? WHERE session_id = ?", (f"{sid}-u1", child))
        con.commit()
    assert _invented(_reread(sid).evidence.turns) == [("owner", "invented repeated ask")] * 2


@pytest.mark.parametrize("brief, stored", [
    ("invented brief Atlas sent", "invented brief Atlas sent"),
    ("invented brief, rotate ghp_" + "a1" * 18, "invented brief, rotate [secret: github token]")], ids=["bare", "masked"])
def test_a_retry_that_merges_a_mirrored_row_into_the_reply_keeps_atlass_text_out(stores, brief, stored):
    sid, db, sent = world.by_title("shelf bracket sizes").native, stores / ".hermes/state.db", Sent(*[frozenset({stored})] * 2)
    _chat(db, sid, NOW.timestamp(), [("user", "invented ask", "u1", None, {}), ("assistant", "invented reply", "u2", None, {}),
                                     ("assistant", brief, "u3", None, {}), *NEXT])
    first = _reread(sid, sent=sent)
    _chat(db, sid, NOW.timestamp(), [("user", "invented ask", "u1", None, {}),
                                     ("assistant", f"invented reply\n{brief}", "u2", ["u3"], {})])
    retried = _reread(sid, first, sent)
    assert _invented(retried.evidence.turns) == _invented(first.evidence.turns) == [
        ("owner", "invented ask"), ("agent", "invented reply"),
        ("owner", "invented next ask"), ("agent", "invented next answer")]
    assert retried.raw == first.raw
    assert brief.encode() not in retried.raw["main"]
