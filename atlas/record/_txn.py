"""PRIVATE to atlas.record. The write lock, staged transactions and roll-forward.

A transaction stages every new file under ``.atlas/txn/<id>/``, then writes the manifest
``.atlas/txn/<id>.ready``. Applying it replaces each target with its staged copy. Replaying
an applied entry finds no staged copy and skips it, so roll-forward is idempotent.
"""
from __future__ import annotations

import contextlib
import errno
import fcntl  # Linux and macOS. flock is unreliable on NFS: setup refuses a network-mounted record dir.
import hashlib
import json
import logging
import os
import shutil
import stat
import subprocess
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Iterator, Mapping

from ._layout import MIRROR, session_folder

MACHINERY = ".atlas"
PRUNED = ("evidence", f"{MIRROR}/evidence")
SWEPT = "swept"
SWEEPING = "sweeping"
GUARDED = "-guarded"
FOLDER = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
SKIPPED = frozenset({errno.ENOENT, errno.ENOTDIR, errno.ELOOP})
KEPT = frozenset({errno.ENOTEMPTY, errno.EEXIST, errno.ENOTDIR, errno.ENOENT})
UNREACHABLE = frozenset({errno.ELOOP, errno.ENOTDIR, errno.EINVAL})
NETWORK_FS = frozenset({
    "nfs", "nfs4", "cifs", "smb", "smb3", "smbfs", "afpfs", "webdav", "davfs", "ncpfs", "9p",
    "afs", "ceph", "glusterfs", "lustre", "gpfs", "fuse.sshfs", "fuse.rclone", "fuse.s3fs",
})
log = logging.getLogger(__name__)


class NotLocal(Exception):
    """The record folder is on a network filesystem, where flock is unreliable (ADR 0005)."""


class Changed(Exception):
    """A file the transaction deletes only while it holds what its commit compared holds something else."""


class Linked(Exception):
    """A link or a file stands where a Session folder the transaction writes in, or a folder in it, is.
    Its args are the `scope` of each target the transaction has left."""


def machinery(root: Path) -> Path:
    return root / MACHINERY


@contextmanager
def write_lock(root: Path) -> Iterator[None]:
    """fcntl.flock(LOCK_EX) on <root>/.atlas/write.lock. Held only while applying bytes."""
    path = machinery(root) / "write.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def stage(root: Path, files: Mapping[str, bytes | None], guards: Mapping[str, str] | None = None) -> str:
    """Stage `files` (record-relative path -> bytes, None = delete) and mark them ready. `guards` maps
    a deleted path to the sha256 it must still hold for the transaction to apply. Returns the
    transaction id."""
    txn_dir = machinery(root) / "txn"
    txn_id = f"{time.time_ns()}-{os.getpid()}"
    staging = txn_dir / txn_id
    staging.mkdir(parents=True)
    manifest = []
    for n, (target, data) in enumerate(sorted(files.items())):
        if data is None:
            manifest.append({"target": target, "delete": True,
                             **({"was": guards[target]} if guards and target in guards else {})})
            continue
        write_synced(staging / str(n), data)
        manifest.append({"target": target, "staged": str(n)})
    fsync_dir(staging)
    _ready(txn_dir, txn_id, manifest)
    return txn_id


def adopt(root: Path, folder: Path, manifest: list[dict]) -> str:
    """Makes `folder`, whose files are synced, a guarded transaction with `manifest`: an entry may
    carry "holds", the sha256 of its staged copy, "was", the sha256 of the bytes it replaces (null
    for none), and "of", the target whose entry it follows. A guarded transaction holds only writes
    its writer can make again, so a replay that fails discards it instead of raising. Returns the
    transaction id."""
    txn_dir = machinery(root) / "txn"
    txn_dir.mkdir(parents=True, exist_ok=True)
    txn_id = f"{time.time_ns()}-{os.getpid()}{GUARDED}"
    fsync_dir(folder)
    os.rename(folder, txn_dir / txn_id)
    _ready(txn_dir, txn_id, manifest)
    return txn_id


def discard(root: Path, txn_id: str) -> None:
    txn_dir = machinery(root) / "txn"
    (txn_dir / f"{txn_id}.ready").unlink(missing_ok=True)
    shutil.rmtree(txn_dir / txn_id, ignore_errors=True)


def _ready(txn_dir: Path, txn_id: str, manifest: list[dict]) -> None:
    ready = txn_dir / f"{txn_id}.ready"
    write_synced(ready.with_suffix(".tmp"), json.dumps(manifest).encode())
    os.replace(ready.with_suffix(".tmp"), ready)
    fsync_dir(txn_dir)


