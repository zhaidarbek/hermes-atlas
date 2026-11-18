"""fsaudit B7: webchat.directory() promises to refuse an archive with more than MAX_ENTRIES
entries or a directory larger than MAX_DIRECTORY "before zipfile builds an entry for each"
("zip64 too"). It reads the zip64 end record only when the classic end record's fields are
saturated (0xFFFF / 0xFFFFFFFF). zipfile reads the zip64 record whenever its locator is present,
whatever the classic record says. So a classic end record that claims one entry, in front of a
zip64 record that states the real directory, passes directory() and zipfile then reads the whole
directory and builds a ZipInfo for every entry (about 390 bytes each, measured), bounded only by
MAX_ARCHIVE: a 2 GiB file is 46 million entries, about 18 GiB.

The fixture is 200,000 bare 46-byte directory records (9.2 MB): over both limits, small on disk.
No constant is patched. Tested on CPython 3.12.16."""
import os
import struct
import zipfile
from datetime import datetime, timezone

from atlas.model import HarnessId
from atlas.sources import webchat

NOW = datetime(2026, 11, 8, 10, tzinfo=timezone.utc)
ENTRIES = 200_000


def archive() -> bytes:
    entry = struct.pack("<4s4B4HL2L5H2L", b"PK\x01\x02", 20, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    directory = entry * ENTRIES
    end64 = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, ENTRIES, ENTRIES, len(directory), 0)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, len(directory), 1)
    end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, len(entry), 0, 0)   # "one entry, 46 bytes"
    return directory + end64 + locator + end


def test_a_zip64_record_behind_a_modest_end_record_gets_past_the_directory_limits(tmp_path, monkeypatch):
    inbox = tmp_path / "atlas" / "inbox"
    inbox.mkdir(parents=True)
    path = inbox / "export.zip"
    path.write_bytes(archive())
    old = int((NOW.timestamp() - 3600) * 1e9)
    os.utime(path, ns=(old, old))
    assert ENTRIES > webchat.MAX_ENTRIES and ENTRIES * 46 > webchat.MAX_DIRECTORY

    built: list[int] = []
    opened = zipfile.ZipFile.__init__

    def counting(self, *args, **kwargs):
        opened(self, *args, **kwargs)
        built.append(len(self.filelist))
    monkeypatch.setattr(zipfile.ZipFile, "__init__", counting)

    (name, stamp), = webchat.waiting(inbox, NOW)
    export = webchat.read(inbox, name, stamp, [HarnessId.CHATGPT])

    assert export.failed is not None                      # it is refused in the end, after the cost
    assert not built or max(built) <= webchat.MAX_ENTRIES, (
        f"zipfile built {max(built)} entries from a {path.stat().st_size} byte directory before Atlas refused "
        f"the file ({export.failed}): directory() let it through, MAX_ENTRIES is {webchat.MAX_ENTRIES} and "
        f"MAX_DIRECTORY {webchat.MAX_DIRECTORY}")
