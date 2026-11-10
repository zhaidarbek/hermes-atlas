"""Expected text, read out of the spec documents so they stay the source."""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
TUTORIAL = DOCS / "tutorial" / "a-week-with-atlas.md"
USAGE = DOCS / "architecture" / "usage.md"


def fenced_block(containing: str) -> str:
    """The tutorial's fenced block that contains `containing`, without the indent of a block
    inside a list item."""
    found = re.findall(r"^( *)```[^\n]*\n(.*?)^\1```", TUTORIAL.read_text(), re.M | re.S)
    blocks = [textwrap.dedent(b) for _, b in found]
    matches = [b for b in blocks if containing in b]
    if len(matches) != 1:
        raise LookupError(f"{len(matches)} tutorial blocks contain {containing!r}")
    return matches[0]


def sentence(starting: str) -> str:
    """The tutorial's prose from `starting` to the end of its paragraph."""
    text = TUTORIAL.read_text()
    start = text.index(starting)
    return text[start:text.index("\n\n", start)]


def usage_list(field: str) -> list[str]:
    """The quoted strings of a list field in usage.md, such as mcp_commands=[...]."""
    m = re.search(rf"{field}=\[([^\]]*)\]", USAGE.read_text())
    return re.findall(r'"([^"]*)"', m.group(1))


def exchange(containing: str) -> list[tuple[str, str]]:
    """The tutorial's chat block that contains `containing`, as (speaker, text) pairs. A line that
    starts with spaces continues the previous speaker's message, and a "..." line marks time passing."""
    said: list[tuple[str, str]] = []
    for line in fenced_block(containing).splitlines():
        if line.startswith(" ") and said:
            said[-1] = (said[-1][0], said[-1][1] + "\n" + line.strip())
        elif line.strip() and line.strip() != "...":
            speaker, text = line.split(maxsplit=1)
            said.append((speaker, text.strip()))
    return said


def normalise(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


@dataclass(frozen=True)
class BoardCard:
    column: str                              # "ACTIVE", "PARKED"
    name: str
    kind: str
    last_worked: str                         # "Fri 13 Nov"
    left_off: str
    next: str


def board() -> list[BoardCard]:
    """The cards of the tutorial's Board, read from the block that starts with ACTIVE."""
    cards, column, head, left = [], None, None, None
    for line in fenced_block("ACTIVE (8)").splitlines():
        if m := re.match(r"^([A-Z]+) \(\d+\)$", line):
            column = m[1]
        elif m := re.match(r"^  Left off  (.*) \[F\]$", line):
            left = m[1]
        elif m := re.match(r"^  Next      (.*) \[I\]$", line):
            cards.append(BoardCard(column, *head, left, m[1]))
        elif line and not line.startswith(" ") and " · " in line:
            head = line.split(" · ")
    return cards


@dataclass(frozen=True)
class JournalLine:
    pursuit: str
    text: str
    label: str                               # "Claude Code via T3 Code"
    at: str                                  # "21:40"


JOURNAL_DAY = "# Monday 16 November 2026"


def journal_lines() -> list[tuple[str, JournalLine]]:
    """The Worked on and Decided lines of the Journal day the tutorial shows, by section."""
    sections = {"Worked on": "worked_on", "Decided": "decided", "Finished": None}
    out, section = [], None
    for line in fenced_block(JOURNAL_DAY).splitlines():
        if line in sections:
            section = sections[line]
        elif section and (m := re.match(r"^- ([^:]+): (.*) \[F (.+) (\d{2}:\d{2})\]$", line)):
            out.append((section, JournalLine(m[1], m[2], m[3], m[4])))
    return out
