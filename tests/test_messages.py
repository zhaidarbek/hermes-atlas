"""messages in the Telegram style: one row per G1 case in the messages module's docstring.

Text meant to leave Atlas (resume_prompt, a status to forward) is not built yet (#4, #16), so its
row goes through statement() with no citation time, which is how that text renders a Fact."""
from datetime import date, datetime, timezone

import pytest

from atlas import messages
from atlas.messages import Bare, Style
from atlas.model import Cite, Count, Fact, Inference
from snapshots import at

NOW = at(date(2026, 11, 16), "07:30")
T = Style.TELEGRAM


def read(text: str, label: str, when: datetime) -> Fact:
    return Fact(text, Cite("cc-8d21f0a4", label, when))


SAT = read("it fails only when two workers share Redis db 0.", "Claude Code", at(date(2026, 11, 14), "22:10"))
STEP = Inference("Run tests/test_retry.py with one worker, ten times in a row.", ("cc-8d21f0a4",))


@pytest.mark.parametrize("lines, one_day, expected", [
    ([SAT], False,
     ["it fails only when two workers share Redis db 0. [F Claude Code, Sat 22:10]"]),
    ([read("CSV totals match.", "Claude Code via T3 Code", datetime(2026, 11, 13, 17, 2, tzinfo=timezone.utc))], False,
     ["CSV totals match. [F Claude Code via T3 Code, Fri 18:02]"]),
    ([read("Named it.", "your Note", at(date(2026, 11, 15), "20:41"))], False,
     ["Named it. [F your Note, Sun 20:41]"]),
    ([read("Ran it once.", "Codex", at(date(2026, 11, 16), "09:40"))], True,
     ["Ran it once. [F Codex 09:40]"]),
    ([read("A week ago.", "Codex", at(date(2026, 11, 9), "21:00"))], False,
     ["A week ago. [F Codex, 9 Nov]"]),
    ([read("The right half drops keys.", "Claude Code", at(date(2026, 8, 30), "22:05"))], False,
     ["The right half drops keys. [F Claude Code, 30 Aug]"]),
    ([read("The right half drops keys.", "Claude Code", at(date(2025, 8, 30), "22:05"))], False,
     ["The right half drops keys. [F Claude Code, 30 Aug 2025]"]),
    ([Fact("Active time 3 h 55 min", Count("active_time", ("cc-1", "cx-2")))], True,
     ["Active time 3 h 55 min [F]"]),
    ([Bare(read("Seen in Codex, Wed 14:30.", "Codex", at(date(2026, 11, 11), "14:30"))), SAT], False,
     ["Seen in Codex, Wed 14:30. [F]", "it fails only when two workers share Redis db 0. [F Claude Code, Sat 22:10]"]),
    ([STEP, SAT, Bare(read("Claude Code on ubuntu-pc, ~/work/payments-api.", "Claude Code", at(date(2026, 11, 14), "21:30")))],
     False,
     ["Run tests/test_retry.py with one worker, ten times in a row. [I]",
      "it fails only when two workers share Redis db 0. [F Claude Code, Sat 22:10]",
      "Claude Code on ubuntu-pc, ~/work/payments-api. [F]"]),
    ([SAT, read("Decided to keep xdist.", "Codex", at(date(2026, 11, 12), "10:00"))], False,
     ["it fails only when two workers share Redis db 0. [F Claude Code, Sat 22:10]", "Decided to keep xdist. [F]"]),
], ids=["first-fact-within-7-days", "in-the-owner-zone", "a-note", "one-day-message", "seven-days-is-older",
        "older", "another-year", "counted", "names-its-source-and-day", "record-fact", "later-fact"])
def test_an_item_cites_its_first_fact_read_from_one_session_or_note(lines, one_day, expected):
    assert messages.item(lines, T, NOW, one_day=one_day) == expected


def test_text_meant_to_leave_atlas_shows_bare_facts():
    assert messages.statement(SAT, T) == "it fails only when two workers share Redis db 0. [F]"


def test_an_inference_is_marked_i_even_when_asked_to_cite():
    assert messages.statement(STEP, T, cite=NOW) == "Run tests/test_retry.py with one worker, ten times in a row. [I]"


@pytest.mark.parametrize("steps, line", [
    (1, 'Reply "1 prompt" for a paste-in prompt.'),
    (2, 'Reply "1 prompt" or "2 prompt" for a paste-in prompt.'),
    (3, 'Reply "1 prompt", "2 prompt" or "3 prompt" for a paste-in prompt.'),
])
def test_the_prompt_hint_names_each_step(steps, line):
    assert messages.prompt_hint(steps) == line
