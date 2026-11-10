import os
import subprocess
import sys
import textwrap
import time as clock
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from atlas.model import ChannelSetting, Forgotten, Pause, Profile, Settings
from atlas.record import Conflict, NotLocal, Record
from atlas.record import _seal, _txn

REPO = Path(__file__).resolve().parents[1]
SETTINGS = Settings(timezone="Europe/Berlin", brief_at=time(7, 30), review_day=7, review_at=time(18, 0),
                    gap=timedelta(minutes=15), machine="box", harnesses={}, exclusions=(),
                    channel=ChannelSetting("telegram"))
PROFILE = Profile(("work", "health", "family"), ())
BERLIN = ZoneInfo(SETTINGS.timezone)


@pytest.fixture
def record(tmp_path) -> Record:
    rec = Record.open(tmp_path / "atlas")
    rec.create(SETTINGS, PROFILE)
    return rec


def files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def run(code: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code), *args], cwd=REPO,
                          capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(REPO)})


def test_create_writes_the_five_files_sealed_and_mirrored(record):
    root = record.root
    for name in ("README.md", "settings.md", "profile.md", "pauses.md", "forgotten.md"):
        data = (root / name).read_bytes()
        assert _seal.verifies(data), name
        assert (root / ".atlas/last-written" / name).read_bytes() == data
    assert root.stat().st_mode & 0o777 == 0o700
    snap = record.snapshot()
    assert snap.settings() == SETTINGS and snap.profile() == PROFILE
    assert list(snap.pauses()) == [] and snap.forgotten() == Forgotten((), frozenset())


def test_create_again_changes_no_file(record):
    before = {p: (d, (record.root / p).stat().st_mtime_ns) for p, d in files(record.root).items()}
    record.create(SETTINGS, Profile(("other",), ()))
    after = {p: (d, (record.root / p).stat().st_mtime_ns) for p, d in files(record.root).items()}
    assert after == before


def test_a_commit_is_all_or_nothing_across_files(record):
    stale = record.snapshot()
    stale.pauses()
    fresh = record.snapshot()
    away = Pause(datetime(2026, 11, 20, 16, 40, tzinfo=BERLIN), datetime(2026, 11, 22, 7, tzinfo=BERLIN), "away")
    record.commit(fresh.changes().put_pauses([away]))
    before = files(record.root)

    cs = stale.changes().put_profile(Profile(("work",), ())).put_pauses([])
    with pytest.raises(Conflict):
        record.commit(cs)
    assert files(record.root) == before


def test_a_changed_base_raises_conflict(record):
    first, second = record.snapshot(), record.snapshot()
    record.commit(first.changes().put_profile(Profile(("work", "home"), ())))
    with pytest.raises(Conflict):
        record.commit(second.changes().put_profile(Profile(("work", "garden"), ())))
    assert record.snapshot().profile().areas == ("work", "home")


def test_a_commit_that_changes_nothing_leaves_head_alone(record):
    head = record.snapshot().revision
    assert record.commit(record.snapshot().changes().put_profile(PROFILE)) == head


def test_a_crash_between_ready_and_apply_is_rolled_forward_on_next_open(record):
    before = (record.root / "profile.md").read_bytes()
    crashed = run("""
        import os, sys
        from pathlib import Path
        from atlas.model import Profile
        from atlas.record import Record, _txn
        rec = Record.open(Path(sys.argv[1]))
        cs = rec.snapshot().changes().put_profile(Profile(("work", "home"), ()))
        _txn.apply = lambda root, txn: os._exit(9)
        rec.commit(cs)
    """, str(record.root))
    assert crashed.returncode == 9, crashed.stderr
    assert (record.root / "profile.md").read_bytes() == before
    assert record.snapshot().profile().areas == ("work", "home")
    assert list((record.root / ".atlas/txn").glob("*.ready"))

    reopened = Record.open(record.root)
    assert reopened.snapshot().profile().areas == ("work", "home")
    assert _seal.verifies((record.root / "profile.md").read_bytes())
    assert list((record.root / ".atlas/txn").iterdir()) == []


def test_a_transaction_that_cannot_be_applied_is_in_each_snapshot_and_stops_each_commit(record, monkeypatch):
    before = (record.root / "profile.md").read_bytes()
    real = _txn._replace_below

    def profile_fails(root, staged, target):
        if target == "profile.md":
            raise OSError(5, "invented I/O failure")
        return real(root, staged, target)
    monkeypatch.setattr(_txn, "_replace_below", profile_fails)
    with pytest.raises(OSError):
        record.commit(record.snapshot().changes().put_profile(Profile(("work", "home"), ())))
    reopened = Record.open(record.root)
    assert (record.root / "profile.md").read_bytes() == before
    assert reopened.snapshot().profile().areas == ("work", "home")
    with pytest.raises(OSError):
        reopened.commit(reopened.snapshot().changes().put_pauses(()))
    monkeypatch.setattr(_txn, "_replace_below", real)
    assert Record.open(record.root).snapshot().profile().areas == ("work", "home")
    assert list((record.root / ".atlas/txn").iterdir()) == []


