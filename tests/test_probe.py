import json
import sqlite3
import uuid
from collections import Counter
from contextlib import closing
from dataclasses import replace

import pytest

from atlas import sources
from atlas.app import Atlas
from atlas.model import HarnessId, HarnessSetting, InterfaceSetting
from atlas.record import Record
from harness import seeds, world
from harness.stores import DEFAULT_ROOTS, T3, materialise, write_store
from test_commit import SETTINGS

BERLIN = replace(SETTINGS, timezone="Europe/Berlin")
ENV = {HarnessId.CLAUDE_CODE: "CLAUDE_CONFIG_DIR", HarnessId.CODEX: "CODEX_HOME", HarnessId.HERMES: "HERMES_HOME",
       T3: "T3CODE_HOME"}
STORES = [HarnessId.CLAUDE_CODE, HarnessId.CODEX, HarnessId.DEVIN, HarnessId.HERMES]


def found(store, settings=BERLIN):
    name = "T3 Code" if store == T3 else store
    return next((f for f in sources.probe(settings) if f.harness == name), None)


def with_root(store, root: str):
    if store == T3:
        return replace(BERLIN, interfaces={"t3_code": InterfaceSetting("owner", root)})
    return replace(BERLIN, harnesses={store: HarnessSetting(True, "store", (root,), "owner")})


@pytest.mark.parametrize("store", STORES)
def test_the_platform_default_is_found_with_the_worlds_own_count(store, throwaway_home):
    write_store(store, throwaway_home / DEFAULT_ROOTS[store], throwaway_home, world.SETUP_AT)
    f = found(store)
    count, since = world.owner_sessions(store, world.SETUP_AT)
    assert (f.sessions, f.since.date()) == (count, since)
    assert f.roots == (f"~/{DEFAULT_ROOTS[store]}",)


def t3_count(now):
    threads = world.via_interface(now)
    return len(threads), min(s.start.date() for s in threads)


def test_t3_code_counts_its_top_level_threads_not_its_subagents(throwaway_home):
    write_store(T3, throwaway_home / ".t3", throwaway_home, world.SETUP_AT)
    f = found(T3)
    assert (f.sessions, f.since.date()) == t3_count(world.SETUP_AT)


def test_a_t3_code_fork_counts_as_its_own_session(throwaway_home):
    write_store(T3, throwaway_home / ".t3", throwaway_home, world.SETUP_AT)
    thread = {s.title: str(uuid.uuid5(uuid.NAMESPACE_URL, f"t3:{s.native}")) for s in world.SESSIONS}
    parent = thread["ledger csv export"]
    with closing(sqlite3.connect(throwaway_home / ".t3/userdata/statev2.sqlite")) as con:
        con.execute("UPDATE orchestration_v2_projection_threads SET payload_json = ? WHERE thread_id = ?", (json.dumps(
            {"lineage": {"parentThreadId": parent, "relationshipToParent": "fork", "rootThreadId": parent}}),
            thread["pdf vat rounding"]))
        con.commit()
    f = found(T3)
    assert (f.sessions, f.since.date()) == t3_count(world.SETUP_AT)


