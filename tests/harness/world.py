"""The fixture world: the tutorial's invented week at reduced scale.

Every Session the tutorial mentions or implies, with the Harness, folder and times the
tutorial gives. Counts the tutorial states at full scale (412 Claude Code Sessions since 2 Jun)
are checked against this world's own counts, which `owner_sessions` computes from the table
below and never from Atlas.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from atlas.model import HarnessId

TZ = ZoneInfo("Europe/Berlin")
MACHINE_IN_TUTORIAL = "ubuntu-pc"
SETUP_AT = datetime(2026, 11, 15, 20, 0, tzinfo=TZ)        # Sunday evening, before "atlas, merge" at 20:39
CORRECTED_AT = datetime(2026, 11, 15, 20, 39, tzinfo=TZ)    # "atlas, merge the two greenhouse ones"
FIRST_BRIEF_AT = datetime(2026, 11, 16, 7, 30, tzinfo=TZ)  # Monday, the brief time kept from setup
PROMPT_AT = datetime(2026, 11, 16, 8, 4, tzinfo=TZ)        # "1 prompt", 40 s before Codex starts at 08:05
DONE_AT = datetime(2026, 11, 16, 8, 34, tzinfo=TZ)         # "1 done, 10/10 pass", after the 08:05 Codex run
SEEN_TICK_AT = datetime(2026, 11, 16, 22, 50, tzinfo=TZ)     # the tick after Monday evening's T3 Code Session
TUESDAY_BRIEF_AT = datetime(2026, 11, 17, 7, 30, tzinfo=TZ)
HOLD_AT = datetime(2026, 11, 17, 8, 10, tzinfo=TZ)         # "3 remind me friday. drop the bike planner"
TABLET_AT = datetime(2026, 11, 17, 20, 45, tzinfo=TZ)      # "2 done. tablet app, nothing fancy", in the evening
WEDNESDAY_BRIEF_AT = datetime(2026, 11, 18, 7, 30, tzinfo=TZ)
KIND_TICK_AT = datetime(2026, 11, 18, 15, 0, tzinfo=TZ)     # the tick after Wednesday's reading tracker Session
ENDING_TICK_AT = datetime(2026, 11, 18, 16, 50, tzinfo=TZ)  # the tick after Wednesday's CI flakes Session
THURSDAY_BRIEF_AT = datetime(2026, 11, 19, 7, 30, tzinfo=TZ)
CLOSE_AT = datetime(2026, 11, 19, 7, 45, tzinfo=TZ)        # "close it. continue the backups"
NEGLECTED_AT = datetime(2026, 11, 19, 12, 5, tzinfo=TZ)    # "atlas, what have I neglected?", before the find
FRIDAY_BRIEF_AT = datetime(2026, 11, 20, 7, 30, tzinfo=TZ)
SKIP_AT = datetime(2026, 11, 20, 9, 45, tzinfo=TZ)         # "skip 2, not today", after the 09:15 billing Step
FORGET_AT = datetime(2026, 11, 20, 13, 5, tzinfo=TZ)       # "atlas, forget the Codex Session from Thu 19 Nov 14:02"
HAND_EDIT_AT = datetime(2026, 11, 20, 8, 47, tzinfo=TZ)     # profile.md edited by hand before the 08:50 tick
PROFILE_AT = datetime(2026, 11, 20, 13, 2, tzinfo=TZ)      # "atlas, profile: ...", once Hermes is back after lunch
FIND_AT = datetime(2026, 11, 19, 12, 10, tzinfo=TZ)        # Thursday's questions: find the GPX Session (scene 22)
OUTSIDE_AT = datetime(2026, 11, 19, 12, 20, tzinfo=TZ)     # "atlas, send Dana ..." (scene 24)
WEEK_TIME_AT = datetime(2026, 11, 22, 18, 40, tzinfo=TZ)   # after Sunday's Review: time by Area (scene 37)
RESUME_AT = datetime(2026, 11, 22, 18, 45, tzinfo=TZ)      # "where did I leave off on the keyboard firmware?" (38)
REACTIVATED_AT = datetime(2026, 11, 22, 17, 0, tzinfo=TZ)   # "by evening", after the keyboard build Session
WEEK_START = datetime(2026, 11, 15, 19, 0, tzinfo=TZ)
WEEK_END = datetime(2026, 11, 22, 23, 59, tzinfo=TZ)

PASTED_PASSWORD = "Lantern7-invented"      # scene 30: masked in every derived text, kept only in raw/
PLANTED = "the quartz heron rollout"       # scene 30: words only the Session to forget holds
CC, CX, DV, HM = HarnessId.CLAUDE_CODE, HarnessId.CODEX, HarnessId.DEVIN, HarnessId.HERMES
GPT = HarnessId.CHATGPT


@dataclass(frozen=True)
class Turn:
    at: datetime
    owner: bool
    text: str


@dataclass(frozen=True)
class WorldSession:
    harness: HarnessId
    title: str
    cwd: str                                 # "~/..." under the throwaway HOME
    start: datetime
    minutes: int
    branch: str | None = None
    interface: str | None = None             # "T3 Code": run through the Interface, one Session under its Harness
    subagents: int = 0                       # the Harness's own subagent parts (folded, never counted)
    source: str = "cli"                      # Hermes: "telegram", "cli", "cron", "subagent"
    parent: str | None = None                # a child's parent title: Codex thread_spawn, Hermes subagent session
    engaged: bool = False                    # the owner writes every 10 minutes until it ends, so all of it is
                                             # Active Time (the tutorial's Monday, at full scale)
    pasted: str | None = None                # what the owner pasted a minute in, a secret Forget removes
    said: tuple[tuple[int, str], ...] = ()   # the agent's words a scene quotes, by minutes after the start

    @property
    def native(self) -> str:
        seed = f"{self.harness.value}:{self.title}:{self.start.isoformat()}"
        if self.harness is DV:
            return "-".join(self.title.split()[:3]) + "-" + hashlib.sha256(seed.encode()).hexdigest()[:6]
        if self.harness is HM:
            return f"{self.start:%Y%m%d_%H%M%S}_{hashlib.sha256(seed.encode()).hexdigest()[:6]}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))

    @property
    def native_key(self) -> str:
        """The key collection names it by: a Devin slug adds the creation time its store keeps, in
        epoch seconds."""
        return f"{self.native}@{int(self.start.timestamp())}" if self.harness is DV else self.native

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)

    @property
    def is_owner_session(self) -> bool:
        """A Session in the glossary's sense: not a subagent child, not a cron run."""
        return self.parent is None and self.source not in ("cron", "subagent")

    def turns(self) -> tuple[Turn, ...]:
        if self.engaged:
            asks = [self.start + timedelta(minutes=m) for m in range(0, self.minutes, 10)]
            return (*(t for n, at in enumerate(asks) for t in (
                        Turn(at, True, f"Let us start on {self.title}." if n == 0 else f"Go on with part {n} of it."),
                        Turn(at + timedelta(minutes=2), False, f"Working on part {n} now."))),
                    Turn(self.end, False, f"Done for now: {self.title}."))
        middle = self.start + timedelta(minutes=self.minutes // 2)
        pasted = (Turn(self.start + timedelta(minutes=1), True, self.pasted),) if self.pasted else ()
        said = (Turn(self.start + timedelta(minutes=m), False, text) for m, text in self.said)
        return tuple(sorted((Turn(self.start, True, self.title), *pasted, Turn(self.start + timedelta(minutes=3), False,
                                                                                f"Looking at {self.title}."),
                             Turn(middle, True, "Go on."), *said, Turn(self.end, False, f"Done for now: {self.title}.")),
                            key=lambda t: t.at))


def utc(at: datetime) -> str:
    """A time as the stores write it."""
    return at.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _at(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def _s(harness, start, minutes, title, cwd, **kw) -> WorldSession:
    return WorldSession(harness, title, cwd, _at(start), minutes, **kw)


SESSIONS: tuple[WorldSession, ...] = (
    # Keyboard firmware, parked: last worked Sun 30 Aug.
    _s(CC, "2026-06-02 21:10", 60, "keyboard matrix scan", "~/code/kb-firmware", subagents=1),
    _s(CC, "2026-06-20 21:30", 45, "split layout keymap", "~/code/kb-firmware"),
    _s(CX, "2026-08-28 20:30", 30, "qmk build", "~/code/kb-firmware", branch="split-right"),
    _s(CC, "2026-08-30 21:15", 50, "right half drops", "~/code/kb-firmware", branch="split-right"),
    # Payments CI flakes: Codex since 9 Jun; Sat 22:10 in Claude Code (scene 8 reopens both).
    _s(CX, "2026-06-09 08:00", 40, "payments api retries", "~/work/payments-api", branch="main"),
    _s(CX, "2026-11-12 11:00", 35, "ci matrix", "~/work/payments-api", branch="main"),
    _s(CX, "2026-11-14 10:00", 20, "redis db per worker", "~/work/payments-api", branch="fix/retry-flake"),
    _s(CX, "2026-11-14 10:05", 8, "check redis config", "~/work/payments-api", parent="redis db per worker"),
    _s(CC, "2026-11-14 21:30", 40, "retry flake", "~/work/payments-api", branch="fix/retry-flake", subagents=2),
    # Static site migration, parked: Devin since 15 Jul; last Thu 3 Sep.
    _s(DV, "2026-07-15 14:00", 40, "static site image pipeline", "~/code/site"),
    _s(CC, "2026-09-03 20:40", 35, "move posts to astro", "~/code/site"),
    # Billing export service: T3 Code since July; Fri 18:02 via T3 Code.
    _s(CX, "2026-07-30 09:00", 30, "ledger csv export", "~/work/billing-export", interface="T3 Code"),
    _s(CX, "2026-07-30 09:10", 6, "ledger column types", "~/work/billing-export", parent="ledger csv export",
       interface="T3 Code"),
    _s(CX, "2026-11-12 16:20", 30, "staging deploy env vars", "~/work/billing-export", branch="deploy-staging"),
    _s(CC, "2026-11-13 17:40", 22, "pdf vat rounding", "~/work/billing-export", branch="pdf-vat", interface="T3 Code"),
    # Pi-hole DNS loop, parked: Fri 14 Aug.
    _s(CC, "2026-08-14 22:00", 40, "pi-hole dns loop", "~/home/pihole"),
    _s(DV, "2026-09-20 15:00", 30, "dns forwarding notes", "~/home/pihole"),
    # Agent-tooling newsletter: Mon 19 Oct, Claude Code.
    _s(CC, "2026-10-19 21:05", 30, "newsletter issue topics", "~/writing/newsletter"),
    # Bike route planner: Codex, Sun 25 and Mon 26 Oct.
    _s(CX, "2026-10-25 19:52", 40, "gpx elevation smoothing", "~/code/bike-routes", branch="main",
       said=((22, "Smoothing works: a 30-point rolling median, then spikes over 8 m removed."),)),
    _s(CX, "2026-10-26 20:10", 30, "gpx tunnel gaps", "~/code/bike-routes", branch="main"),
    # Home server backups: Thu 29 Oct, in Devin.
    _s(DV, "2026-10-29 21:30", 40, "restic nightly backups", "~/home/backups"),
    # Reading tracker for the kids: Fri 6 Nov.
    _s(CC, "2026-11-06 20:10", 30, "reading tracker ideas", "~/family/reading"),
    # Greenhouse dashboard Mon 9 Nov; logger firmware Wed 11 Nov 17:25.
    _s(CC, "2026-11-09 21:00", 45, "greenhouse humidity chart", "~/home/greenhouse/dashboard"),
    _s(CC, "2026-11-11 17:00", 25, "logger sd card swap", "~/home/greenhouse/firmware", subagents=1),
    # Hermes since 6 Nov.
    _s(HM, "2026-11-06 19:00", 15, "shelf bracket sizes", "~", source="telegram"),
    _s(HM, "2026-11-10 08:00", 10, "summarise backup logs", "~/home/backups", source="cli"),
    _s(HM, "2026-11-10 08:04", 2, "read backup log", "~/home/backups", source="subagent", parent="summarise backup logs"),
    _s(HM, "2026-11-12 06:00", 1, "nightly digest", "~", source="cron"),
    # The week. Monday's Journal (scene 27) at full scale: Payments CI flakes 1 h 10, Billing export
    # service 1 h 55 with the shared 12:30 Session split between them, a 10-minute one-off.
    _s(CX, "2026-11-16 08:05", 60, "test_retry one worker", "~/work/payments-api", branch="fix/retry-flake",
       engaged=True),
    _s(CX, "2026-11-16 12:30", 20, "invoice ids in payments", "~/work/payments-api", engaged=True),
    _s(CC, "2026-11-16 13:10", 10, "invoice number regex", "~/work/payments-api", engaged=True),
    _s(CC, "2026-11-16 21:00", 105, "per-invoice vat rounding", "~/work/billing-export", branch="pdf-vat",
       interface="T3 Code", engaged=True),
    _s(CC, "2026-11-17 21:10", 15, "greenhouse log interval", "~/home/greenhouse/firmware"),
    _s(CC, "2026-11-18 10:12", 30, "rotate staging db password", "~/work/billing-export", branch="deploy-staging"),
    _s(CX, "2026-11-18 11:00", 15, "rotate staging db password again", "~/work/billing-export",
       branch="deploy-staging"),
    _s(CX, "2026-11-18 14:00", 40, "reading tracker app screen", "~/family/reading", engaged=True),
    _s(CX, "2026-11-18 15:35", 40, "suite 50 runs per worker", "~/work/payments-api", branch="fix/retry-flake",
       said=((30, "test_retry passed 50 of 50 runs with a Redis db per worker."),)),
    _s(HM, "2026-11-18 19:30", 10, "dinner plan", "~", source="telegram"),
    _s(CX, "2026-11-19 08:10", 30, "flake fix merged", "~/work/payments-api", branch="fix/retry-flake"),
    _s(CX, "2026-11-19 14:02", 20, "staging deploy config", "~/work/billing-export", branch="deploy-staging",
       pasted=f"the staging db password is {PASTED_PASSWORD}", said=((10, f"Config set for {PLANTED}."),)),
    _s(CC, "2026-11-20 21:05", 25, "greenhouse evening check", "~/home/greenhouse/firmware"),
    _s(CC, "2026-11-22 09:30", 40, "keyboard debounce 10 ms", "~/code/kb-firmware"),
)


# How the scripted model (harness/model.py) groups the Sessions when backfill builds the Pursuits:
# each Pursuit on the tutorial's Board with its Area and Sessions, and the one-off Sessions with
# theirs. Names, Kinds, Left off and Next lines are read from the tutorial's Board. The Areas are
# setup's three, because "home" comes with scene 5.
BACKFILL_PURSUITS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Billing export service": ("work", ("ledger csv export", "staging deploy env vars", "pdf vat rounding")),
    "Payments CI flakes": ("work", ("payments api retries", "ci matrix", "redis db per worker", "retry flake")),
    "Greenhouse logger firmware": ("family", ("logger sd card swap",)),
    "Greenhouse dashboard": ("family", ("greenhouse humidity chart",)),
    "Reading tracker for the kids": ("family", ("reading tracker ideas",)),
    "Agent-tooling newsletter": ("work", ("newsletter issue topics",)),
    "Bike route planner": ("family", ("gpx elevation smoothing", "gpx tunnel gaps")),
    "Home server backups": ("family", ("restic nightly backups",)),
    "Keyboard firmware": ("family", ("keyboard matrix scan", "split layout keymap", "qmk build", "right half drops")),
    "Static site migration": ("work", ("static site image pipeline", "move posts to astro")),
    "Pi-hole DNS loop": ("family", ("pi-hole dns loop",)),
}
# Web chats reach Atlas through an export (#15), never a store: Monday's knee question, 40 minutes.
WEB_SESSIONS: tuple[WorldSession, ...] = (
    _s(GPT, "2026-11-16 19:10", 40, "knee physio weeks 1 and 2", "~", engaged=True),
)
MONDAY_PURSUITS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Payments CI flakes": ("work", ("test_retry one worker", "invoice ids in payments")),
    "Billing export service": ("work", ("invoice ids in payments", "per-invoice vat rounding")),
    "Knee rehab plan": ("health", ("knee physio weeks 1 and 2",)),
}
MONDAY_ONE_OFFS: dict[str, str] = {"invoice number regex": "work"}

