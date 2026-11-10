"""The Settings tab: ChangeSettings checked and applied (settings_rules, pure), written through the
record with a Board Note, a folder added in Settings found by sources.probe, a folder reached
through a link refused, and the dashboard routes."""
import json
import logging
from dataclasses import replace
from datetime import date, time, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas import settings_rules, sources
from atlas.app import Atlas
from atlas.dashboard import plugin_api
from atlas.model import ChangeSettings, HarnessId, HarnessSetting, Librarian, Owner
from atlas.record import Conflict, Record
from harness import seeds, world
from harness.home import AtlasHome
from harness.stores import write_store
from snapshots import SETTINGS, after, at, snapshot

NOW = at(date(2026, 11, 15), "20:05")
CLICK = Owner("board", "nt-0set0001")
INBOX = "~/atlas/inbox"
HERE = settings_rules.Here(INBOX)
BAD = "~/invented\ud800x"


def changed(patch, settings=SETTINGS, linked=False, defaults={}):
    return settings_rules.change(settings, patch, settings_rules.Here(INBOX, linked, defaults))


def test_a_web_chat_is_added_as_an_export_harness_waiting_in_the_inbox():
    s, said = changed({"add_harness": "chatgpt"})
    s, _ = changed({"add_harness": "claude_ai"}, s)
    assert s.harnesses[HarnessId.CHATGPT] == HarnessSetting(True, "export", (INBOX,), "owner")
    assert said == "Added ChatGPT in Settings."
    assert settings_rules.added(s) == [("ChatGPT", f"waiting for an export in {INBOX}"),
                                       ("claude.ai", f"waiting for an export in {INBOX}")]
    assert s.late_harnesses == {HarnessId.CHATGPT, HarnessId.CLAUDE_AI}
    assert settings_rules.view(s)["addable"] == []


@pytest.mark.parametrize("patch, refused", [
    ({}, "ONE_CHANGE"),
    ({"brief_at": "08:00", "gap_minutes": 20}, "ONE_CHANGE"),
    ({"timezone": "Asia/Tokyo"}, "ONE_CHANGE"),
    ([("brief_at", "08:00")], "ONE_CHANGE"),
    ({"add_harness": "codex"}, "NOT_A_WEB_CHAT"),
    ({"add_harness": None}, "NOT_A_WEB_CHAT"),
    ({"brief_at": "7:30"}, "BAD_TIME"),
    ({"brief_at": "24:00"}, "BAD_TIME"),
    ({"brief_at": "07:60"}, "BAD_TIME"),
    ({"brief_at": 730}, "BAD_TIME"),
    ({"review": {"day": "funday", "at": "18:00"}}, "BAD_DAY"),
    ({"review": {"day": "sunday", "at": "6pm"}}, "BAD_TIME"),
    ({"review": "sunday 18:00"}, "BAD_DAY"),
    ({"gap_minutes": 0}, "BAD_GAP"),
    ({"gap_minutes": 121}, "BAD_GAP"),
    ({"gap_minutes": "15"}, "BAD_GAP"),
    ({"gap_minutes": True}, "BAD_GAP"),
    ({"gap_minutes": 15.5}, "BAD_GAP"),
    ({"add_root": {"harness": "codex", "root": "work/codex"}}, "BAD_PATH"),
    ({"add_root": {"harness": "codex", "root": "~/work/../.ssh"}}, "BAD_PATH"),
    ({"add_root": {"harness": "codex", "root": "/srv/./codex"}}, "BAD_PATH"),
    ({"add_root": {"harness": "codex", "root": "/"}}, "BAD_PATH"),
    ({"add_root": {"harness": "codex", "root": "~/"}}, "BAD_PATH"),
    ({"add_root": {"harness": "codex", "root": "~/codex//x"}}, "BAD_PATH"),
    ({"add_root": {"harness": "codex", "root": "~/codex/"}}, "BAD_PATH"),
    ({"add_root": {"harness": "codex", "root": "~/co\ndex"}}, "BAD_PATH"),
    ({"add_root": {"harness": "codex", "root": "~/" + "x" * 1100}}, "BAD_PATH"),
    ({"add_root": {"harness": "codex"}}, "BAD_PATH"),
    ({"add_root": {"harness": "chatgpt", "root": "~/exports"}}, "NOT_A_STORE"),
    ({"remove_root": {"harness": "codex", "root": "~/never-added"}}, "NO_SUCH_ROOT"),
    ({"add_exclusion": "clients"}, "BAD_PATH"),
    ({"remove_exclusion": "~/never-named"}, "NO_SUCH_EXCLUSION"),
    ({"atlas_chat": "telegram"}, "BAD_CHAT"),
    ({"atlas_chat": "telegram:abc"}, "BAD_CHAT"),
    ({"atlas_chat": "slack:-1001:7"}, "BAD_CHAT"),
    ({"atlas_chat": 42}, "BAD_CHAT"),
    ({"atlas_chat": "telegram:\u0661\u0662"}, "BAD_CHAT"),
    ({"brief_at": "\u0660\u0667:30"}, "BAD_TIME"),
    ({"add_root": {"harness": "codex", "root": BAD}}, "BAD_PATH"),
    ({"remove_root": {"harness": "codex", "root": BAD}}, "BAD_PATH"),
    ({"add_exclusion": BAD}, "BAD_PATH"),
    ({"atlas_chat": "telegram:-1001\ud800"}, "BAD_CHAT"),
])
def test_an_invalid_change_is_refused_with_a_reason(patch, refused):
    assert changed(patch) == refused
    assert refused in settings_rules.REASONS


