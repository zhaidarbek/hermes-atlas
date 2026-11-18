"""fsaudit A1. README: "It refuses a folder you add in Settings when any part of its path is a
link", and `_Store.roots` skips such a root at every later tick. The check (`linked`) walks the
path by name; the store is then resolved again by name (`_folder` -> os.path.realpath). A parent
folder swapped for a link between the two is read through."""
import shutil

from atlas.app import Atlas
from atlas.sources import _adapters
from test_collect import CX, NOW, held, record, rollout


def test_an_added_folder_whose_parent_becomes_a_link_after_the_link_check_is_not_read(tmp_path, throwaway_home,
                                                                                      monkeypatch):
    shared, elsewhere = tmp_path / "shared", tmp_path / "elsewhere"
    rollout(shared / "codex", "added", ["an invented ask"], ["ok"])
    rollout(elsewhere / "codex", "through-the-link", ["never the owner's folder"], ["ok"])
    rec = record(tmp_path, CX, roots=[shared / "codex"])
    real = _adapters._within

    def swapped_after_the_check(folder):             # runs after roots() found the root unlinked
        if not shared.is_symlink():
            shutil.move(shared, tmp_path / "kept")
            shared.symlink_to(elsewhere)
        return real(folder)

    monkeypatch.setattr(_adapters, "_within", swapped_after_the_check)
    Atlas(rec, None).backfill(NOW)
    assert "through-the-link" not in held(rec), "Atlas read a Settings folder through a link"
