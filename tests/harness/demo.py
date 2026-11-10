"""python -m harness.demo (from tests/): replay the scenes docs/demo-script.md names, on the fake
data and the fake Channel, and render them to docs/recordings/demo-week.mp4 on the script's
timings. A scene that does not pass at this commit is cut, with a visible note in its place.

Needs ffmpeg on PATH and the `demo` extra (`pip install -e '.[demo]'`)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "docs" / "demo-script.md"
VIDEO = REPO / "docs" / "recordings" / "demo-week.mp4"
BUDGET = 88.0
SKIPPED_SECONDS = 2.0
LABEL = "fake data, fake Channel"
FPS = 10
WIDTH, HEIGHT = 1280, 720
RECORD_READ = "27"


# ------------------------------------------------------------------ the script and the plan (pure)

@dataclass(frozen=True)
class Row:
    start: float
    scenes: tuple[str, ...]
    on_screen: str
    voiceover: str


@dataclass(frozen=True)
class Shot:
    seconds: float
    on_screen: str
    scene: str | None = None
    note: str | None = None


def read_script(text: str) -> list[Row]:
    """The demo script's table rows, in order: `| m:ss | scene ids or none | on screen | voiceover |`."""
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or not re.fullmatch(r"\d+:\d\d", cells[0]):
            continue
        minutes, seconds = map(int, cells[0].split(":"))
        scenes = () if cells[1] == "none" else tuple(s.strip() for s in cells[1].split(","))
        rows.append(Row(minutes * 60 + seconds, scenes, cells[2].replace("`", ""), cells[3]))
    return rows


def slots(rows: list[Row], budget: float = BUDGET) -> list[float]:
    """Each row runs until the next row starts; the last runs to the budget."""
    starts = [r.start for r in rows] + [budget]
    if starts != sorted(starts) or len(set(starts)) != len(starts):
        raise ValueError(f"the row times {starts[:-1]} do not rise, or pass the {budget:g} s budget")
    return [b - a for a, b in zip(starts, starts[1:])]


def plan(rows: list[Row], passing: frozenset[str], budget: float = BUDGET) -> list[Shot]:
    """The video's shots. A row's slot is shared by its scenes that pass. The rest are named in a
    note; a row with none that pass shrinks to a short note, and its voiceover line is cut."""
    shots = []
    for row, seconds in zip(rows, slots(rows, budget)):
        if not row.scenes:
            shots.append(Shot(seconds, row.on_screen))
            continue
        shown = [s for s in row.scenes if s in passing]
        missing = [s for s in row.scenes if s not in passing]
        note = f"not passing yet, cut: scene {', '.join(missing)}" if missing else None
        if not shown:
            shots.append(Shot(min(SKIPPED_SECONDS, seconds), row.on_screen, note=note))
            continue
        shots.extend(Shot(seconds / len(shown), row.on_screen, scene, note) for scene in shown)
    return shots


def frame_counts(shots: list[Shot], fps: int = FPS) -> list[int]:
    """Frames per shot, rounded on the running total so the video never drifts from the script."""
    ends, total = [], 0.0
    for shot in shots:
        total += shot.seconds
        ends.append(round(total * fps))
    return [b - a for a, b in zip([0] + ends, ends)]


def title_of(on_screen: str) -> str:
    quoted = re.search(r'"([^"]+)"', on_screen)
    return quoted[1] if quoted else on_screen


def cropped(lines: list[str], on_screen: str) -> list[str]:
    """The script's `from "X" down` crops the output to start at the line holding X."""
    start = re.search(r'from "([^"]+)" down', on_screen)
    if start:
        at = next((n for n, line in enumerate(lines) if start[1] in line), 0)
        return lines[at:]
    return lines


def said(speaker: str, text: str) -> list[str]:
    return [f"{speaker if n == 0 else '':<6} │ {line}" for n, line in enumerate(text.splitlines() or [""])]