def test_a_folder_reached_through_a_link_is_refused():
    assert changed({"add_root": {"harness": "codex", "root": "~/orca-codex"}}, linked=True) == "LINKED"


@pytest.mark.parametrize("patch, field, value", [
    ({"brief_at": "08:15"}, "brief_at", time(8, 15)),
    ({"review": {"day": "saturday", "at": "17:00"}}, "review_day", 6),
    ({"gap_minutes": 20}, "gap", timedelta(minutes=20)),
    ({"add_exclusion": "~/clients"}, "exclusions", ("~/clients",)),
])
def test_a_valid_change_lands_in_settings(patch, field, value):
    s, _ = changed(patch)
    assert getattr(s, field) == value


def test_a_root_is_added_once_to_its_harness_and_removed_again():
    s, said = changed({"add_root": {"harness": "codex", "root": "~/orca/codex"}})
    assert said == "Added a Codex folder in Settings."
    again, _ = changed({"add_root": {"harness": "codex", "root": "~/orca/codex"}}, s)
    assert again == s and s.harnesses[HarnessId.CODEX].roots[-1] == "~/orca/codex"
    devin, _ = changed({"add_root": {"harness": "devin", "root": "~/Library/Application Support/devin"}}, s)
    assert devin.harnesses[HarnessId.DEVIN] == HarnessSetting(True, "store", ("~/Library/Application Support/devin",), "owner")
    back, _ = changed({"remove_root": {"harness": "codex", "root": "~/orca/codex"}}, s)
    assert back.harnesses[HarnessId.CODEX] == SETTINGS.harnesses.get(HarnessId.CODEX, back.harnesses[HarnessId.CODEX])


def test_the_folder_atlas_always_reads_is_shown_found_and_cannot_be_removed():
    s = replace(SETTINGS, harnesses={HarnessId.CODEX: HarnessSetting(True, "store", ("~/.codex", "~/orca/codex"), "probe")})
    defaults = {HarnessId.CODEX: "~/.codex"}
    assert changed({"remove_root": {"harness": "codex", "root": "~/.codex"}}, s, defaults=defaults) == "ALWAYS_READ"
    kept, _ = changed({"remove_root": {"harness": "codex", "root": "~/orca/codex"}}, s, defaults=defaults)
    assert kept.harnesses[HarnessId.CODEX].roots == ("~/.codex",)
    (codex,) = settings_rules.view(s, defaults)["found"]
    assert (codex["roots"], codex["always"]) == (["~/.codex", "~/orca/codex"], "~/.codex")


def test_a_folder_settings_md_already_holds_that_is_not_utf8_can_be_removed():
    s = replace(SETTINGS, exclusions=(BAD,),
                harnesses={HarnessId.CODEX: HarnessSetting(True, "store", ("~/.codex", BAD), "owner")})
    gone, _ = changed({"remove_root": {"harness": "codex", "root": BAD}}, s)
    assert gone.harnesses[HarnessId.CODEX].roots == ("~/.codex",)
    gone, _ = changed({"remove_exclusion": BAD}, s)
    assert gone.exclusions == ()


def test_atlas_chat_is_set_and_cleared():
    s, _ = changed({"atlas_chat": "telegram:-1001:7"})
    assert s.channel.atlas_chat == "telegram:-1001:7"
    cleared, said = changed({"atlas_chat": None}, s)
    assert cleared.channel.atlas_chat is None and said == "Moved Atlas back to the Hermes home chat in Settings."


