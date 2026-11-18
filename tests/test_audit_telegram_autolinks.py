"""Audit finding: Session text reaches Telegram in forms Telegram links by itself.

README: "Text that comes from your Sessions is escaped, so it cannot add formatting, links or
hidden text", and telegram._coded says a place in code is one Telegram "links none of". Only a
token with a slash, a scheme or a known file ending is put in code. Telegram's clients also link a
bare domain, an @mention, a bot command with a bot's name, an e-mail address and a hashtag, with no
markup at all. A tap on the command sends it to that bot in the home chat; in a group the mention
notifies that account."""
import re

import pytest

from atlas.telegram import Kind, styled

LINKED = ["canary-7f3a.com", "www.canary-7f3a.com", "@canary7f3a_bot", "/wipe@canary7f3a_bot", "a@canary-7f3a.com"]
BRIEF = "Atlas · Daily Brief · Monday 16 November\n\nSTEPS\n1 · Greenhouse logger   Open {} now [I]\n    Left off: see {} [I]"
ANSWER = "Greenhouse logger: Step done.\nLeft off: see {} [I]"


@pytest.mark.parametrize("bait", LINKED)
@pytest.mark.parametrize("text, kind", [(BRIEF, Kind.BRIEF), (ANSWER, Kind.ANSWER)], ids=["brief", "answer"])
def test_text_telegram_links_by_itself_goes_out_in_code(bait, text, kind):
    shown = styled(text.format(bait, bait), kind)
    code = re.findall(r"<code>(.*?)</code>", shown) if kind is Kind.BRIEF else re.findall(r"`([^`]*)`", shown)
    outside = re.sub(r"<code>.*?</code>|`[^`]*`", "", shown)
    assert bait in code and bait not in outside, shown
