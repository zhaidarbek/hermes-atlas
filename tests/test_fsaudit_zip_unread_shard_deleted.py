"""fsaudit B9: the README says Atlas "deletes the zip from the inbox once every conversation in it
is in the record. A file it cannot read stays in the inbox". A conversation shard that is a JSON
object without a "conversations" key is opened, read as holding nothing
(`items.get("conversations", [])`), and counted neither as broken nor as skipped. When another
shard of the same zip is readable, the zip is deleted with that shard's conversations unread, and
the import log shows a clean import."""
import json
import os
import zipfile
from dataclasses import replace
from datetime import datetime, timezone

from atlas.app import Atlas
from atlas.model import HarnessId, HarnessSetting
from atlas.record import Record
from test_commit import PROFILE, SETTINGS

NOW = datetime(2026, 11, 8, 10, tzinfo=timezone.utc)
T = NOW.timestamp()


def conversation(id: str) -> dict:
    said = {"id": "m-" + id, "author": {"role": "user"}, "create_time": T - 86400,
            "content": {"content_type": "text", "parts": ["what " + id + " says"]}}
    return {"conversation_id": id, "title": id, "create_time": T - 86400, "update_time": T - 86400,
            "current_node": "a", "mapping": {"a": {"id": "a", "parent": None, "children": [], "message": said}}}


def test_a_zip_with_a_shard_atlas_read_nothing_from_is_not_deleted(tmp_path):
    rec = Record.open(tmp_path / "atlas")
    rec.create(replace(SETTINGS, timezone="UTC", harnesses={
        HarnessId.CHATGPT: HarnessSetting(True, "export", ("~/atlas/inbox",), "owner")}), PROFILE)
    path = rec.root / "inbox" / "export.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("conversations-1.json", json.dumps([conversation("first")]))
        z.writestr("conversations-2.json", json.dumps({"items": [conversation("second")]}))
    old = int((T - 3600) * 1e9)
    os.utime(path, ns=(old, old))

    Atlas(rec, None)._import(NOW)

    held = {s.native_key for s in rec.snapshot().sessions()}
    assert "first" in held
    assert "second" in held or path.exists(), (
        f"the record holds {sorted(held)} only, and the zip that held 'second' was deleted; "
        f"import log: {[(e.sessions, e.deleted, e.failure, e.broken, e.skipped) for e in rec.snapshot().imports()]}")
