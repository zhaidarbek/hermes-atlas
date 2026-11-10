"""The scripted model for the fixture world: what a model reading its Sessions answers. Cards
say what each Session was; group calls file the Sessions as world.BACKFILL_PURSUITS and
world.BACKFILL_ONE_OFFS say, with the names, Kinds, Left off and Next lines of the tutorial's
Board; reconciliation merges nothing. Each Left off quotes how its last Session ended, so it is
grounded as a Fact. Journal calls write the Worked on and Decided lines the tutorial's Monday
shows, each quoting the turn at the time its mark gives, and for any other day one Worked on line
quoting how the Session began. Read against the Profile, the owner's words correct entry 2 as
scene 28 shows."""
from __future__ import annotations

import re
from datetime import datetime

from . import seeds, tutorial, world
from .fake_llm import FakeLlm, Respond

_SESSION = re.compile(r"^Session (\S+) · ", re.M)


def world_model() -> FakeLlm:
    return FakeLlm({"atlas_backfill": _respond, "atlas_journal": _respond})


def filing_model() -> FakeLlm:
    """The world model, which also files each Session the tick reads as world.TICK_FILINGS says."""
    return FakeLlm({"atlas_backfill": _respond, "atlas_journal": _respond, "atlas_file": _file})


def week_model(**more: Respond) -> FakeLlm:
    """Every scripted answer at once, for the week replayed in one chain: backfill, filing, the
    Journal and the owner's words."""
    return FakeLlm({task: _respond for task in ("atlas_backfill", "atlas_journal", "atlas_file", "atlas_converse")}
                   | more)


def profile_model() -> FakeLlm:
    return FakeLlm({"atlas_converse": _respond})


def _respond(call: dict) -> dict:
    return {"card": _card, "group": _group, "reconcile": _reconcile, "journal": _journal, "converse": _converse,
            "partition": _partition, "filing": _file, "profile_correction": _correction,
            "profile_edit": _edit, "profile": _profile}[call["schema"]["title"]](call)


def _sessions(call: dict) -> dict[str, world.WorldSession]:
    by_id = {seeds.evidence_id(s): s for s in (*world.SESSIONS, *world.WEB_SESSIONS, *world.EXPORTED[world.GPT],
                                               *world.EXPORTED[world.CAI], world.SUNDAY_KNEE)}
    return {id: by_id[id] for id in _SESSION.findall(call["input"])}


def _card(call: dict) -> dict:
    (s,) = _sessions(call).values()
    return {"intent": f"Work on {s.title}.", "outcome": f"Stopped after {s.minutes} minutes.", "unfinished": None}


def _group(call: dict) -> dict:
    sessions = _sessions(call)
    ids = {s.title: id for id, s in sessions.items()}
    board = {c.name: c for c in tutorial.board()}
    pursuits = []
    for name, (area, titles) in world.BACKFILL_PURSUITS.items():
        mine = [t for t in titles if t in ids]
        if not mine:
            continue
        last = ids[mine[-1]]
        card = board[name]
        pursuits.append({
            "name": name, "kind": card.kind, "area": area, "sessions": [ids[t] for t in mine],
            "left_off": [{"text": card.left_off, "quote": f"Done for now: {mine[-1]}.", "sessions": [last]}],
            **_resume(name, ids, last, f"Done for now: {mine[-1]}."),
            "next_step": {"text": card.next, "quote": None, "sessions": [last]}})
    one_offs = [{"session": ids[t], "area": area} for t, area in world.BACKFILL_ONE_OFFS.items() if t in ids]
    return {"pursuits": pursuits, "one_offs": one_offs}


def _resume(name: str, ids: dict[str, str], last: str, quote: str) -> dict:
    decided, question, reopen = world.BACKFILL_RESUME.get(name, (None, None, ()))
    return {"decided": [{"text": decided, "quote": quote, "sessions": [last]}] if decided else [],
            "open_questions": [{"text": question, "quote": None, "sessions": [last]}] if question else [],
            "reopen": [ids[t] for t in reopen if t in ids] or [last]}