def test_a_change_writes_settings_and_a_board_note_that_names_no_folder():
    snap = snapshot()
    d = settings_rules.from_settings(snap, ChangeSettings({"add_exclusion": "~/clients/invented"}), CLICK, NOW, HERE)
    assert d.outcome.ok and after(d).settings().exclusions == ("~/clients/invented",)
    (note,) = after(d).notes()
    assert (note.id, note.via, note.text) == ("nt-0set0001", "board", "Added an exclusion in Settings.")
    assert "clients" not in note.text + (note.atlas_reply or "")


def test_a_change_that_changes_nothing_writes_nothing():
    snap = after(settings_rules.from_settings(snapshot(), ChangeSettings({"add_harness": "chatgpt"}), CLICK, NOW, HERE))
    d = settings_rules.from_settings(snap, ChangeSettings({"add_harness": "chatgpt"}), CLICK, NOW, HERE)
    assert d.outcome.ok and d.changes.is_empty()


def test_a_refused_change_writes_nothing_and_only_the_owner_may_change_settings():
    d = settings_rules.from_settings(snapshot(), ChangeSettings({"gap_minutes": 0}), CLICK, NOW, HERE)
    assert (d.outcome.ok, d.outcome.refused, d.changes.is_empty()) == (False, "BAD_GAP", True)
    d = settings_rules.from_settings(snapshot(), ChangeSettings({"gap_minutes": 20}), Librarian((), 0), NOW, HERE)
    assert (d.outcome.ok, d.outcome.refused) == (False, "OWNER_ONLY")


# ------------------------------------------------------------------ folders on disk

def test_sources_probe_finds_a_codex_home_added_in_settings(throwaway_home):
    write_store(HarnessId.CODEX, throwaway_home / "orca" / "codex", throwaway_home, world.SETUP_AT)
    base = replace(SETTINGS, timezone="Europe/Berlin", harnesses={})
    assert not any(f.harness == HarnessId.CODEX for f in sources.probe(base))
    s, _ = changed({"add_root": {"harness": "codex", "root": "~/orca/codex"}}, base)
    (f,) = [f for f in sources.probe(s) if f.harness == HarnessId.CODEX]
    assert f.roots == ("~/orca/codex",)
    assert f.sessions == world.owner_sessions(HarnessId.CODEX, world.SETUP_AT)[0]


def test_a_folder_with_a_link_in_its_path_is_linked(throwaway_home, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "codex").mkdir(parents=True)
    (throwaway_home / "orca").symlink_to(elsewhere)
    (throwaway_home / "plain" / "codex").mkdir(parents=True)
    assert sources.linked("~/orca/codex") and sources.linked("~/orca")
    assert not sources.linked("~/plain/codex") and not sources.linked("~/not-yet/codex")


@pytest.mark.parametrize("folder", ["sessions", "archived_sessions"])
def test_a_store_folder_one_level_inside_a_named_folder_that_is_a_link_is_linked(throwaway_home, tmp_path, folder):
    (tmp_path / "elsewhere").mkdir()
    (throwaway_home / "work" / "codex").mkdir(parents=True)
    (throwaway_home / "work" / "codex" / folder).symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    assert sources.linked("~/work/codex", "codex") and not sources.linked("~/work/codex")
    assert not sources.linked("~/work/codex", "claude_code")


@pytest.mark.parametrize("named", [True, False], ids=["added_folder", "default_folder"])
@pytest.mark.parametrize("folder", ["sessions", "archived_sessions"])
def test_a_store_folder_swapped_for_a_link_is_skipped_at_read_time(throwaway_home, tmp_path, caplog, named, folder):
    root = throwaway_home / ("work/codex" if named else ".codex")
    write_store(HarnessId.CODEX, tmp_path / "elsewhere", throwaway_home, world.SETUP_AT)
    root.mkdir(parents=True)
    if folder == "archived_sessions":
        (root / "sessions").mkdir()
    store = tmp_path / "elsewhere" / "sessions"
    (root / folder).symlink_to(store, target_is_directory=True)
    roots = ("~/work/codex",) if named else ()
    s = replace(SETTINGS, timezone="Europe/Berlin",
                harnesses={HarnessId.CODEX: HarnessSetting(True, "store", roots, "owner")})
    with caplog.at_level(logging.INFO):
        found = [f for f in sources.probe(s) if f.harness == HarnessId.CODEX]
    assert sum(f.sessions for f in found) == 0
    assert "LinkedElsewhere" in caplog.text and str(tmp_path) not in caplog.text


