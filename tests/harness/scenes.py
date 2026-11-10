"""Scenes: each tutorial scene as a seed (scene mode only, written through atlas.record) and a
step that drives the plugin's registered entry points and checks the tutorial's text.

Scene ids follow docs/architecture/atlas-v1.md §1, in the tutorial's order, with scene 3
checked as 3a, 3b and 3c. A ticket proves a scene by adding it to SCENES and claiming it in
manifest.toml.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Literal

import zstandard

from atlas import rules
from atlas.model import Done, Fact, HarnessId, Inference, SendStatus, Skipped, Status, Who, skip_key
from atlas.record import Record, Snapshot

from . import exports, seeds, tutorial, world
from .fake_llm import FakeLlm
from .home import AtlasHome
from .model import CORRECTED, STAGING, WRONG, corrections_model, filing_model, profile_model, world_model

SCENE_IDS: tuple[str, ...] = ("1", "2", "3a", "3b", "3c", *map(str, range(4, 10)), "9b", *map(str, range(10, 21)),
                              "20b", *map(str, range(21, 40)))
MANIFEST = Path(__file__).with_name("manifest.toml")


class Failed(Exception):
    """The scene's output or effects differ from the tutorial; the message says where."""


@dataclass(frozen=True)
class Scene:
    at: datetime                                    # the fake clock when the scene happens
    seed: Callable[[AtlasHome], None]               # scene mode only: the state the scene starts from
    step: Callable[[AtlasHome], None]               # drives entry points; raises Failed
    llm: Callable[[], FakeLlm] | None = None        # the model the scene's calls get; none by default


@dataclass(frozen=True)
class Result:
    status: Literal["pass", "fail", "not claimed"]
    reason: str | None = None

    def __str__(self) -> str:
        return f"{self.status} ({self.reason})" if self.reason else self.status


def expect_lines(expected: str, got: str) -> None:
    """`got` starts with `expected`, line by line, after whitespace normalisation."""
    want, have = tutorial.normalise(expected), tutorial.normalise(got)
    for n, line in enumerate(want):
        if n >= len(have) or have[n] != line:
            raise Failed(f"line {n + 1}: the tutorial has {line!r}, Atlas printed {have[n] if n < len(have) else 'nothing'!r}")


def expect_all_lines(expected: str, got: str) -> None:
    """As expect_lines, and `got` has no more lines than `expected`."""
    expect_lines(expected, got)
    extra = tutorial.normalise(got)[len(tutorial.normalise(expected)):]
    if extra:
        raise Failed(f"Atlas printed more than the tutorial shows, from {extra[0]!r}")


# ------------------------------------------------------------------ scene 1: install and setup

def _setup_report_text(now: datetime) -> str:
    """The tutorial's setup report, with its full-scale counts replaced by this world's own."""
    text = tutorial.fenced_block("Found on this machine").replace(world.MACHINE_IN_TUTORIAL, socket.gethostname())
    for harness in (HarnessId.CLAUDE_CODE, HarnessId.CODEX, HarnessId.DEVIN, HarnessId.HERMES):
        count, since = world.owner_sessions(harness, now)
        text = re.sub(rf"^(\s*{re.escape(harness.label)}\s+\S+\s+)\d+ Sessions since \d+ \w+$",
                      lambda m: f"{m[1]}{count} Sessions since {since.day} {since:%b}", text, flags=re.M)
    return text


def _setup(home: AtlasHome) -> None:
    first = home.cli("atlas", "setup")
    if first.code != 0:
        raise Failed(f"hermes atlas setup exited {first.code}: {first.err.strip()}")
    expect_lines(_setup_report_text(home.now), first.out)
    after_report = " ".join(first.out.split())
    for line in tutorial.usage_list("mcp_commands"):
        if line not in after_report:
            raise Failed(f"no MCP add line {line!r}")
    offer = " ".join(tutorial.sentence("If you already use that chat").split())
    if offer not in after_report:
        raise Failed("setup does not offer Atlas's own chat (D Q16)")
    for disclosed in ("~/.claude", "~/.codex", "devin", "state.db", "~/.t3", "~/atlas", "atlas-tick"):
        if disclosed not in after_report.split("What Atlas does on this machine:")[-1]:
            raise Failed(f"the disclosures do not name {disclosed}")

    if not any(call[:1] == ["send"] and "-l" in call for call in home.hermes_calls()):
        raise Failed("the Channel check did not run `hermes send -l`")
    jobs = home.cron_jobs()
    wanted = {"name": "atlas-tick", "schedule": "*/10 * * * *", "script": "atlas_tick.sh", "no_agent": True,
              "catch_up": "off", "deliver": "local"}
    if [{k: job[k] for k in wanted} for job in jobs] != [wanted]:
        raise Failed(f"cron jobs are {jobs}, not one no-agent atlas-tick every 10 minutes with catch-up off")
    script = home.home / ".hermes" / "scripts" / "atlas_tick.sh"
    if script.stat().st_mode & 0o777 != 0o755 or "hermes atlas tick --quiet" not in script.read_text():
        raise Failed("the tick script is not an executable `hermes atlas tick --quiet`")
    for name in ("README.md", "settings.md", "profile.md", "pauses.md", "forgotten.md"):
        if not (home.record_root / name).is_file():
            raise Failed(f"setup did not write {name}")
    if home.record_root.stat().st_mode & 0o777 != 0o700:
        raise Failed("the record folder is not mode 0700")

    before = home.files()
    second = home.cli("atlas", "setup")
    expect_lines(_setup_report_text(home.now), second.out)
    changed = sorted(p for p in before.keys() | home.files().keys() if before.get(p) != home.files().get(p))
    if changed:
        raise Failed(f"a second setup changed {changed}")
    if len(home.cron_jobs()) != 1:
        raise Failed("a second setup created a second cron job")


# ------------------------------------------------------------------ scene 3a: backfill reads the Sessions

def _backfill_read_text(now: datetime) -> str:
    """The first two lines of the tutorial's backfill output, with this world's own counts."""
    _, read, interface = tutorial.fenced_block("Read 977 Sessions").splitlines()[:3]
    sessions = sum(s.is_owner_session for s in world.visible(now))
    read = re.sub(r"^Read \d+ Sessions\. \d+ subagent", f"Read {sessions} Sessions. {world.folded(now)} subagent", read)
    return read + "\n" + re.sub(r"^\d+", str(len(world.via_interface(now))), interface)


def _backfill(home: AtlasHome) -> None:
    ran = home.cli("atlas", "backfill")
    if ran.code != 0:
        raise Failed(f"hermes atlas backfill exited {ran.code}: {ran.err.strip()}")
    expect_lines(_backfill_read_text(home.now), ran.out)

    snap = Record.open(home.record_root).snapshot()
    held = {s.id: s for s in snap.sessions()}
    wanted = {seeds.evidence_id(s): s for s in world.visible(home.now) if s.is_owner_session}
    if set(held) != set(wanted):
        raise Failed(f"the record holds {len(held)} Sessions, the world has {len(wanted)}: "
                     f"missing {sorted(wanted.keys() - held.keys())}, extra {sorted(held.keys() - wanted.keys())}")
    for id, mine in wanted.items():
        s = held[id]
        children = [c for c in world.visible(home.now) if c.parent == mine.title]
        if len(s.parts) != 1 + mine.subagents + len(children):
            raise Failed(f"{mine.title!r} has {len(s.parts)} parts, not itself plus {mine.subagents + len(children)} folded")
        if s.interface != mine.interface:
            raise Failed(f"{mine.title!r} is noted as run through {s.interface}, not {mine.interface}")
        if [t.text for t in s.turns if t.role == "owner"] != [t.text for t in mine.turns() if t.owner]:
            raise Failed(f"{mine.title!r} has other owner turns than the owner typed")
        folder = home.record_root / f"evidence/{s.harness.value}/{s.started:%Y/%m}/{id}"
        for part in s.parts:
            copy = zstandard.ZstdDecompressor().stream_reader((folder / "raw" / part.name).read_bytes(),
                                                              read_across_frames=True).read()
            if hashlib.sha256(copy).hexdigest() != part.sha256:
                raise Failed(f"raw/{part.name} of {mine.title!r} does not match its sha256")

    before = home.files()
    again = home.cli("atlas", "backfill")
    if again.out != ran.out:
        raise Failed("a second backfill printed something else")
    changed = sorted(p for p in before.keys() | home.files().keys() if before.get(p) != home.files().get(p))
    if changed:
        raise Failed(f"a second backfill changed {changed}")


# ------------------------------------------------------------------ scene 3b: backfill builds the Pursuits

def _backfill_filed_text(now: datetime) -> str:
    """The tutorial's backfill output without its Journal line (scene 3c), with this world's
    own counts."""
    text = _backfill_read_text(now) + "\n" + "\n".join(
        line for line in tutorial.fenced_block("Read 977 Sessions").splitlines()[3:] if not line.startswith("Journal"))
    last = {name: max(world.by_title(t).end for t in titles) for name, (_, titles) in world.BACKFILL_PURSUITS.items()}
    active = sum((now.date() - at.date()).days <= 30 for at in last.values())
    one_offs = sum(world.by_title(t).start <= now for t in world.BACKFILL_ONE_OFFS)
    for label, n in (("Pursuits", len(last)), ("  active", active), ("  parked", len(last) - active),
                     ("Not a Pursuit", one_offs)):
        text = re.sub(rf"^({label}\s+)\d+", lambda m: f"{m[1]}{n}", text, flags=re.M)
    return text


def _without_journal(out: str) -> str:
    return "\n".join(line for line in out.splitlines() if not line.startswith("Journal"))


