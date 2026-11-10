"""In-memory Snapshots for the pure modules' tests: the codec renders each value, nothing touches disk."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from atlas.model import (Actor, ChannelSetting, Cite, Fact, HistoryLine, Inference, Kind, Membership, Pause, Profile,
                         Pursuit, ResumeNote, SentBrief, Settings, Status, Step, harness_of)
from atlas.record import Revision, Snapshot, _codec, _layout, _merged_seen, _seal, _seen_bytes, _seen_marks

BERLIN = ZoneInfo("Europe/Berlin")
SETTINGS = Settings(timezone="Europe/Berlin", brief_at=time(7, 30), review_day=7, review_at=time(18, 0),
                    gap=timedelta(minutes=15), machine="ubuntu-pc", harnesses={}, exclusions=(),
                    channel=ChannelSetting("telegram"))


def at(day: date, hm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hm), tzinfo=BERLIN)


def pursuit(id: str, name: str, *, worked: Sequence[date] = (), status: Status = Status.ACTIVE, focus: bool = False,
            starred: bool | None = None, step: str | None = "Take the next small step.", held_until: date | None = None,
            continued: date | None = None, kind: Kind = Kind.PROJECT) -> Pursuit:
    """A Pursuit with a Session on each day in `worked`. `starred` writes the owner's Focus History line;
    it defaults to `focus`."""
    sessions = tuple(Membership(f"cc-{id[2:]}{n:04d}", at(d, "21:00"), True) for n, d in enumerate(worked))
    basis = (sessions[-1].evidence,) if sessions else ("nt-0seed000",)
    left_off = Fact("Left it here.", Cite(basis[0], "Claude Code", at(worked[-1], "21:30"))) if worked else \
        Inference("Named it.", basis)
    history = [HistoryLine("h-0001", at(date(2026, 11, 15), "20:00"), Actor.ATLAS,
                           Inference("Created it from your Sessions.", basis))]
    if focus if starred is None else starred:
        history.append(HistoryLine("h-0002", at(date(2026, 11, 15), "21:00"), Actor.OWNER,
                                   Fact("Added it to Focus on the Board.",
                                        Cite("nt-0board00", "your Note", at(date(2026, 11, 15), "21:00")))))
    next_step = Step("s-20261115-1", Inference(step, basis), date(2026, 11, 15), held_until=held_until) if step else None
    return Pursuit(id=id, name=name, kind=kind, area="work", status=status, focus=focus, created=date(2026, 11, 15),
                   continued=continued, resume=ResumeNote((), (left_off,), (), (), ()), next_step=next_step,
                   past_steps=(), filing_rules=(), sessions=sessions, proposals=(), history=tuple(history))


def snapshot(pursuits: Sequence[Pursuit] = (), *, pauses: Sequence[Pause] = (), briefs: Sequence[SentBrief] = (),
             settings: Settings = SETTINGS, profile: Profile | None = None) -> Snapshot:
    tz = ZoneInfo(settings.timezone)
    files = {"settings.md": _codec.render("settings", settings, None), "pauses.md": _codec.render("pauses", tuple(pauses), tz)}
    if profile is not None:
        files["profile.md"] = _codec.render("profile", profile, tz)
    for p in pursuits:
        refs = {m.evidence: _codec.SessionRef(harness_of(m.evidence).label, m.evidence, m.at.date()) for m in p.sessions}
        files[_layout.pursuit_path(p.id, _layout.slug(p.name))] = _codec.render("pursuit", _codec.PursuitFile(p, refs), tz)
    for b in briefs:
        files[_layout.brief_path(b.day)] = _codec.render("brief", b, tz)
    return Snapshot(Path("/no-record"), Revision(0), {path: _seal.stamp(data) for path, data in files.items()})


def after(decision) -> Snapshot:
    """The Snapshot a commit of the Decision's ChangeSet would leave, in memory."""
    files = dict(decision.changes._snapshot._files)
    for op in decision.changes._ops:
        files[op.path] = op.data
    if decision.changes._seen:
        files[_layout.SEEN] = _seen_bytes(_merged_seen(_seen_marks(files.get(_layout.SEEN)), decision.changes._seen))
    return Snapshot(Path("/no-record"), Revision(decision.changes.base.head + 1),
                    {path: data for path, data in files.items() if data is not None})
