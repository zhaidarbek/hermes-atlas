"""Every test runs with HOME in a temporary folder, no Harness or Hermes override pointing out
of it, and no network. Atlas never resolves a store, its record or the Hermes home outside the
test's own temporary folder: where it would, the test fails at once with LeftHome, before
anything there is read, and a test that leaves HOME outside that folder fails at teardown. A
test's `monkeypatch.undo()` restores the real HOME, so these checks are not monkeypatched."""
from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest_plugins = ["pytester"]
OUTSIDE_HOME = ("HERMES_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "T3CODE_HOME", "ATLAS_RECORD",
                "ATLAS_NOW", "XDG_DATA_HOME", "XDG_CONFIG_HOME")
_TEST_FOLDER: list[Path] = []


class LeftHome(BaseException):
    """Atlas resolved a path outside the test's temporary folder. A BaseException, so no
    `except Exception` in Atlas logs it and carries on."""


def _inside(path: Path, folder: Path) -> bool:
    return Path(os.path.realpath(path)).is_relative_to(os.path.realpath(folder))


def _stays(what: str, path: Path) -> None:
    if _TEST_FOLDER and not _inside(path, _TEST_FOLDER[-1]):
        raise LeftHome(f"Atlas resolved {what} outside the test's temporary folder")


def _watched_store(resolve):
    def store(self, root):
        path = resolve(self, root)
        _stays(f"a {type(self).__name__} store", path)
        return path
    return store


def _watched_record(init):
    def __init__(self, root, *args, **kw):
        _stays("its record", Path(root))
        init(self, root, *args, **kw)
    return __init__


def _watched_tick(install):
    def install_tick(hermes_home):
        _stays("the Hermes home", Path(hermes_home))
        return install(hermes_home)
    return install_tick


def _subclasses(cls: type) -> list[type]:
    return [cls, *(sub for child in cls.__subclasses__() for sub in _subclasses(child))]


def pytest_configure(config):
    from atlas import record, schedule
    from atlas.sources import _adapters
    for cls in _subclasses(_adapters._Store):
        if "store" in vars(cls):
            cls.store = _watched_store(vars(cls)["store"])
    record.Record.__init__ = _watched_record(record.Record.__init__)
    schedule.install_tick = _watched_tick(schedule.install_tick)


@pytest.fixture(autouse=True)
def throwaway_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in OUTSIDE_HOME:
        monkeypatch.delenv(name, raising=False)
    kept = tempfile.tempdir
    tempfile.tempdir = str(tmp_path)
    _TEST_FOLDER.append(tmp_path)
    yield home
    _TEST_FOLDER.pop()
    tempfile.tempdir = kept
    if not _inside(Path.home(), tmp_path):
        pytest.fail("the test left HOME outside its temporary folder (a monkeypatch.undo()?)", pytrace=False)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("tests use no network")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
