"""PRIVATE to atlas.record. Masks the record files an older Atlas wrote with bare secrets (D Q4).

Until ``.atlas/masked`` exists, each open writes those files again through the codec, under the
write lock, for about SECONDS with every sync counted, at least one step. A step is a file the pass
reads or a folder it lists to reach them, in path order from where the open before it stopped. The
files whose names and Areas other files match, the top files and Pursuits, come first and are staged
across opens in ``.atlas/masking/``, then written in one transaction, so names and Areas change
together. Briefs, Notes and evidence follow, one transaction per open. ``.atlas/masking/state`` keeps
the last step and the staged batch with a checksum and the sha256 of each staged copy; a state or a
copy that does not check out starts the pass over. Each write replaces a file only if it still holds
the bytes the pass read, so an edit saved meanwhile is kept, and a commit drops the staged copies of
the files it writes, a Forget before it is staged. The pass follows a link in place of a folder above
a Session folder, never one where a Session folder, its mirror, a folder in them or a file in them
is: it leaves those files and keeps them in the state, and once it has walked the record each open
reads only them, until no link stands in their way. Then it walks the record again.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections import Counter
from contextlib import suppress
from datetime import tzinfo
from pathlib import Path
from typing import Callable, Collection, Iterator

from ..model import HarnessId
from . import _codec, _layout, _seal, _txn

DONE = "masked"
UNDER_WAY = "masking"
STATE = "state"
KINDS = frozenset(_codec.CODECS) - {"settings", "readme"}
ALONE = ("briefs", "evidence", "notes")
SHAPES = {"pursuits": (), "briefs": (r"\d{4}", r"\d{2}"),
          "evidence": ("|".join(harness.value for harness in HarnessId), r"\d{4}", r"\d{2}"),
          "notes": (r"\d{4}", r"\d{2}")}
SESSION = ("card.md", "session.md")
EDITED = "edited by hand"
LINKED = _txn.Linked.__name__
SECONDS = 1.0
FLUSH = 5
log = logging.getLogger(__name__)


def run(root: Path, head: Callable[[], int], zone_of: Callable[[bytes], tzinfo | None]) -> None:
    """Under the write lock: one step of the pass, if it is not done. Never raises: a failure is
    logged by type and the next open goes on from the last state saved."""
    machinery = _txn.machinery(root)
    folder = machinery / UNDER_WAY
    started = time.monotonic()
    try:
        if (machinery / DONE).exists():
            return
        settings = _bytes(root / _layout.TOP_FILES["settings"])
        zone = None if settings is None else zone_of(settings)
        after, batch, waiting, linked = loaded = _load(folder)
        before, met = _check(*loaded), set(linked)
        unlisted: Counter[str] = Counter()
        left: Counter[str] = Counter()
        begun, steps, masked = time.monotonic(), 0, 0
        while True:
            rechecking = waiting is not None
            for path in _again(waiting, after) if rechecking else _walk(root, after, unlisted):
                together = bool(batch) and _alone(path) and not _alone(after)
                if steps and _late(started, begun, steps, batch if together or _alone(after) else []):
                    break
                if settings is None and not path.endswith("/"):
                    break
                if together:
                    masked += _flush(root, folder, batch, head)
                    steps += 1
                if not path.endswith("/"):
                    _stage(root, folder, path, zone, batch, left, met)
                after, steps = path, steps + 1
            else:
                if _alone(after) or not (steps and _late(started, begun, steps, batch)):
                    masked += _flush(root, folder, batch, head)
                    after, waiting, met = None, sorted(met) if met or not rechecking else None, set()
                    if waiting is None:
                        continue
            break
        if waiting == []:
            shutil.rmtree(folder, ignore_errors=True)
            _txn.write_note(machinery / DONE)
        else:
            if _alone(after):
                masked += _flush(root, folder, batch, head)
            state = (after, batch, waiting, sorted(met))
            if _check(*state) == before and (folder / STATE).is_file():
                return
            _save(folder, *state)
        if masked:
            log.info("Atlas masked the secrets in %d record files an older version wrote", masked)
        if unlisted:
            log.warning("Atlas could not list %d record folders (%s) and leaves the files in them as they are",
                        sum(unlisted.values()), ", ".join(sorted(unlisted)))
        if left:
            log.warning("Atlas left as they were %d record files: %s", sum(left.values()),
                        ", ".join(f"{n} {why}" for why, n in sorted(left.items())))
    except Exception as error:
        log.warning("Atlas will mask the files an older version wrote at the next open (%s)", type(error).__name__)


def _late(started: float, begun: float, steps: int, batch: list[dict]) -> bool:
    """Whether this open has spent SECONDS, counting the syncs of writing `batch`, one for each
    folder it replaces files in and FLUSH more, at about the time a step since `begun` took."""
    now = time.monotonic()
    syncs = len({entry["target"].rpartition("/")[0] for entry in batch}) + FLUSH if batch else 0
    return now + syncs * (now - begun) / steps >= started + SECONDS


def forget(root: Path, targets: Collection[str]) -> None:
    """Under the write lock, before a commit that removes text from `targets` is staged: deletes
    the staged copies of those files, which may hold that text, and the state, so the pass starts
    over, and syncs their folder, so no copy outlasts the commit. Raises OSError when a copy stays,
    so that commit fails and changes nothing."""
    folder = _txn.machinery(root) / UNDER_WAY
    if not folder.is_dir():
        return
    gone = False
    for target in targets:
        with suppress(FileNotFoundError):
            (folder / _digest(target.encode())).unlink()
            gone = True
    if gone:
        (folder / STATE).unlink(missing_ok=True)
        _txn.fsync_dir(folder)


def drop(root: Path, targets: Collection[str]) -> None:
    """Under the write lock, once a commit that writes `targets` is applied: deletes the staged
    copies of those files, which the commit made stale, and every staged file the state does not
    name. A commit that fails leaves them. Never raises: a copy it cannot delete is logged by error
    type, and the pass passes over its write, as its file no longer holds the bytes the pass read."""
    folder = _txn.machinery(root) / UNDER_WAY
    if not folder.is_dir():
        return
    try:
        try:
            state = _state(folder)
        except Exception:
            state = None
        after, batch, waiting, linked = state or (None, [], None, [])
        stale = {entry["target"] for entry in batch if entry["target"] in targets}
        kept = [entry for entry in batch if entry["target"] not in stale and entry.get("of") not in stale]
        named = {entry["staged"] for entry in kept} | ({STATE} if state else set())
        for path in folder.iterdir():
            if path.name not in named:
                path.unlink()
        if state and stale:
            _save(folder, after, kept, waiting, linked)
    except Exception as error:
        log.warning("Atlas left the masking pass's staged copies of the files it wrote (%s)", type(error).__name__)


def _walk(root: Path, after: str | None, unlisted: Counter[str]) -> Iterator[str]:
    """Every record file a codec writes but settings.md and README.md, and each folder listed to reach
    them, with its slash, in the pass's order after `after`. Each top folder is listed down to the
    folders its SHAPES names, a Harness's folder by its id, through a link in place of one of them,
    and never a folder inside itself. A Session's folder is not listed: its SESSION files are named.
    A folder that cannot be listed is counted in `unlisted` by error type. raw/ is never listed."""
    def below(folder: str, shape: tuple[str, ...], above: frozenset[tuple[int, int]] = frozenset()) -> Iterator[str]:
        listed = _listed(root / folder, above, unlisted) if _ahead(folder, after) else None
        if listed is None:
            return
        entries, above = listed
        yield folder
        for entry in entries:
            path = folder + entry.name
            if shape:
                if re.fullmatch(shape[0], entry.name) and _is(entry.is_dir):
                    yield from below(f"{path}/", shape[1:], above)
            elif folder.startswith("evidence/"):
                if _is(entry.is_symlink) or _is(lambda: entry.is_dir(follow_symlinks=False)):
                    yield from (f"{path}/{name}" for name in SESSION)
            elif _layout.kind_of(path) in KINDS and _is(entry.is_file):
                yield path

    tops = [path for kind, path in _layout.TOP_FILES.items() if kind in KINDS] + [f"{top}/" for top in SHAPES]
    for top in sorted(tops, key=_order):
        found = below(top, SHAPES[top[:-1]]) if top.endswith("/") else [top] if (root / top).is_file() else []
        yield from (path for path in found if _past(path, after))


def _listed(folder: Path, above: frozenset[tuple[int, int]],
            unlisted: Counter[str]) -> tuple[list[os.DirEntry], frozenset[tuple[int, int]]] | None:
    """The entries of `folder` in name order, and `above` with its device and inode. None for a
    folder `above` holds, and for one missing or that cannot be listed, counted in `unlisted` by
    error type."""
    try:
        found = os.stat(folder)
        if (found.st_dev, found.st_ino) in above:
            return None
        with os.scandir(folder) as entries:
            return sorted(entries, key=lambda entry: entry.name), above | {(found.st_dev, found.st_ino)}
    except OSError as error:
        if not isinstance(error, FileNotFoundError):
            unlisted[type(error).__name__] += 1
        return None


def _is(test: Callable[[], bool]) -> bool:
    try:
        return test()
    except OSError:
        return False


def _again(waiting: list[str], after: str | None) -> list[str]:
    """The files a link kept from the round before, in the pass's order after `after`."""
    return sorted((path for path in waiting if _past(path, after)), key=_order)