def _filed(home: AtlasHome) -> None:
    calls = len(home.ctx.fake_llm.calls)
    ran = home.cli("atlas", "backfill")
    if ran.code != 0:
        raise Failed(f"hermes atlas backfill exited {ran.code}: {ran.err.strip()}")
    expect_all_lines(_backfill_filed_text(home.now), _without_journal(ran.out))

    snap = Record.open(home.record_root).snapshot()
    ids = {s.title: seeds.evidence_id(s) for s in world.SESSIONS}
    board = {c.name: c for c in tutorial.board()}
    built = {p.name: p for p in snap.pursuits()}
    if set(built) != set(board):
        raise Failed(f"backfill built {sorted(built)}, not the tutorial's {sorted(board)}")
    for name, p in built.items():
        card = board[name]
        wanted = tuple(ids[t] for t in world.BACKFILL_PURSUITS[name][1])
        if tuple(m.evidence for m in p.sessions) != wanted:
            raise Failed(f"{name!r} lists other Sessions than its own")
        if (p.kind.value, p.status.value.upper(), p.focus) != (card.kind, card.column, False):
            raise Failed(f"{name!r} is a {p.kind.value}, {p.status.value}, focus {p.focus}; the Board shows a "
                         f"{card.kind}, {card.column.lower()}, outside Focus")
        if f"{p.last_worked:%a} {p.last_worked.day} {p.last_worked:%b}" != card.last_worked:
            raise Failed(f"{name!r} was last worked on {p.last_worked}, not {card.last_worked}")
        left = p.resume.left_off
        if len(left) != 1 or not isinstance(left[0], Fact) or left[0].text != card.left_off:
            raise Failed(f"{name!r} left off at {left}, not the Fact {card.left_off!r}")
        if p.next_step is None or p.next_step.text.text != card.next:
            raise Failed(f"{name!r} has the Next Step {p.next_step}, not {card.next!r}")
        created = p.history[0].statement if len(p.history) == 1 else None
        wanted_line = f"Created from {len(wanted)} Session{'' if len(wanted) == 1 else 's'}"
        if not isinstance(created, Inference) or (created.text, created.basis) != (wanted_line, wanted):
            raise Failed(f"{name!r} has the History {p.history}, not one line {wanted_line!r} on its Sessions")
    for title, area in world.BACKFILL_ONE_OFFS.items():
        card = snap.card(ids[title])
        if card is None or card.one_off_area != area:
            raise Failed(f"{title!r} is not marked a one-off in {area}")
    uncarded = [h.id for h in snap.held().sessions() if snap.card(h.id) is None]
    if uncarded:
        raise Failed(f"{len(uncarded)} Sessions have no card")

    asked = len(home.ctx.fake_llm.calls)
    before = home.files()
    again = home.cli("atlas", "backfill")
    if again.out != ran.out:
        raise Failed("a second backfill printed something else")
    if len(home.ctx.fake_llm.calls) != asked:
        raise Failed(f"a second backfill made {len(home.ctx.fake_llm.calls) - asked} model calls")
    changed = sorted(p for p in before.keys() | home.files().keys() if before.get(p) != home.files().get(p))
    if changed:
        raise Failed(f"a second backfill changed {changed}")
    if asked == calls:
        raise Failed("backfill built the Pursuits without the model")


# ------------------------------------------------------------------ scene 3c: backfill writes the Journal

def _worked_days(now: datetime) -> list:
    """The days this world's filed Sessions ran on, by its own table."""
    filed = {t for _, titles in world.BACKFILL_PURSUITS.values() for t in titles} | set(world.BACKFILL_ONE_OFFS)
    return sorted({s.start.date() for s in world.visible(now) if s.title in filed})


def _backfill_text(now: datetime) -> str:
    """The tutorial's whole backfill output, with this world's own counts and days."""
    days = _worked_days(now)
    journal = next(line for line in tutorial.fenced_block("Read 977 Sessions").splitlines() if line.startswith("Journal"))
    journal = re.sub(r"\d+ days, \d+ \w+ to \d+ \w+$",
                     f"{len(days)} days, {days[0].day} {days[0]:%b} to {days[-1].day} {days[-1]:%b}", journal)
    filed = _backfill_filed_text(now).splitlines()
    board = next(n for n, line in enumerate(filed) if line.startswith("Board"))
    return "\n".join([*filed[:board], journal, *filed[board:]])


def _journaled(home: AtlasHome) -> None:
    ran = home.cli("atlas", "backfill")
    if ran.code != 0:
        raise Failed(f"hermes atlas backfill exited {ran.code}: {ran.err.strip()}")
    expect_all_lines(_backfill_text(home.now), ran.out)

    snap = Record.open(home.record_root).snapshot()
    days = _worked_days(home.now)
    if list(snap.journal_days()) != days:
        raise Failed(f"the Journal has {len(snap.journal_days())} days, not this world's {len(days)}")
    filed = {t for _, titles in world.BACKFILL_PURSUITS.values() for t in titles} | set(world.BACKFILL_ONE_OFFS)
    for day in days:
        wanted = {seeds.evidence_id(s) for s in world.visible(home.now) if s.title in filed and s.start.date() == day}
        if set(snap.journal(day).evidence) != wanted:
            raise Failed(f"the Journal day {day} counts other Sessions than the ones that ran that day")

    asked = len(home.ctx.fake_llm.calls)
    before = home.files()
    again = home.cli("atlas", "backfill")
    if again.out != ran.out:
        raise Failed("a second backfill printed something else")
    if len(home.ctx.fake_llm.calls) != asked:
        raise Failed(f"a second backfill made {len(home.ctx.fake_llm.calls) - asked} model calls")
    changed = sorted(p for p in before.keys() | home.files().keys() if before.get(p) != home.files().get(p))
    if changed:
        raise Failed(f"a second backfill changed {changed}")


# ------------------------------------------------------------------ scene 27: the record read without Atlas

AFTER_MONDAY = datetime(2026, 11, 17, 0, 10, tzinfo=world.TZ)    # the first tick after Monday closes
AT_LUNCH = datetime(2026, 11, 20, 12, 30, tzinfo=world.TZ)

# Runs with the atlas package unimportable and no `hermes` on PATH. It does what the owner does
# with `ls` and `less`, then follows each Evidence id the day names to its file, by README.md's
# rule alone: a Session's id is its folder under evidence/, a Note's id heads it in notes/.
_READER = """
import glob, json, os, re, sys
sys.modules["atlas"] = None
try:
    import atlas
    sys.exit("atlas is importable")
except ImportError:
    pass
root, day = sys.argv[1], sys.argv[2]
names = sorted(n for n in os.listdir(root) if not n.startswith("."))
text = open(os.path.join(root, day), encoding="utf-8").read()
notes = "".join(open(p, encoding="utf-8").read() for p in glob.glob(os.path.join(root, "notes", "**", "*.md"), recursive=True))
unresolved = []
for id in sorted(set(re.findall(r"\\b(?:cc|cx|dv|hm|gpt|cai|nt)-[a-z0-9]+\\b", text))):
    found = f"· {id}" in notes if id.startswith("nt-") else glob.glob(os.path.join(root, "evidence", "*", "*", "*", id, "session.md"))
    if not found:
        unresolved.append(id)
print(json.dumps({"ls": [n + "/" if os.path.isdir(os.path.join(root, n)) else n for n in names], "day": text,
                  "readme": open(os.path.join(root, "README.md"), encoding="utf-8").read(), "unresolved": unresolved}))
"""


def _monday_journaled(home: AtlasHome) -> None:
    """Monday as the record holds it on Friday: the tick after midnight wrote the day from its
    Sessions, then the owner's word and the ChatGPT export added to it."""
    seeds.after_scene_1(home.record_root)
    home.set_clock(AFTER_MONDAY)
    home.cli("atlas", "tick", "--quiet")
    seeds.monday_filed(home.record_root)
    home.cli("atlas", "tick", "--quiet")
    seeds.monday_corrected(home.record_root)
    home.set_clock(AT_LUNCH)


_SESSION_CITE = re.compile(r"\[F ([^\[\]·]+?) · \d{4}-\d{2}-\d{2} (\d{2}:\d{2}) · (?!nt-)[a-z]+-[a-z0-9]+\]")


def as_shown(line: str) -> str:
    """A record line as the tutorial prints the file: a Fact read from a Session as
    `[F <Harness> <time>]`, every other Fact as `[F]`, an Inference as `[I]`."""
    line = _SESSION_CITE.sub(r"[F \1 \2]", line)
    return re.sub(r"\[I · [^\[\]]+\]", "[I]", re.sub(r"\[F [^\[\]]*·[^\[\]]*\]", "[F]", line))


def _read_without_atlas(home: AtlasHome) -> None:
    calls = len(home.hermes_calls())
    shell = tutorial.fenced_block("$ ls ~/atlas").splitlines()
    shown = shell[shell.index("$ ls ~/atlas") + 1].split()
    path = next(line for line in shell if line.startswith("$ less ")).removeprefix("$ less ~/atlas/")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PATH")}
    ran = subprocess.run([sys.executable, "-I", "-c", _READER, str(home.record_root), path], capture_output=True,
                         text=True, env={**env, "PATH": "/usr/bin:/bin"}, cwd=home.home)
    if ran.returncode != 0:
        raise Failed(f"reading the record without Atlas failed: {ran.stderr.strip()[-200:]}")
    read = json.loads(ran.stdout)
    if [n for n in read["ls"] if n in shown] != shown:
        raise Failed(f"ls ~/atlas shows {read['ls']}, not {shown} in that order")
    body = read["day"].split("\n---\n", 1)[1]
    expect_lines(tutorial.fenced_block(tutorial.JOURNAL_DAY), "\n".join(map(as_shown, body.splitlines())))
    if read["unresolved"]:
        raise Failed(f"README.md's rules find no file for {read['unresolved']}")
    marks = read["readme"].split("## Marks", 1)[-1]
    for mark in ("[F counted · active_time ·", "[F Codex · ", "[I · "):
        if mark not in marks:
            raise Failed(f"README.md does not explain the mark {mark!r}")
    if len(home.hermes_calls()) != calls:
        raise Failed("reading the record called hermes")


# ------------------------------------------------------------------ scene 5: corrections by chat

def _after_backfill(home: AtlasHome) -> None:
    """Scenes 3 and 4's end: the Pursuits backfill builds, with every Session and card behind them."""
    seeds.after_scene_1(home.record_root)
    ran = home.cli("atlas", "backfill")
    if ran.code != 0:
        raise Failed(f"the seed's backfill exited {ran.code}: {ran.err.strip()}")


def _world_counts(text: str, counts: dict[int, int]) -> str:
    """The tutorial's text with its full-scale Session counts replaced by this world's own."""
    return re.sub(r"\b(\d+) Sessions\b", lambda m: f"{counts[int(m[1])]} Session{'' if counts[int(m[1])] == 1 else 's'}",
                  text)


