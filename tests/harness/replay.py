"""python -m harness.replay 8 9 26 (from tests/): run scenes and print what the fake Channel
carried, for the recordings convention 7 asks of a Telegram slice: the brief of the scene's day the
owner replies to, if any, then each owner message and each Atlas reply."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from atlas.model import NoteOrigin  # noqa: E402
from atlas.record import Record  # noqa: E402
from harness.home import AtlasHome  # noqa: E402
from harness.scenes import SCENES, run_scene  # noqa: E402


def show(scene_id: str):
    printed = {"seed": False, "sent": 0}
    seeded: set[tuple[str, str]] = set()

    def at_each_end(home: AtlasHome) -> None:
        lines = {(p.name, h.id): h for p in Record.open(home.record_root).snapshot().pursuits() for h in p.history}
        if printed["seed"]:
            for (name, _), h in sorted(lines.items(), key=lambda kv: kv[1].at):
                if (name, h.id) not in seeded and h.actor.value == "Atlas":
                    print(said("Board", f"{name}, changed by Atlas {h.at:%a %H:%M}:\n{h.statement.text}"), "\n")
        seeded.update(lines)
        if home.chat:
            for n in Record.open(home.record_root).snapshot().notes():
                if n.origin is NoteOrigin.HAND_EDIT:
                    print(said(f"edited {n.via}", f"{n.at:%a %H:%M}, put back by Atlas:\n{n.diff}"))
            print("\n".join(said(speaker, text) for speaker, text in home.chat))
        elif not printed["seed"]:
            briefs = [b for b in Record.open(home.record_root).snapshot().briefs() if b.day == SCENES[scene_id].at.date()]
            print(f"\n=== scene {scene_id} · {SCENES[scene_id].at:%a %d %b %H:%M}"
                  + (" · the brief, from the seed ===\n" if briefs else " ===\n"))
            if briefs:
                print(said("Atlas", briefs[-1].text), "\n")
        if printed["seed"]:
            _imports(home)
        printed["seed"] = True
        for message in home.sent()[printed["sent"]:]:
            print(f"=== scene {scene_id} · {home.now:%a %d %b %H:%M} · the tick sends ===\n")
            print(said("Atlas", message["text"]), "\n")
        printed["sent"] = len(home.sent())
    return at_each_end


def _imports(home: AtlasHome) -> None:
    """The web-chat exports the ticks read, what is left in the inbox, and each Journal correction."""
    snap = Record.open(home.record_root).snapshot()
    for e in snap.imports():
        what = "deleted it" if e.deleted else f"kept it ({e.failure})"
        print(said("inbox", f"{e.at:%a %H:%M} read {e.sessions} {e.harness.label if e.harness else ''} Sessions, {what}"))
    if snap.imports():
        left = sorted(p.name for p in (home.record_root / "inbox").iterdir())
        print(said("$ ls ~/atlas/inbox", " ".join(left) or "(empty)"), "\n")
    for day in snap.journal_days():
        for c in snap.journal(day).corrections:
            print(said(f"journal/{day}", f"Corrected {c}"), "\n")


def said(speaker: str, text: str) -> str:
    return "\n".join(f"{speaker if n == 0 else '':<17} │ {line}" for n, line in enumerate(text.splitlines()))


def main(scene_ids: list[str]) -> int:
    results = {i: run_scene(i, show(i)) for i in scene_ids}
    print("\n".join(f"scene {i}: {r}" for i, r in results.items()))
    return 1 if any(r.status != "pass" for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
