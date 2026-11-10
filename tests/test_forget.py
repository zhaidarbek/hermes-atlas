"""Forget (scene 30): one confirmation, then every copy of the Session's content goes, and nothing else."""
import hashlib
from dataclasses import replace
from datetime import date, timedelta

import pytest

from atlas import forget
from atlas.model import (ConfirmForget, EvidenceId, HarnessId, Inference, Note, NoteOrigin, OwnerMessage, Profile, ProfileEntry,
                         RawPart, RequestForget, SessionEvidence, Turn, Who, part_origin, skip_key)
from atlas.record import FilingRetry, ForgetPlan, Record
from harness import scenes, seeds, world
from harness.home import AtlasHome

ASK = "atlas, forget the Codex Session from Thu 19 Nov 14:02. I pasted a password in it"
CONFIG = seeds.evidence_id(world.by_title("staging deploy config"))
ENVVARS = seeds.evidence_id(world.by_title("staging deploy env vars"))
STAGING_FILE = "pursuits/billing-export-staging-deploy.md"


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        scenes._forgettable(home)
        yield home


def _snap(home):
    return Record.open(home.record_root).snapshot()


def _held(home) -> bool:
    return any(home.record_root.glob(f"evidence/codex/*/*/{CONFIG}"))


def test_any_other_reply_cancels_the_forget(home):
    assert home.telegram(ASK).startswith("Forgetting removes these for good:")
    assert home.telegram("park the staging deploy") is not None
    assert _snap(home).pending_forget() is None
    assert home.telegram("forget") is None
    assert _held(home)


def test_a_reply_atlas_cannot_read_cancels_the_forget_too(home):
    home.telegram(ASK)
    assert home.telegram("hmm, let me think about it") is None
    assert _snap(home).pending_forget() is None
    home.telegram("atlas, forget")
    assert _held(home)


def test_forget_an_hour_later_does_nothing(home):
    home.telegram(ASK)
    home.set_clock(world.FORGET_AT + timedelta(hours=1))
    assert home.telegram("forget") is None
    assert _held(home)


def test_a_stranger_cannot_confirm(home):
    home.telegram(ASK)
    assert home.telegram("forget", sender="4242") is None
    assert _held(home) and _snap(home).pending_forget() is not None


def test_forget_with_nothing_asked_is_left_to_hermes(home):
    before = home.files()
    assert home.telegram("atlas, forget") is None
    assert home.files() == before


def test_no_session_at_that_minute_is_said_and_nothing_waits(home):
    reply = home.telegram("atlas, forget the Codex Session from Thu 19 Nov 14:03")
    assert reply == "I found no Codex Session from Thu 19 Nov 14:03."
    assert _snap(home).pending_forget() is None and _held(home)


def test_a_pursuit_made_from_it_alone_goes_with_it_and_so_does_a_day_left_without_time(home):
    record = Record.open(home.record_root)
    snap = record.snapshot()
    staging = snap.pursuit(seeds.STAGING)
    alone = replace(staging, sessions=tuple(m for m in staging.sessions if m.evidence == CONFIG),
                    resume=replace(staging.resume, reopen=(CONFIG,)))
    payments = snap.pursuit("p-2x7b")
    flake = seeds.evidence_id(world.by_title("flake fix merged"))
    record.commit(snap.changes().put_pursuit(alone).put_pursuit(
        replace(payments, sessions=tuple(m for m in payments.sessions if m.evidence != flake)))
        .put_profile(Profile(("work",), (ProfileEntry(Inference("You deploy on Thursdays.", (CONFIG,)),
                                                       date(2026, 11, 19), Who.LIBRARIAN),
                                          ProfileEntry(Inference("You work late.", (flake,)),
                                                       date(2026, 11, 19), Who.LIBRARIAN)))))
    preview = home.telegram(ASK)
    assert "- the Pursuit Billing export: staging deploy, made from it alone" in preview
    assert "- 1 entry in your Profile" in preview
    assert home.telegram("forget") == "Forgotten."
    after = _snap(home)
    assert seeds.STAGING not in {p.id for p in after.pursuits()}
    assert after.journal(seeds.THURSDAY) is None
    assert [e.statement.text for e in after.profile().entries] == ["You work late."]


def test_the_next_collect_never_reads_it_again(home):
    home.telegram(ASK)
    home.telegram("forget")
    home.set_clock(world.FORGET_AT + timedelta(minutes=10))
    assert home.cli("atlas", "tick", "--quiet").code == 0
    assert not _held(home)
    assert ENVVARS in {h.id for h in _snap(home).held().sessions()}


