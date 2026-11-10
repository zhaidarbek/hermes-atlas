import json

import pytest

from harness import scenes, world
from harness.home import AtlasHome


@pytest.mark.parametrize("scene_id", sorted(scenes.claimed()))
def test_every_claimed_scene_passes(scene_id):
    assert str(scenes.run_scene(scene_id)) == "pass"


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(world.SETUP_AT)
        yield home


def test_scene_1_fails_without_a_telegram_home_channel(home):
    home.script_hermes("send -l", stdout=json.dumps({"platforms": {}}))
    with pytest.raises(scenes.Failed, match="line 3"):
        scenes.SCENES["1"].step(home)


def test_scene_1_fails_when_hermes_refuses_the_cron_job(home):
    home.script_hermes("cron create", exit=2, stderr="usage error")
    with pytest.raises(scenes.Failed):
        scenes.SCENES["1"].step(home)


def test_scene_1_counts_come_from_the_world_not_the_tutorial(home):
    text = scenes._setup_report_text(world.SETUP_AT)
    count, _ = world.owner_sessions(world.CC, world.SETUP_AT)
    assert f"{count} Sessions since 2 Jun" in text and "412" not in text


def test_a_session_appears_only_once_the_clock_passes_it(home):
    week_session = world.by_title("rotate staging db password")
    projects = home.home / ".claude" / "projects"
    assert not list(projects.rglob(f"{week_session.native}.jsonl"))
    home.set_clock(week_session.start)
    assert list(projects.rglob(f"{week_session.native}.jsonl"))


def test_the_report_lists_every_scene_and_gates_only_on_claimed_ones():
    results = scenes.run_scenes(frozenset({"1", "36"}))
    lines = scenes.report(results, {"1": scenes.Result("fail", "not built")}).splitlines()
    assert [line.split()[0] for line in lines[1:44]] == list(scenes.SCENE_IDS)
    assert sorted(scenes.WEEK) == sorted(scenes.SCENE_IDS)
    assert len(scenes.SCENE_IDS) == 43 and {"3a", "3b", "3c", "9b", "20b"} <= set(scenes.SCENE_IDS)
    assert str(results["36"]) == "fail (claimed, but the harness has no step for it)"
    assert str(results["3a"]) == "not claimed"


def test_scene_7_fails_when_the_send_is_not_confirmed(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(world.FIRST_BRIEF_AT)
        scenes.SCENES["7"].seed(home)
        home.script_hermes("send", exit=1)
        with pytest.raises(scenes.Failed, match="sent 0 messages"):
            scenes.SCENES["7"].step(home)
