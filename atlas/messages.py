"""Render domain values as text for one Channel style. Pure. W Q1 notation lives here (the
record's own notation lives in record._codec, and the record's README explains it).

Style       Fact                                   Inference   Citation detail
TELEGRAM    [F Claude Code via T3 Code, Fri 18:02]  [I]         first Evidence only; full list in the record
            Citation rule (G1): once per item (a Step, a Pursuit in an answer, one Journal line,
            one Review line, one Profile change), on the item's first Fact read from ONE Session
            or Note ("your Note", "your reply", "Note from Codex"). Bare "[F]" for: a counted
            Fact (Count, D Q9), e.g. "Active time 3 h 55 min [F]"; a line whose words already name
            its source and day ("Seen in Codex, Wed 14:30"); a record Fact (where the work lives,
            Status change, filing); a later Fact in an item that already cited; every Fact in text
            meant to leave Atlas (resume_prompt, a status to forward).
            Time: "[F Claude Code, Sat 22:10]" within 7 days; "[F Claude Code 09:40]" (no comma) inside a message about one day; "30 Aug"
            when older ("30 Aug 2025" in another year).
BOARD       badge "F" / "I", tooltip = all Evidence with links to the Session's session.md
MCP         text as TELEGRAM + a JSON block: [{"text", "mark": "fact"|"inference",
            "evidence": [{"id", "harness", "at", "path"}]}] so agents can follow citations
CLI         as TELEGRAM, plus Evidence ids
Adding a Channel = add a Style member + its row in each render function's table (a test
fails if a Style lacks a renderer).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence
from zoneinfo import ZoneInfo

from .model import (Answer, Cite, Count, EvidenceId, Fact, Followup, Outcome, Pursuit, SentBrief, SentReview,
                    Statement, harness_of)
from .record import Snapshot

RESUME_HINT = 'Reply "prompt" for a paste-in prompt.'   # how a ResumeQ answer ends (D Q2)
MESSAGE_MAX = 4000
"""The longest message Atlas sends, in UTF-16 code units as Telegram counts them: under Telegram's
4096, so Hermes sends it as one message, and far under the length one `hermes send` argument may have."""


class Style(str, Enum):
    TELEGRAM = "telegram"; BOARD = "board"; MCP = "mcp"; CLI = "cli"


@dataclass(frozen=True)
class Bare:
    """A line that never shows its source (G1): a Fact from Atlas's own record, or a line whose
    words already name its source and day."""
    s: Statement


def statement(s: Statement, style: Style, *, cite: datetime | None = None, one_day: bool = False) -> str:
    """The statement with its mark. `cite` is the message's time in the owner's zone, given when
    this is the item's first Fact read from one Session or Note (G1); without it a Fact shows a
    bare [F], as in text meant to leave Atlas. A counted Fact is always bare."""
    _telegram_only(style)
    if not isinstance(s, Fact):
        return f"{s.text} [I]"
    if cite is None or isinstance(s.source, Count):
        return f"{s.text} [F]"
    return f"{s.text} [F {s.source.label}{_when(s.source, cite, one_day)}]"


def citation(s: Statement, now: datetime) -> str | None:
    """Where a read Fact comes from, as the Board's badge shows it on hover: "ChatGPT, Mon 19:10"."""
    if isinstance(s, Fact) and isinstance(s.source, Cite):
        return s.source.label + _when(s.source, now, False)
    return None


def _when(source: Cite, now: datetime, one_day: bool) -> str:
    at = source.at.astimezone(now.tzinfo)
    if one_day:
        return f" {at:%H:%M}"
    if 0 <= (now.date() - at.date()).days < 7:
        return f", {at:%a %H:%M}"
    return f", {at.day} {at:%b}" + ("" if at.year == now.year else f" {at.year}")