def without_redeliveries(chat: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Each exchange, an owner's message and Atlas's replies to it, once: a redelivery or a
    repeated ask is the harness proving idempotence, not something the owner sees twice."""
    exchanges: list[list[tuple[str, str]]] = []
    for speaker, text in chat:
        if speaker != "Atlas" or not exchanges:
            exchanges.append([])
        exchanges[-1].append(("You" if speaker.endswith("(redelivered)") else speaker, text))
    return [line for exchange in dict.fromkeys(map(tuple, exchanges)) for line in exchange]


def board_text(board: dict) -> list[str]:
    """The Board's columns as text: each card's star, kind, last day, marks, Left off and Next."""
    lines = [f"The Board · {board.get('in_focus', 0)} in Focus", ""]
    for column, cards in board["columns"].items():
        if not cards:
            continue
        lines.append(f"{column.upper()} · {len(cards)}")
        for c in cards:
            marks = " ".join(m for m, on in (("NEW", c.get("new")), ("CHANGED", c.get("changed"))) if on)
            star = "★" if c.get("focus") else "☆"
            lines.append(f"  {star} {c['name']} · {c['kind']} · {c['last_worked']}  {marks}".rstrip())
            for label, part in (("Left off", c.get("left_off")), ("Next", c.get("next"))):
                if part:
                    lines.append(f"      {label:<9}{_marked(part)}")
            for proposal in c.get("proposals") or []:
                lines.append(f"      Proposal {_marked(proposal)}")
        lines.append("")
    return lines


def _marked(part: dict) -> str:
    return f"{part['text']} [{part['mark']}{' ' + part['citation'] if part.get('citation') else ''}]"


# ------------------------------------------------------------------ replaying a scene (the harness)

@dataclass(frozen=True)
class Take:
    scene: str
    at: datetime
    passed: bool
    reason: str | None
    lines: tuple[str, ...]


def replay(scene_id: str) -> Take:
    """Run one scene through the harness and keep what the owner would see: the commands' output,
    the brief the owner replies to, the chat, what the tick sends, the Board after a click, or the
    record read without Atlas."""
    from atlas.record import Record
    from harness import tutorial
    from harness.scenes import SCENES, as_shown, run_scene

    state: dict = {"armed": False, "lines": []}

    def observe(home) -> None:
        if not state["armed"]:
            state["armed"] = True
            day = SCENES[scene_id].at.date()
            briefs = [b for b in Record.open(home.record_root).snapshot().briefs() if b.day == day]
            state["brief"] = briefs[-1].text if briefs else None
            state["sent"] = len(home.sent())
            state["commands"], state["board"] = [], False
            cli, dashboard = home.cli, home.dashboard

            def recorded_cli(*argv):
                ran = cli(*argv)
                state["commands"].append((argv, ran.out + ran.err))
                return ran

            def recorded_dashboard(*args, **kw):
                state["board"] = True
                return dashboard(*args, **kw)

            home.cli, home.dashboard = recorded_cli, recorded_dashboard
            return
        lines: list[str] = []
        for argv, out in dict.fromkeys(state["commands"]):
            lines += [f"$ hermes {' '.join(argv)}", *out.rstrip().splitlines(), ""]
        chat = without_redeliveries(home.chat)
        if chat and state["brief"]:
            lines += said("Atlas", state["brief"]) + [""]
        sent = [["the tick sends", *said("Atlas", m["text"]), ""] for m in home.sent()[state["sent"]:]]
        asked = [m for m in sent if "Atlas · Review" in m[1]] if chat else []
        lines += [line for m in asked for line in m]
        for speaker, text in chat:
            lines += said(speaker, text)
        lines += [line for m in sent if m not in asked for line in m]
        if state["board"]:
            lines += board_text(home.dashboard("GET", "/board").json())
        if not lines:
            lines += ["$ ls ~/atlas", "  ".join(sorted(p.name for p in home.record_root.iterdir()
                                                    if not p.name.startswith("."))), ""]
            shell = tutorial.fenced_block("$ ls ~/atlas").splitlines()
            less = next((line for line in shell if line.startswith("$ less ~/atlas/")), None)
            if less and scene_id == RECORD_READ:
                day = home.record_root / less.removeprefix("$ less ~/atlas/")
                body = day.read_text().split("\n---\n", 1)[-1]
                lines += [less, *map(as_shown, body.splitlines())]
        state["lines"] = [_masked(line, home) for line in lines]

    result = run_scene(scene_id, observe)
    return Take(scene_id, SCENES[scene_id].at, result.status == "pass", result.reason, tuple(state["lines"]))


def _masked(line: str, home) -> str:
    """The harness's temporary folders read as the tutorial's home, and this machine's name as
    the tutorial's machine."""
    from harness import world
    line = line.replace(str(home.home), "~").replace(str(home.base), "~")
    return line.replace(socket.gethostname(), world.MACHINE_IN_TUTORIAL)


# ------------------------------------------------------------------ rendering

def font_file() -> str | None:
    """A monospace font with box-drawing glyphs, found at run time: fontconfig first, then the
    usual folders."""
    if shutil.which("fc-match"):
        found = subprocess.run(["fc-match", "-f", "%{file}", "monospace"], capture_output=True, text=True).stdout
        if found and Path(found).is_file():
            return found
    for folder in ("/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts", "/System/Library/Fonts",
                   "C:/Windows/Fonts"):
        for name in ("DejaVuSansMono.ttf", "Menlo.ttc", "consola.ttf", "LiberationMono-Regular.ttf"):
            hits = sorted(Path(folder).rglob(name)) if Path(folder).is_dir() else []
            if hits:
                return str(hits[0])
    return None


class Painter:
    BG, PANEL, TEXT, DIM, ACCENT, NOTE = "#14161b", "#1d2028", "#e6e6e6", "#8a909c", "#7cc4ff", "#ffb86c"
    TOP, BOTTOM, MARGIN = 74, 34, 24

    def __init__(self, font: str | None) -> None:
        from PIL import ImageFont
        load = (lambda size: ImageFont.truetype(font, size)) if font else ImageFont.load_default
        self.body, self.caption, self.small, self.title = load(16), load(20), load(15), load(46)
        self.line_height = 20
        self.columns = int((WIDTH - 2 * self.MARGIN) // self.body.getlength("M"))
        self.rows = (HEIGHT - self.TOP - self.BOTTOM - 16) // self.line_height

    def pages(self, lines: list[str]) -> list[list[str]]:
        """Wrapped lines in pages, breaking at blank lines where a block fits on the page."""
        blocks, block = [], []
        for line in lines + [""]:
            if line.strip():
                block += self.wrapped(line)
            elif block:
                blocks.append(block)
                block = []
        pages = [[]]
        for block in blocks:
            if pages[-1] and len(pages[-1]) + 1 + len(block) > self.rows:
                pages.append([])
            for line in ([""] if pages[-1] else []) + block:
                if len(pages[-1]) == self.rows:
                    pages.append([])
                pages[-1].append(line)
        return pages

    def wrapped(self, line: str) -> list[str]:
        gutter = re.match(r"^(.{0,24})│ ", line)
        indent = " " * len(gutter[1]) + "│   " if gutter else " " * (len(line) - len(line.lstrip()) + 2)
        return textwrap.wrap(line, self.columns, subsequent_indent=indent, drop_whitespace=False,
                             replace_whitespace=False) or [""]

    def frame(self, top: str, under: str, body: list[str], note: str | None = None, title: str | None = None):
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (WIDTH, HEIGHT), self.BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, WIDTH, self.TOP - 8), fill=self.PANEL)
        draw.text((self.MARGIN, 10), top, font=self.caption, fill=self.ACCENT)
        draw.text((self.MARGIN, 40), under, font=self.small, fill=self.DIM)
        if title:
            width = draw.textlength(title, font=self.title)
            draw.text(((WIDTH - width) / 2, HEIGHT / 2 - 40), title, font=self.title, fill=self.TEXT)
        for n, line in enumerate(body):
            draw.text((self.MARGIN, self.TOP + 8 + n * self.line_height), line, font=self.body, fill=self.TEXT)
        foot = HEIGHT - self.BOTTOM + 8
        if note:
            draw.text((self.MARGIN, foot), note, font=self.small, fill=self.NOTE)
        width = draw.textlength(LABEL, font=self.small)
        draw.text((WIDTH - self.MARGIN - width, foot), LABEL, font=self.small, fill=self.DIM)
        return image


