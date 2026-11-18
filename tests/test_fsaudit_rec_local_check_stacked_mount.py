"""fsaudit C15: README: "When the record folder is on a network mount, setup stops". An NFS share
mounted on demand (x-systemd.automount, or an autofs direct map) has two lines in /proc/mounts
for one mount point: the autofs trigger first, the NFS mount over it second. fs_type keeps the
first of two mounts of equal depth, so it answers "autofs" and setup goes on."""
import pytest

from atlas.record import NotLocal, Record, _txn
from test_commit import PROFILE, SETTINGS


def test_setup_refuses_an_nfs_share_mounted_on_demand(tmp_path, monkeypatch):
    table = (f"/dev/sda1 / ext4 rw 0 0\n"
             f"systemd-1 {tmp_path} autofs rw,relatime,fd=44,pgrp=1,timeout=0,minproto=5,maxproto=5,direct 0 0\n"
             f"nas:/export {tmp_path} nfs4 rw,relatime,vers=4.2 0 0\n")
    monkeypatch.setattr(_txn, "_mount_table", lambda: table)
    assert _txn.fs_type(tmp_path / "atlas", table) == "nfs4"
    with pytest.raises(NotLocal):
        Record.open(tmp_path / "atlas").create(SETTINGS, PROFILE)