def item(lines: Sequence[Statement | Bare], style: Style, now: datetime, *, one_day: bool = False) -> list[str]:
    """One item's lines, marked by the G1 rule: the first Fact read from one Session or Note
    cites its source, and every other Fact is a bare [F]."""
    out, cited = [], False
    for line in lines:
        if isinstance(line, Bare):
            out.append(statement(line.s, style))
        elif not cited and isinstance(line, Fact) and isinstance(line.source, Cite):
            out.append(statement(line, style, cite=now, one_day=one_day))
            cited = True
        else:
            out.append(statement(line, style))
    return out


def profile_change(s: Statement, now: datetime) -> str:
    """The answer to a Profile correction: the new entry, quoted, then its mark."""
    return f'Changed: "{s.text}"' + statement(s, Style.TELEGRAM, cite=now).removeprefix(s.text)


def apply_offer(path: str) -> str:
    """The once-only offer, in Atlas's next answer, of a file Atlas put back (W Q15)."""
    return f'You edited {path} by hand. I put it back and kept your text as a Note. Reply "apply" to make the change.'


def prompt_hint(steps: int) -> str:
    """The brief's last Steps line (D Q2: no buttons)."""
    asks = [f'"{n} prompt"' for n in range(1, steps + 1)]
    listed = asks[0] if steps == 1 else ", ".join(asks[:-1]) + " or " + asks[-1]
    return f"Reply {listed} for a paste-in prompt."


def fit(text: str, limit: int = MESSAGE_MAX) -> str:
    """`text` as one message: when it is longer than `limit`, its longest lines are cut, each to
    the same length and ending in "…", so every line still shows."""
    if _units(text) <= limit:
        return text
    lines = text.split("\n")
    low, high = 1, max(_units(line) for line in lines)
    while low < high:
        width = (low + high + 1) // 2
        if _units("\n".join(_cut(line, width) for line in lines)) <= limit:
            low = width
        else:
            high = width - 1
    return "\n".join(_cut(line, low) for line in lines)[:limit]


