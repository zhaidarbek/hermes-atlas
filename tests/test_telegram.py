"""Telegram's look: what Hermes v0.23.0's formatter and Telegram make of a styled message is
exactly the markup Atlas meant, and the plain text comes back from it."""
import ast
import html.parser
import os
import random
import re
from pathlib import Path

import pytest

from atlas import telegram
from atlas.telegram import Kind, plain, styled
from hermes_formatter import TelegramAdapter, adapter_send, telegram_format

MONDAY = """Atlas · Daily Brief · Monday 16 November

STEPS
1 · Payments CI flakes
    Run tests/test_retry.py with one worker, ten times in a row. [I]
    Left off: it fails only when two workers share Redis db 0. [F Claude Code, Sat 22:10]
    Resume: Claude Code on ubuntu-pc, ~/work/payments-api, branch fix/retry-flake. [F]

    Reply "1 prompt" for a paste-in prompt.

YESTERDAY'S STEPS
    None. This is your first brief.

NEEDS A DECISION
    Agent-tooling newsletter · idea · quiet 28 days [F]
    Last: you drafted three issue topics in Claude Code on Mon 19 Oct. [F]
    Continue, park, or drop?"""

TUESDAY = """Atlas · Daily Brief · Tuesday 17 November

YESTERDAY'S STEPS
    ✓ Payments CI flakes: done, you said so. [F]
    · Greenhouse logger: no sign of it in your Sessions. [F] It is still the next Step. [I]

STEPS
1 · Greenhouse logger             Read the last 20 lines of the logger's log over ssh. [I]
    Left off: the logger moved to a new SD card. [F Claude Code, Wed 17:25]
2 · Reading tracker for the kids  Write down paper chart or tablet app, and why. [I]

    Reply "1 prompt" or "2 prompt" for a paste-in prompt.

NEEDS A DECISION
    Nothing today."""

REVIEW = """Atlas · Review · week of 16 to 22 November

DECIDED
- Billing export service: round VAT per invoice. [F Claude Code via T3 Code, Mon 21:52]

ENDINGS
- Payments CI flakes: I propose marking it done. [I]
  Reply "close it" or "leave it".

PARKED
- Pi-hole DNS loop · problem · quiet 100 days [F]
  Left off: found the loop between Pi-hole and the router; not fixed. [F Claude Code, 14 Aug]
  Continue, keep parked, or drop?"""

ANSWERS = ["Payments CI flakes: Step done.\nAgent-tooling newsletter: parked.",
           'Resuming "Payments CI flakes".\nWhere: ubuntu-pc, ~/work/payments-api, branch fix/retry-flake\n'
           "Left off: it fails only when two workers share Redis db 0. [F]\n"
           "Next Step: run tests/test_retry.py with one worker, ten times in a row.",
           "Nothing is Neglected today. [F]\nHome server backups was Neglected until you said \"continue\". [F]"]

HOSTILE = ["*", "**", "_", "__", "`", "```", "~", "~~", "||", "|", "[x](http://evil)", "[x]", "](", "(", ")",
           "tg://user?id=1", "||spoiler||", "> quote", ">", "#", "## head", "\\", "\\*", "[secret: api key]",
           "<b>", "</i>", "<a href=\"tg://user?id=1\">x</a>", "&amp;", "&", "&#8288;", "<details>", "$$", "$",
           "- [ ] task", "|---|---|", "a|b", "tests/test_retry.py", "my_file_name.py", "https://example.org/a_b",
           "@someone", "#tag", "🧭 ", "✅ ", "🅿️ ", "✓", "·", " · ", "Привет", "日本語", "\u2060", "∗", "ˋ", "\u2060∗",
           "MEDIA:", "**>", "=", "+", "-", ".", "!", "{", "}", ": ", "  ", "\t", " ", "Reply \"x\"", "[F]",
           "[I]", "[F x](y)", "x" * 300]


def _hostile(rng: random.Random, words: int = 6) -> str:
    return "".join(rng.choice(HOSTILE + ["word", " ", " ", "Name"]) for _ in range(rng.randint(1, words)))


