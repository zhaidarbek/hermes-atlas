"""fsaudit C16/C13: README: setup creates "the record folder, mode 0700". When `record_dir` is a
folder that already exists with a loose mode (one another user of the machine could write in),
setup only chmods it. It does not look at what is already in it, so a `.atlas` planted there as a
link to that user's folder is kept and followed: the write lock, the staged transactions and the
last-written copy of every record file are then written in the other user's folder."""
import os

from atlas.record import Record
from test_commit import PROFILE, SETTINGS


def test_setup_never_writes_through_a_link_planted_in_a_folder_it_adopts(tmp_path):
    theirs = tmp_path / "another-users-folder"
    theirs.mkdir()
    root = tmp_path / "shared" / "atlas"
    root.mkdir(parents=True)
    os.chmod(root, 0o777)                                  # as the other user found it
    (root / ".atlas").symlink_to(theirs, target_is_directory=True)

    try:
        Record.open(root).create(SETTINGS, PROFILE)
    except Exception:
        pass   # refusing the folder is one way to keep the record private

    leaked = sorted(str(p.relative_to(theirs)) for p in theirs.rglob("*") if p.is_file())
    assert leaked == [], "setup wrote Atlas's machinery outside the 0700 record folder"
