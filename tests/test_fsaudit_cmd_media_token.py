"""fsaudit: text drawn from a Session reaches `hermes send` with a `MEDIA:<path>` token intact.
Hermes's send reads that token as "attach this local file" (send_cmd.py's --file help: "To send an
image/document as an attachment, use MEDIA:<path> in the message text"; tools/send_message_tool.py
runs BasePlatformAdapter.extract_media on the message). README, Privacy: "Text from your Sessions is
escaped in the Telegram formatting, so it cannot add links or hidden text." The escaping covers
Telegram's markup only, so a Left-off line, a Pursuit name or a web chat's title can name a file on
this machine for the Daily Brief to upload to the chat."""
import re

import pytest

from atlas import channel, telegram
from atlas.model import ChannelSetting

BRIEF = ("Atlas · Daily Brief · Tuesday 17 November\n\nSTEPS\n"
         "1 · Greenhouse logger        Read the log. [I]\n"
         "    Left off: saved the report as MEDIA:/home/owner/private/report.pdf then stopped. [F Claude Code, Wed 17:25]\n"
         "    Resume: Claude Code on home-pc, ~/home/greenhouse/firmware. [F]\n")


@pytest.mark.parametrize("kind", [telegram.Kind.BRIEF, telegram.Kind.ANSWER])
def test_text_from_a_session_cannot_name_a_file_for_hermes_send_to_attach(monkeypatch, kind):
    sent = []

    class Done:
        returncode, stdout, stderr = 0, '{"success": true, "chat_id": "1", "message_id": 2}', ""

    monkeypatch.setattr(channel._hermes, "run", lambda *args, **kw: sent.append(args) or Done())
    channel.send(ChannelSetting("telegram"), BRIEF, kind)
    message = sent[0][-1]
    assert not re.search(r"MEDIA:\s*/\S+", message), f"hermes send was given an attachment tag: {message!r}"