def _cut(line: str, width: int) -> str:
    if _units(line) <= width:
        return line
    while _units(line) > width - 1:
        line = line[:-max(1, (_units(line) - width + 1) // 2)]
    return line + "…"


def _units(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def brief(b: SentBrief, snap: Snapshot, style: Style) -> str:
    """V1 layout: per Step, one line on where the owner left off and one on how to resume; footer
    'Reply "1 prompt", "2 prompt" or "3 prompt" for a paste-in prompt.' (D Q2: no buttons).
    no_focus_yet -> STEPS heading reads "STEPS (no Focus picked yet)" (D Q10).
    The first brief shows its Steps first and says there is nothing to follow up. A later brief
    opens with the follow-up and puts each Step's text on its Pursuit's line."""
    _telegram_only(style)
    now = b.attempts[0].at.astimezone(ZoneInfo(snap.settings().timezone))
    pursuits = {p.id: p for p in snap.pursuits()}
    first = b.follows_up is None
    width = max((len(pursuits[s.pursuit].name) for s in b.steps), default=0)
    steps = ["STEPS (no Focus picked yet)" if b.no_focus_yet else "STEPS"]
    for s in b.steps:
        p = pursuits[s.pursuit]
        text, *rest = _step_item(p, s.step, style, now, snap)
        head = [f"{s.n} · {p.name}", f"    {text}"] if first else [f"{s.n} · {p.name:<{width}}  {text}"]
        steps += [*head, *(f"    {line}" for line in rest)]
    steps += ["", f"    {prompt_hint(len(b.steps))}"] if b.steps else ["    Nothing today."]
    decision = ["NEEDS A DECISION"]
    if b.decision is None:
        decision.append("    Nothing today.")
    else:
        p = pursuits[b.decision.pursuit]
        decision += [f"    {p.name} · {p.kind.value} · quiet {b.decision.quiet_days} days [F]",
                     *(f"    {line}" for line in _last(p, style)), "    Continue, park, or drop?"]
    head = f"Atlas · Daily Brief · {b.day:%A} {b.day.day} {b.day:%B}"
    if first:
        return fit("\n".join([head, "", *steps, "", "YESTERDAY'S STEPS", "    None. This is your first brief.", "",
                              *decision]))
    return fit("\n".join([head, "", *_followup_lines(b, snap, now), "", *steps, "", *decision]))


FINISHED = 'This Pursuit looks finished. [I] Reply "close it" to mark it done.'
"""The ending a follow-up proposes when yesterday's Step finished the Pursuit."""
FOLLOWUP_RANK = {"done_word": 0, "done_seen": 0, "skipped_word": 1, "skipped_silence": 1, "held": 2, "no_sign": 3,
                 "not_visible_yet": 4}


def _followup_lines(b: SentBrief, snap: Snapshot, now: datetime) -> list[str]:
    """What became of the last brief's Steps, in its order: done first, then skipped, held, no
    sign, and not visible yet. Steps with no sign that rest today share one line."""
    title = "YESTERDAY'S STEPS" if (b.day - b.follows_up).days == 1 else f"YESTERDAY'S STEPS (from {b.follows_up:%A})"
    pursuits = {p.id: p for p in snap.pursuits()}
    today = {(x.pursuit, x.step) for x in b.steps}
    ordered = sorted(b.followups, key=lambda f: FOLLOWUP_RANK[f.state])
    resting = [pursuits[f.pursuit].name for f in ordered if f.state == "no_sign" and (f.pursuit, f.step) not in today]
    lines = [title]
    for f in ordered:
        p = pursuits[f.pursuit]
        if f.state == "no_sign" and (f.pursuit, f.step) not in today:
            if p.name == resting[0]:
                lines += _resting(resting)
            continue
        lines.append(f"    {_followup(f, p, now)}")
        if f.ending_proposed:
            lines.append(f"      {FINISHED}")
    return lines if b.followups else [title, "    None."]


def _followup(f: Followup, p: Pursuit, now: datetime) -> str:
    step = next(st for st in (p.next_step, *p.past_steps) if st is not None and st.id == f.step)
    if f.state == "done_word":
        picked = _decided_by(p, step.ended.basis.source)
        return f"✓ {p.name}: done, you said so{': ' + picked.text if picked else ''}. [F]"
    if f.state == "done_seen":
        seen = step.ended.basis
        detail = seen.text.strip().rstrip(".")
        at = seen.source.at.astimezone(now.tzinfo)
        if not detail:
            return f"✓ {p.name}: done. Seen in {seen.source.label}, {at:%a %H:%M}. [F]"
        return f"✓ {p.name}: done. Seen in {seen.source.label}, {at:%a %H:%M}:\n      {detail}. [F]"
    if f.state == "skipped_word":
        return f"· {p.name}: skipped, you said so. [F]"
    if f.state == "skipped_silence":
        return f"· {p.name}: no sign of it in your Sessions. [F] Offered twice, so I skipped it. [I]"
    if f.state == "held":
        return f"· {p.name}: held until {step.held_until:%A}, as you asked. [F]"
    if f.state == "not_visible_yet":
        latest = harness_of(max(p.sessions, key=lambda m: m.at).evidence)
        return f"· {p.name}: no sign yet. You work on it in {latest.label}, whose Sessions reach me late. [F]"
    return f"· {p.name}: no sign of it in your Sessions. [F] It is still the next Step. [I]"


def _resting(names: list[str]) -> list[str]:
    them = "it" if len(names) == 1 else "them"
    stay = {1: "It stays open; I'll offer it once more.", 2: "Both stay open; I'll offer each once more."}.get(
        len(names), "All stay open; I'll offer each once more.")
    return [f"    · {', '.join(names)}: no sign of {them} in your Sessions. [F]", f"      {stay} [I]"]


def _decided_by(p: Pursuit, said: Cite) -> Statement | None:
    return next((d for d in p.resume.decided if isinstance(d, Fact) and isinstance(d.source, Cite)
                 and d.source.evidence == said.evidence), None)


def _step_item(p: Pursuit, step_id: str, style: Style, now: datetime, snap: Snapshot) -> list[str]:
    """A Resume Note with no place to work in, as for a web chat, resumes in its first Session to
    reopen: 'Resume: ChatGPT, Session "knee weeks 1 and 2" (Mon 16 Nov). [F]'."""
    step = next(s for s in (p.next_step, *p.past_steps) if s is not None and s.id == step_id)
    lines: list[tuple[str, Statement | Bare]] = [("", step.text)]
    if p.resume.left_off:
        lines.append(("Left off: ", p.resume.left_off[0]))
    if p.resume.where:
        where = p.resume.where[0]
        harness = harness_of(where.source.evidence) if isinstance(where, Fact) and isinstance(where.source, Cite) else None
        lines.append(("Resume: ", Bare(_reworded(where, f"{harness.label} on {where.text}." if harness else f"{where.text}."))))
    elif p.resume.reopen and (session := _session_named(p, p.resume.reopen[0], snap)):
        lines.append(("Resume: ", Bare(session)))
    marked = item([line for _, line in lines], style, now)
    return [prefix + line for (prefix, _), line in zip(lines, marked)]


def _session_named(p: Pursuit, id: EvidenceId, snap: Snapshot) -> Fact | None:
    """The Session as the Pursuit lists it, a record Fact."""
    listed = next((m for m in p.sessions if m.evidence == id), None)
    if listed is None:
        return None
    try:
        ref = snap.session_ref(id)
    except KeyError:
        return None
    harness = harness_of(id)
    text = f'{harness.label if harness else ref.label}, Session "{ref.title}" ({ref.day:%a} {ref.day.day} {ref.day:%b}).'
    return Fact(text, Cite(id, ref.label, listed.at))


def _last(p: Pursuit, style: Style) -> list[str]:
    """The decision's "Last:" line names its source and day, so its Fact is bare (G1)."""
    if not p.resume.left_off:
        return []
    s = p.resume.left_off[0]
    if isinstance(s, Fact) and isinstance(s.source, Cite):
        day = s.source.at.date()
        s = _reworded(s, f"{s.text.rstrip('.')} in {s.source.label} on {day:%a} {day.day} {day:%b}.")
    return [f"Last: {statement(s, style)}"]


def _reworded(s: Statement, text: str) -> Statement:
    return Fact(text, s.source) if isinstance(s, Fact) else type(s)(text, s.basis)


def _telegram_only(style: Style) -> None:
    if style is not Style.TELEGRAM:
        raise NotImplementedError(f"the {style.value} style")


def review(r: SentReview, snap: Snapshot, style: Style) -> str: raise NotImplementedError
def answer(a: Answer, style: Style) -> str: raise NotImplementedError
def outcome(o: Outcome, style: Style) -> str: raise NotImplementedError


def resume_prompt(p: Pursuit, snap: Snapshot) -> str:
    """V3's paste-in prompt, deterministic from the Resume Note: Resuming "<name>", Where, Left
    off, Decided, Open question, Sessions to reopen (Harness, title, day), Next Step. Works in
    any Harness (R3 Q5). Text meant to leave Atlas, so every Fact is a bare [F] (G1). A part
    with nothing in it has no line, and a part with several statements has a line for each."""
    style = Style.TELEGRAM
    lines = [f'Resuming "{p.name}".', *(f"Where: {s.text}" for s in p.resume.where)]
    for label, part in (("Left off", p.resume.left_off), ("Decided", p.resume.decided),
                        ("Open question", p.resume.open_questions)):
        lines += [f"{label}: {statement(s, style)}" for s in part]
    if p.resume.reopen:
        lines.append("Sessions to reopen: " + ", ".join(_reopen(e, snap) for e in p.resume.reopen))
    if p.next_step is not None:
        lines.append(f"Next Step: {_lowered(p.next_step.text.text)}")
    return "\n".join(lines)


def _reopen(id: EvidenceId, snap: Snapshot) -> str:
    ref = snap.session_ref(id)
    harness = harness_of(id)
    return f'{harness.label if harness else ref.label} "{ref.title}" ({ref.day:%a} {ref.day.day} {ref.day:%b})'


def _lowered(step: str) -> str:
    """A Step begins with its verb, which reads in lower case after "Next Step:"."""
    return step[:1].lower() + step[1:] if step[1:2].islower() else step
