"""PRIVATE to atlas.record. Hand-edit detection: each sealed file carries
``sealed: sha256:<hex>`` in its front matter, the hash of the file's bytes with that value
blanked. A file whose seal does not verify was changed outside Atlas.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import _layout

BLANK = b"sealed: "
_LINE = re.compile(rb"^sealed: ?(.*)$", re.M)


def seal(data_with_blank_seal: bytes) -> str:
    return "sha256:" + hashlib.sha256(data_with_blank_seal).hexdigest()


def blanked(data: bytes) -> bytes:
    return _LINE.sub(BLANK, data, count=1)


def stamp(data: bytes) -> bytes:
    """Write the seal into a rendered file whose `sealed:` line is still blank."""
    blank = blanked(data)
    return blank.replace(BLANK, BLANK + seal(blank).encode(), 1)


def stored(data: bytes) -> str | None:
    """The seal value a file claims, or None for a file with no seal line."""
    m = _LINE.search(data)
    return m.group(1).decode().strip() if m else None


def verifies(data: bytes) -> bool:
    claimed = stored(data)
    return claimed is not None and claimed == seal(blanked(data))


@dataclass(frozen=True)
class Found:
    path: str                                # record-relative
    current: bytes | None                    # None: the file was deleted
    mirror: bytes                            # what Atlas last wrote there


def sweep(root: Path) -> list[Found]:
    """Every file Atlas wrote outside the evidence whose seal fails or that is gone while its
    last-written copy is there. A file Atlas did not write is never read. A path with a link in it,
    from its folder to the file, is passed over, and so is anything not a regular file."""
    found: list[Found] = []
    mirror = root / _layout.MIRROR
    for path in sorted(_files(mirror)):
        if path.startswith("evidence/") or linked(root, path):
            continue
        target = root / path
        if not target.exists():
            found.append(Found(path, None, (mirror / path).read_bytes()))
        elif target.is_file() and not verifies(data := target.read_bytes()):
            found.append(Found(path, data, (mirror / path).read_bytes()))
    return found


def linked(root: Path, path: str) -> bool:
    """Whether a link stands at `path` or at a folder between the record root and it."""
    parts = path.split("/")
    return any(root.joinpath(*parts[:n]).is_symlink() for n in range(1, len(parts) + 1))


def _files(folder: Path) -> list[str]:
    """The files below `folder`, as paths relative to it, without following a link and without
    editor droppings or hidden folders."""
    out = []
    for top, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not _ignored(d)]
        rel = os.path.relpath(top, folder)
        out += [name if rel == "." else f"{rel}/{name}" for name in names if not _ignored(name)]
    return out


def _ignored(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in _layout.IGNORED_NAMES)