def _reconcile(call: dict) -> dict:
    return {"merges": []}


_LISTED = re.compile(r"^(p-\S+) · (.+?) · \w+ · \w+ · \S+$", re.M)
_RULE = re.compile(r"^Filing rule (\d+): (.*)$", re.M)


def _file(call: dict) -> dict:
    """Files the Session where world.TICK_FILINGS puts it, by the Filing rule that names that
    Pursuit when there is one; a Session the backfill put in a Pursuit the record holds goes back
    there, and any other is a one-off."""
    ((id, s),) = _sessions(call).items()
    listed = {name: id for id, name in _LISTED.findall(call["input"])}
    filed = world.TICK_FILINGS.get(s.title)
    backfilled = next((name for name, (_, titles) in world.BACKFILL_PURSUITS.items() if s.title in titles), None)
    name = filed.pursuit if filed else backfilled
    turns = {f"{t.at:%H:%M}": t.text for t in s.turns()}
    out = {"intent": f"Work on {s.title}.", "outcome": f"Stopped after {s.minutes} minutes.", "pursuit": None,
           "new_pursuit": None, "one_off_area": None, "rule": None, "advances": True, "left_off": [],
           "step_done": None, "next_step": None, "kind": None, "ending": None}
    if name not in listed and filed and filed.new:
        kind, area = filed.new
        return {**out, "new_pursuit": {"name": name, "kind": kind, "area": area}}
    if name not in listed:
        return {**out, "advances": False, "one_off_area": world.BACKFILL_ONE_OFFS.get(s.title, "work")}
    out["pursuit"] = listed[name]
    rule = next((n for n, text in _RULE.findall(call["input"])
                 if "belong here" in text and _owner(call["input"], n) == listed[name]), None)
    out["rule"] = int(rule) if rule and (filed is None or filed.by_rule) else None
    if filed and filed.left_off:
        quote = turns[filed.left_at] if filed.left_at else f"Done for now: {s.title}."
        out["left_off"] = [{"text": filed.left_off, "quote": quote, "sessions": [id]}]
    if filed and filed.done_at:
        out["step_done"] = {"text": filed.done, "quote": turns[filed.done_at], "sessions": []}
    if filed and filed.next_step:
        out["next_step"] = {"text": filed.next_step, "quote": None, "sessions": []}
    if filed and filed.kind:
        out["kind"] = {"to": filed.kind[0], "reason": filed.kind[1]}
    if filed and filed.ending:
        out["ending"] = {"to": filed.ending[0], "reason": filed.ending[1]}
    return out


def _owner(input: str, rule: str) -> str | None:
    """The Pursuit whose listing holds Filing rule `rule`."""
    owner = None
    for line in input.splitlines():
        if m := _LISTED.match(line):
            owner = m[1]
        elif line.startswith(f"Filing rule {rule}:"):
            return owner
    return None


_DAY = re.compile(r"^Day: \w+ (\d+ \w+ \d{4})$", re.M)
_PURSUITS = re.compile(r"^Pursuits: (.*)$", re.M)


def _journal(call: dict) -> dict:
    (s,) = _sessions(call).values()
    day = datetime.strptime(_DAY.search(call["input"])[1], "%d %B %Y").date()
    pursuits = _PURSUITS.search(call["input"])[1].split(", ")
    turns = {f"{t.at:%H:%M}": t.text for t in s.turns() if t.at.date() == day}
    label = s.harness.label + (f" via {s.interface}" if s.interface else "")
    out = {"worked_on": [], "decided": [], "finished": []}
    if day == world.FIRST_BRIEF_AT.date():
        for section, line in tutorial.journal_lines():
            if line.label == label and line.at in turns and line.pursuit in pursuits:
                out[section].append({"pursuit": line.pursuit, "text": line.text, "quote": turns[line.at]})
    else:
        first = min((t for t in s.turns() if t.at.date() == day), key=lambda t: t.at)
        out["worked_on"].append({"pursuit": pursuits[0], "text": f"worked on {s.title}.", "quote": first.text})
    return out