def test_a_guarded_commit_conflicts_on_what_its_plan_read_or_listed_and_nothing_else(record):
    def plan():
        snap = record.snapshot()
        snap.pauses(), snap.pursuits()
        return snap.changes().guard_reads().put_profile(Profile(("work", "plan"), ()))

    unrelated = plan()
    record.commit(record.snapshot().changes().put_forgotten(
        Forgotten(((datetime(2026, 11, 18, tzinfo=BERLIN), "a Session"),), frozenset())))
    record.commit(unrelated)
    read = plan()
    record.commit(record.snapshot().changes().put_pauses((Pause(datetime(2026, 11, 20, tzinfo=BERLIN),
                                                                datetime(2026, 11, 22, tzinfo=BERLIN), "pause"),)))
    with pytest.raises(Conflict):
        record.commit(read)
    listed = plan()
    (record.root / "pursuits").mkdir(exist_ok=True)
    (record.root / "pursuits" / "invented.md").write_bytes(_seal.stamp(b"---\nsealed: \n---\nan invented file\n"))
    with pytest.raises(Conflict):
        record.commit(listed)


def test_a_crash_before_ready_leaves_only_staging_junk(record):
    before = files(record.root)
    crashed = run("""
        import os, sys
        from pathlib import Path
        from atlas.model import Profile
        from atlas.record import Record, _txn
        rec = Record.open(Path(sys.argv[1]))
        cs = rec.snapshot().changes().put_profile(Profile(("work", "home"), ()))
        real_write = _txn.write_synced
        def die_at_ready(path, data):
            if path.suffix == ".tmp":
                os._exit(9)
            real_write(path, data)
        _txn.write_synced = die_at_ready
        rec.commit(cs)
    """, str(record.root))
    assert crashed.returncode == 9, crashed.stderr
    Record.open(record.root)
    assert files(record.root) == before
    assert list((record.root / ".atlas/txn").iterdir()) == []


def test_concurrent_commits_serialise_and_the_loser_gets_conflict(record):
    racer = """
        import sys, time
        from pathlib import Path
        from atlas.model import Profile
        from atlas.record import Conflict, Record
        root, me = Path(sys.argv[1]), sys.argv[2]
        rec = Record.open(root)
        snap = rec.snapshot()
        snap.profile()
        (root.parent / f"ready-{me}").touch()
        while not (root.parent / "go").exists():
            time.sleep(0.005)
        try:
            rec.commit(snap.changes().put_profile(Profile(("work", me), ())))
            print("won")
        except Conflict:
            print("conflict")
    """
    procs = [subprocess.Popen([sys.executable, "-c", textwrap.dedent(racer), str(record.root), name], cwd=REPO,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env={**os.environ, "PYTHONPATH": str(REPO)}) for name in ("alpha", "beta")]
    deadline = clock.monotonic() + 30
    while not all((record.root.parent / f"ready-{n}").exists() for n in ("alpha", "beta")):
        assert clock.monotonic() < deadline, [p.stderr.read() for p in procs if p.poll() is not None]
        clock.sleep(0.005)
    (record.root.parent / "go").touch()
    results = {name: p.communicate(timeout=30)[0].strip() for name, p in zip(("alpha", "beta"), procs)}

    assert sorted(results.values()) == ["conflict", "won"], results
    winner = next(name for name, r in results.items() if r == "won")
    assert record.snapshot().profile().areas == ("work", winner)
    assert record.snapshot().revision.head == 2


def test_setup_refuses_a_network_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(_txn, "_mount_table", lambda: f"server:/export {tmp_path} nfs4 rw 0 0\n/dev/sda1 / ext4 rw 0 0\n")
    with pytest.raises(NotLocal):
        Record.open(tmp_path / "atlas").create(SETTINGS, PROFILE)
    assert not (tmp_path / "atlas").exists()


@pytest.mark.parametrize("record_dir, table, kind", [
    ("/home/me/atlas", "/dev/sda1 / ext4 rw 0 0\nhost:/home /home nfs4 rw 0 0\n", "nfs4"),
    ("/home/me/atlas", "/dev/sda1 / ext4 rw 0 0\nhost:/srv /srv nfs rw 0 0\n", "ext4"),
    ("/home/me/atlas", "//nas/share /home/me cifs rw 0 0\n", "cifs"),
    ("/Users/me/atlas", "/dev/disk3s1 on / (apfs, local, journaled)\nserver:/exp on /Users/me (nfs, nodev)\n", "nfs"),
])
def test_the_mount_holding_the_record_decides(record_dir, table, kind):
    assert _txn.fs_type(Path(record_dir), table) == kind