def _corrections(home: AtlasHome) -> None:
    said = tutorial.exchange("atlas, merge the two greenhouse ones")
    titles = {name: titles for name, (_, titles) in world.BACKFILL_PURSUITS.items()}
    greenhouse = titles["Greenhouse logger firmware"] + titles["Greenhouse dashboard"]
    staging = tuple(t for t in titles["Billing export service"] if "staging" in t)
    counts = {23: len(greenhouse), 4: len(staging), 15: len(titles["Billing export service"]) - len(staging)}
    for n, ((_, words), (_, answer)) in enumerate(zip(said[::2], said[1::2])):
        if n:
            home.set_clock(home.now + timedelta(minutes=2))
        reply = home.telegram(words)
        if reply is None:
            raise Failed(f"Atlas did not claim {words!r}")
        expect_all_lines(_world_counts(answer, counts), reply)

    snap = Record.open(home.record_root).snapshot()
    ids = {s.title: seeds.evidence_id(s) for s in world.SESSIONS}
    names = {p.name: p for p in snap.pursuits()}
    if "Greenhouse dashboard" in names or "Greenhouse logger firmware" in names:
        raise Failed(f"the greenhouse Pursuits are still apart: {sorted(names)}")
    logger = names["Greenhouse logger"]
    if {m.evidence for m in logger.sessions} != {ids[t] for t in greenhouse} or logger.area != "home":
        raise Failed(f"Greenhouse logger holds {[m.evidence for m in logger.sessions]} in {logger.area}, "
                     f"not both histories' Sessions in home")
    if sum(isinstance(h.statement, Inference) and h.statement.text.startswith("Created from") for h in logger.history) != 2:
        raise Failed("Greenhouse logger does not keep both histories")
    split, billing = names[STAGING], names["Billing export service"]
    if {m.evidence for m in split.sessions} != {ids[t] for t in staging}:
        raise Failed(f"{STAGING!r} holds other Sessions than the staging deploy ones")
    if {m.evidence for m in billing.sessions} & {m.evidence for m in split.sessions}:
        raise Failed("Billing export service still lists the Sessions split out of it")
    if not split.filing_rules or not billing.filing_rules[-1:] or not logger.filing_rules:
        raise Failed("the corrections left no Filing rule for later filing (scene 15)")
    if snap.profile().areas != ("work", "health", "family", "home"):
        raise Failed(f"the Areas are {snap.profile().areas}")
    notes = [(n.text, n.atlas_reply) for n in snap.notes() if n.via == "telegram"]
    if [t for t, _ in notes] != [words.removeprefix("atlas, ") for _, words in said[::2]]:
        raise Failed(f"the Telegram Notes hold {len(notes)} messages, not the owner's three")


# ------------------------------------------------------------------ scene 7: the first Daily Brief

def _first_brief(home: AtlasHome) -> None:
    ran = home.cli("atlas", "tick", "--quiet")
    if ran.code != 0 or ran.out:
        raise Failed(f"hermes atlas tick --quiet exited {ran.code} and printed {ran.out!r}: {ran.err.strip()}")
    sent = home.sent()
    if len(sent) != 1:
        raise Failed(f"the tick sent {len(sent)} messages, not one Daily Brief")
    if sent[0]["to"] != "telegram":
        raise Failed(f"the brief went to {sent[0]['to']}, not the Telegram home channel")
    expect_all_lines(tutorial.fenced_block("Atlas · Daily Brief · Monday 16 November"), sent[0]["text"])
    kept = Record.open(home.record_root).snapshot().brief(home.now.date())
    if kept is None or kept.status is not SendStatus.SENT or kept.attempts[-1].message_ids != (sent[0]["message_id"],):
        raise Failed(f"the brief file is {kept and kept.attempts}, not sent with message id {sent[0]['message_id']}")
    if kept.text != sent[0]["text"]:
        raise Failed("the brief file does not hold the text as sent")

    home.set_clock(home.now + timedelta(minutes=10))
    home.cli("atlas", "tick", "--quiet")
    if len(home.sent()) != 1:
        raise Failed("the next tick sent the brief again")


# ------------------------------------------------------------------ scenes 8, 9, 26: replies to the brief

def _reply_once(home: AtlasHome, said: str, brief: str) -> str:
    """Reply to the brief with `said`. The Note keeps the words and Atlas's reply, and a redelivery
    of the same Telegram message gets the same reply and changes nothing."""
    earlier = _telegram_notes(home)
    reply = home.telegram(said, reply_to=brief)
    if reply is None:
        raise Failed(f"Atlas did not claim the reply {said!r} to its brief")
    notes = _telegram_notes(home)
    if notes != [*earlier, (said, reply)]:
        raise Failed(f"the Telegram Notes are {notes}, not the owner's words and Atlas's reply")
    before = home.files()
    again = home.telegram(said, reply_to=brief, message_id=home.last_message_id)
    if again != reply or home.files() != before:
        raise Failed("a redelivered message got another reply or changed the record")
    return reply


def _telegram_notes(home: AtlasHome) -> list[tuple[str, str]]:
    return [(n.text, n.atlas_reply) for n in Record.open(home.record_root).snapshot().notes() if n.via == "telegram"]


def _resume_prompt(home: AtlasHome) -> None:
    reply = _reply_once(home, "1 prompt", seeds.MONDAY_BRIEF_ID)
    expect_all_lines(tutorial.fenced_block('Resuming "Payments CI flakes".'), reply)


def _plain_words(home: AtlasHome) -> None:
    (_, said), (_, answer) = tutorial.exchange("1 done, 10/10 pass with one worker")
    expect_all_lines(answer, _reply_once(home, said, seeds.MONDAY_BRIEF_ID))
    snap = Record.open(home.record_root).snapshot()
    payments, newsletter = snap.pursuit("Payments CI flakes"), snap.pursuit("Agent-tooling newsletter")
    ended = payments.past_steps[-1].ended if payments.past_steps else None
    if payments.next_step is not None or not isinstance(ended, Done) or ended.how != "word":
        raise Failed(f"the Payments CI flakes Step is {payments.next_step or ended}, not done by your word")
    if ended.basis.text != "10/10 pass with one worker":
        raise Failed(f"the done Step keeps {ended.basis.text!r}, not what you said about it")
    if newsletter.status is not Status.PARKED or newsletter.focus:
        raise Failed(f"Agent-tooling newsletter is {newsletter.status.value}, focus {newsletter.focus}, not parked")


def _skip_by_word(home: AtlasHome) -> None:
    (_, said), (_, answer) = tutorial.exchange("skip 2, not today")
    expect_all_lines(answer, _reply_once(home, said, seeds.FRIDAY_BRIEF_ID))
    knee = Record.open(home.record_root).snapshot().pursuit(seeds.KNEE)
    skipped = knee.past_steps[-1] if knee.past_steps else None
    ended = skipped.ended if skipped else None
    if not isinstance(ended, Skipped) or ended.how != "word":
        raise Failed(f"the Knee rehab plan Step is {knee.next_step or ended}, not skipped by your word")
    again = knee.next_step
    if again is None or again.id == skipped.id or again.text != skipped.text or again.ended is not None:
        raise Failed(f"the skipped Step did not come back as a new Step in the same words: {again}")
    if rules.skipped_in_a_row(knee) != 1:
        raise Failed("the skip does not count toward the three skips in a row")


# ------------------------------------------------------------------ scenes 11, 13, 25: Steps across the week

def _hold_and_drop(home: AtlasHome) -> None:
    (_, hold), (_, answer), (_, evening), (_, done) = tutorial.exchange("3 remind me friday")
    expect_all_lines(answer, _reply_once(home, hold, seeds.TUESDAY_BRIEF_ID))
    snap = Record.open(home.record_root).snapshot()
    billing, bike = snap.pursuit(seeds.BILLING), snap.pursuit(seeds.BIKE)
    step = billing.next_step
    if step is None or step.ended is not None or step.held_until != date(2026, 11, 20):
        raise Failed(f"the billing Step is {step}, not open and held until Friday 20 November")
    if rules.offers(snap, billing.id, step) != 0 or rules.skipped_in_a_row(billing) != 0:
        raise Failed("the hold counts as an offer or a skip")
    if bike.status is not Status.DROPPED or bike.focus:
        raise Failed(f"Bike route planner is {bike.status.value}, not dropped")

    home.set_clock(world.TABLET_AT)
    expect_all_lines(done, _reply_once(home, evening, seeds.TUESDAY_BRIEF_ID))
    reading = Record.open(home.record_root).snapshot().pursuit(seeds.READING)
    ended = reading.past_steps[-1].ended if reading.past_steps else None
    if reading.next_step is not None or not isinstance(ended, Done) or ended.how != "word":
        raise Failed(f"the reading tracker Step is {reading.next_step or ended}, not done by your word")
    decided = [d.text for d in reading.resume.decided if d.source.evidence == ended.basis.source.evidence]
    if decided != ["tablet app"]:
        raise Failed(f"the reading tracker decided {decided}, not the tablet app from your reply")


ELIDED = ("Left off: ", "Resume: ", "Reply ")
"""Lines the tutorial leaves out of the later briefs: "each Step has its Left off and Resume lines,
and the brief ends with the "prompt" hint, as on Monday"."""


def _brief_of_the_day(home: AtlasHome, shown: str, steps: int) -> str:
    """Tick at the brief time: one brief goes out, and reads as the tutorial shows it."""
    ran = home.cli("atlas", "tick", "--quiet")
    if ran.code != 0 or ran.out:
        raise Failed(f"hermes atlas tick --quiet exited {ran.code} and printed {ran.out!r}: {ran.err.strip()}")
    sent = home.sent()
    if len(sent) != 1:
        raise Failed(f"the tick sent {len(sent)} messages, not one Daily Brief")
    expect_brief(sent[0]["text"], shown, steps, home.now)
    return sent[0]["text"]


def expect_brief(text: str, shown: str, steps: int, now: datetime) -> None:
    """A later brief reads as the tutorial shows it once the lines it elides are left out. Each
    Step keeps its Left off line, and the hint names each Step."""
    head, _, body = text.partition("\n")
    if head != f"Atlas · Daily Brief · {now:%A} {now.day} {now:%B}":
        raise Failed(f"the brief opens with {head!r}")
    expect_all_lines(shown, "\n".join(line for line in body.splitlines() if not line.strip().startswith(ELIDED)))
    lines = tutorial.normalise(body)
    if sum(line.startswith("Left off: ") for line in lines) != steps:
        raise Failed(f"the brief does not give each of its {steps} Steps a Left off line")
    if rules.messages.prompt_hint(steps) not in lines:
        raise Failed("the brief does not end its Steps with the prompt hint")


def _two_steps(home: AtlasHome) -> None:
    _brief_of_the_day(home, tutorial.fenced_block("Run the suite 50 times with a separate Redis db"), 2)
    kept = Record.open(home.record_root).snapshot().brief(home.now.date())
    if kept.follows_up != date(2026, 11, 17) or [f.state for f in kept.followups] != ["done_seen", "done_word", "held"]:
        raise Failed(f"the brief follows up {kept.follows_up} as {[f.state for f in kept.followups]}")
    if kept.decision is None or (kept.decision.pursuit, kept.decision.ask) != (seeds.BACKUPS, 1):
        raise Failed(f"the decision slot asks {kept.decision}, not the backups for the first time")


def _held_step_first(home: AtlasHome) -> None:
    _brief_of_the_day(home, tutorial.fenced_block("Knee rehab plan         Ask for a week-3"), 2)
    snap = Record.open(home.record_root).snapshot()
    kept = snap.brief(home.now.date())
    if [f.state for f in kept.followups] != ["not_visible_yet", "no_sign", "no_sign"]:
        raise Failed(f"Thursday's Steps are followed up as {[f.state for f in kept.followups]}")
    billing = snap.pursuit(seeds.BILLING)
    if rules.offers(snap, billing.id, billing.next_step) != 1:
        raise Failed("the held Step's first offer after the hold is not its first")
    for id in (seeds.READING, seeds.GREENHOUSE):
        p = snap.pursuit(id)
        if p.next_step is None or p.next_step.ended is not None or rules.offers(snap, id, p.next_step) != 1:
            raise Failed(f"{p.name}'s Step is not open with one offer, to be offered once more")
