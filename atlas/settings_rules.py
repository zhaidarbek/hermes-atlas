"""The Settings tab's rules, pure: a ChangeSettings patch checked and applied to Settings, and the
view the tab shows. No I/O. Whether a folder is reached through a link is learned by the caller
(sources.linked) and passed in.

A patch holds one change:

    {"add_harness": "chatgpt" | "claude_ai"}
    {"add_root": {"harness": "codex", "root": "~/work/codex"}}     a store Harness's extra folder
    {"remove_root": {"harness": "codex", "root": "~/work/codex"}}
    {"add_exclusion": "~/clients"}                                   a cwd prefix never collected
    {"remove_exclusion": "~/clients"}
    {"brief_at": "07:30"}
    {"review": {"day": "sunday", "at": "18:00"}}
    {"gap_minutes": 15}
    {"atlas_chat": "telegram:<chat>[:<topic>]" | None}

A folder Atlas always reads (sources.defaults) cannot be removed: an exclusion leaves part of it
out. A refused patch changes nothing and answers a code from REASONS. A folder the owner typed is
never logged and never written into a Note: Notes name only the kind of change.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import replace
from datetime import datetime, time, timedelta
from typing import Mapping, NamedTuple

from . import rules
from .model import (ChangeSettings, HarnessId, HarnessSetting, Note, NoteOrigin, Outcome, Owner, Settings)
from .record import Snapshot

WEB_CHATS = (HarnessId.CHATGPT, HarnessId.CLAUDE_AI)
STORE_HARNESSES = (HarnessId.CLAUDE_CODE, HarnessId.CODEX, HarnessId.DEVIN, HarnessId.HERMES)
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
GAP_MINUTES = range(1, 121)
MAX_PATH = 1024

REASONS = {
    "ONE_CHANGE": "Send one change at a time.",
    "NOT_A_WEB_CHAT": "The web chats Atlas can add are ChatGPT and claude.ai.",
    "NOT_A_STORE": "Folders can be added for Claude Code, Codex, Devin and Hermes.",
    "BAD_PATH": "Type a full folder path that starts with / or ~/, without . or .. parts.",
    "LINKED": "That folder is reached through a link. Name the folder the link points to.",
    "NO_SUCH_ROOT": "Settings has no such folder for that Harness.",
    "ALWAYS_READ": "Atlas always reads this folder. To leave part of it out, add an exclusion.",
    "NO_SUCH_EXCLUSION": "Settings has no such exclusion.",
    "BAD_TIME": "Type a time as HH:MM, from 00:00 to 23:59.",
    "BAD_DAY": "Pick a day of the week.",
    "BAD_GAP": "The Active Time gap is a whole number of minutes, from 1 to 120.",
    "BAD_CHAT": 'Name a chat as "telegram:<chat id>" or "telegram:<chat id>:<topic id>".',
    "OWNER_ONLY": "Only you can change Atlas's settings.",
}

_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
_CHAT = re.compile(r"(?P<platform>[a-z]+):-?[0-9]{1,20}(:[0-9]{1,12})?")


class Here(NamedTuple):
    """What the rules learn from the machine: where exports are dropped (~/atlas/inbox), whether
    the add_root folder is reached through a link, and the folder each Harness always reads."""
    inbox: str
    linked: bool = False
    defaults: Mapping[HarnessId, str] = {}


def _utf8(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def path_of(value: object) -> str | None:
    """The folder as typed when it is a full, plain path: absolute or under ~/, no . or .. parts,
    no repeated or trailing slash, no control character, and text that encodes as UTF-8. None
    otherwise."""
    if (not isinstance(value, str) or not 0 < len(value) <= MAX_PATH or not _utf8(value)
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        return None
    if value.startswith("~/"):
        rest = value[2:]
    elif value.startswith("/"):
        rest = value[1:]
    else:
        return None
    if not rest or posixpath.normpath("/" + rest) != "/" + rest or any(p in (".", "..") for p in rest.split("/")):
        return None
    return value


def roots_typed(patch: dict) -> tuple[str, str] | None:
    """The Harness and folder an add_root patch names, for the caller to check for links before
    deciding."""
    body = patch.get("add_root") if isinstance(patch, dict) and len(patch) == 1 else None
    root = path_of(body.get("root")) if isinstance(body, dict) else None
    return (str(body.get("harness")), root) if root else None


def change(settings: Settings, patch: object, here: Here) -> tuple[Settings, str] | str:
    """(the new Settings, the Note's text) or a refusal code."""
    if not isinstance(patch, dict) or len(patch) != 1:
        return "ONE_CHANGE"
    ((key, value),) = patch.items()
    apply = _CHANGES.get(key)
    if apply is None:
        return "ONE_CHANGE"
    return apply(settings, value, here)


def _add_harness(s: Settings, value: object, here: Here) -> tuple[Settings, str] | str:
    harness = next((h for h in WEB_CHATS if h.value == value), None)
    if harness is None:
        return "NOT_A_WEB_CHAT"
    if harness not in s.harnesses:
        s = replace(s, harnesses={**s.harnesses, harness: HarnessSetting(True, "export", (here.inbox,), "owner")})
    return s, f"Added {harness.label} in Settings."


def _root(value: object, held: Settings | None = None) -> tuple[HarnessId, str] | str:
    """The Harness and folder named. With `held`, a folder settings.md already holds for that
    Harness is named as it is, so one written there by hand can still be removed."""
    if not isinstance(value, dict) or set(value) != {"harness", "root"}:
        return "BAD_PATH"
    harness = next((h for h in STORE_HARNESSES if h.value == value["harness"]), None)
    if harness is None:
        return "NOT_A_STORE"
    if held is not None and harness in held.harnesses and value["root"] in held.harnesses[harness].roots:
        return harness, value["root"]
    root = path_of(value["root"])
    return (harness, root) if root else "BAD_PATH"


def _add_root(s: Settings, value: object, here: Here) -> tuple[Settings, str] | str:
    named = _root(value)
    if isinstance(named, str):
        return named
    if here.linked:
        return "LINKED"
    harness, root = named
    current = s.harnesses.get(harness) or HarnessSetting(True, "store", (), "owner")
    if root not in current.roots:
        s = replace(s, harnesses={**s.harnesses, harness: replace(current, roots=(*current.roots, root))})
    return s, f"Added a {harness.label} folder in Settings."


def _remove_root(s: Settings, value: object, here: Here) -> tuple[Settings, str] | str:
    named = _root(value, s)
    if isinstance(named, str):
        return named
    harness, root = named
    current = s.harnesses.get(harness)
    if current is None or root not in current.roots:
        return "NO_SUCH_ROOT"
    if here.defaults.get(harness) == root:
        return "ALWAYS_READ"
    kept = replace(current, roots=tuple(r for r in current.roots if r != root))
    return replace(s, harnesses={**s.harnesses, harness: kept}), f"Removed a {harness.label} folder in Settings."


def _add_exclusion(s: Settings, value: object, here: Here) -> tuple[Settings, str] | str:
    prefix = path_of(value)
    if prefix is None:
        return "BAD_PATH"
    if prefix not in s.exclusions:
        s = replace(s, exclusions=(*s.exclusions, prefix))
    return s, "Added an exclusion in Settings."


def _remove_exclusion(s: Settings, value: object, here: Here) -> tuple[Settings, str] | str:
    if value not in s.exclusions:
        return "NO_SUCH_EXCLUSION"
    return replace(s, exclusions=tuple(e for e in s.exclusions if e != value)), "Removed an exclusion in Settings."


def _time(value: object) -> time | None:
    m = _TIME.fullmatch(value) if isinstance(value, str) else None
    return time(int(m[1]), int(m[2])) if m else None


def _brief_at(s: Settings, value: object, here: Here) -> tuple[Settings, str] | str:
    at = _time(value)
    if at is None:
        return "BAD_TIME"
    return replace(s, brief_at=at), f"Moved the Daily Brief to {at:%H:%M} in Settings."


def _review(s: Settings, value: object, here: Here) -> tuple[Settings, str] | str:
    if not isinstance(value, dict) or set(value) != {"day", "at"} or value["day"] not in DAYS:
        return "BAD_DAY"
    at = _time(value["at"])
    if at is None:
        return "BAD_TIME"
    day = DAYS.index(value["day"]) + 1
    return replace(s, review_day=day, review_at=at), f"Moved the Review to {value['day'].title()} {at:%H:%M} in Settings."


def _gap(s: Settings, value: object, here: Here) -> tuple[Settings, str] | str:
    if type(value) is not int or value not in GAP_MINUTES:
        return "BAD_GAP"
    return replace(s, gap=timedelta(minutes=value)), f"Set the Active Time gap to {value} minutes in Settings."


def _atlas_chat(s: Settings, value: object, here: Here) -> tuple[Settings, str] | str:
    if value is None:
        return replace(s, channel=replace(s.channel, atlas_chat=None)), "Moved Atlas back to the Hermes home chat in Settings."
    m = _CHAT.fullmatch(value) if isinstance(value, str) else None
    if m is None or m["platform"] != s.channel.platform:
        return "BAD_CHAT"
    return replace(s, channel=replace(s.channel, atlas_chat=value)), "Gave Atlas its own chat in Settings."


_CHANGES = {"add_harness": _add_harness, "add_root": _add_root, "remove_root": _remove_root,
            "add_exclusion": _add_exclusion, "remove_exclusion": _remove_exclusion, "brief_at": _brief_at,
            "review": _review, "gap_minutes": _gap, "atlas_chat": _atlas_chat}


def from_settings(snap: Snapshot, cmd: ChangeSettings, who: Owner, now: datetime, here: Here) -> rules.Decision:
    """A change from the Settings tab: settings.md through the record's ChangeSet, and a one-line
    Note naming the kind of change (who.note). A change that leaves Settings as they are writes
    nothing."""
    refused = rules.authorize(cmd, who)
    if refused:
        return rules.Decision(snap.changes(), Outcome(False, (), refused))
    before = snap.settings()
    changed = change(before, cmd.patch, here)
    if isinstance(changed, str):
        return rules.Decision(snap.changes(), Outcome(False, (), changed))
    after, said = changed
    if after == before:
        return rules.Decision(snap.changes(), Outcome(True, (said,)))
    note = Note(who.note, now.replace(second=0, microsecond=0), NoteOrigin.OWNER, "board", said, atlas_reply=said)
    return rules.Decision(snap.changes().put_settings(after).append_note(note), Outcome(True, (said,)))


def added(s: Settings) -> list[tuple[str, str]]:
    """Each web chat the owner added, with its state: (label, "waiting for an export in <inbox>")."""
    return [(h.label, f"waiting for an export in {s.harnesses[h].roots[0]}")
            for h in WEB_CHATS if h in s.harnesses and s.harnesses[h].roots]


def view(s: Settings, defaults: Mapping[HarnessId, str] = {}) -> dict:
    """What the Settings tab shows: the Harnesses found and their folders (`always`: the one
    Atlas always reads, which has no remove control), the web chats added and their state, the
    exclusions, the times, the gap and Atlas's own chat."""
    return {
        "found": [{"harness": h.value, "label": h.label, "roots": list(s.harnesses[h].roots), "always": defaults.get(h)}
                  for h in STORE_HARNESSES if h in s.harnesses],
        "stores": [{"harness": h.value, "label": h.label} for h in STORE_HARNESSES],
        "interfaces": [{"label": "T3 Code" if k == "t3_code" else k, "root": v.root} for k, v in s.interfaces.items()],
        "added": [{"label": label, "state": state} for label, state in added(s)],
        "addable": [{"harness": h.value, "label": h.label} for h in WEB_CHATS if h not in s.harnesses],
        "exclusions": list(s.exclusions),
        "brief_at": f"{s.brief_at:%H:%M}",
        "review": {"day": DAYS[s.review_day - 1], "at": f"{s.review_at:%H:%M}"},
        "gap_minutes": int(s.gap.total_seconds() // 60),
        "timezone": s.timezone,
        "atlas_chat": s.channel.atlas_chat,
        "days": list(DAYS),
    }
