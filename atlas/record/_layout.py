"""PRIVATE to atlas.record. The ONE id -> path map. Paths are record-relative strings."""
from __future__ import annotations

import re
from datetime import date
from typing import Literal

from ..model import EvidenceId, HarnessId, PursuitId

FileKind = Literal["pursuit", "journal", "profile", "settings", "pauses", "forgotten", "readme",
                   "notes", "brief", "review", "session", "card", "raw"]

RESTORE: dict[FileKind, Literal["mirror", "regenerate", "recopy"]] = {
    "pursuit": "mirror", "journal": "mirror", "profile": "mirror", "settings": "mirror",
    "pauses": "mirror", "forgotten": "mirror", "readme": "mirror", "notes": "mirror",
    "brief": "mirror", "review": "mirror", "card": "mirror",
    "session": "regenerate",   # deterministic rendering of raw/
    "raw": "recopy",           # from the Harness store if still present, else reported damaged
}
IGNORED_NAMES = (".*", "*~", "*.swp", "*.tmp", "#*#")   # editor droppings are never hand edits

TOP_FILES: dict[FileKind, str] = {
    "readme": "README.md", "settings": "settings.md", "profile": "profile.md",
    "pauses": "pauses.md", "forgotten": "forgotten.md",
}
FOLDERS = ("pursuits", "journal", "briefs", "reviews", "notes", "evidence", "inbox")
SNAPSHOT_FOLDERS = ("pursuits", "journal", "briefs", "reviews", "notes")
MIRROR = ".atlas/last-written"
SLUG_MAX = 60
HEAD = ".atlas/HEAD"
PENDING_FORGET = ".atlas/pending/forget.json"   # the Forget plan waiting for "forget"
FILING_RETRY = ".atlas/pending/filing.json"     # Sessions the model gave no usable filing for, and when to ask again
SEEN = ".atlas/seen.json"


def pursuit_path(id: PursuitId, slug: str) -> str:
    """pursuits/<slug>.md. Slug fixed at creation; Rename renames the file and rewrites links."""
    return f"pursuits/{slug}.md"


def slug(name: str) -> str:
    """Lowercase letters and digits, hyphens between, cut to SLUG_MAX so the file name fits
    any filesystem."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower())[:SLUG_MAX].strip("-") or "pursuit"


def evidence_dir(id: EvidenceId, harness: HarnessId, started_ym: str) -> str:
    """evidence/<harness>/<yyyy>/<mm>/<id>/ holding session.md, card.md, raw/*.zst"""
    yyyy, mm = started_ym.split("-")
    return f"evidence/{harness.value}/{yyyy}/{mm}/{id}/"


def evidence_kind(path: str) -> FileKind | None:
    if re.fullmatch(r"evidence/[a-z_]+/\d{4}/\d{2}/[^/]+/session\.md", path):
        return "session"
    if re.fullmatch(r"evidence/[a-z_]+/\d{4}/\d{2}/[^/]+/card\.md", path):
        return "card"
    if re.fullmatch(r"evidence/[a-z_]+/\d{4}/\d{2}/[^/]+/raw/[^/]+", path):
        return "raw"
    return None


def session_folder(path: str) -> str | None:
    """The Session evidence folder that `path` is or lies in, or that folder's mirror, with its slash."""
    found = re.match(rf"(?:{re.escape(MIRROR)}/)?evidence/[a-z_]+/\d{{4}}/\d{{2}}/[^/]+/", path)
    return found.group() if found else None


def journal_path(day: date) -> str:
    return f"journal/{day.isoformat()}.md"


def notes_path(day: date, via: str) -> str:
    return f"notes/{day:%Y}/{day:%m}/{day.isoformat()}-{via.replace('_', '-')}.md"


def brief_path(day: date) -> str:
    return f"briefs/{day:%Y}/{day:%m}/{day.isoformat()}.md"


def review_path(week: str) -> str:
    return f"reviews/{week[:4]}/{week}.md"


def mirror_path(path: str) -> str:
    return f"{MIRROR}/{path}"


def in_snapshot(path: str) -> bool:
    """A top file, the seen marks, the pending Forget plan, or a Markdown file under the snapshot folders."""
    return path in (*TOP_FILES.values(), SEEN, PENDING_FORGET, FILING_RETRY) or (
        path.split("/", 1)[0] in SNAPSHOT_FOLDERS and path.endswith(".md"))


def kind_of(path: str) -> FileKind | None:
    for kind, top in TOP_FILES.items():
        if path == top:
            return kind
    if re.fullmatch(r"pursuits/[^/]+\.md", path):
        return "pursuit"
    if re.fullmatch(r"journal/\d{4}-\d{2}-\d{2}\.md", path):
        return "journal"
    if re.fullmatch(r"notes/\d{4}/\d{2}/[^/]+\.md", path):
        return "notes"
    if re.fullmatch(r"briefs/\d{4}/\d{2}/\d{4}-\d{2}-\d{2}\.md", path):
        return "brief"
    return evidence_kind(path)