@pytest.mark.parametrize("store", list(ENV))
def test_the_env_override_comes_before_the_platform_default(store, throwaway_home, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    write_store(store, elsewhere, throwaway_home, world.SETUP_AT)
    write_store(store, throwaway_home / DEFAULT_ROOTS[store], throwaway_home, world.WEEK_START.replace(month=7))
    monkeypatch.setenv(ENV[store], str(elsewhere))
    f = found(store)
    assert f.roots == (str(elsewhere),)
    if store != T3:
        assert f.sessions == world.owner_sessions(store, world.SETUP_AT)[0]


@pytest.mark.parametrize("store", STORES + [T3])
def test_a_root_added_in_settings_is_found(store, throwaway_home, tmp_path):
    added = tmp_path / "added"
    write_store(store, added, throwaway_home, world.SETUP_AT)
    assert found(store) is None
    f = found(store, with_root(store, str(added)))
    assert f.roots == (str(added),)
    assert f.sessions > 0


@pytest.mark.parametrize("store", STORES + [T3])
def test_an_absent_store_is_not_found(store, throwaway_home):
    (throwaway_home / DEFAULT_ROOTS[store]).mkdir(parents=True)
    assert found(store) is None


def test_a_second_copy_of_a_store_counts_once(throwaway_home, tmp_path):
    write_store(HarnessId.CODEX, throwaway_home / ".codex", throwaway_home, world.SETUP_AT)
    write_store(HarnessId.CODEX, tmp_path / "orca-codex", throwaway_home, world.SETUP_AT)
    f = found(HarnessId.CODEX, with_root(HarnessId.CODEX, str(tmp_path / "orca-codex")))
    assert f.roots == ("~/.codex", str(tmp_path / "orca-codex"))
    assert f.sessions == world.owner_sessions(HarnessId.CODEX, world.SETUP_AT)[0]


def test_a_binary_on_path_finds_nothing(throwaway_home, monkeypatch):
    bin_dir = throwaway_home / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex", "devin", "hermes", "t3"):
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert sources.probe(BERLIN) == []


def test_every_harness_has_a_source():
    assert set(HarnessId) == set(sources.ADAPTERS) | {HarnessId.CHATGPT, HarnessId.CLAUDE_AI}


def t3_names_as_subagent(home, child: str, parent: str):
    thread = {title: str(uuid.uuid5(uuid.NAMESPACE_URL, f"t3:{title}")) for title in (child, parent)}
    lineage = {child: {"parentThreadId": thread[parent], "relationshipToParent": "subagent"}, parent: {}}
    driver = {HarnessId.CLAUDE_CODE: "claudeAgent", HarnessId.CODEX: "codex"}
    with closing(sqlite3.connect(home / ".t3/userdata/statev2.sqlite")) as con:
        for title in (child, parent):
            s = world.by_title(title)
            con.execute("INSERT INTO orchestration_v2_projection_threads VALUES (?, '', ?, '', ?, ?, NULL, NULL, ?)",
                        (thread[title], title, s.start.isoformat(), s.end.isoformat(),
                         json.dumps({"lineage": lineage[title]})))
            ref = {"nativeThreadRef": {"driver": driver[s.harness], "nativeId": s.native}}
            con.execute("INSERT INTO orchestration_v2_projection_provider_threads VALUES (?, ?)",
                        (thread[title], json.dumps(ref)))
        con.commit()


def test_setup_counts_the_sessions_backfill_keeps_when_t3_code_names_subagent_children(throwaway_home, tmp_path):
    materialise(throwaway_home, world.SETUP_AT)
    t3_names_as_subagent(throwaway_home, "keyboard matrix scan", "split layout keymap")
    t3_names_as_subagent(throwaway_home, "gpx tunnel gaps", "gpx elevation smoothing")
    probed = {f.harness: f.sessions for f in sources.probe(seeds.AFTER_SETUP) if not f.is_interface}
    seeds.after_scene_1(tmp_path / "atlas")
    Atlas.open(tmp_path / "atlas").backfill(world.SETUP_AT)
    kept = Counter(s.harness for s in Record.open(tmp_path / "atlas").snapshot().sessions())
    assert probed == {h: kept[h] for h in STORES}
    assert (probed[HarnessId.CLAUDE_CODE], probed[HarnessId.CODEX]) == (
        world.owner_sessions(HarnessId.CLAUDE_CODE, world.SETUP_AT)[0] - 1,
        world.owner_sessions(HarnessId.CODEX, world.SETUP_AT)[0] - 1)


def test_setup_leaves_out_the_sessions_of_an_excluded_folder_as_backfill_does(throwaway_home, tmp_path):
    materialise(throwaway_home, world.SETUP_AT)
    settings = replace(seeds.AFTER_SETUP, exclusions=("~/code/kb-firmware",))
    probed = {f.harness: f.sessions for f in sources.probe(settings) if not f.is_interface}
    Record.open(tmp_path / "atlas").create(settings, seeds.PROFILE)
    Atlas.open(tmp_path / "atlas").backfill(world.SETUP_AT)
    kept = Counter(s.harness for s in Record.open(tmp_path / "atlas").snapshot().sessions())
    assert probed == {h: kept[h] for h in STORES}
    assert probed[HarnessId.CLAUDE_CODE] == world.owner_sessions(HarnessId.CLAUDE_CODE, world.SETUP_AT)[0] - 3