def _stage(root: Path, folder: Path, path: str, zone: tzinfo | None, batch: list[dict], left: Counter[str],
           linked: set[str]) -> None:
    """`path` masked into the batch, with its last-written copy, when it holds what Atlas wrote and
    masking changes it. A last-written copy that differs from a file Atlas wrote is staged too. A file
    a link stands in the way of, or in place of it or its copy, is left and joins `linked`."""
    kind = _layout.kind_of(path)
    mirror = _layout.mirror_path(path) if _layout.RESTORE.get(kind) == "mirror" else None
    try:
        data = _txn.read_file(root, path)
        if data is None:
            return
        again = _masked(kind, data, zone)
        copy = None
        if mirror and again is not None:
            try:
                copy = _txn.read_file(root, mirror)
            except OSError:
                if again == data:
                    raise
    except _txn.Linked:
        linked.add(path)
        left[LINKED] += 1
        return
    except Exception as error:
        left[type(error).__name__] += 1
        return
    if again is None:
        left[EDITED] += 1
        return
    if again != data:
        batch.append({**_put(folder, path, again), "was": _digest(data)})
        if mirror:
            batch.append({**_put(folder, mirror, again), "of": path})
    elif mirror and copy != data:
        batch.append({**_put(folder, mirror, data), "was": None if copy is None else _digest(copy)})