_SHOWN = re.compile(r"Session (\S+) · \S+.*? · (\d{4}-\d{2}-\d{2}) ")


def _profile(call: dict) -> dict:
    """The Profile the tutorial's `cat ~/atlas/profile.md` shows: its first and third entries
    from the whole history at backfill, its second from Monday, when the greenhouse Step went
    undone, placed after the first."""
    blocks, hardware, parks = (re.sub(r" \[I from .*\]$", "", line[2:]) for line in
                               tutorial.fenced_block("# Profile").splitlines() if line.startswith("- "))
    shown = dict(_SHOWN.findall(call["input"]))
    titles = {seeds.evidence_id(s): s.title for s in world.SESSIONS}
    on = lambda *wanted: [id for id in shown if titles.get(id) in wanted]
    if not (day := _DAY.search(call["input"])):
        return {"entries": [
            {"text": blocks, "sessions": [], "pattern": f"{len(set(shown.values()))} days of Sessions", "after": None},
            {"text": parks, "sessions": on("right half drops", "pi-hole dns loop"), "pattern": None, "after": None}]}
    if datetime.strptime(day[1], "%d %B %Y").date() == world.FIRST_BRIEF_AT.date() and on("logger sd card swap"):
        return {"entries": [{"text": hardware, "sessions": on("logger sd card swap"), "pattern": None, "after": 1}]}
    return {"entries": []}


WRONG = "You skip Steps that need hardware."
CORRECTED = "You do Greenhouse logger Steps in the evening, not in the morning."


def _correction(call: dict) -> dict:
    """The owner's words, or their hand edit, correct Profile entry 2, the one scene 28 names."""
    if f"2. {WRONG}" not in call["input"]:
        raise ValueError("the model was not shown the Profile entry to correct")
    return {"entry": 2, "text": CORRECTED}


def _edit(call: dict) -> dict:
    return {"changes": [_correction(call)]}


# Scene 5: what a model reading the owner's corrections answers.
STAGING = "Billing export: staging deploy"
FILINGS = {
    "merge the two greenhouse ones. two folders, one thing": [
        {"command": "Merge", "a": "Greenhouse logger firmware", "b": "Greenhouse dashboard",
         "into_name": "Greenhouse logger", "rule": "two folders, one thing"}],
    "split the staging deploy chores out of Billing export service": [
        {"command": "Split", "pursuit": "Billing export service", "new_name": STAGING,
         "criterion": "staging deploy chores"}],
    "put the greenhouse logger in a new Area, home": [
        {"command": "AddArea", "area": "home"}, {"command": "SetArea", "pursuit": "Greenhouse logger", "area": "home"}],
}


def corrections_model() -> FakeLlm:
    """The world model, which also reads scene 5's corrections and splits the staging deploy
    Sessions out of the billing work."""
    return FakeLlm({"atlas_backfill": _respond, "atlas_journal": _respond, "atlas_converse": _respond})


def _converse(call: dict) -> dict:
    said = call["input"].rsplit("Message: ", 1)[-1]
    return {"commands": FILINGS.get(said, [])}


def _partition(call: dict) -> dict:
    ids = [id for id, s in _sessions(call).items() if "staging" in s.title]
    last = ids[-1]
    return {"sessions": ids, "kind": "project",
            "left_off": [{"text": "The staging deploy reads its settings from env vars.",
                          "quote": f"Done for now: {_sessions(call)[last].title}.", "sessions": [last]}],
            "decided": [], "open_questions": [], "reopen": [last],
            "next_step": {"text": "Rotate the staging database password.", "quote": None, "sessions": [last]}}