@pytest.mark.parametrize("text, selector", [
    ("forget the Codex Session from Thu 19 Nov 14:02. I pasted a password in it", "codex 2026-11-19T14:02:00+01:00"),
    ("forget the codex session from 19 Nov 14:02", "codex 2026-11-19T14:02:00+01:00"),
    ("Forget my Claude Code session on Fri 20 November 2026 9:15", "claude_code 2026-11-20T09:15:00+01:00"),
    ("forget the Codex Session from 21 Dec 10:00", "codex 2025-12-21T10:00:00+01:00"),
])
def test_the_ask_names_a_harness_and_a_minute(home, text, selector):
    msg = OwnerMessage(text, world.FORGET_AT, "telegram", None, None)
    assert forget.interpret(msg, _snap(home)) == RequestForget(selector)


@pytest.mark.parametrize("text", ["forget the Gemini Session from 19 Nov 14:02", "forget it", "forget about it",
                                  "forget the Codex Session from 31 Feb 10:00"])
def test_other_words_are_no_forget(home, text):
    assert forget.interpret(OwnerMessage(text, world.FORGET_AT, "telegram", None, None), _snap(home)) is None


def test_only_the_bare_word_confirms(home):
    record = Record.open(home.record_root)
    snap = record.snapshot()
    record.commit(snap.changes().set_pending_forget(ForgetPlan((CONFIG,), world.FORGET_AT)))
    say = lambda text: forget.interpret(OwnerMessage(text, world.FORGET_AT, "telegram", None, None), _snap(home))
    assert say(" Forget ") == ConfirmForget()
    assert say("forget it") is None and say("forget!") is None


def test_a_session_folded_into_it_is_never_collected_again():
    parent, child = "thread-parent", "thread-child"
    s = SessionEvidence(EvidenceId("cx-aaaa"), HarnessId.CODEX, "T3 Code", "local", parent, "t", world.FORGET_AT,
                        world.FORGET_AT, "m", None, None,
                        (RawPart("main.jsonl.zst", "", "~/.codex/x", "f", part_origin(HarnessId.CODEX, parent, "main")),
                         RawPart(f"t3-child-{child}.jsonl.zst", "", "~/.codex/y", "f",
                                 part_origin(HarnessId.CODEX, child, "main"))),
                        (Turn("agent", world.FORGET_AT, "hi", f"t3-child:{child}"),))
    assert forget.folded_keys(s) == {skip_key(HarnessId.CODEX, child)}
    assert skip_key(HarnessId.CODEX, child) == "sha256:" + hashlib.sha256(f"codex:{child}".encode()).hexdigest()


def _hand_edit(home, path: str, line: str) -> None:
    file = home.record_root / path
    file.write_text(file.read_text() + line)


def _everywhere(root, needle: str) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and needle.encode() in p.read_bytes())


def test_a_hand_edit_note_that_copied_text_from_it_goes_and_no_other_note_changes(home):
    root = home.record_root
    record = Record.open(root)
    snap = record.snapshot()
    other = Note(EvidenceId("nt-invented"), world.FORGET_AT - timedelta(days=1), NoteOrigin.HAND_EDIT, "profile.md",
                 "- an invented edit\n", diff="+- an invented edit\n", derived_from=(ENVVARS,))
    record.commit(snap.changes().append_note(other))
    kept = sorted(root.glob("**/2026-11-19-hand-edits.md"))
    kept_bytes = [p.read_bytes() for p in kept]
    home.set_clock(world.FORGET_AT - timedelta(minutes=5))
    _hand_edit(home, STAGING_FILE, "- invented line typed by hand\n")
    _hand_edit(home, "pauses.md", "invented pause typed by hand\n")
    assert home.cli("atlas", "tick", "--quiet").code == 0
    edits = {n.via: n for n in _snap(home).pending_applies()}
    assert CONFIG in edits[STAGING_FILE].derived_from
    assert CONFIG not in edits["pauses.md"].derived_from
    home.set_clock(world.FORGET_AT)

    preview = home.telegram(ASK)
    assert "- 1 hand-edit Note that copied text from it, with the edit typed there" in preview
    assert home.telegram("forget") == "Forgotten."
    record = Record.open(root)
    for minutes in (10, 20):
        home.set_clock(world.FORGET_AT + timedelta(minutes=minutes))
        assert home.cli("atlas", "tick", "--quiet").code == 0

    after = _snap(home)
    assert [n.id for n in after.notes() if n.origin is NoteOrigin.HAND_EDIT] == [other.id, edits["pauses.md"].id]
    assert [n for n in after.notes() if n.id == edits["pauses.md"].id] == [edits["pauses.md"]]
    assert [n.via for n in after.pending_applies()] == ["profile.md", "pauses.md"]
    assert [p.read_bytes() for p in kept] == kept_bytes and len(kept) == 2
    native = world.by_title("staging deploy config").native_key
    assert _everywhere(root, CONFIG) == [] and _everywhere(root, native) == []
    assert _everywhere(root, skip_key(HarnessId.CODEX, native)) == [".atlas/last-written/forgotten.md", "forgotten.md"]