# ------------------------------------------------------------------ scenes 32, 33, 34: the Pause

def _no_cron(home: AtlasHome) -> None:
    calls = [c for c in home.hermes_calls() if c[:1] == ["cron"]]
    if calls:
        raise Failed(f"Atlas called hermes {' '.join(calls[0])}: a Pause never touches the Hermes cron job")


def _pause(home: AtlasHome) -> None:
    (_, said), (_, answer) = tutorial.exchange(seeds.PAUSE_SAID)
    reply = home.telegram(said)
    if reply is None or " ".join(reply.split()) != " ".join(answer.split()):
        raise Failed(f"Atlas answered {reply!r}, not the tutorial's {answer!r}")
    before = home.files()
    if home.telegram(said, message_id=home.last_message_id) != reply or home.files() != before:
        raise Failed("a redelivered pause got another reply or changed the record")
    if home.telegram(said, sender="5550002", chat="5550002") is not None or home.files() != before:
        raise Failed("a stranger's pause reached Atlas")
    snap = Record.open(home.record_root).snapshot()
    kept = [(p.start, p.end, p.said) for p in snap.pauses()]
    if kept != [(world.PAUSE_AT, datetime(2026, 11, 22, 7, 0, tzinfo=world.TZ), seeds.PAUSE_SAID.removeprefix("pause "))]:
        raise Failed(f"pauses.md holds {kept}, not Friday 16:40 to Sunday 07:00 in your words")
    _no_cron(home)


SATURDAY_TICKS = (time(7, 30), time(12, 0), time(18, 0), time(23, 50))


def _filed_then_paused(home: AtlasHome) -> None:
    """Friday after the skip, with the week's Sessions collected and filed by ticks before 16:40,
    then paused."""
    seeds.collecting(seeds.after_scene_26)(home.record_root)
    home.set_clock(world.PAUSE_AT - timedelta(minutes=10))
    _tick_files(home, ticks=2)
    seeds.pause(home.record_root)
    home.set_clock(world.SATURDAY_AT)


def _paused_saturday(home: AtlasHome) -> None:
    friday = Record.open(home.record_root).snapshot()
    for at in SATURDAY_TICKS:
        home.set_clock(datetime.combine(world.SATURDAY_AT.date(), at, world.TZ))
        snap = _tick_files(home)
        if home.sent():
            raise Failed(f"a tick sent a message by {at:%H:%M} on the paused Saturday")
    if snap.brief(world.SATURDAY_AT.date()) is not None:
        raise Failed("Saturday has a brief file")
    evening = seeds.evidence_id(world.by_title("greenhouse evening check"))
    greenhouse = snap.pursuit(seeds.GREENHOUSE)
    done = greenhouse.past_steps[-1].ended if greenhouse.past_steps else None
    if evening not in {m.evidence for m in greenhouse.sessions} or not isinstance(done, Done) or (
            done.basis.source.evidence != evening):
        raise Failed("Saturday's ticks did not file Friday evening's Session and see the greenhouse Step done")
    s, pauses = snap.settings(), snap.pauses()
    friday_day = world.PAUSE_AT.date()
    if rules.clock_days(friday_day, world.SATURDAY_AT.date(), pauses, s) != 0:
        raise Failed("Saturday counts on a clock")
    if rules.clock_days(friday_day, world.SUNDAY_BRIEF_AT.date(), pauses, s) != 1:
        raise Failed("Sunday does not count on the clocks once the Pause ends")
    for p in snap.pursuits():
        if p.next_step is not None and rules.offers(snap, p.id, p.next_step) != rules.offers(friday, p.id, p.next_step):
            raise Failed(f"{p.name}'s Step was offered while Atlas was paused")
    _no_cron(home)


def expect_sunday(text: str) -> None:
    """Sunday's brief follows up Friday's. The tutorial lists Knee rehab plan, then Reading tracker;
    the Step order rule (a held Step on its first offer, then the oldest Step) gives the reading
    tracker's Step of Wednesday first. That order is an open owner decision (PR #128), so each Step
    reads as the tutorial shows it and the order is the rule's."""
    lines = tutorial.fenced_block("YESTERDAY'S STEPS (from Friday)").splitlines()
    knee = next(n for n, line in enumerate(lines) if line.startswith("1 · Knee rehab plan"))
    reading = next(n for n, line in enumerate(lines) if line.startswith("2 · Reading tracker"))
    reordered = (lines[:knee] + [lines[reading].replace("2 ·", "1 ·")] + [lines[knee].replace("1 ·", "2 ·")]
                 + lines[knee + 1:reading] + lines[reading + 1:])
    head, _, body = text.partition("\n")
    if head != "Atlas · Daily Brief · Sunday 22 November":
        raise Failed(f"the brief opens with {head!r}")
    out = body.splitlines()
    first = next(n for n, line in enumerate(out) if line.startswith("1 · "))
    second = next(n for n, line in enumerate(out) if line.startswith("2 · "))
    if not out[first].startswith("1 · Reading tracker") or not any(
            line.strip().startswith("Left off: ") for line in out[first + 1:second]):
        raise Failed(f"the brief's first Step is {out[first]!r}, with no Left off line")
    expect_all_lines("\n".join(reordered), "\n".join(out[:first + 1] + out[second:]))


def _sunday_follow_up(home: AtlasHome) -> None:
    ran = home.cli("atlas", "tick", "--quiet")
    if ran.code != 0 or ran.out:
        raise Failed(f"hermes atlas tick --quiet exited {ran.code} and printed {ran.out!r}: {ran.err.strip()}")
    sent = home.sent()
    if len(sent) != 1:
        raise Failed(f"the tick sent {len(sent)} messages, not one Daily Brief")
    expect_sunday(sent[0]["text"])
    kept = Record.open(home.record_root).snapshot().brief(home.now.date())
    if kept.follows_up != world.PAUSE_AT.date() or [f.state for f in kept.followups] != ["done_seen", "skipped_word"]:
        raise Failed(f"the brief follows up {kept.follows_up} as {[f.state for f in kept.followups]}")
    _no_cron(home)


# ------------------------------------------------------------------ scenes 9b, 10, 14, 15, 39: filing on the tick

def _tick_files(home: AtlasHome, ticks: int = 1) -> Snapshot:
    """Tick `ticks` times; each prints nothing."""
    for _ in range(ticks):
        ran = home.cli("atlas", "tick", "--quiet")
        if ran.code != 0 or ran.out:
            raise Failed(f"hermes atlas tick --quiet exited {ran.code} and printed {ran.out!r}: {ran.err.strip()}")
    return Record.open(home.record_root).snapshot()


def _changed_on_board(home: AtlasHome, name: str) -> dict:
    column, card = _cards(_board(home))[name]
    if not card["changed"]:
        raise Failed(f"the card {name!r} is not marked as changed")
    return {**card, "column": column}


def _seen_done(home: AtlasHome) -> None:
    snap = _tick_files(home)
    billing = snap.pursuit(seeds.BILLING)
    session = seeds.evidence_id(world.by_title("per-invoice vat rounding"))
    via = [s for s in snap.sessions() if s.interface == "T3 Code" and s.started.date() == home.now.date()]
    if [(s.id, s.harness) for s in via] != [(session, HarnessId.CLAUDE_CODE)]:
        raise Failed("the T3 Code Session is not held once, under Claude Code")
    done = billing.past_steps[-1] if billing.past_steps else None
    ended = done.ended if done else None
    if not isinstance(ended, Done) or ended.how != "seen" or done.text.text != seeds.AFTER_SCENE_6[1].step:
        raise Failed(f"Monday's billing Step is {done}, not done as seen in your Session")
    cite = ended.basis.source
    if (cite.evidence, cite.label, cite.at, ended.basis.text) != (
            session, "Claude Code via T3 Code", seeds.SEEN_BILLING, "per-invoice rounding matches the ledger"):
        raise Failed(f"the Step is seen in {cite.label} at {cite.at:%a %H:%M} as {ended.basis.text!r}")
    if billing.next_step is None or billing.next_step.text.text != world.TICK_FILINGS[
            "per-invoice vat rounding"].next_step or not isinstance(billing.next_step.text, Inference):
        raise Failed(f"the billing Pursuit's next Step is {billing.next_step}")
    if session not in {m.evidence for m in billing.sessions} or billing.history[-1].actor.value != "Atlas":
        raise Failed("the billing Pursuit does not list the Session, or Atlas left no History line")
    _changed_on_board(home, "Billing export service")


def _tuesday_follow_up(home: AtlasHome) -> None:
    follow_up = tutorial.fenced_block("✓ Billing export service: done. Seen in Claude Code via T3 Code")
    steps = "\n".join(line for line in tutorial.fenced_block("2 · Reading tracker for the kids Write down").splitlines()
                      if not line.strip().startswith("(each Step"))
    _brief_of_the_day(home, follow_up + "\n" + steps, 3)
    kept = Record.open(home.record_root).snapshot().brief(home.now.date())
    if [f.state for f in kept.followups] != ["done_word", "done_seen", "no_sign"]:
        raise Failed(f"Monday's Steps are followed up as {[f.state for f in kept.followups]}")
    if kept.decision is None or (kept.decision.pursuit, kept.decision.ask) != (seeds.BIKE, 1):
        raise Failed(f"the decision slot asks {kept.decision}, not the bike route planner for the first time")


def _no_proposals(home: AtlasHome, shown: dict[str, list[str]] | None = None) -> None:
    """No card carries a proposal but those the tutorial has shown by then."""
    proposed = {name: [_shown(p) for p in c["proposals"]] for name, (_, c) in _cards(_board(home)).items()
                if c["proposals"]}
    if proposed != (shown or {}):
        raise Failed(f"the cards carry the proposals {proposed}, the tutorial {shown or {}}")


def _idea_becomes_project(home: AtlasHome) -> None:
    _tick_files(home)
    _no_proposals(home)
    head, left, step = tutorial.fenced_block("project (an idea until Wed 18 Nov)").splitlines()
    name, kind, last, star = re.match(r"^(.+?) · (.+?) · (\w+ \d+ \w+)  (★)  CHANGED$", head).groups()
    card = _changed_on_board(home, name)
    marked = lambda s: s and f"{s['text']} [{s['mark']}]"
    shown = (card["column"], card["kind"], card["last_worked"], card["focus"], marked(card["left_off"]),
             marked(card["next"]))
    wanted = ("active", kind, last, True, left.split("Left off", 1)[1].strip(), step.split("Next", 1)[1].strip())
    if shown != wanted:
        raise Failed(f"the card {name!r} shows {shown}, the tutorial {wanted}")
    p = Record.open(home.record_root).snapshot().pursuit(name)
    session = seeds.evidence_id(world.by_title("reading tracker app screen"))
    line = next((h for h in p.history if h.statement.text.startswith(rules.KIND_CHANGED)), None)
    if line is None or line.actor.value != "Atlas" or not isinstance(line.statement, Inference) \
            or line.statement.basis != (session,):
        raise Failed(f"no History line by Atlas changes the Kind on the Codex Session's evidence: {line}")


