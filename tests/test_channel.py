"""Telegram.send against the harness's fake `hermes`, which prints what Hermes v0.23.0 prints."""
import json
import subprocess
from dataclasses import replace

import pytest

from atlas import channel
from atlas.model import ChannelSetting
from harness import world
from harness.home import AtlasHome
from snapshots import SETTINGS

TEXT = "Atlas · Daily Brief · Monday 16 November\n\nSTEPS"


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(world.SETUP_AT)
        yield home


def test_exit_0_is_sent_with_the_chat_and_message_id_read_past_the_container_start_up_lines(home):
    assert channel.Telegram(SETTINGS).send(TEXT) == channel.Delivered("telegram:5550001", ("8900",))
    assert home.sent() == [{"to": "telegram", "text": TEXT, "message_id": "8900"}]


def test_atlas_own_chat_is_the_target_when_set_and_its_topic_is_part_of_the_chat(home):
    own = replace(SETTINGS, channel=ChannelSetting("telegram", "telegram:-1001234:7"))
    assert channel.Telegram(own).send(TEXT) == channel.Delivered("telegram:-1001234:7", ("8900",))
    assert [s["to"] for s in home.sent()] == ["telegram:-1001234:7"]


def test_a_forum_general_topic_is_its_chat(home):
    general = replace(SETTINGS, channel=ChannelSetting("telegram", "telegram:-1001234:1"))
    assert channel.Telegram(general).send(TEXT) == channel.Delivered("telegram:-1001234", ("8900",))


def test_a_skipped_send_exits_0_with_no_message_id(home):
    home.script_hermes("send", stdout=json.dumps({"success": True, "skipped": True}, indent=2))
    assert channel.Telegram(SETTINGS).send(TEXT) == channel.Delivered(None, ())


def test_exit_1_is_arrival_unknown(home):
    home.script_hermes("send", exit=1, stdout=json.dumps({"error": "Telegram send timed out"}, indent=2))
    with pytest.raises(channel.ArrivalUnknown, match="timed out"):
        channel.Telegram(SETTINGS).send(TEXT)


def test_a_timeout_is_arrival_unknown(home, monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(channel.ArrivalUnknown):
        channel.Telegram(SETTINGS).send(TEXT)


def test_exit_2_raises(home):
    home.script_hermes("send", exit=2, stderr="hermes send: --to PLATFORM[:channel[:thread]] is required\n")
    with pytest.raises(channel.SendRefused, match="--to"):
        channel.Telegram(SETTINGS).send(TEXT)


def test_the_result_is_the_last_top_level_json_object_on_stdout(home):
    noisy = '[stage2] {"not": "this"}\n{\n  "success": true,\n  "message_id": 8812,\n  "meta": {\n    "chunks": 1\n  }\n}\n'
    home.script_hermes("send", stdout=noisy)
    assert channel.Telegram(SETTINGS).send(TEXT) == channel.Delivered(None, ("8812",))
