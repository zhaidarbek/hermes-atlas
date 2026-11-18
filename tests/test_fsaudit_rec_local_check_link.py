"""fsaudit C15: README: "When the record folder is on a network mount, setup stops". The check
compares the record path as written (os.path.abspath) with the mount points, so a record path
that is a link to a folder on a network mount (~/atlas -> /mnt/nas/atlas) passes."""
from atlas.record import NotLocal, Record, _txn
from test_commit import PROFILE, SETTINGS


def test_setup_refuses_a_record_path_that_links_to_a_network_mount(tmp_path, monkeypatch):
    nas = tmp_path / "nas"
    (nas / "atlas").mkdir(parents=True)
    (tmp_path / "atlas").symlink_to(nas / "atlas", target_is_directory=True)
    monkeypatch.setattr(_txn, "_mount_table", lambda: f"/dev/sda1 / ext4 rw 0 0\nnas:/export {nas} nfs4 rw 0 0\n")
    refused = False
    try:
        Record.open(tmp_path / "atlas").create(SETTINGS, PROFILE)
    except NotLocal:
        refused = True
    assert refused, "setup made the record on the nfs4 mount the record path links to"
    assert list((nas / "atlas").iterdir()) == []