def _split_by_itself(home: AtlasHome) -> None:
    snap = _tick_files(home)
    _no_proposals(home)
    staging, billing = snap.pursuit(seeds.STAGING), snap.pursuit(seeds.BILLING)
    rotations = {seeds.evidence_id(s) for s in world.SESSIONS if s.title.startswith("rotate staging db password")}
    if len(rotations) != 2 or not rotations <= {m.evidence for m in staging.sessions}:
        raise Failed("the password-rotation Sessions are not filed under Billing export: staging deploy")
    if rotations & {m.evidence for m in billing.sessions}:
        raise Failed("a password-rotation Session is filed under the billing service")
    by_rule = [h for h in staging.history if staging.filing_rules[0].text in h.statement.text]
    if len(by_rule) != 2:
        raise Failed(f"{len(by_rule)} History lines, not 2, say the Sessions were filed by Sunday's split")


def _reactivated(home: AtlasHome) -> None:
    snap = _tick_files(home, ticks=2)
    _no_proposals(home, {"Payments CI flakes": [ENDING_MARK]})
    keyboard = snap.pursuit("Keyboard firmware")
    session = seeds.evidence_id(world.by_title("keyboard debounce 10 ms"))
    if keyboard.status is not Status.ACTIVE or keyboard.focus:
        raise Failed(f"Keyboard firmware is {keyboard.status.value}, focus {keyboard.focus}, not active outside Focus")
    line = keyboard.history[-1]
    if line.actor.value != "Atlas" or not line.statement.text.startswith("Active again") \
            or not isinstance(line.statement, Fact) or line.statement.source.evidence != session:
        raise Failed(f"the last History line is {line.statement.text!r}, not Atlas's on the Session that advanced it")
    if keyboard.last_worked != world.by_title("keyboard debounce 10 ms").start.date():
        raise Failed("the quiet clock does not count from the Session that advanced it")
    card = _changed_on_board(home, "Keyboard firmware")
    if card["column"] != "active":
        raise Failed(f"the card stays in {card['column'].upper()}")


# ------------------------------------------------------------------ scenes 19, 20, 20b, 21: endings and Neglected

THURSDAY_SHOWN = "This Pursuit looks finished."
ENDING_MARK = "Atlas proposes: mark it done [I]"


def _section(text: str, title: str) -> list[str]:
    """The lines of one section of a brief, its title first, up to the next blank line."""
    lines = text.splitlines()
    start = next(n for n, line in enumerate(lines) if line.startswith(title))
    end = next((n for n in range(start, len(lines)) if not lines[n].strip()), len(lines))
    return lines[start:end]


def _proposals(home: AtlasHome, name: str) -> list[str]:
    _, card = _cards(_board(home))[name]
    return [_shown(p) for p in card["proposals"]]


def _thursday_brief(home: AtlasHome) -> str:
    """Thursday's brief: the follow-up word for word, wrapped where Atlas wraps it; the three Steps
    the tutorial names, in the order the Step rule gives; the backups question."""
    shown = tutorial.fenced_block(THURSDAY_SHOWN)
    ran = home.cli("atlas", "tick", "--quiet")
    if ran.code != 0 or ran.out:
        raise Failed(f"hermes atlas tick --quiet exited {ran.code} and printed {ran.out!r}: {ran.err.strip()}")
    sent = home.sent()
    if len(sent) != 1:
        raise Failed(f"the tick sent {len(sent)} messages, not one Daily Brief")
    text = sent[0]["text"]
    want, got = (" ".join(tutorial.normalise("\n".join(_section(t, "YESTERDAY'S STEPS")))) for t in (shown, text))
    if got != want:
        raise Failed(f"the follow-up reads {got!r}, the tutorial {want!r}")
    if f'      {rules.messages.FINISHED}' not in text.splitlines():
        raise Failed("the ending proposal is not a line of its own under the CI flakes")
    expect_all_lines("\n".join(_section(shown, "NEEDS A DECISION")), "\n".join(_section(text, "NEEDS A DECISION")))
    snap = Record.open(home.record_root).snapshot()
    kept = snap.brief(home.now.date())
    names = [snap.pursuit(s.pursuit).name for s in kept.steps]
    listed = re.search(r"\(three Steps: (.*)\)", shown)[1].split(", ")
    if sorted(names) != sorted(listed):
        raise Failed(f"the brief offers {names}, the tutorial {listed}")
    return text


def _ending_proposed(home: AtlasHome) -> None:
    snap = _tick_files(home, ticks=2)
    payments = snap.pursuit(seeds.PAYMENTS)
    ended = payments.past_steps[-1].ended if payments.past_steps else None
    if not isinstance(ended, Done) or ended.how != "seen" or ended.basis.source.label != "Codex":
        raise Failed(f"the CI flakes Step is {payments.next_step or ended}, not seen done in Codex")
    (proposal,) = payments.proposals or (None,)
    if proposal is None or proposal.to is not Status.DONE or proposal.in_followup:
        raise Failed(f"the CI flakes proposals are {payments.proposals}, not one ending not yet raised")
    if _proposals(home, payments.name) != [ENDING_MARK]:
        raise Failed(f"the CI flakes card shows {_proposals(home, payments.name)}, not the ending proposal")
    home.set_clock(world.THURSDAY_BRIEF_AT)
    _thursday_brief(home)
    snap = Record.open(home.record_root).snapshot()
    kept = snap.brief(home.now.date())
    if [f.pursuit for f in kept.followups if f.ending_proposed] != [seeds.PAYMENTS]:
        raise Failed("the brief file does not record that it proposed the CI flakes' ending")
    if not all(p.in_followup for p in snap.pursuit(seeds.PAYMENTS).proposals):
        raise Failed("the ending proposal is not marked as raised, so a later brief could raise it again")
    if _proposals(home, payments.name) != [ENDING_MARK]:
        raise Failed("the card lost its ending mark before you answered")


def _second_ask(home: AtlasHome) -> None:
    _thursday_brief(home)
    kept = Record.open(home.record_root).snapshot().brief(home.now.date())
    if kept.decision is None or (kept.decision.pursuit, kept.decision.ask, kept.decision.quiet_days) != (
            seeds.BACKUPS, 2, 21):
        raise Failed(f"the decision slot asks {kept.decision}, not the backups a second time, 21 days quiet")


def _close_and_continue(home: AtlasHome) -> None:
    (_, said), (_, answer) = tutorial.exchange("close it. continue the backups")
    if _proposals(home, "Payments CI flakes") != [ENDING_MARK]:
        raise Failed("the CI flakes card does not show the ending proposal before you answer")
    expect_all_lines(answer, _reply_once(home, said, seeds.THURSDAY_BRIEF_ID))
    snap = Record.open(home.record_root).snapshot()
    payments, backups = snap.pursuit(seeds.PAYMENTS), snap.pursuit(seeds.BACKUPS)
    if payments.status is not Status.DONE or payments.focus or payments.proposals:
        raise Failed(f"Payments CI flakes is {payments.status.value}, focus {payments.focus}, "
                     f"proposals {payments.proposals}")
    if _proposals(home, payments.name):
        raise Failed("the CI flakes card keeps its ending mark after you answered")
    if backups.status is not Status.ACTIVE or rules.quiet_since(backups) != home.now.date():
        raise Failed(f"Home server backups is {backups.status.value}, quiet since {rules.quiet_since(backups)}")
    if backups.id in {p.id for p in rules.neglected(snap, home.now.date())}:
        raise Failed("Home server backups is still Neglected after you said continue")


def _what_neglected(home: AtlasHome) -> None:
    (_, said), (_, answer) = tutorial.exchange("what have I neglected?")
    expect_all_lines(answer, _ask(home, said))
    home.set_clock(world.FIND_AT)
    (_, find), (_, found) = tutorial.exchange("find the conversation where I got the GPX")
    expect_all_lines(found, home.telegram(find) or "")


# ------------------------------------------------------------------ scenes 28, 29: the Profile

TYPED = "- I skip greenhouse steps in the morning because it is dark"


def _corrected(home: AtlasHome, basis: str, at: datetime) -> None:
    """The Profile holds the corrected entry in place of the wrong one, by the owner, resting on `basis`."""
    entries = Record.open(home.record_root).snapshot().profile().entries
    texts = [e.statement.text for e in entries]
    wanted = [CORRECTED if t == WRONG else t for t in [e.statement.text for e in seeds._profile().entries]]
    if texts != wanted:
        raise Failed(f"the Profile reads {texts}, not {wanted}")
    s = entries[1].statement
    if not isinstance(s, Fact) or (s.source.evidence, s.source.label, s.source.at) != (basis, "your Note", at) \
            or entries[1].by is not Who.OWNER:
        raise Failed(f"the corrected entry is {entries[1]}, not yours on your Note {basis} of {at:%a %H:%M}")
    if f"[F your Note · {at:%Y-%m-%d %H:%M} · {basis}]" not in (home.record_root / "profile.md").read_text():
        raise Failed("profile.md does not show the corrected entry's basis")


def _profile_correction(home: AtlasHome) -> None:
    (_, said), (_, answer) = tutorial.exchange("atlas, profile:")
    reply = home.telegram(said)
    if reply is None:
        raise Failed(f"Atlas did not claim {said!r}")
    expect_all_lines(answer, reply)
    [note] = [n for n in Record.open(home.record_root).snapshot().notes() if n.via == "telegram" and n.id != seeds.SPLIT_SAID.id]
    _corrected(home, note.id, home.now)
    before = home.files()
    if home.telegram(said, message_id=home.last_message_id) != reply or home.files() != before:
        raise Failed("a redelivered correction got another reply or changed the record")


def _offer() -> str:
    """The once-only "apply" offer, as usage.md words it for scene 29."""
    return json.loads(re.search(r'^("You edited profile\.md by hand\..*")$', tutorial.USAGE.read_text(), re.M)[1])