def _messages(rng: random.Random) -> list[tuple[str, Kind]]:
    h = lambda: _hostile(rng)
    brief = (f"Atlas · Daily Brief · {h()}\n\nYESTERDAY'S STEPS\n    ✓ {h()}: done. {h()} [F]\n      {h()}\n"
             f"    · {h()}: {h()}\n\nSTEPS\n1 · {h()}  {h()} [I]\n    Left off: {h()} [F Claude Code, Sat 22:10]\n"
             f"    Resume: Claude Code on {h()}, ~/{h()}, branch {h()}. [F]\n2 · {h()}\n    {h()}\n\n"
             f"    Reply \"1 prompt\" {h()}\n\nNEEDS A DECISION\n    {h()} · idea · quiet 3 days [F]\n    Last: {h()}")
    review = (f"Atlas · Review · {h()}\n\nDECIDED\n- {h()}: {h()} [F {h()}]\n\nENDINGS\n- {h()}: {h()} [I]\n"
              f"  Reply \"close it\" or \"leave it\".\n\nPARKED\n- {h()} · problem · quiet 9 days [F]\n  Left off: {h()}\n"
              f"\nWHAT I DID ON MY OWN\n- {h()} [I] Reply \"add {h()}\" or \"leave it\".\n  {h()}")
    answer = (f"{h()}: Step done.\n{h()}: parked.\nResuming \"{h()}\".\nWhere: {h()}\nLeft off: {h()} [F]\n"
              f"{h()}\n{h()}: {h()}\nNext Step: {h()}")
    free = "\n".join(h() for _ in range(rng.randint(1, 5)))
    return [(brief, Kind.BRIEF), (review, Kind.REVIEW), (answer, Kind.ANSWER), (free, Kind.ANSWER),
            (free, Kind.BRIEF), (free, Kind.REVIEW)]


def _cases():
    rng = random.Random(1116)
    fixed = [(MONDAY, Kind.BRIEF), (TUESDAY, Kind.BRIEF), (REVIEW, Kind.REVIEW), *((a, Kind.ANSWER) for a in ANSWERS)]
    return fixed + [m for _ in range(400) for m in _messages(rng)]


CASES = _cases()


# ---- what Telegram receives, read as Telegram reads it -------------------------------------------