def _forget_and_tick(home):
    preview = home.telegram(ASK)
    assert home.telegram("forget") == "Forgotten."
    for minutes in (10, 20):
        home.set_clock(world.FORGET_AT + timedelta(minutes=minutes))
        assert home.cli("atlas", "tick", "--quiet").code == 0
    return preview


def test_a_hand_edit_that_deleted_the_sessions_lines_goes_too(home):
    root = home.record_root
    file = root / STAGING_FILE
    file.write_text("".join(line for line in file.read_text().splitlines(keepends=True) if CONFIG not in line))
    home.set_clock(world.FORGET_AT - timedelta(minutes=5))
    assert home.cli("atlas", "tick", "--quiet").code == 0
    [edit] = [n for n in _snap(home).pending_applies() if n.via == STAGING_FILE]
    assert CONFIG not in edit.text and CONFIG in edit.diff and CONFIG in edit.derived_from
    home.set_clock(world.FORGET_AT)

    assert "- 1 hand-edit Note that copied text from it, with the edit typed there" in _forget_and_tick(home)
    assert [n for n in _snap(home).notes() if n.id == edit.id] == []
    native = world.by_title("staging deploy config").native_key
    assert _everywhere(root, CONFIG) == [] and _everywhere(root, native) == []


def test_a_hand_edit_note_on_disk_whose_diff_alone_cites_it_goes_and_an_owner_note_naming_it_stays(home):
    root = home.record_root
    record = Record.open(root)
    at = world.FORGET_AT - timedelta(days=1)
    written = Note(EvidenceId("nt-written"), at, NoteOrigin.HAND_EDIT, STAGING_FILE,
                   "- an invented line\n", diff=f"-- an invented Fact [F {CONFIG}]\n")
    owner = Note(EvidenceId("nt-owner"), at, NoteOrigin.OWNER, "telegram", f"invented words naming {CONFIG}\n")
    record.commit(record.snapshot().changes().append_note(written).append_note(owner))
    told = sorted(root.glob("**/2026-11-19-telegram.md"))
    told_bytes = [p.read_bytes() for p in told]
    home.set_clock(world.FORGET_AT)

    assert "- 1 hand-edit Note that copied text from it, with the edit typed there" in _forget_and_tick(home)
    assert [n.id for n in _snap(home).notes() if n.id in (written.id, owner.id)] == [owner.id]
    assert [p.read_bytes() for p in told] == told_bytes and len(told) == 2
    assert _everywhere(root, CONFIG) == [str(p.relative_to(root)) for p in sorted(told)]


def test_after_a_find_and_a_hand_edit_citing_it_no_file_holds_its_id_but_the_skip_key(home):
    root = home.record_root
    index = root / ".atlas" / "index.sqlite"
    assert _snap(home).find("staging deploy config")
    assert CONFIG.encode() in index.read_bytes()
    home.set_clock(world.FORGET_AT - timedelta(minutes=5))
    _hand_edit(home, STAGING_FILE, "- invented line typed by hand\n")
    assert home.cli("atlas", "tick", "--quiet").code == 0
    assert any(CONFIG in n.derived_from for n in _snap(home).pending_applies())
    home.set_clock(world.FORGET_AT)

    assert "- 1 hand-edit Note that copied text from it, with the edit typed there" in _forget_and_tick(home)
    native = world.by_title("staging deploy config").native_key
    assert index.exists()
    assert _everywhere(root, CONFIG) == [] and _everywhere(root, native) == []
    assert _everywhere(root, skip_key(HarnessId.CODEX, native)) == [".atlas/last-written/forgotten.md", "forgotten.md"]


def test_a_session_waiting_out_a_filing_back_off_leaves_no_trace_of_it(home):
    root = home.record_root
    record = Record.open(root)
    waiting = FilingRetry(world.FORGET_AT - timedelta(hours=1), 1, world.FORGET_AT + timedelta(hours=1))
    record.commit(record.snapshot().changes().set_filing_retries({CONFIG: waiting, ENVVARS: waiting}))
    assert _everywhere(root, CONFIG) != []

    _forget_and_tick(home)
    assert _everywhere(root, CONFIG) == []
    assert set(_snap(home).filing_retries()) == {ENVVARS}
