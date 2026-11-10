"""The record-layout.md examples, read from the doc so the doc stays the source of fixtures."""
from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture" / "record-layout.md"


def examples() -> dict[str, str]:
    """Record-relative path -> the example file's text, keyed by the heading that names it."""
    out: dict[str, str] = {}
    heading = None
    lines = DOC.read_text().splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            heading = line[3:].split(" ")[0].strip()
        fence = re.match(r"^(`{3,})markdown$", line.rstrip("\n"))
        if fence and heading:
            end = next(j for j in range(i + 1, len(lines)) if lines[j].rstrip("\n") == fence.group(1))
            out[heading] = "".join(lines[i + 1:end])
            i = end
        i += 1
    return out