def _masked(kind: _layout.FileKind, data: bytes, zone: tzinfo | None) -> bytes | None:
    """`data` written again through the codec, which masks its secrets. None for a file Atlas did
    not write as it reads: a sealed file whose seal fails, or a session.md that an Atlas that did
    not mask would not have written byte for byte."""
    if kind == "session":
        value = _codec.parse(kind, data, zone)
        return _codec.render(kind, value, zone) if _codec.render_bare(kind, value, zone) == data else None
    if not _seal.verifies(data):
        return None
    return _seal.stamp(_codec.render(kind, _codec.parse(kind, data, zone), zone))


def _flush(root: Path, folder: Path, batch: list[dict], head: Callable[[], int]) -> int:
    """The batch as one transaction, and HEAD one more. The number of record files it masks."""
    if not batch:
        return 0
    txn = _txn.adopt(root, folder, [*batch, _put(folder, _layout.HEAD, f"{head() + 1}\n".encode())])
    try:
        _txn.apply(root, txn)
    except Exception:
        _txn.discard(root, txn)
        raise
    masked = sum(not entry["target"].startswith(_layout.MIRROR) for entry in batch)
    batch.clear()
    return masked


def _state(folder: Path) -> tuple[str | None, list[dict], list[str] | None, list[str]]:
    """The last step, the batch, the files this round reads again, None for a round that walks the
    record, and the files it met a link at. Raises when the state does not check out."""
    state = json.loads((folder / STATE).read_bytes())
    after, batch, waiting, linked = state["after"], state["batch"], state["waiting"], state["linked"]
    staged = [entry["staged"] for entry in batch]
    if (state["check"] != _check(after, batch, waiting, linked)
            or not (after is None or _layout.kind_of(after) in KINDS or after.endswith("/"))
            or not all(_layout.kind_of(path) in KINDS for path in [*(waiting or []), *linked])
            or not all(isinstance(name, str) and (folder / name).is_file() for name in staged)):
        raise ValueError("the masking state does not check out")
    return after, batch, waiting, linked


def _load(folder: Path) -> tuple[str | None, list[dict], list[str] | None, list[str]]:
    """The state, if it checks out; else the pass starts over."""
    try:
        return _state(folder)
    except FileNotFoundError:
        pass
    except Exception as error:
        log.warning("Atlas starts masking the files an older version wrote over (%s)", type(error).__name__)
    shutil.rmtree(folder, ignore_errors=True)
    return None, [], None, []


def _save(folder: Path, after: str | None, batch: list[dict], waiting: list[str] | None, linked: list[str]) -> None:
    folder.mkdir(exist_ok=True)
    state = {"after": after, "batch": batch, "waiting": waiting, "linked": linked,
             "check": _check(after, batch, waiting, linked)}
    _txn.write_note(folder / STATE, json.dumps(state).encode())


def _put(folder: Path, target: str, data: bytes) -> dict[str, str]:
    """`data` staged for `target`, under a name no other target's copy takes."""
    name = _digest(target.encode())
    folder.mkdir(exist_ok=True)
    _txn.write_synced(folder / name, data)
    return {"target": target, "staged": name, "holds": _digest(data)}


def _check(*state: object) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _alone(path: str | None) -> bool:
    """Whether `path` lies where each open masks in a transaction of its own, as no other file
    matches what the files there hold."""
    return path is not None and path.partition("/")[0] in ALONE


def _order(path: str) -> tuple[bool, list[str]]:
    """The files masked together first, then the others, each in the order a walk of sorted folders
    reaches them."""
    return _alone(path), path.split("/")


def _past(path: str, after: str | None) -> bool:
    return after is None or _order(path) > _order(after)


def _ahead(folder: str, after: str | None) -> bool:
    """Whether the folder `folder` may hold a path past `after`."""
    if after is None:
        return True
    alone, parts = _order(folder.rstrip("/"))
    mark_alone, mark = _order(after)
    return (alone, parts) >= (mark_alone, mark[:len(parts)])