def _hand_edit(home: AtlasHome) -> None:
    profile = home.record_root / "profile.md"
    written = profile.read_bytes()
    edited = re.sub(rf"^- {re.escape(WRONG)}.*$", TYPED, written.decode(), flags=re.M)
    profile.write_text(edited)
    home.set_clock(home.now + timedelta(minutes=3))
    ran = home.cli("atlas", "tick", "--quiet")
    if ran.code != 0:
        raise Failed(f"hermes atlas tick exited {ran.code}: {ran.err.strip()}")
    if profile.read_bytes() != written:
        raise Failed("the tick did not put profile.md back as Atlas wrote it")
    [kept] = Record.open(home.record_root).snapshot().pending_applies() or [None]
    if kept is None or (kept.via, kept.text, kept.offered) != ("profile.md", edited, False):
        raise Failed(f"the hand-edit Note is {kept}, not your edited profile.md waiting for its offer")
    if f"+{TYPED}\n" not in kept.diff or f"-- {WRONG}" not in kept.diff:
        raise Failed(f"the hand-edit Note's diff is {kept.diff!r}")

    home.set_clock(world.SKIP_AT)
    (_, said), (_, answer) = tutorial.exchange("skip 2, not today")
    expect_all_lines(f"{answer}\n{_offer()}", home.telegram(said, reply_to=seeds.FRIDAY_BRIEF_ID))

    home.set_clock(world.SKIP_AT + timedelta(minutes=1))
    (_, words), (_, changed) = tutorial.exchange("atlas, profile:")
    expect_all_lines(changed.replace(f"{world.PROFILE_AT:%a %H:%M}", f"{kept.at:%a %H:%M}"), home.telegram("apply"))
    _corrected(home, kept.id, kept.at)
    snap = Record.open(home.record_root).snapshot()
    if snap.pending_applies():
        raise Failed("the hand edit still waits for \"apply\" after it was applied")


# ------------------------------------------------------------------ scene 30: forget a Session

FORGET_COLLECTED = datetime(2026, 11, 20, 0, 10, tzinfo=world.TZ)   # the tick after Thursday, before the brief


def _forgettable(home: AtlasHome) -> None:
    seeds.after_scene_1(home.record_root)
    home.set_clock(FORGET_COLLECTED)
    home.cli("atlas", "tick", "--quiet")
    seeds.thursday_filed(home.record_root)
    home.set_clock(world.FORGET_AT)


def _forget(home: AtlasHome) -> None:
    (_, ask), (_, preview), (_, word), (_, done) = tutorial.exchange("atlas, forget the Codex Session")
    root = home.record_root
    forgotten = seeds.evidence_id(world.by_title("staging deploy config"))
    before = Record.open(root).snapshot()
    held = {h.id for h in before.held().sessions()}
    folder = next(root.glob(f"evidence/codex/*/*/{forgotten}"), None)
    if folder is None:
        raise Failed("the seed holds no Codex Session from Thu 19 Nov 14:02")
    if forgotten not in {h.evidence for h in before.find(world.PLANTED)}:
        raise Failed("a find did not reach the Session to forget")

    expect_all_lines(preview, home.telegram(ask) or "nothing")
    if not folder.is_dir() or Record.open(root).snapshot().pending_forget() is None:
        raise Failed("Atlas did not keep the Session and wait for \"forget\"")
    files = home.files()
    if home.telegram(ask, message_id=home.last_message_id) is None or home.files() != files:
        raise Failed("a redelivered ask got no reply or changed the record")

    expect_all_lines(done, home.telegram(word) or "nothing")
    after = Record.open(root).snapshot()
    if folder.exists() or {h.id for h in after.held().sessions()} != held - {forgotten}:
        raise Failed("Forget did not remove exactly the Session's evidence")
    for path in (p for p in root.rglob("*") if p.is_file()):
        data = path.read_bytes()
        if any(words.encode() in data for words in (forgotten, world.PASTED_PASSWORD, world.PLANTED)):
            raise Failed(f"{path.relative_to(root)} still holds the forgotten Session")
    staging, payments = after.pursuit(seeds.STAGING), after.pursuit("p-2x7b")
    if staging.resume.left_off or [m.evidence for m in staging.sessions] != [m.evidence for m in before.pursuit(
            seeds.STAGING).sessions if m.evidence != forgotten] or staging.next_step != before.pursuit(
            seeds.STAGING).next_step:
        raise Failed("Billing export: staging deploy kept the forgotten Fact or lost something else")
    if payments != before.pursuit("p-2x7b"):
        raise Failed("Forget changed Payments CI flakes")
    day = after.journal(seeds.THURSDAY)
    flake = seeds.evidence_id(world.by_title("flake fix merged"))
    if [s.text for s in day.lines] != ["Merged the retry flake fix."] or day.evidence != (flake,):
        raise Failed(f"Thursday's Journal is {day}, not its one other line counted from its other Session")
    if day.time.total >= before.journal(seeds.THURSDAY).time.total:
        raise Failed("Thursday's Active Time still counts the forgotten Session")
    record = after.forgotten()
    if len(record.entries) != 1 or skip_key(HarnessId.CODEX, world.by_title("staging deploy config").native_key) \
            not in record.skip_keys:
        raise Failed(f"forgotten.md holds {record.entries}, not one Session forgotten with its skip key")

    files = home.files()
    if home.telegram(word, message_id=home.last_message_id) != done.strip() or home.files() != files:
        raise Failed("a redelivered \"forget\" got another reply or changed the record")
    home.set_clock(world.FORGET_AT + timedelta(minutes=10))
    ran = home.cli("atlas", "tick", "--quiet")
    if ran.code != 0 or folder.exists() or len(home.sent()) != 1:
        raise Failed(f"the next tick exited {ran.code}, collected the Session again or sent no Daily Brief")


# ------------------------------------------------------------------ scene 2: the Settings tab

def _add_web_chats(home: AtlasHome) -> None:
    """Settings → "Add ChatGPT", "Add claude.ai": each shows as waiting for an export, settings.md
    holds it as an export Harness, and each click leaves a one-line Board Note."""
    before = home.dashboard("GET", "/settings").json()
    if before["added"] or [a["label"] for a in before["addable"]] != ["ChatGPT", "claude.ai"]:
        raise Failed(f"Settings offers {before['addable']} with {before['added']} already added")
    for harness in ("chatgpt", "claude_ai"):
        _click(home, "PATCH", "/settings", {"add_harness": harness})
    shown = home.dashboard("GET", "/settings").json()
    page = "\n".join(["Added in settings", *(f"  {a['label']:<13}{a['state']}" for a in shown["added"])])
    if tutorial.normalise(page) != tutorial.normalise(tutorial.fenced_block("Added in settings")):
        raise Failed(f"the Settings tab shows {page!r}")
    snap = Record.open(home.record_root).snapshot()
    inbox = f"~/{(home.record_root / 'inbox').relative_to(home.home)}"
    for harness in (HarnessId.CHATGPT, HarnessId.CLAUDE_AI):
        h = snap.settings().harnesses.get(harness)
        if h is None or (h.mode, h.found_by, h.roots) != ("export", "owner", (inbox,)):
            raise Failed(f"settings.md holds {harness.label} as {h}")
    said = sorted(n.text for n in snap.notes() if n.via == "board")
    if said != ["Added ChatGPT in Settings.", "Added claude.ai in Settings."]:
        raise Failed(f"the Board Notes say {said}")


# ------------------------------------------------------------------ scenes 4, 6, 17, 31: the Board

def _board(home: AtlasHome) -> dict:
    got = home.dashboard("GET", "/board")
    if got.status_code != 200:
        raise Failed(f"GET /board answered {got.status_code}: {got.text}")
    return got.json()


def _cards(board: dict) -> dict[str, tuple[str, dict]]:
    return {c["name"]: (column, c) for column, cards in board["columns"].items() for c in cards}


def _click(home: AtlasHome, method: str, path: str, body: dict) -> None:
    got = home.dashboard(method, path, body)
    if got.status_code != 200:
        raise Failed(f"{method} {path} {body} answered {got.status_code}: {got.text}")


def _shown(s: dict | None) -> str | None:
    return s and f"{s['text']} [{s['mark']}{' ' + s['citation'] if s['citation'] else ''}]"


def _bold(starting: str) -> list[str]:
    return re.findall(r"\*\*(.+?)\*\*", tutorial.sentence(starting))


def _board_clicked(home: AtlasHome, name: str, history: str) -> None:
    """A Board click leaves a History line in the owner's name and a one-line Note from the Board."""
    snap = Record.open(home.record_root).snapshot()
    p = snap.pursuit(name)
    last = p.history[-1]
    if last.actor.value != "You" or last.statement.text != history:
        raise Failed(f"{name!r} ends its History with {last.statement.text!r} by {last.actor.value}, not {history!r}")
    if not any(n.via == "board" and n.about == p.id and n.id == last.statement.source.evidence for n in snap.notes()):
        raise Failed(f"no Board Note holds the click on {name!r}")


def _read_board(home: AtlasHome) -> None:
    board = _board(home)
    cards = _cards(board)
    tutorial_cards = tutorial.board()
    for column in ("active", "parked", "dropped", "done"):
        want = sum(c.column == column.upper() for c in tutorial_cards)
        if len(board["columns"][column]) != want:
            raise Failed(f"{column.upper()} has {len(board['columns'][column])} cards, the tutorial {want}")
    for t in tutorial_cards:
        if t.name not in cards:
            raise Failed(f"the Board has no card {t.name!r}")
        column, c = cards[t.name]
        left, next = c["left_off"] or {}, c["next"] or {}
        shown = (column.upper(), c["kind"], c["last_worked"], left.get("text"), left.get("mark"), next.get("text"),
                 next.get("mark"))
        wanted = (t.column, t.kind, t.last_worked, t.left_off, "F", t.next, "I")
        if shown != wanted:
            raise Failed(f"the card {t.name!r} shows {shown}, the tutorial {wanted}")
        if not c["new"] or c["focus"] or c["proposals"]:
            raise Failed(f"the card {t.name!r} is not marked new, or is starred or carries a proposal")
        if not c["resume_prompt"].startswith(f'Resuming "{t.name}".'):
            raise Failed(f"the card {t.name!r} copies another prompt")
    seen = cards[tutorial_cards[0].name][1]
    _click(home, "POST", "/seen", {"marks": {seen["id"]: seen["upto"]}})
    after = _cards(_board(home))
    if after[seen["name"]][1]["new"] or not all(c["new"] for n, (_, c) in after.items() if n != seen["name"]):
        raise Failed("a seen mark did not clear that card's mark alone")


def _star_four(home: AtlasHome) -> None:
    names = _bold("On the Board, click the star")
    ids = {name: c["id"] for name, (_, c) in _cards(_board(home)).items()}
    for name in names:
        _click(home, "PATCH", f"/pursuits/{ids[name]}", {"focus": True})
        _board_clicked(home, name, f"{rules.FOCUS_ADDED} on the Board.")
    board = _board(home)
    starred = sorted(name for name, (_, c) in _cards(board).items() if c["focus"])
    if starred != sorted(names) or board["in_focus"] != len(names):
        raise Failed(f"the stars are on {starred}, not {sorted(names)}")