def apply(root: Path, txn_id: str) -> None:
    """Replaces each target with its staged copy, in its folder reached as `below` reaches it, then
    deletes. Raises ValueError, before any replace, when a staged copy does not hold what its "holds"
    names. An entry whose target no longer holds the bytes its "was" names, read as `read_file` reads
    it, is passed over, right before its replace, and so is each entry "of" it, also of a target a link
    stands in the way of. When the target of a delete with "was" holds other bytes, apply raises
    Changed before any change. Once they all check out, the manifest is saved without
    them, so a replay finishes what this apply began. When a link or a file stands where a folder of a
    target is, apply replaces every other target, deletes nothing and raises Linked with the `scope` of
    each target left, keeping the transaction ready, so a replay once the link is gone finishes it. It
    deletes only once each file it writes in a Session folder, in this apply or an earlier one, is there."""
    txn_dir = machinery(root) / "txn"
    ready = txn_dir / f"{txn_id}.ready"
    touched: dict[str, str] = {}
    deleted: dict[str, str] = {}
    changed: set[str] = set()
    blocked: list[str] = []
    manifest = json.loads(ready.read_bytes())
    if any("holds" in entry and _digest(txn_dir / txn_id / entry["staged"]) not in (entry["holds"], None)
           for entry in manifest):
        raise ValueError("a staged copy does not hold what was written")
    guarded = [entry for entry in manifest if entry.get("delete") and "was" in entry]
    for entry in guarded:
        if _digest_below(root, entry["target"]) not in (entry["was"], None):
            raise Changed(entry["target"])
    if guarded:
        manifest = [{k: v for k, v in entry.items() if k != "was"} if entry.get("delete") else entry
                    for entry in manifest]
        _ready(txn_dir, txn_id, manifest)
    for entry in sorted(manifest, key=lambda entry: "of" in entry):
        if entry.get("delete") or entry.get("of") in changed or entry.get("of") in blocked:
            continue
        staged = txn_dir / txn_id / entry["staged"]
        if not staged.exists():
            continue
        try:
            if "was" in entry and _digest_file(root, entry["target"]) != entry["was"]:
                changed.add(entry["target"])
                continue
        except Linked:
            blocked.append(entry["target"])
            continue
        if _replace_below(root, staged, entry["target"]):
            touched.setdefault(entry["target"].rpartition("/")[0], entry["target"])
        else:
            blocked.append(entry["target"])
    deletes = [entry["target"] for entry in manifest if entry.get("delete")]
    if deletes and not blocked:
        blocked = [entry["target"] for entry in manifest
                   if not entry.get("delete") and not _present(root, entry["target"])]
    if not blocked:
        for target in deletes:
            if _unlink(root, target):
                deleted.setdefault(target.rpartition("/")[0], target)
        for folder in sorted(deleted, key=lambda f: f.count("/"), reverse=True):
            _prune(root, folder)
    for target in {**deleted, **touched}.values():
        _sync_below(root, target)
    if blocked:
        raise Linked(*sorted({scope(target) for target in blocked + deletes}))
    ready.unlink()
    shutil.rmtree(txn_dir / txn_id, ignore_errors=True)


def scope(path: str) -> str:
    """The Session folder, or mirror, that the record path `path` lies in, else `path` itself."""
    return session_folder(path) or path


