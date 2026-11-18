"""fsaudit C15: README: "When the record folder is on a network mount, setup stops".
_parse_mounts decodes each mount point with `.encode().decode("unicode_escape")`, which reads the
UTF-8 bytes of a non-ASCII name as Latin-1, so that mount point never matches the record path and
the mount above it (the local root) decides."""
from pathlib import Path

from atlas.record import _txn


def test_a_network_mount_with_a_non_ascii_name_is_still_found():
    table = "/dev/sda1 / ext4 rw 0 0\nnas:/export /mnt/données nfs4 rw 0 0\n"
    assert _txn.fs_type(Path("/mnt/données/atlas"), table) == "nfs4"
    assert _txn.fs_type(Path("/mnt/my nas/atlas"), "/dev/sda1 / ext4 rw 0 0\nnas:/x /mnt/my\\040nas nfs4 rw 0 0\n") == "nfs4"