def _knee_proposal(home: AtlasHome) -> None:
    head, left, step = tutorial.fenced_block("Knee rehab plan · problem").splitlines()[:3]
    name, kind, rest = head.split(" · ")
    day, mark, proposal = re.split(r"\s{2,}", rest)
    _, c = _cards(_board(home))[name]
    shown = (c["kind"], c["last_worked"], "NEW" if c["new"] else None, [_shown(p) for p in c["proposals"]],
             _shown(c["left_off"]), _shown(c["next"]))
    wanted = (kind, day, mark, [proposal], left.split("Left off", 1)[1].strip(), step.split("Next", 1)[1].strip())
    if shown != wanted:
        raise Failed(f"the knee card shows {shown}, the tutorial {wanted}")
    _click(home, "PATCH", f"/pursuits/{c['id']}", {"focus": True})
    board = _board(home)
    _, c = _cards(board)[name]
    if not c["focus"] or c["proposals"]:
        raise Failed(f"the star left the knee card at focus {c['focus']} with {c['proposals']}")
    if board["in_focus"] != 5:
        raise Failed(f"Focus holds {board['in_focus']} Pursuits, not five")
    _board_clicked(home, name, f"{rules.FOCUS_ADDED} on the Board.")


def _drag_to_parked(home: AtlasHome) -> None:
    (name,) = _bold("Before you leave, open the Board")
    _, c = _cards(_board(home))[name]
    _click(home, "PATCH", f"/pursuits/{c['id']}", {"status": "parked"})
    column, c = _cards(_board(home))[name]
    if column != "parked" or c["focus"]:
        raise Failed(f"{name!r} is in {column.upper()}, focus {c['focus']}, after the drag to Parked")
    _board_clicked(home, name, "Parked it.")


# ------------------------------------------------------------------ scenes 16, 18, 35: web-chat exports

def _drop_exports(home: AtlasHome, chatgpt=world.EXPORTED[world.GPT], claude=world.EXPORTED[world.CAI]) -> list[Path]:
    """The owner moves the export zip files that arrived by email into ~/atlas/inbox/."""
    inbox, arrived = home.record_root / "inbox", home.now - timedelta(hours=1)
    return [*([exports.chatgpt_zip(inbox / "chatgpt-export.zip", chatgpt, arrived)] if chatgpt else []),
            *([exports.claude_zip(inbox / "claude-export.zip", claude, arrived)] if claude else [])]


def _imported(home: AtlasHome, dropped: list[Path], sessions: dict[HarnessId, tuple]) -> Snapshot:
    """The tick reads every conversation of each export as a Session, deletes the files, and
    never takes in the group chats."""
    snap = _tick_files(home)
    for harness, wanted in sessions.items():
        held = {s.id for s in snap.sessions() if s.harness is harness}
        if held != {seeds.evidence_id(s) for s in wanted}:
            raise Failed(f"the record holds {len(held)} {harness.label} Sessions, not the export's {len(wanted)}")
    if [p for p in dropped if p.exists()] or list((home.record_root / "inbox").iterdir()):
        raise Failed("an export file is still in the inbox after its import")
    planted = exports.GROUP_CHAT.encode()
    for path in home.record_root.rglob("*"):
        data = path.read_bytes() if path.is_file() else b""
        if planted in (zstandard.ZstdDecompressor().decompressobj().decompress(data) if path.suffix == ".zst" else data):
            raise Failed(f"the group chats reached {path.relative_to(home.record_root)}")
    return snap


def _web_chats_imported(home: AtlasHome) -> None:
    dropped = _drop_exports(home)
    snap = _imported(home, dropped, world.EXPORTED)
    knee = snap.pursuit("Knee rehab plan")
    knee_sessions = {seeds.evidence_id(world.by_title(t)) for t, f in world.TICK_FILINGS.items()
                     if f.pursuit == knee.name and world.by_title(t) in world.EXPORTED[world.GPT]}
    if knee.area != "health" or {m.evidence for m in knee.sessions} != knee_sessions:
        raise Failed(f"the knee Pursuit is in {knee.area} with {len(knee.sessions)} Sessions")
    for s in (*world.EXPORTED[world.GPT], *world.EXPORTED[world.CAI]):
        card = snap.card(seeds.evidence_id(s))
        if seeds.evidence_id(s) not in knee_sessions and (card is None or card.one_off_area is None):
            raise Failed(f"the {s.harness.label} Session {s.title!r} is not kept as a one-off")
    head, left, step = tutorial.fenced_block("Knee rehab plan · problem").splitlines()[:3]
    name, kind, rest = head.split(" · ")
    day, mark, proposal = re.split(r"\s{2,}", rest)
    _, c = _cards(_board(home))[name]
    shown = (c["kind"], c["last_worked"], "NEW" if c["new"] else None, [_shown(p) for p in c["proposals"]],
             _shown(c["left_off"]), _shown(c["next"]))
    wanted = (kind, day, mark, [proposal], left.split("Left off", 1)[1].strip(), step.split("Next", 1)[1].strip())
    if shown != wanted:
        raise Failed(f"the knee card shows {shown}, the tutorial {wanted}")
    asked = len(home.ctx.fake_llm.calls)
    _drop_exports(home)
    _tick_files(home)
    if len(home.ctx.fake_llm.calls) != asked or list((home.record_root / "inbox").iterdir()):
        raise Failed("the same exports dropped again were filed again or kept")


def _monday_before_exports(home: AtlasHome) -> None:
    """Monday's Journal day as the tick after midnight wrote it, from the store Sessions alone."""
    seeds.web_chats_added(seeds.monday_before_exports)(home.record_root)
    home.set_clock(AFTER_MONDAY)
    _tick_files(home)
    home.set_clock(world.EXPORTS_AT)


def _monday_corrected(home: AtlasHome) -> None:
    before = Record.open(home.record_root).snapshot().journal(seeds.MONDAY)
    if before is None or before.time.total != timedelta(hours=3, minutes=15):
        raise Failed("Monday's Journal day is not written from the store Sessions before the exports")
    _imported(home, _drop_exports(home), world.EXPORTED)
    _tick_files(home)
    snap = Record.open(home.record_root).snapshot()
    if any(n.origin.value == "hand_edit" for n in snap.notes()):
        raise Failed("the sweep took the corrected Journal day for a hand edit")
    text = (home.record_root / "journal" / f"{seeds.MONDAY}.md").read_text()
    body = "\n".join(map(as_shown, text.split("\n---\n", 1)[1].splitlines()))
    day = tutorial.fenced_block(tutorial.JOURNAL_DAY).splitlines()
    finished = day.index("Finished")
    expect_lines("\n".join([*day[:finished], *day[finished + 2:]]), body)


def _knee_said_done(home: AtlasHome) -> None:
    prompt = _reply_once(home, "1 prompt", seeds.SUNDAY_BRIEF_ID)
    line = next(x for x in tutorial.fenced_block("1 · Knee rehab plan").splitlines() if x.startswith("1 · Knee"))
    step = re.split(r"\s{2,}", line)[-1].removesuffix(" [I]")
    if not prompt.startswith('Resuming "Knee rehab plan".') or f"Next Step: {step[0].lower()}{step[1:]}" not in prompt:
        raise Failed(f"the prompt for Step 1 reads {prompt!r}")
    home.set_clock(world.SUNDAY_DONE_AT)
    _reply_once(home, "1 done", seeds.SUNDAY_BRIEF_ID)
    knee = Record.open(home.record_root).snapshot().pursuit(seeds.KNEE)
    done = knee.past_steps[-1] if knee.past_steps else None
    if done is None or not isinstance(done.ended, Done) or done.ended.how != "word":
        raise Failed(f"the knee Step is {knee.next_step or done}, not done by your word")
    home.set_clock(world.AFTER_SUNDAY)
    _tick_files(home)
    sunday = world.SUNDAY_DONE_AT.date()
    if Record.open(home.record_root).snapshot().journal(sunday) is None:
        raise Failed("Sunday's Journal day was not written")
    home.set_clock(world.NEXT_EXPORT_AT)
    snap = _imported(home, _drop_exports(home, chatgpt=(), claude=(world.SUNDAY_KNEE,)),
                     {world.CAI: (world.SUNDAY_KNEE,)})
    knee = snap.pursuit(seeds.KNEE)
    if seeds.evidence_id(world.SUNDAY_KNEE) not in {m.evidence for m in knee.sessions}:
        raise Failed("the claude.ai Session is not Evidence of the Knee rehab plan")
    if knee.past_steps != (done,) or knee.next_step is not None or any(
            h.actor.value == "Atlas" and "Step done" in h.statement.text for h in knee.history):
        raise Failed("the later export marked the knee Step again")
    corrected = snap.journal(sunday).corrections
    wanted = f"{world.NEXT_EXPORT_AT:%a} {world.NEXT_EXPORT_AT.day} {world.NEXT_EXPORT_AT:%b}: added claude.ai " \
             f"{world.SUNDAY_KNEE.start:%H:%M} ({world.SUNDAY_KNEE.minutes} min) when your claude.ai export arrived."
    if corrected != (wanted,):
        raise Failed(f"Sunday's Journal day reads corrections {corrected}, not {wanted!r}")


# ------------------------------------------------------------------ scenes 22, 24, 37, 38: questions to Atlas

def _ask(home: AtlasHome, said: str) -> str:
    """The owner asks Atlas in Telegram. Atlas answers from its record and changes nothing in it
    but the conversation Note, and sends nothing else."""
    before = Record.open(home.record_root).snapshot()
    sends = len(home.sent())
    reply = home.telegram(said)
    if reply is None:
        raise Failed(f"Atlas did not claim {said!r}")
    after = Record.open(home.record_root).snapshot()
    if [p for p in after.pursuits()] != [p for p in before.pursuits()]:
        raise Failed(f"asking {said!r} changed a Pursuit")
    if after.journal_days() != before.journal_days() or len(after.notes()) != len(before.notes()) + 1:
        raise Failed(f"asking {said!r} wrote more than its Note")
    if len(home.sent()) != sends:
        raise Failed(f"asking {said!r} sent a message besides the reply")
    return reply


def _addressed(said: str) -> str:
    """The tutorial's line falls inside the window an earlier exchange opened; in scene mode it
    is addressed instead."""
    return said if said.lower().startswith("atlas") else f"atlas, {said}"


def _gpx_answer(call: dict) -> dict:
    """The model reading the Sessions the index found: it picks the GPX smoothing Session when it
    was shown, quoting the turn that says how."""
    gpx = world.by_title("gpx elevation smoothing")
    if f"Session {seeds.evidence_id(gpx)} · " not in call["input"]:
        return {"session": None, "text": "", "quote": None}
    (said,) = (text for _, text in gpx.said)
    return {"session": seeds.evidence_id(gpx), "text": tutorial.exchange("find the conversation")[1][1].splitlines()[-1]
            .removesuffix(" [F]"), "quote": said.split(": ", 1)[1].rstrip(".")}


