"""What `hermes send` is handed as its message: one argument it cannot read as an option, and one
the operating system accepts."""
import os
import re
import subprocess

import pytest

from atlas import _hermes, channel, telegram
from atlas.model import ChannelSetting

SENT = '{"success": true, "chat_id": "1", "message_id": 2}'


@pytest.fixture
def argv(monkeypatch):
    """The argument lists `hermes` was started with, each encoded as exec encodes it."""
    started = []

    def run(args, **_):
        started.append([os.fsencode(a) for a in args])
        if any(b"\0" in a for a in started[-1]):
            raise ValueError("embedded null byte")
        return subprocess.CompletedProcess(args, 0, SENT, "")

    monkeypatch.setattr(_hermes.subprocess, "run", run)
    return started


def test_the_message_follows_a_double_dash_so_it_is_never_read_as_an_option(argv):
    channel.send(ChannelSetting("telegram"), "-l", telegram.Kind.ANSWER)
    assert argv == [[b"hermes", b"send", b"--to", b"telegram", b"--json", b"--", b"-l"]]


@pytest.mark.parametrize("cut, sent", [("\ud83d", "�"), ("\udc00", "�"), ("\x00", ""), ("😀", "😀")])
def test_a_title_cut_inside_an_emoji_or_holding_a_nul_does_not_stop_the_send(argv, cut, sent):
    done = channel.send(ChannelSetting("telegram"), f"Greenhouse {cut}logger: parked.", telegram.Kind.ANSWER)
    assert done.message_ids == ("2",)
    assert argv[0][-1].decode() == telegram.styled(f"Greenhouse {sent}logger: parked.", telegram.Kind.ANSWER)


@pytest.mark.parametrize("kind", list(telegram.Kind))
@pytest.mark.parametrize("bait", ["MEDIA\0:/home/owner/private/report.pdf", "ME\0DIA:~/notes/plan.md", "[[as_\0document]]"])
def test_a_nul_inside_an_attachment_tag_does_not_put_the_tag_back_together(argv, bait, kind):
    channel.send(ChannelSetting("telegram"), f"Left off: saved {bait} then stopped. [I]", kind)
    message = argv[0][-1].decode()
    assert not re.search(r"MEDIA:\s*[~/]\S+|\[\[as_document\]\]", message, re.I), message
