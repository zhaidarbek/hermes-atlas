"""When Atlas speaks first (W Q4, W Q17). Hermes gives no plugin timer, so timing is one
no_agent cron job that runs every 10 minutes:

    $HERMES_HOME/scripts/atlas_tick.sh:   #!/bin/sh
                                          exec hermes atlas tick --quiet    # prints nothing

    created once by setup through the public CLI (cron.jobs is a private import). In Hermes
    v0.23.0 the schedule is `cron create`'s positional argument; `--schedule` exists only on
    `cron edit`:
    hermes cron create "*/10 * * * *" --name atlas-tick --no-agent \
        --script atlas_tick.sh --catch-up off --deliver local

Due rules (pure, below). The brief is due iff not paused now, local now >= brief_at, today's
brief file does not exist, and today's date is the local date. That gives late same day and
never the next day. The cron job's own catch-up is off and never matters, because the guard
is the date. Pause does NOT pause the Hermes job, because resume_job fires the elapsed slot.
The tick checks the Pause itself. Changing the brief time edits settings.md only.
If the gateway is down there is no cron, so nothing is sent, which matches "a missed day is
skipped".

Overlap: a tick takes the non-blocking `.atlas/tick.lock` for collect+file. If a long backfill
or slow upkeep holds it, the next tick still runs the send phase on the current Snapshot. The
send phase takes the non-blocking `.atlas/send.lock`: a tick that finds it busy skips sending,
and the next tick finds the brief sent. The brief file's create-if-absent commit stops two
senders that do not share the lock.
"""
from __future__ import annotations

import fcntl
import os
import re
import shlex
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Union
from zoneinfo import ZoneInfo

from . import _hermes, rules
from .model import resend
from .record import Snapshot

JOB = "atlas-tick"
TICK_SCRIPT = "#!/bin/sh\nexec hermes atlas tick --quiet\n"
CRON_CREATE = ("hermes", "cron", "create", "*/10 * * * *", "--name", JOB, "--no-agent",
               "--script", "atlas_tick.sh", "--catch-up", "off", "--deliver", "local")


@dataclass(frozen=True)
class BriefDue:
    day: date


@dataclass(frozen=True)
class ReviewDue:
    week: str


Due = Union[BriefDue, ReviewDue]


def due(snap: Snapshot, now: datetime) -> list[Due]:
    """The brief is due when Atlas is not paused, the local time is at or past brief_at, and
    today's file is missing, or holds an attempt model.RESEND sends again (D Q13). A brief at 23:40
    after downtime still goes out (W Q4: same day); after midnight the date is new.
    Review: not paused, local weekday == review_day, now >= review_at, no review file for this
    ISO week, with the same retry rule. Not built yet (#20)."""
    s = snap.settings()
    local = now.astimezone(ZoneInfo(s.timezone))
    if rules.paused(snap.pauses(), now):
        return []
    today = local.date()
    sent = snap.brief(today)
    first = sent is None and local.time() >= s.brief_at
    again = sent is not None and resend(sent.attempts)
    return [BriefDue(today)] if first or again else []


def install_tick(hermes_home: Path) -> str:
    """Idempotent: writes the script (0755), creates the job unless `hermes cron list` shows
    atlas-tick. Returns a line for the setup report and the rule-13 disclosure. When Hermes
    refuses, the line gives the command for the owner to run instead."""
    script = hermes_home / "scripts" / "atlas_tick.sh"
    if not script.exists() or script.read_text() != TICK_SCRIPT:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(TICK_SCRIPT)
    if script.stat().st_mode & 0o777 != 0o755:
        os.chmod(script, 0o755)
    listed = _hermes.run("cron", "list", "--all")
    if listed.returncode == 0 and re.search(rf"^\s*Name:\s+{JOB}\s*$", listed.stdout, re.M):
        return f"Runs the Hermes cron job {JOB} every 10 minutes."
    created = listed if listed.returncode != 0 else _hermes.run(*CRON_CREATE[1:])
    if created.returncode != 0:
        reason = (created.stderr or created.stdout).strip().splitlines()[-1:] or ["no reason given"]
        return f"Could not create the Hermes cron job {JOB} ({reason[0]}). Run: {shlex.join(CRON_CREATE)}"
    return f"Runs the Hermes cron job {JOB} every 10 minutes."



class TickLease:
    """Context manager: fcntl.flock(LOCK_EX | LOCK_NB) on .atlas/tick.lock; `.held` False if busy.
    A tick never waits, so a long backfill or collect holding it never delays the send phase.
    Backfill waits (`wait=True`) for a tick's collect to finish."""
    NAME = "tick.lock"

    def __init__(self, root: Path, wait: bool = False) -> None:
        self._path = root / ".atlas" / self.NAME
        self._file = None
        self._wait = wait
        self.held = False

    def __enter__(self) -> "TickLease":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a+b")
        try:
            fcntl.flock(self._file, fcntl.LOCK_EX | (0 if self._wait else fcntl.LOCK_NB))
            self.held = True
        except BlockingIOError:
            self.held = False
        return self

    def __exit__(self, *exc) -> None:
        if self.held:
            fcntl.flock(self._file, fcntl.LOCK_UN)
        self._file.close()
        self.held = False


class SendLease(TickLease):
    """Held from planning a message through recording its outcome, so one process sends at a time.
    The OS drops the lock when its holder dies, so a SENDING attempt that a holder finds was left
    by a crash, never by a send still in flight."""
    NAME = "send.lock"
