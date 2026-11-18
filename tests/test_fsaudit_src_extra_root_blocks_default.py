"""fsaudit A2. sources.collect: "A store that cannot be read is logged and skipped", and
_Harness.units reads "every unit of every root" with each root guarded on its own. A folder added
in Settings that Atlas may not enter (its parent's owner took the permission away, or its drive
is gone with an error other than "not found") raises in `_Store.roots`, outside that guard, and
the Harness's own default store is no longer collected either."""
import os

import pytest

from atlas.app import Atlas
from test_collect import CX, NOW, held, record, rollout


@pytest.mark.skipif(os.geteuid() == 0, reason="root enters any folder")
def test_an_added_folder_atlas_may_not_enter_leaves_the_default_store_collected(tmp_path, throwaway_home):
    rollout(throwaway_home / ".codex", "default", ["an invented ask"], ["ok"])
    locked = tmp_path / "shared"
    (locked / "codex/sessions").mkdir(parents=True)
    rec = record(tmp_path, CX, roots=[locked / "codex"])
    locked.chmod(0)
    try:
        Atlas(rec, None).backfill(NOW)
    finally:
        locked.chmod(0o700)
    assert list(held(rec)) == ["default"], "an unreadable extra folder stopped the Harness's own store being read"