def _find(home: AtlasHome) -> None:
    (_, said), (_, answer) = tutorial.exchange("find the conversation where I got the GPX")
    expect_all_lines(answer, _ask(home, _addressed(said)))
    if [c["task"] for c in home.ctx.fake_llm.calls] != ["atlas_answer"]:
        raise Failed("the answer did not come from one atlas_answer call")
    index = home.record_root / ".atlas" / "index.sqlite"
    index.unlink()
    again = home.telegram(_addressed(said))
    expect_all_lines(answer, again or "")


def _outside(home: AtlasHome) -> None:
    (_, said), (_, answer) = tutorial.exchange("send Dana the billing export status")
    expect_all_lines(answer, _ask(home, said))
    if any(call[:1] == ["send"] for call in home.hermes_calls()):
        raise Failed("Atlas called hermes send")


def _area_rows(text: str) -> list[str]:
    """The time table's rows, each Area with its Pursuits on one line, whatever the wrapping."""
    rows: list[str] = []
    for line in tutorial.normalise(text)[1:]:
        if re.match(r"\S+ \d+ (h \d\d|min) ", line) or not rows:
            rows.append(line)
        else:
            rows[-1] += " " + line
    return rows


def _week_time(home: AtlasHome) -> None:
    (_, said), (_, answer) = tutorial.exchange("how long did I work this week, by Area?")
    reply = _ask(home, said)
    expect_lines(answer.splitlines()[0], reply)
    want, got = _area_rows(answer), _area_rows(reply)
    if sorted(want) != sorted(got):
        raise Failed(f"Atlas shows the Areas {got}, the tutorial {want}")


def _resume_parked(home: AtlasHome) -> None:
    (_, said), (_, answer) = tutorial.exchange("where did I leave off on the keyboard firmware?")
    expect_all_lines(answer, _ask(home, said))
    prompt = _ask(home, "prompt")
    expect_lines('Resuming "Keyboard firmware".', prompt)
    snap = Record.open(home.record_root).snapshot()
    keyboard = snap.pursuit("Keyboard firmware")
    if keyboard.status is not Status.PARKED or rules.quiet_since(keyboard) != keyboard.last_worked:
        raise Failed("asking where you left off counted as work on the keyboard firmware")


SCENES: dict[str, Scene] = {
    "1": Scene(world.SETUP_AT, seed=lambda home: None, step=_setup),
    "2": Scene(world.SETUP_AT + timedelta(minutes=5), seed=lambda home: seeds.after_scene_1(home.record_root),
               step=_add_web_chats),
    "3a": Scene(seeds.BACKFILLED, seed=lambda home: seeds.after_scene_1(home.record_root), step=_backfill,
                llm=world_model),
    "3b": Scene(seeds.BACKFILLED, seed=lambda home: seeds.after_scene_1(home.record_root), step=_filed,
                llm=world_model),
    "3c": Scene(seeds.BACKFILLED, seed=lambda home: seeds.after_scene_1(home.record_root), step=_journaled,
                llm=world_model),
    "4": Scene(seeds.BACKFILLED, step=_read_board, llm=world_model,
               seed=lambda home: (seeds.after_scene_1(home.record_root), home.cli("atlas", "backfill"))),
    "5": Scene(world.CORRECTED_AT, seed=_after_backfill, step=_corrections, llm=corrections_model),
    "6": Scene(seeds.STARRED, seed=lambda home: seeds.after_scene_6(home.record_root, starred=False), step=_star_four),
    "7": Scene(world.FIRST_BRIEF_AT, seed=lambda home: seeds.after_scene_6(home.record_root), step=_first_brief),
    "8": Scene(world.PROMPT_AT, seed=lambda home: seeds.scene_8(home.record_root), step=_resume_prompt),
    "9": Scene(world.DONE_AT, seed=lambda home: seeds.after_scene_7(home.record_root), step=_plain_words),
    "9b": Scene(world.SEEN_TICK_AT, seed=lambda home: seeds.collecting(seeds.after_scene_9)(home.record_root), step=_seen_done,
               llm=filing_model),
    "10": Scene(world.TUESDAY_BRIEF_AT, seed=lambda home: seeds.after_scene_9b(home.record_root),
                step=_tuesday_follow_up),
    "14": Scene(world.KIND_TICK_AT, seed=lambda home: seeds.collecting(seeds.after_scene_12)(home.record_root),
                step=_idea_becomes_project, llm=filing_model),
    "15": Scene(world.KIND_TICK_AT, seed=lambda home: seeds.collecting(seeds.after_scene_5_split)(home.record_root),
                step=_split_by_itself, llm=filing_model),
    "39": Scene(world.REACTIVATED_AT, seed=lambda home: seeds.collecting(seeds.after_scene_6)(home.record_root), step=_reactivated,
                llm=filing_model),
    "19": Scene(world.ENDING_TICK_AT, seed=lambda home: seeds.collecting(seeds.wednesday_afternoon)(home.record_root),
                step=_ending_proposed, llm=filing_model),
    "20": Scene(world.THURSDAY_BRIEF_AT, seed=lambda home: seeds.wednesday_evening(home.record_root), step=_second_ask),
    "20b": Scene(world.CLOSE_AT, seed=lambda home: seeds.after_scene_20(home.record_root), step=_close_and_continue),
    "21": Scene(world.NEGLECTED_AT, seed=lambda home: seeds.after_scene_20b(home.record_root), step=_what_neglected,
                llm=lambda: FakeLlm({"atlas_answer": _gpx_answer})),
    "11": Scene(world.HOLD_AT, seed=lambda home: seeds.after_scene_10(home.record_root), step=_hold_and_drop),
    "13": Scene(world.WEDNESDAY_BRIEF_AT, seed=lambda home: seeds.after_scene_12(home.record_root), step=_two_steps),
    "25": Scene(world.FRIDAY_BRIEF_AT, seed=lambda home: seeds.after_scene_24(home.record_root), step=_held_step_first),
    "17": Scene(seeds.KNEE_FILED + timedelta(hours=12), seed=lambda home: seeds.after_scene_16(home.record_root),
                step=_knee_proposal),
    "16": Scene(world.EXPORTS_AT, seed=lambda home: seeds.web_chats_added(seeds.after_scene_6)(home.record_root),
                step=_web_chats_imported, llm=filing_model),
    "18": Scene(world.EXPORTS_AT, seed=_monday_before_exports, step=_monday_corrected, llm=filing_model),
    "35": Scene(world.SUNDAY_PROMPT_AT, seed=lambda home: seeds.web_chats_added(seeds.sunday_knee_offered)(
        home.record_root), step=_knee_said_done, llm=filing_model),
    "26": Scene(world.SKIP_AT, seed=lambda home: seeds.after_scene_25(home.record_root), step=_skip_by_word),
    "27": Scene(AT_LUNCH, seed=_monday_journaled, step=_read_without_atlas, llm=world_model),
    "28": Scene(world.PROFILE_AT, seed=lambda home: seeds.scene_28(home.record_root), step=_profile_correction,
                llm=profile_model),
    "29": Scene(world.HAND_EDIT_AT, seed=lambda home: seeds.scene_29(home.record_root), step=_hand_edit,
                llm=profile_model),
    "30": Scene(world.FORGET_AT, seed=_forgettable, step=_forget),
    "31": Scene(world.SKIP_AT + timedelta(hours=8), seed=lambda home: seeds.after_scene_6(home.record_root),
                step=_drag_to_parked),
    "22": Scene(world.FIND_AT, seed=lambda home: seeds.thursday(home.record_root), step=_find,
                llm=lambda: FakeLlm({"atlas_answer": _gpx_answer})),
    "24": Scene(world.OUTSIDE_AT, seed=lambda home: seeds.thursday(home.record_root), step=_outside),
    "32": Scene(world.PAUSE_AT, seed=lambda home: seeds.after_scene_26(home.record_root), step=_pause),
    "33": Scene(world.SATURDAY_AT, seed=_filed_then_paused, step=_paused_saturday, llm=filing_model),
    "34": Scene(world.SUNDAY_BRIEF_AT, seed=lambda home: seeds.after_scene_33(home.record_root), step=_sunday_follow_up),
    "37": Scene(world.WEEK_TIME_AT, seed=lambda home: seeds.sunday_evening(home.record_root), step=_week_time),
    "38": Scene(world.RESUME_AT, seed=lambda home: seeds.keyboard_parked(home.record_root), step=_resume_parked),
}


# ------------------------------------------------------------------ running

def claimed() -> frozenset[str]:
    return frozenset(tomllib.loads(MANIFEST.read_text())["claimed"])


def run_scene(scene_id: str, show: Callable[[AtlasHome], None] = lambda home: None) -> Result:
    """`show` runs between seed and step, and again after the step, on the scene's home."""
    scene = SCENES.get(scene_id)
    if scene is None:
        return Result("fail", "claimed, but the harness has no step for it")
    with tempfile.TemporaryDirectory(prefix=f"atlas-scene-{scene_id}-") as base, \
            AtlasHome(Path(base), scene.llm() if scene.llm else None) as home:
        home.set_clock(scene.at)
        return _attempt(lambda: (scene.seed(home), show(home), scene.step(home), show(home)))


def run_scenes(claims: frozenset[str]) -> dict[str, Result]:
    return {i: run_scene(i) if i in claims else Result("not claimed") for i in SCENE_IDS}


def run_week() -> Result:
    """Sunday to Sunday in one chain from an empty home, with no seeds."""
    with tempfile.TemporaryDirectory(prefix="atlas-week-") as base, AtlasHome(Path(base)) as home:
        for n, scene_id in enumerate(SCENE_IDS):
            scene = SCENES.get(scene_id)
            if scene is None:
                return Result("fail", f"stopped at scene {scene_id}, which has no step yet; {n} of {len(SCENE_IDS)} ran")
            home.set_clock(scene.at)
            home.ctx.fake_llm = scene.llm() if scene.llm else FakeLlm()
            result = _attempt(lambda: scene.step(home))
            if result.status == "fail":
                return Result("fail", f"scene {scene_id}: {result.reason}")
    return Result("pass")


def _attempt(run: Callable[[], object]) -> Result:
    try:
        run()
    except Failed as failed:
        return Result("fail", str(failed))
    except Exception as error:
        return Result("fail", f"{type(error).__name__}: {error}")
    return Result("pass")


def report(results: dict[str, Result], week: Result) -> str:
    passed = sum(r.status == "pass" for r in results.values())
    failed = sum(r.status == "fail" for r in results.values())
    claims = sum(r.status != "not claimed" for r in results.values())
    return "\n".join([
        "Atlas proof harness, scene mode",
        *(f"  {scene_id:<4} {result}" for scene_id, result in results.items()),
        "",
        f"Week mode (reported, not gating): {week}",
        f"Claimed {claims} of {len(results)} scenes: {passed} passed, {failed} failed.",
    ])