BACKFILL_ONE_OFFS: dict[str, str] = {"dns forwarding notes": "family", "summarise backup logs": "family",
                                     "shelf bracket sizes": "family"}


# How the scripted model (harness/model.py) files the Sessions the tick reads after backfill
# (scenes 9b, 14, 15, 19, 39): the Pursuit by its name, and what each Session shows. A Step seen done
# names the minute of the turn that shows it and what the follow-up says about it.
@dataclass(frozen=True)
class TickFiling:
    pursuit: str
    left_off: str | None = None
    done_at: str | None = None               # "21:40": the owner's turn that shows the open Step done
    done: str = ""
    next_step: str | None = None
    kind: tuple[str, str] | None = None      # (Kind, the reason the Session shows)
    ending: tuple[str, str] | None = None    # (done or dropped, the reason the Session shows)


TICK_FILINGS: dict[str, TickFiling] = {
    "test_retry one worker": TickFiling("Payments CI flakes"),
    "invoice ids in payments": TickFiling("Payments CI flakes"),
    "per-invoice vat rounding": TickFiling(
        "Billing export service", done_at="21:40", done="per-invoice rounding matches the ledger",
        next_step="Switch the PDF export to per-invoice rounding and rerun October."),
    "greenhouse log interval": TickFiling("Greenhouse logger"),
    "rotate staging db password": TickFiling("Billing export: staging deploy"),
    "rotate staging db password again": TickFiling("Billing export: staging deploy"),
    "reading tracker app screen": TickFiling(
        "Reading tracker for the kids", left_off="The app logs a book and minutes on one screen", done_at="14:30",
        next_step='Add the "one sentence" field', kind=("project", "it has its own repository now")),
    "suite 50 runs per worker": TickFiling(
        "Payments CI flakes", done_at="16:05", done="test_retry passed 50 of 50 runs with a Redis db per worker",
        ending=("done", "The flake is gone in 50 of 50 runs, which is what this Pursuit was for.")),
    "flake fix merged": TickFiling("Payments CI flakes"),
    "staging deploy config": TickFiling("Billing export: staging deploy"),
    "greenhouse evening check": TickFiling("Greenhouse logger"),
    "keyboard debounce 10 ms": TickFiling("Keyboard firmware"),
}


def visible(now: datetime) -> tuple[WorldSession, ...]:
    """Sessions written to their store by `now`: a Session appears once it has started."""
    return tuple(s for s in SESSIONS if s.start <= now)


def owner_sessions(harness: HarnessId, now: datetime) -> tuple[int, date | None]:
    """This world's own count and first day for one Harness, as the tutorial counts them."""
    mine = [s for s in visible(now) if s.harness is harness and s.is_owner_session]
    return len(mine), min((s.start.date() for s in mine), default=None)


def folded(now: datetime) -> int:
    """Subagent conversations folded into the Sessions that started them: a Harness's own
    subagent parts, and child Sessions whose parent is a Session (a cron run's child is not)."""
    sessions = visible(now)
    parents = {s.title for s in sessions if s.is_owner_session}
    return sum(s.subagents for s in sessions if s.is_owner_session) + sum(s.parent in parents for s in sessions)


def via_interface(now: datetime) -> tuple[WorldSession, ...]:
    return tuple(s for s in visible(now) if s.is_owner_session and s.interface)


def by_title(title: str) -> WorldSession:
    return next(s for s in (*SESSIONS, *WEB_SESSIONS) if s.title == title)
