import shlex
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from atlas import schedule
from atlas.app import Atlas, MODEL_LINE, OWN_CHAT_OFFER, SetupReport
from atlas.model import HarnessId
from atlas.sources import Found
from harness import tutorial
from harness.home import AtlasHome

BERLIN = ZoneInfo("Europe/Berlin")


def at(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=BERLIN)


def test_setup_report_renders_the_tutorials_text():
    report = SetupReport(
        record=Path.home() / "atlas", channel="Telegram, through the Hermes gateway", model=MODEL_LINE,
        found=(Found(HarnessId.CLAUDE_CODE, "~/.claude/projects", 412, at("2026-06-02")),
               Found(HarnessId.CODEX, "~/.codex/sessions", 538, at("2026-06-09")),
               Found(HarnessId.DEVIN, "~/.local/share/devin", 21, at("2026-07-15")),
               Found(HarnessId.HERMES, "~/.hermes/state.db", 6, at("2026-11-06")),
               Found("T3 Code", "~/.t3", 92, at("2026-07-30"), is_interface=True)),
        not_found=(), mcp_commands=("claude mcp add atlas -- hermes atlas mcp",), disclosures=("Writes: ~/atlas",),
        version="0.1", machine="ubuntu-pc", exclusions=(), own_chat_offer=OWN_CHAT_OFFER)
    expected = tutorial.fenced_block("Found on this machine")
    assert report.render().startswith(expected.rstrip("\n"))


@pytest.fixture
def hermes(tmp_path):
    with AtlasHome(tmp_path / "base") as home:
        yield home


def test_install_tick_is_idempotent_against_the_fake_hermes(hermes):
    hermes_home = hermes.home / ".hermes"
    first = schedule.install_tick(hermes_home)
    script = hermes_home / "scripts" / "atlas_tick.sh"
    written = (script.read_bytes(), script.stat().st_mtime_ns)
    second = schedule.install_tick(hermes_home)

    assert first == second
    assert (script.read_bytes(), script.stat().st_mtime_ns) == written
    assert script.stat().st_mode & 0o777 == 0o755
    assert [(j["name"], j["schedule"], j["no_agent"], j["catch_up"], j["deliver"], j["script"]) for j in hermes.cron_jobs()] == [
        ("atlas-tick", "*/10 * * * *", True, "off", "local", "atlas_tick.sh")]
    assert sum(call[:2] == ["cron", "create"] for call in hermes.hermes_calls()) == 1


def test_install_tick_gives_the_command_to_run_when_hermes_refuses(hermes):
    hermes.script_hermes("cron create", exit=1, stderr="gateway config unreadable")
    line = schedule.install_tick(hermes.home / ".hermes")
    assert "gateway config unreadable" in line
    assert line.endswith(shlex.join(schedule.CRON_CREATE))
    assert hermes.cron_jobs() == []


def test_setup_with_no_harness_names_no_reads(hermes):
    report = Atlas.open(hermes.record_root).setup()
    assert report.found == ()
    assert not any(line.startswith("Reads, never writes") for line in report.disclosures)