def test_collection_skips_a_linked_folder_in_settings_and_logs_no_path(throwaway_home, tmp_path, caplog):
    write_store(HarnessId.CODEX, tmp_path / "elsewhere", throwaway_home, world.SETUP_AT)
    (throwaway_home / "orca-link").symlink_to(tmp_path / "elsewhere")
    s = replace(SETTINGS, timezone="Europe/Berlin",
                harnesses={HarnessId.CODEX: HarnessSetting(True, "store", ("~/orca-link",), "owner")})
    with caplog.at_level(logging.INFO):
        assert not any(f.harness == HarnessId.CODEX for f in sources.probe(s))
    assert "LinkedElsewhere" in caplog.text
    assert "orca" not in caplog.text and str(tmp_path) not in caplog.text


# ------------------------------------------------------------------ the routes

@pytest.fixture
def dashboard(throwaway_home, monkeypatch):
    root = throwaway_home / "atlas"
    seeds.after_scene_1(root)
    monkeypatch.setenv("ATLAS_RECORD", str(root))
    monkeypatch.setenv("ATLAS_NOW", NOW.isoformat())
    app = FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/atlas")
    return TestClient(app)


API = "/api/plugins/atlas/settings"


def test_the_routes_add_both_web_chats_and_show_them_waiting(dashboard, throwaway_home):
    shown = dashboard.get(API).json()
    assert shown["added"] == [] and [a["harness"] for a in shown["addable"]] == ["chatgpt", "claude_ai"]
    assert [f["label"] for f in shown["found"]] == ["Claude Code", "Codex", "Devin", "Hermes"]
    for harness in ("chatgpt", "claude_ai"):
        assert dashboard.patch(API, json={"add_harness": harness}).status_code == 200
    shown = dashboard.get(API).json()
    assert shown["added"] == [{"label": "ChatGPT", "state": "waiting for an export in ~/atlas/inbox"},
                              {"label": "claude.ai", "state": "waiting for an export in ~/atlas/inbox"}]
    assert Record.open(throwaway_home / "atlas").reconcile() == []


def test_a_route_refuses_bad_input_with_a_reason_that_does_not_echo_the_folder(dashboard, throwaway_home, tmp_path, caplog):
    (tmp_path / "elsewhere").mkdir()
    (throwaway_home / "invented-link").symlink_to(tmp_path / "elsewhere")
    with caplog.at_level(logging.DEBUG):
        got = dashboard.patch(API, json={"add_root": {"harness": "codex", "root": "~/invented-link"}})
        bad = dashboard.patch(API, json={"add_root": {"harness": "codex", "root": "invented-relative"}})
    assert (got.status_code, got.json()["detail"]) == (422, settings_rules.REASONS["LINKED"])
    assert (bad.status_code, bad.json()["detail"]) == (422, settings_rules.REASONS["BAD_PATH"])
    assert "invented" not in got.text + bad.text + caplog.text
    assert dashboard.patch(API, json={"gap_minutes": 0}).status_code == 422
    assert dashboard.patch(API, json={"brief_at": "25:00"}).json()["detail"] == settings_rules.REASONS["BAD_TIME"]