def below(root: Path, path: str, make: bool = False) -> tuple[int, str]:
    """The open folder that holds the record path `path`, and the name `path` has in it, its missing
    folders made first when `make`. A Session evidence folder, its mirror and every folder in them are
    opened, and made, without following a link: OSError(ELOOP or ENOTDIR) when a link or a file stands
    there, EINVAL for a "." or ".." name."""
    parts = path.rstrip("/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise OSError(errno.EINVAL, "not a record path")
    folder = session_folder(path.rstrip("/") + "/")
    trusted = folder.count("/") - 1 if folder else len(parts) - 1
    above = root.joinpath(*parts[:trusted])
    if make:
        above.mkdir(parents=True, exist_ok=True)
    return _descend(os.open(above, os.O_RDONLY | os.O_DIRECTORY), parts[trusted:-1], make), parts[-1]


def reachable(root: Path, path: str) -> bool:
    """Whether `path` is reached without a link or a file where its Session folder or a folder in it is.
    True for a path in no Session folder."""
    if not session_folder(path):
        return True
    try:
        os.close(below(root, path)[0])
    except FileNotFoundError:
        return True
    except OSError as error:
        if error.errno not in UNREACHABLE:
            raise
        return False
    return True


def _descend(fd: int, parts: list[str], make: bool = False) -> int:
    """The folder `parts` below the open folder `fd`, opened without following a link, and made first
    when `make` and missing. Closes `fd`."""
    for part in parts:
        try:
            try:
                child = os.open(part, FOLDER, dir_fd=fd)
            except FileNotFoundError:
                if not make:
                    raise
                with suppress(FileExistsError):
                    os.mkdir(part, dir_fd=fd)
                child = os.open(part, FOLDER, dir_fd=fd)
        finally:
            os.close(fd)
        fd = child
    return fd


def read_below(root: Path, path: str) -> bytes | None:
    """The bytes of the file at `path`, opened as `below` opens it. None for no file, and for a link
    or anything else that is not a regular file."""
    try:
        file = _open_below(root, path, os.O_NOFOLLOW)
    except OSError as error:
        if error.errno in UNREACHABLE | {errno.ENOENT, errno.ENXIO}:
            return None
        raise
    with open(file, "rb") as fh:
        return fh.read() if stat.S_ISREG(os.fstat(fh.fileno()).st_mode) else None


def read_file(root: Path, path: str) -> bytes | None:
    """The bytes of the regular file at `path`, None for none, opened as `below` opens its folder and
    without blocking, and in a Session folder or its mirror without following a link. Raises Linked
    when a link or a file stands where a folder `below` opens is, or a link where that file is, and
    OSError for anything else that is not a regular file."""
    try:
        file = _open_below(root, path, os.O_NOFOLLOW if session_folder(path) else 0)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in UNREACHABLE:
            raise Linked(scope(path)) from error
        raise
    with open(file, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        return fh.read()


def _open_below(root: Path, path: str, flags: int) -> int:
    """The file at `path` opened to read without blocking, with `flags`, in its folder opened as
    `below` opens it."""
    fd, name = below(root, path)
    try:
        return os.open(name, os.O_RDONLY | os.O_NONBLOCK | flags, dir_fd=fd)
    finally:
        os.close(fd)


def files_below(root: Path, folder: str) -> list[str]:
    """The record paths of every file and link in the Session folder `folder`, or its mirror, and in
    its subfolders, walked without following a link. Raises OSError(ELOOP or ENOTDIR) when a link or
    a file stands where the folder or a subfolder is."""
    try:
        fd, name = below(root, folder)
    except FileNotFoundError:
        return []
    try:
        top = os.open(name, FOLDER, dir_fd=fd)
    except FileNotFoundError:
        return []
    finally:
        os.close(fd)
    found: list[str] = []
    try:
        _walk(top, folder, found)
    finally:
        os.close(top)
    return found


def _walk(fd: int, prefix: str, found: list[str]) -> None:
    with os.scandir(fd) as entries:
        listed = list(entries)
    for entry in listed:
        if entry.is_dir(follow_symlinks=False):
            child = os.open(entry.name, FOLDER, dir_fd=fd)
            try:
                _walk(child, f"{prefix}{entry.name}/", found)
            finally:
                os.close(child)
        elif entry.is_symlink() or entry.is_file(follow_symlinks=False):
            found.append(prefix + entry.name)


def _unlink(root: Path, target: str) -> bool:
    """Deletes `target`, a link as itself, reached as `below` reaches it. Leaves it, logged, and
    returns False when a link stands where one of its folders was."""
    try:
        fd, name = below(root, target)
    except FileNotFoundError:
        return True
    except OSError as error:
        if error.errno not in UNREACHABLE:
            raise
        log.warning("Atlas left a file in place: a link stands where its folder was")
        return False
    try:
        os.unlink(name, dir_fd=fd)
    except FileNotFoundError:
        pass
    finally:
        os.close(fd)
    return True


def _replace_below(root: Path, staged: Path, target: str) -> bool:
    """Moves `staged` to `target`, reached as `below` reaches it, making the folders it lacks. Leaves
    it staged and returns False when a link or a file stands where one of its folders was, also when
    the folder it opened is no longer the one at its path once the file is in it: it moves the file
    back then, so nothing it wrote stays in a folder taken out of the record."""
    try:
        fd, name = below(root, target, make=True)
    except OSError as error:
        if error.errno not in UNREACHABLE:
            raise
        return False
    try:
        os.replace(staged, name, dst_dir_fd=fd)
        opened = os.fstat(fd)
        if _folder_at(root, target) == (opened.st_dev, opened.st_ino):
            return True
        os.replace(name, staged, src_dir_fd=fd)
    except FileNotFoundError:
        pass
    finally:
        os.close(fd)
    return False


def _folder_at(root: Path, path: str) -> tuple[int, int] | None:
    """The device and inode of the folder that holds `path`, reached as `below` reaches it. None when
    it is gone or a link or a file stands where it is."""
    try:
        fd, _ = below(root, path)
    except OSError as error:
        if error.errno not in UNREACHABLE | {errno.ENOENT}:
            raise
        return None
    try:
        found = os.fstat(fd)
    finally:
        os.close(fd)
    return found.st_dev, found.st_ino


def _present(root: Path, path: str) -> bool:
    """Whether a file is at `path`, reached as `below` reaches it. True for a path in no Session folder."""
    if not session_folder(path):
        return True
    try:
        fd, name = below(root, path)
    except OSError as error:
        if error.errno not in UNREACHABLE | {errno.ENOENT}:
            raise
        return False
    try:
        return stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode)
    except FileNotFoundError:
        return False
    finally:
        os.close(fd)


def _sync_below(root: Path, path: str) -> None:
    """Syncs the folder that holds `path`, reached as `below` reaches it. Passes over a folder that is
    gone or that a link stands for."""
    try:
        fd, _ = below(root, path)
    except OSError as error:
        if error.errno not in UNREACHABLE | {errno.ENOENT}:
            raise
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _prune(root: Path, folder: str) -> None:
    """Removes the folders a removal left empty, from `folder` up to the pruned top it lies in. Opens
    each down from that top without following a link, so a link below the top ends the prune. Passes
    over a folder already gone, so a replay finishes the ancestors of an interrupted prune."""
    top = next((top for top in PRUNED if folder.startswith(f"{top}/")), None)
    if top is None:
        return
    parts = folder.removeprefix(f"{top}/").split("/")
    while parts:
        name = parts.pop()
        try:
            fd = _descend(os.open(root / top, os.O_RDONLY | os.O_DIRECTORY), parts)
        except FileNotFoundError:
            continue
        except OSError:
            return
        try:
            os.rmdir(name, dir_fd=fd)
        except FileNotFoundError:
            pass
        except OSError:
            return
        finally:
            os.close(fd)


def sweep(root: Path) -> None:
    """Until done once: removes the empty folders under each pruned top, bottom up, which versions
    that pruned only `evidence/` left in the mirror. Opens every folder down from the record root
    without following a symlink, and passes over symlinks. A note, synced with its folder before any
    removal and dropped once every removal is synced, makes a sweep after a failed sync or a crash
    sync every folder it visits. Never raises: on any failure it logs, leaves no mark and the next
    open sweeps again."""
    marker, note = machinery(root) / SWEPT, machinery(root) / SWEEPING
    try:
        if marker.exists():
            with suppress(OSError):
                note.unlink(missing_ok=True)
            return
        resumed = note.exists()
        if not resumed:
            write_synced(note, b"")
        fsync_dir(note.parent)
        failed: list[OSError] = []
        unvisited: list[OSError] = []
        for top in PRUNED:
            try:
                fd = _open_top(root, top)
            except OSError as error:
                unvisited.append(error)
                continue
            if fd is None:
                continue
            try:
                _empty_below(fd, failed, unvisited, resumed)
            finally:
                os.close(fd)
        if not failed and not unvisited:
            write_synced(marker, b"")
            fsync_dir(marker.parent)
        if not (resumed and unvisited):
            note.unlink()
        if failed or unvisited:
            raise (failed + unvisited)[0]
    except Exception as error:
        log.warning("Atlas left the empty evidence folders for the next open (%s)", type(error).__name__)
        with suppress(OSError):
            marker.unlink(missing_ok=True)


def _open_top(root: Path, top: str) -> int | None:
    """`top` opened down from the record root, or None when a part of it is missing or a symlink."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    for part in Path(top).parts:
        try:
            child = os.open(part, FOLDER, dir_fd=fd)
        except OSError as error:
            if error.errno not in SKIPPED:
                raise
            return None
        finally:
            os.close(fd)
        fd = child
    return fd


def _empty_below(fd: int, failed: list[OSError], unvisited: list[OSError], resumed: bool) -> None:
    """Removes the empty folders below the open folder `fd`, bottom up, then syncs it if it lost one
    or the sweep resumed."""
    with os.scandir(fd) as entries:
        names = [entry.name for entry in entries if entry.is_dir(follow_symlinks=False)]
    removed = False
    for name in names:
        try:
            child = os.open(name, FOLDER, dir_fd=fd)
        except OSError as error:
            if error.errno not in SKIPPED:
                unvisited.append(error)
            continue
        try:
            _empty_below(child, failed, unvisited, resumed)
        finally:
            os.close(child)
        try:
            os.rmdir(name, dir_fd=fd)
            removed = True
        except OSError as error:
            if error.errno not in KEPT:
                failed.append(error)
    if removed or resumed:
        os.fsync(fd)


def write_note(path: Path, data: bytes = b"") -> None:
    """Writes the note `path` whole and syncs its folder, so a crash leaves it as it was or as written."""
    write_synced(path.with_suffix(".tmp"), data)
    os.replace(path.with_suffix(".tmp"), path)
    fsync_dir(path.parent)


def roll_forward(root: Path) -> set[str]:
    """For each .atlas/txn/<id>.ready: replay os.replace(staged -> target) for staged files that
    still exist (idempotent), then remove. Delete txn dirs without .ready. A transaction a link
    stands in the way of stays ready; returns the `scope` of each target it has left."""
    txn_dir = machinery(root) / "txn"
    waiting: set[str] = set()
    if not txn_dir.is_dir():
        return waiting
    for ready in sorted(txn_dir.glob("*.ready")):
        txn_id = ready.name.removesuffix(".ready")
        try:
            apply(root, txn_id)
        except Exception as error:
            if isinstance(error, Linked) and not txn_id.endswith(GUARDED):
                waiting.update(error.args)
                continue
            if not txn_id.endswith(GUARDED) and not isinstance(error, Changed):
                raise
            log.warning("Atlas dropped a transaction it will make again (%s)", type(error).__name__)
            with contextlib.suppress(OSError):
                discard(root, txn_id)
    for leftover in txn_dir.iterdir():
        if leftover.is_dir() and not (txn_dir / f"{leftover.name}.ready").exists():
            shutil.rmtree(leftover, ignore_errors=True)
        elif leftover.suffix == ".tmp":
            leftover.unlink(missing_ok=True)
    return waiting


def pending(root: Path) -> dict[str, bytes | None]:
    """What the ready transactions roll_forward has not applied yet hold, by target (None removes
    it): bytes already committed that a failed apply left in staging."""
    txn_dir = machinery(root) / "txn"
    held: dict[str, bytes | None] = {}
    for ready in sorted(txn_dir.glob("*.ready")) if txn_dir.is_dir() else ():
        for entry in json.loads(ready.read_bytes()):
            staged = txn_dir / ready.name.removesuffix(".ready") / entry.get("staged", "")
            if entry.get("delete"):
                held[entry["target"]] = None
            elif staged.is_file():
                held[entry["target"]] = staged.read_bytes()
    return held


def require_local(root: Path) -> None:
    kind = fs_type(root, _mount_table())
    if kind in NETWORK_FS:
        raise NotLocal(f"{root} is on a {kind} mount")


def fs_type(path: Path, mounts: str) -> str | None:
    """The filesystem type of the mount holding `path`. `mounts` is /proc/mounts on Linux or
    the output of `mount` on macOS."""
    path = Path(os.path.abspath(path))
    best: tuple[int, str] | None = None
    for point, kind in _parse_mounts(mounts):
        p = Path(point)
        if path == p or p in path.parents:
            depth = len(p.parts)
            if best is None or depth > best[0]:
                best = (depth, kind)
    return best[1] if best else None


def _parse_mounts(text: str) -> list[tuple[str, str]]:
    table = []
    for line in text.splitlines():
        if " on " in line and line.rstrip().endswith(")"):
            point, _, rest = line.partition(" on ")[2].rpartition(" (")
            table.append((point, rest.split(",")[0].strip()))
            continue
        fields = line.split()
        if len(fields) >= 3:
            table.append((fields[1].encode().decode("unicode_escape"), fields[2]))
    return table


def _mount_table() -> str:
    proc = Path("/proc/mounts")
    if proc.exists():
        return proc.read_text()
    return subprocess.run(["mount"], capture_output=True, text=True, check=False).stdout


def write_synced(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _digest(path: Path) -> str | None:
    """The sha256 of `path`'s bytes, None for no file, and a value no digest takes for a file that
    cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        return "unreadable"


def _digest_file(root: Path, path: str) -> str | None:
    """`_digest` of the file at `path`, read as `read_file` reads it. Raises Linked as it does."""
    try:
        data = read_file(root, path)
    except OSError:
        return "unreadable"
    return None if data is None else hashlib.sha256(data).hexdigest()


def _digest_below(root: Path, path: str) -> str | None:
    """`_digest` of the file at `path`, read as `read_below` reads it."""
    try:
        data = read_below(root, path)
    except OSError:
        return "unreadable"
    return None if data is None else hashlib.sha256(data).hexdigest()


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
