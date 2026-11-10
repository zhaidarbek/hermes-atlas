"""Scenes 12 and 23: agents in Harnesses reach Atlas through `hermes atlas mcp`, driven by an MCP
client that plays Claude Code, Devin and Codex."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, timedelta

from atlas import agentapi, agents
from atlas.model import (Actor, Area, Cite, DayTime, Fact, Inference, JournalDay, Membership, NoteOrigin, PursuitId,
                         ResumeNote, SessionCard, Status)
from atlas.record import Record

from . import mcp_client, seeds, tutorial, world
from .home import AtlasHome
from .mcp_client import CLAUDE_CODE, CODEX, DEVIN, Call

READ_AT = datetime(2026, 11, 17, 21, 18, tzinfo=world.TZ)     # Tuesday evening, in the greenhouse Session
NOTE_AT = datetime(2026, 11, 17, 21, 20, tzinfo=world.TZ)
UPDATE_AT = datetime(2026, 11, 17, 21, 24, tzinfo=world.TZ)
DEVIN_AT = datetime(2026, 11, 17, 21, 40, tzinfo=world.TZ)
ASKED_AT = datetime(2026, 11, 17, 21, 50, tzinfo=world.TZ)
AUGUST_AT = datetime(2026, 11, 19, 12, 15, tzinfo=world.TZ)    # Thursday, between finding the GPX and Dana
LIVE = "greenhouse log interval"                                # the Claude Code Session scene 12 runs in
AUGUST = date(2026, 8, 14)
PIHOLE = PursuitId("p-8p1h")
DEVIN_SAID = "tell atlas we decided to keep the 32 GB card for now"
DEVIN_DECIDED = "We decided to keep the 32 GB card for now."
TITLES = {'"sd card swap"': '"logger sd card swap"', '"humidity chart"': '"greenhouse humidity chart"'}


class Failed(Exception):
    pass


def _read_answer() -> str:
    """Scene 12's atlas_read answer as the tutorial shows it, with this world's Session titles."""
    (_, _), (_, said) = tutorial.exchange("where did I leave off on the greenhouse logger?")
    said = said.removeprefix("(atlas_read) ").replace(",\nClaude Code", ", Claude Code")
    for shown, title in TITLES.items():
        said = said.replace(shown, title)
    return said


def _unmarked(line: str) -> str:
    return re.sub(r"^[A-Za-z ]+: ", "", line, count=1).removesuffix(" [F]").removesuffix(" [I]")


def greenhouse_evening(root) -> None:
    """Tuesday evening as scene 12 reads it: scene 10's record, the Greenhouse logger's Resume Note
    as the tutorial shows it, and the Claude Code Session the owner is in, filed into the
    Greenhouse logger by the 21:20 tick."""
    seeds.after_scene_10(root)
    record = Record.open(root)
    snap = seeds._lent(record)
    swap, chart = seeds.evidence_id(world.by_title("logger sd card swap")), seeds.evidence_id(
        world.by_title("greenhouse humidity chart"))
    lines = _read_answer().splitlines()
    shown = {line.split(": ", 1)[0]: _unmarked(line) for line in lines[1:]}
    p = snap.pursuit(seeds.GREENHOUSE)
    cite = seeds._cite("logger sd card swap", datetime(2026, 11, 11, 17, 25, tzinfo=world.TZ))
    resume = ResumeNote(where=(Fact(shown["Where"], cite),), left_off=(Fact(shown["Left off"], cite),),
                        decided=(Fact(shown["Decided"], seeds._cite("greenhouse humidity chart")),),
                        open_questions=(Inference(shown["Open question"], (swap,)),), reopen=(swap, chart))
    step = shown["Next Step"]
    p = replace(seeds._worked(p, LIVE), resume=resume,
                next_step=replace(p.next_step, text=Inference(step[:1].upper() + step[1:], (swap,))))
    record.commit(snap.changes().put_pursuit(p))


def _expect(expected: str, got: str, what: str) -> None:
    if tutorial.normalise(expected) != tutorial.normalise(got):
        raise Failed(f"{what}: expected {tutorial.normalise(expected)}, got {tutorial.normalise(got)}")


def agent_in_claude_code(home: AtlasHome) -> None:
    live = world.by_title(LIVE)
    folder = home.home / live.cwd.removeprefix("~/")
    session = seeds.evidence_id(live)
    mcp_client.claude_code_running(home.home, live)

    listed, [read] = mcp_client.calls(home.home, CLAUDE_CODE, folder, [Call("atlas_read", {"pursuit": "greenhouse logger"})])
    if listed != [t.name for t in agentapi.TOOLS]:
        raise Failed(f"hermes atlas mcp lists {listed}")
    _expect(_read_answer(), read.text, "atlas_read")
    if read.error or not read.citations or "cc-" not in str(read.citations):
        raise Failed(f"atlas_read gave no citations to follow: {read.citations}")

    home.set_clock(NOTE_AT)
    shown = " ".join(tutorial.fenced_block("(atlas_note)").split("(atlas_note) ", 1)[1].split())
    text = re.search(r'"(.*)"', shown)[1]
    _, [noted] = mcp_client.calls(home.home, CLAUDE_CODE, folder,
                                  [Call("atlas_note", {"text": text, "pursuit": "Greenhouse logger"})])
    _expect(shown, noted.text, "atlas_note")
    [note] = [n for n in Record.open(home.record_root).snapshot().notes() if n.origin is NoteOrigin.AGENT]
    if (note.via, note.text, note.about, note.derived_from, note.cwd) != (
            "claude_code", text, seeds.GREENHOUSE, (session,), live.cwd):
        raise Failed(f"the agent's Note is {note}")

    home.set_clock(UPDATE_AT)
    (_, words), (_, updated) = tutorial.exchange("tell atlas the open question is settled")
    mcp_client.owner_says(home.home, live, UPDATE_AT, words)
    rest = words.removeprefix("tell atlas ")
    decided = rest[:1].upper() + rest[1:] + "."
    said = " ".join(updated.removeprefix("(atlas_update) ").split())
    about = re.search(r"open question about (.+?) and added", said)[1]
    _, [update] = mcp_client.calls(home.home, CLAUDE_CODE, folder, [Call("atlas_update", {
        "pursuit": "Greenhouse logger", "owner_words": words,
        "change": {"remove": [about], "add_decided": True}})])
    if update.error or update.text != said:
        raise Failed(f"atlas_update answered {update.text!r}, not {said!r}")
    after = Record.open(home.record_root).snapshot().pursuit(seeds.GREENHOUSE)
    line = after.history[-1]
    if after.resume.open_questions or after.resume.decided[-1] != Fact(decided, Cite(session, "Claude Code", UPDATE_AT)):
        raise Failed(f"the Resume Note reads {after.resume}")
    if (line.actor, line.agent) != (Actor.AGENT, "Claude Code") or f'"{words}"' not in line.statement.text:
        raise Failed(f"the History line is {line}")

    _refused_in_devin(home, folder)


def _offer(harness: str, pursuit: str, decided: str) -> str:
    """The once-only "apply" offer of a refused update, showing the change it makes."""
    return f'{harness} asked to change {pursuit}: add the decision "{decided}" Reply "apply" to make it.'


def _refused_in_devin(home: AtlasHome, folder) -> None:
    """Devin sends no Session id, so the same kind of update is kept as a Note and offered once."""
    home.set_clock(DEVIN_AT)
    before = Record.open(home.record_root).snapshot().pursuit(seeds.GREENHOUSE)
    _, [refused] = mcp_client.calls(home.home, DEVIN, folder, [Call("atlas_update", {
        "pursuit": "Greenhouse logger", "owner_words": DEVIN_SAID, "change": {"add_decided": True}})])
    snap = Record.open(home.record_root).snapshot()
    if refused.text != agents.REFUSED or snap.pursuit(seeds.GREENHOUSE) != before:
        raise Failed(f"Devin's update was not refused: {refused.text!r}")
    [kept] = snap.pending_applies() or [None]
    if kept is None or (kept.origin, kept.via, kept.text) != (NoteOrigin.REFUSED_UPDATE, "devin", DEVIN_SAID):
        raise Failed(f"Devin's update was kept as {kept}")

    home.set_clock(ASKED_AT)
    asked = "atlas, where did I leave off on the greenhouse logger?"
    first = home.telegram(asked)
    if first is None or first.splitlines()[-1] != _offer("Devin", "Greenhouse logger", DEVIN_DECIDED):
        raise Failed(f"Atlas's next answer does not end with the offer: {first!r}")
    home.set_clock(ASKED_AT + timedelta(minutes=1))
    again = home.telegram(asked)
    if again is None or "apply" in again:
        raise Failed("the offer was made twice")
    applied = home.telegram("apply")
    snap = Record.open(home.record_root).snapshot()
    if applied != f'Greenhouse logger: Resume Note changed. Added the decision "{DEVIN_DECIDED}"' or \
            snap.pending_applies() or snap.pursuit(seeds.GREENHOUSE).resume.decided[-1].text != DEVIN_DECIDED:
        raise Failed(f'"apply" answered {applied!r} and left {snap.pending_applies()}')


def august(root) -> None:
    """Thursday as scene 23 reads it: Thursday's record, with the Pi-hole DNS loop built from its
    August Session and the Journal day that Session wrote."""
    seeds.thursday(root)
    record = Record.open(root)
    snap = record.snapshot()
    s = world.by_title("pi-hole dns loop")
    id = seeds.evidence_id(s)
    keyboard = snap.pursuit("Keyboard firmware")
    pihole = replace(keyboard, id=PIHOLE, name="Pi-hole DNS loop", area=Area("home"), status=Status.PARKED,
                     created=s.start.date(), resume=ResumeNote((), (), (), (), ()), next_step=None, past_steps=(),
                     sessions=(Membership(id, s.end, True),), proposals=(), history=(), filing_rules=())
    worked = next(line for line in _august_answer() if line.startswith("- Pi-hole"))
    text = re.sub(r" \(home\)", "", worked.removeprefix("- ")).rsplit(" [F", 1)[0]
    minutes = timedelta(minutes=s.minutes)
    day = JournalDay(AUGUST, DayTime(minutes, {Area("home"): {PIHOLE: minutes}}, (id,)),
                     (Fact(text, Cite(id, "Claude Code", s.start + timedelta(minutes=10))),), (), (), ())
    record.commit(snap.changes().put_pursuit(pihole))
    record.commit(record.snapshot().changes().put_journal(day))
    party = world._s(world.GPT, "2026-08-14 21:30", 20, "birthday party plan", "~")
    one_off = seeds.evidence_id(party)
    intent = re.search(r"One-off question, no Pursuit \(family\): (.+?)\.? \[F", next(
        line for line in _august_answer() if line.startswith("- One-off")))[1]
    cs = record.snapshot().changes()
    cs.put_session(seeds._evidence(party), {})
    record.commit(cs)
    cs = record.snapshot().changes()
    cs.put_card(SessionCard(one_off, Inference(intent[:1].upper() + intent[1:] + ".", (one_off,)),
                            Inference("Answered.", (one_off,)), None, None, Area("family"), AUGUST))
    record.commit(cs)


def _august_answer() -> list[str]:
    (_, _), (_, said) = tutorial.exchange("what was I doing on 14 August?")
    lines = said.removeprefix("(atlas_read) ").splitlines()
    joined: list[str] = []
    for line in lines:
        if line.startswith("[F") and joined:
            joined[-1] += " " + line
        else:
            joined.append(line)
    return joined


def codex_asks_about_august(home: AtlasHome) -> None:
    """Every line the fixture holds for that day, as the tutorial shows it: the Pi-hole Session
    and the one-off ChatGPT Session of the tutorial's three, so the Pi-hole line's own time and
    the day's Active Time stand in, and the Static site line is not there."""
    asking = world.by_title("flake fix merged")
    folder = home.home / asking.cwd.removeprefix("~/")
    _, [read] = mcp_client.calls(home.home, CODEX, folder, [august_call(asking)])
    check_august(read)


def august_call(asking) -> Call:
    return Call("atlas_read", {"day": AUGUST.isoformat()}, meta={"threadId": asking.native})


def check_august(read) -> None:
    """Scene 23's answer, at the fixture's scale."""
    s = world.by_title("pi-hole dns loop")
    shown = _august_answer()
    pihole = next(line for line in shown if line.startswith("- Pi-hole"))
    at = f"{s.start + timedelta(minutes=10):%H:%M}"
    one_off = next(line for line in shown if line.startswith("- One-off"))
    expected = [shown[0], re.sub(r"\d{2}:\d{2}\]$", f"{at}]", pihole), one_off,
                f"Active time: {s.minutes} min. [F]", shown[-1]]
    _expect("\n".join(expected), read.text, "atlas_read of a day")
    party = seeds.evidence_id(world._s(world.GPT, "2026-08-14 21:30", 20, "birthday party plan", "~"))
    if read.error or [e["id"] for c in read.citations for e in c["evidence"]] != [seeds.evidence_id(s), party]:
        raise Failed(f"the day's citations are {read.citations}")