def test_a_route_refuses_a_folder_whose_store_folder_is_a_link(dashboard, throwaway_home, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (throwaway_home / "work" / "codex").mkdir(parents=True)
    (throwaway_home / "work" / "codex" / "sessions").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    got = dashboard.patch(API, json={"add_root": {"harness": "codex", "root": "~/work/codex"}})
    assert (got.status_code, got.json()["detail"]) == (422, settings_rules.REASONS["LINKED"])


@pytest.mark.parametrize("patch, reason", [
    ({"add_exclusion": BAD}, "BAD_PATH"),
    ({"add_root": {"harness": "codex", "root": BAD}}, "BAD_PATH"),
    ({"atlas_chat": "telegram:-1001\ud800"}, "BAD_CHAT"),
    ({"brief_at": "07:3\ud800"}, "BAD_TIME"),
    ({"review": {"day": "sunday\ud800", "at": "18:00"}}, "BAD_DAY"),
    ({"add_harness": "chatgpt\ud800"}, "NOT_A_WEB_CHAT"),
])
def test_a_route_refuses_text_that_is_not_utf8_and_writes_nothing(dashboard, throwaway_home, patch, reason):
    settings = throwaway_home / "atlas" / "settings.md"
    before = settings.read_bytes()
    body = json.dumps(patch)
    got = dashboard.patch(API, content=body, headers={"content-type": "application/json"})
    assert (got.status_code, got.json()["detail"]) == (422, settings_rules.REASONS[reason])
    assert settings.read_bytes() == before and dashboard.get(API).status_code == 200


def test_the_route_refuses_to_remove_the_folder_atlas_always_reads(dashboard):
    codex = next(f for f in dashboard.get(API).json()["found"] if f["harness"] == "codex")
    assert codex["always"] in codex["roots"]
    got = dashboard.patch(API, json={"remove_root": {"harness": "codex", "root": codex["always"]}})
    assert (got.status_code, got.json()["detail"]) == (422, settings_rules.REASONS["ALWAYS_READ"])


@pytest.mark.parametrize("where", ["exclusion", "folder"])
def test_settings_md_that_holds_a_value_that_is_not_utf8_still_collects_every_session_and_shows(tmp_path, where):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(seeds.BACKFILLED)
        codex = seeds.AFTER_SETUP.harnesses[HarnessId.CODEX]
        s = (replace(seeds.AFTER_SETUP, exclusions=(BAD,)) if where == "exclusion" else replace(
            seeds.AFTER_SETUP, harnesses={**seeds.AFTER_SETUP.harnesses,
                                          HarnessId.CODEX: replace(codex, roots=(*codex.roots, BAD))}))
        Record.open(home.record_root).create(replace(s, brief_at=time(23, 59)), seeds.PROFILE)
        owner = {seeds.evidence_id(x) for x in world.visible(seeds.BACKFILLED) if x.is_owner_session}
        assert Atlas.open(home.record_root).tick(seeds.BACKFILLED).collected == len(owner)
        shown = home.dashboard("GET", "/settings")
        assert shown.status_code == 200
        if where == "exclusion":
            assert shown.json()["exclusions"] == [BAD]
            assert home.dashboard("PATCH", "/settings", {"remove_exclusion": BAD}).status_code == 200
        else:
            assert BAD in next(f for f in shown.json()["found"] if f["harness"] == "codex")["roots"]
            assert home.dashboard("PATCH", "/settings", {"remove_root": {"harness": "codex", "root": BAD}}).status_code == 200
        assert BAD not in json.dumps(home.dashboard("GET", "/settings").json())


def test_a_route_answers_404_before_setup_and_503_on_a_conflict(dashboard, throwaway_home, monkeypatch):
    def lost(*args, **kwargs):
        raise Conflict("settings.md")
    monkeypatch.setattr(Record, "commit", lost)
    assert dashboard.patch(API, json={"brief_at": "08:00"}).status_code == 503
    monkeypatch.setenv("ATLAS_RECORD", str(throwaway_home / "no-record-yet"))
    assert dashboard.get(API).status_code == 404



def test_a_tick_with_a_skipped_settings_folder_still_files_the_other_sessions_and_sends(tmp_path, caplog):
    """Scene 9b's night tick, then Tuesday's brief, with one folder in settings that is a link and
    one that cannot be encoded: the tick files the Sessions from the other stores and sends."""
    from harness.model import filing_model
    from atlas import filing
    with AtlasHome(tmp_path / "scene", filing_model()) as home:
        seeds.collecting(seeds.after_scene_9)(home.record_root)
        write_store(HarnessId.CODEX, tmp_path / "elsewhere", home.home, world.SETUP_AT)
        (home.home / "orca-link").symlink_to(tmp_path / "elsewhere")
        record = Record.open(home.record_root)
        s = record.snapshot().settings()
        codex = s.harnesses[HarnessId.CODEX]
        record.commit(record.snapshot().changes().put_settings(replace(s, harnesses={
            **s.harnesses, HarnessId.CODEX: replace(codex, roots=(*codex.roots, "~/orca-link", BAD))})))
        with caplog.at_level(logging.WARNING, logger="atlas"):
            for at in (world.SEEN_TICK_AT, world.TUESDAY_BRIEF_AT):
                home.set_clock(at)
                ran = home.cli("atlas", "tick", "--quiet")
                assert (ran.code, ran.out) == (0, ""), ran.err
        snap = Record.open(home.record_root).snapshot()
        assert filing.queue(snap, world.TUESDAY_BRIEF_AT) == []
        billing = snap.pursuit(seeds.BILLING)
        assert seeds.evidence_id(world.by_title("per-invoice vat rounding")) in {m.evidence for m in billing.sessions}
        assert len(home.sent()) == 1
        assert "LinkedElsewhere" in caplog.text and "UnicodeEncodeError" in caplog.text
        assert "orca" not in caplog.text and str(tmp_path) not in caplog.text