class _Html(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.spans, self.open = [], []

    def handle_starttag(self, tag, attrs):
        assert tag in ("b", "i", "code") and not attrs and tag not in self.open, tag
        self.open.append(tag)

    def handle_endtag(self, tag):
        assert self.open and self.open.pop() == tag, tag

    def handle_data(self, data):
        self.spans.append(("".join(self.open[-1:]), data))


def _entities_html(text: str) -> list[tuple[str, str]]:
    assert not re.search(r"&(?!(?:amp|lt|gt|#8288);)", text), "a bare & Telegram would refuse"
    parser = _Html()
    parser.feed(text)
    parser.close()
    assert not parser.open
    return _merged(parser.spans)


def _entities_mdv2(text: str) -> list[tuple[str, str]]:
    """MarkdownV2 as Telegram parses it, for the entities Atlas may make: every other special
    character must arrive escaped."""
    spans, style, n, buf = [], "", 0, []
    markers = {"*": "b", "_": "i", "`": "code"}
    while n < len(text):
        ch = text[n]
        if style == "code":
            if ch == "\\":
                buf.append(text[n + 1]); n += 2; continue
            if ch == "`":
                spans.append(("code", "".join(buf))); buf, style = [], ""; n += 1; continue
            buf.append(ch); n += 1; continue
        if ch == "\\":
            assert text[n + 1] in "_*[]()~`>#+-=|{}.!\\", text[n + 1]
            buf.append(text[n + 1]); n += 2; continue
        if ch in markers:
            if style == markers[ch]:
                spans.append((style, "".join(buf))); buf, style = [], ""
            else:
                assert not style, f"{ch} inside {style}"
                spans.append(("", "".join(buf))); buf, style = [], markers[ch]
            n += 1; continue
        assert ch not in "[]()~>#+-=|{}.!", f"unescaped {ch!r}"
        buf.append(ch); n += 1
    assert not style
    spans.append(("", "".join(buf)))
    return _merged(spans)


def _merged(spans):
    out = []
    for style, text in spans:
        if not text:
            continue
        if out and out[-1][0] == style:
            out[-1] = (style, out[-1][1] + text)
        else:
            out.append((style, text))
    return out


def _received(shown: str, kind: Kind) -> list[tuple[str, str]]:
    """The entities Telegram makes of a styled message, by the path its kind leaves on."""
    if kind is Kind.ANSWER:
        how, sent = adapter_send(shown)
    else:
        how, sent = telegram_format(shown)
    assert how != "rich", "the gateway would send it as a rich message"
    return _entities_html(sent) if how == "HTML" else _entities_mdv2(sent)


def _meant(text: str, kind: Kind) -> list[tuple[str, str]]:
    """The entities Atlas meant: its spans, each text as Telegram shows it."""
    bare = styled(text, kind) == telegram._markdown(telegram._bare(text))
    lines = telegram._bare(text) if bare else telegram._lines(text, kind)
    spans = []
    for n, line in enumerate(lines):
        spans += [("", "\n")] if n else []
        if not bare and kind is not Kind.ANSWER:
            layout = {"deco": lambda v: ("", v + " "), "join": lambda v: ("", telegram.JOIN),
                      "spacer": lambda v: ("", telegram.SPACER), "heading": lambda v: ("b", telegram.HEADINGS[v][1])}
            spans += [layout[style](value) if style in layout else (style, value) for style, value in line]
            continue
        kept = [span for span in line if span[0] != "join"]
        for (style, value), part in zip(kept, telegram._markdown_line(line)):
            inner = part[2:-2] if style == "b" else part[1:-1] if style == "i" else part
            spans.append(("", part) if style == "deco" else ("code", value) if style == "code" else (style, inner))
    return _merged(spans)


@pytest.mark.parametrize("text, kind", CASES[:6], ids=["monday", "tuesday", "review", "done", "prompt", "neglected"])
def test_the_week_s_messages_come_back_as_composed_and_render_as_meant(text, kind):
    shown = styled(text, kind)
    assert plain(shown) == text
    assert _received(shown, kind) == _meant(text, kind)


def test_hostile_text_never_opens_markup_and_always_comes_back():
    for text, kind in CASES:
        shown = styled(text, kind)
        assert plain(shown) == text, (kind, text)
        assert _received(shown, kind) == _meant(text, kind), (kind, text)


def test_no_text_from_a_session_makes_a_link_a_mention_a_spoiler_a_quote_or_a_block():
    for bait in ["[x](http://evil)", "[x](tg://user?id=1)", "||spoiler||", "> quote", "```\ncode\n```", "**bold**",
                 "~~gone~~", "<a href=\"tg://user?id=1\">x</a>", "<tg-spoiler>x</tg-spoiler>", "**> hidden||",
                 "$$x$$", "| a | b |\n|---|---|\n| 1 | 2 |", "- [ ] task", "<details>x</details>"]:
        for text, kind in ((f"Name: Step done.\nLeft off: {bait} [F]\n{bait}", Kind.ANSWER),
                           (f"Atlas · Daily Brief · Monday\n\nSTEPS\n1 · {bait}\n    {bait} [I]", Kind.BRIEF)):
            received = _received(styled(text, kind), kind)
            assert {style for style, _ in received} <= {"", "b", "i", "code"}
            assert plain(styled(text, kind)) == text


def test_the_brief_goes_as_html_and_an_answer_as_markdown():
    assert telegram_format(styled(MONDAY, Kind.BRIEF))[0] == "HTML"
    for answer in ANSWERS + ["I couldn't answer that: <b>x</b> & <i>."]:
        assert telegram_format(styled(answer, Kind.ANSWER))[0] == "MarkdownV2"


def test_headings_show_in_sentence_case_and_read_back_in_capitals():
    assert {telegram.COMPOSED[shown]: shown for shown in telegram.COMPOSED} == telegram.HEADINGS
    assert len({shown for _, shown in telegram.HEADINGS.values()}) == len(telegram.HEADINGS)
    for heading, (emoji, shown) in telegram.HEADINGS.items():
        assert shown[0].isupper() and shown[1:] == shown[1:].lower() or '"apply"' in shown or "I " in shown
        for rest in ("", " (from Monday)", " (no Focus picked yet)"):
            text = f"Atlas · Daily Brief · Monday 16 November\n\n{heading}{rest}\n    None. [F]"
            assert f"{emoji} <b>{shown}</b>{rest}\n" in styled(text, Kind.BRIEF)
            assert plain(styled(text, Kind.BRIEF)) == text
    shown = styled(MONDAY, Kind.BRIEF)
    assert "👣 <b>Steps</b>" in shown and "↩️ <b>Yesterday's steps</b>" in shown and "STEPS" not in shown
    assert '⏳ <b>Waiting for "apply"</b>' in styled(f'Atlas · Review · week\n\nWAITING FOR "apply"\n- x [F]', Kind.REVIEW)


def test_a_line_that_merely_reads_like_a_shown_heading_stays_as_written():
    for line in ("Steps", "👣 Steps", "Parked", "Yesterday's steps (from Monday)"):
        text = f"Atlas · Daily Brief · Monday 16 November\n\n{line}\n    None."
        assert plain(styled(text, Kind.BRIEF)) == text


def test_paths_are_code_so_telegram_links_none_of_them():
    shown = styled(MONDAY, Kind.BRIEF)
    for path in ("tests/test_retry.py", "~/work/payments-api", "fix/retry-flake", "ubuntu-pc"):
        assert f"<code>{path}</code>" in shown
    assert "`tests/test_retry.py`" in styled(ANSWERS[1], Kind.ANSWER)


def test_the_markdown_length_is_what_hermes_makes_of_it():
    for text, kind in CASES:
        lines = telegram._lines(text, Kind.ANSWER)
        made = TelegramAdapter.__new__(TelegramAdapter).format_message(telegram._markdown(lines))
        assert telegram._mdv2_units(lines) == telegram._units(made), text


@pytest.mark.parametrize("kind", list(Kind))
def test_a_message_whose_markup_would_not_fit_goes_bare_and_still_reads_as_composed(kind):
    text = "Atlas · Daily Brief · Monday\n\nSTEPS\n" + "\n".join(f"    Left off: a.b.c.d.e.f.g.h ~/x/{n} [F]" for n in range(85))
    assert telegram._units(text) < 4000
    shown = styled(text, kind)
    assert shown == telegram._markdown(telegram._bare(text))
    assert "<" not in shown and "**" not in shown
    assert plain(shown) == text
    how, sent = telegram_format(shown)
    assert how == "MarkdownV2" and {s for s, _ in _entities_mdv2(sent)} == {""}


def test_a_message_that_fits_keeps_its_markup():
    text = "Atlas · Daily Brief · Monday\n\nSTEPS\n" + "\n".join(f"    Left off: words here [F]" for n in range(40))
    assert "👣 <b>Steps</b>" in styled(text, Kind.BRIEF)


@pytest.mark.skipif(not os.environ.get("ATLAS_HERMES_SRC"), reason="needs an extracted Hermes v0.23.0 tree")
def test_the_copied_formatter_is_hermes_s_own():
    root = Path(os.environ["ATLAS_HERMES_SRC"])
    ours = Path(__file__).with_name("hermes_formatter.py").read_text()
    for path, names in (("plugins/platforms/telegram/adapter.py",
                         ("_MDV2_ESCAPE_RE", "_escape_mdv2", "format_message", "_escape_bare_bracket",
                          "_needs_rich_rendering")),
                        ("gateway/platforms/helpers.py", ("TABLE_SEPARATOR_RE", "is_table_row",
                                                           "convert_table_to_bullets"))):
        source = (root / path).read_text()
        for node in ast.walk(ast.parse(source)):
            name = getattr(node, "name", None) or (
                node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else None)
            if name in names:
                assert ast.get_source_segment(source, node, padded=True) in ours, name
    senders = (root / "tools/send_message_senders.py").read_text()
    assert "if re.search(r'<[a-zA-Z/][^>]*>', message):\n        return message, ParseMode.HTML, True" in senders
