"""fsaudit A1. `_files`: "a symlink is never followed, so it never leads out of the store"; README:
Atlas "skips a store folder that is itself a link". A Claude Code Session's `subagents` folder
that is a link is the starting point of `folder.glob("**/agent-*.jsonl")`, which walks the whole
tree the link points to, at every listing. No file there is opened, but the walk has no bound: a
link to / lists the machine."""
import os

from atlas.app import Atlas
from test_collect import CC, NOW, claude, held, record


def test_a_subagents_folder_that_is_a_link_is_not_walked(tmp_path, throwaway_home, monkeypatch):
    transcript = claude(throwaway_home, "chat", "an invented ask")
    outside = tmp_path / "outside"
    for n in range(5):
        (outside / f"folder-{n}" / "deeper").mkdir(parents=True)
    (transcript.parent / "chat").mkdir()
    (transcript.parent / "chat" / "subagents").symlink_to(outside)
    rec = record(tmp_path, CC)
    listed, real = [], os.scandir

    def scandir(path="."):
        if not isinstance(path, int) and os.path.realpath(path).startswith(str(outside)):
            listed.append(os.path.relpath(os.path.realpath(path), outside))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    Atlas(rec, None).backfill(NOW)
    assert list(held(rec)) == ["chat"]
    assert listed == [], f"Atlas listed {len(listed)} folders outside the store, through a link"