def frames(shots: list[Shot], takes: dict[str, Take], painter: Painter):
    for shot, count in zip(shots, frame_counts(shots)):
        if shot.scene is None and shot.note is None:
            pages, top, title = [[]], "Atlas", title_of(shot.on_screen)
        elif shot.scene is None:
            pages, top, title = [["", "This row of the demo script is cut, with its voiceover line."]], "Cut", None
        else:
            take = takes[shot.scene]
            pages, title = painter.pages(cropped(list(take.lines), shot.on_screen)), None
            top = f"{take.at:%A %d %B, %H:%M} · scene {shot.scene}"
        for n in range(count):
            page = pages[min(n * len(pages) // max(count, 1), len(pages) - 1)]
            yield painter.frame(top, shot.on_screen, page, shot.note, title)


def encode(images, out: Path) -> str:
    """Pipe raw frames to ffmpeg as H.264; return the sha256 of the frames, which this commit fixes."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg is not on PATH")
    digest = hashlib.sha256()
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
               "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryslow", "-tune", "stillimage",
               "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-map_metadata", "-1",
               "-fflags", "+bitexact", "-flags:v", "+bitexact", str(out)]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
        for image in images:
            raw = image.tobytes()
            digest.update(raw)
            encoder.stdin.write(raw)
        encoder.stdin.close()
        if encoder.wait() != 0:
            raise SystemExit(f"ffmpeg exited {encoder.returncode}")
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    from harness.scenes import claimed
    out = Path(argv[0]) if argv else VIDEO
    rows = read_script(SCRIPT.read_text())
    wanted = [s for row in rows for s in row.scenes if s in claimed()]
    takes = {s: replay(s) for s in wanted}
    for take in takes.values():
        print(f"scene {take.scene}: {'pass' if take.passed else 'fail (' + str(take.reason) + ')'}", file=sys.stderr)
    shots = plan(rows, frozenset(s for s, t in takes.items() if t.passed))
    font = font_file()
    digest = encode(frames(shots, takes, Painter(font)), out)
    shown = [s.scene for s in shots if s.scene]
    cut = [s for row in rows for s in row.scenes if s not in shown]
    print(json.dumps({"video": str(out), "seconds": sum(s.seconds for s in shots), "frames_sha256": digest,
                      "font": Path(font).name if font else "Pillow default", "shown": shown, "cut": cut}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
