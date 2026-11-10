import random
import re
import string
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from atlas.model import (ONE_OFF, Actor, BriefStep, ChannelSetting, Cite, Count, DayTime, DecisionAsk, Done,
                         EndingProposal, Fact, JournalDay,
                         FocusProposal, Followup, HarnessId, HarnessSetting, HistoryLine, Inference, InterfaceSetting,
                         Kind, Membership, Note, NoteOrigin, Pattern, Pause, PauseCmd, Profile, ProfileEntry, Pursuit,
                         ReopenProposal, ResumeNote, SendAttempt, SendStatus, SentBrief, SessionCard, Settings, SetStatus, Skipped,
                         Status, Step, Who, EditResumeNote, HoldStep, MarkStep, sent_sha256)
from atlas.record import _codec, _layout, _seal
from layout_examples import examples

EXAMPLES = examples()
TZ = ZoneInfo(_codec.parse("settings", EXAMPLES["settings.md"].encode(), None).timezone)
ROUND_TRIPS = ["settings.md", "profile.md", "pauses.md", "forgotten.md", "pursuits/billing-export-staging-deploy.md",
               "notes/2026/11/2026-11-15-telegram.md", "notes/2026/11/2026-11-17-claude-code.md",
               "notes/2026/11/2026-11-20-hand-edits.md", "briefs/2026/11/2026-11-20.md", "reviews/2026/2026-W47.md",
               "evidence/codex/2026/10/cx-kxmhgr73ardh6q4s/session.md", "evidence/codex/2026/10/cx-kxmhgr73ardh6q4s/card.md",
               "journal/2026-11-16.md"]


def round_trip(path: str, text: str) -> bytes:
    kind = _layout.kind_of(path)
    return _codec.render(kind, _codec.parse(kind, text.encode(), TZ), TZ)


@pytest.mark.parametrize("path", ROUND_TRIPS)
def test_example_round_trips_byte_for_byte(path):
    assert round_trip(path, EXAMPLES[path]) == _seal.blanked(EXAMPLES[path].encode())


def test_readme_begins_with_the_documented_excerpt():
    excerpt = EXAMPLES["README.md"].split("\n...\n")[0]
    rendered = _seal.stamp(_codec.render("readme", None, None)).decode()
    assert _seal.blanked(rendered.encode()).decode().startswith(_seal.blanked(excerpt.encode()).decode())


def test_readme_lists_who_may_issue_each_command():
    rendered = _codec.render("readme", None, None).decode()
    assert "- SetFocus: you\n" in rendered
    assert "- AddNote: you, an agent you told, an agent\n" in rendered


@pytest.mark.parametrize("path", [p for p in ROUND_TRIPS if _layout.kind_of(p) != "session"] + ["README.md"])
def test_the_seal_verifies_and_breaks_on_any_edit(path):
    kind = _layout.kind_of(path)
    value = None if kind == "readme" else _codec.parse(kind, EXAMPLES[path].encode(), TZ)
    sealed = _seal.stamp(_codec.render(kind, value, TZ))
    assert _seal.verifies(sealed)
    assert not _seal.verifies(sealed + b"edited by hand\n")


def test_example_seals_are_placeholders_that_do_not_verify():
    assert not _seal.verifies(EXAMPLES["settings.md"].encode())


def test_settings_value_round_trips():
    s = Settings(
        timezone="Europe/Berlin", brief_at=time(6, 45), review_day=6, review_at=time(19, 0),
        gap=timedelta(minutes=20), machine="box",
        harnesses={HarnessId.CODEX: HarnessSetting(True, "store", ("~/.codex", "/srv/orca/codex"), "owner")},
        exclusions=("~/private",), channel=ChannelSetting("telegram", "telegram:-100:7"),
        interfaces={"t3_code": InterfaceSetting("probe", "~/.t3")})
    assert _codec.parse("settings", _codec.render("settings", s, None), None) == s


def test_profile_entries_keep_who_added_them():
    p = Profile(("work", "side projects"), (
        ProfileEntry(Fact("You review on Sundays.", Cite("nt-1", "your Note", datetime(2026, 11, 20, 9, 0, tzinfo=TZ))),
                     date(2026, 11, 20), Who.OWNER),
        ProfileEntry(Inference("You start late on Mondays.", ("cc-1", "cx-2")), date(2026, 11, 16), Who.LIBRARIAN)))
    assert _codec.parse("profile", _codec.render("profile", p, TZ), TZ) == p


def test_a_multi_line_note_round_trips():
    nf = _codec.NotesFile(date(2026, 11, 15), "telegram", (_codec.NoteEntry(
        Note("nt-1", datetime(2026, 11, 15, 20, 39, tzinfo=TZ), NoteOrigin.OWNER, "telegram", "line one\nline two",
             atlas_reply="ok\nand more")),))
    assert _codec.parse("notes", _codec.render("notes", nf, TZ), TZ) == nf


@pytest.mark.parametrize("line_break", ["\n", "\u2028", "\x85"])
def test_a_line_break_in_a_statement_is_written_as_a_space(line_break):
    at = datetime(2026, 10, 25, 19, 52, tzinfo=TZ)
    card = SessionCard("cx-1", Inference("a", ("cx-1",)), Inference("b", ("cx-1",)), None,
                       Fact(f"~/code/a{line_break}b.", Cite("cx-1", "Codex", at)))
    assert _codec.parse("card", _codec.render("card", card, TZ), TZ).where.text == "~/code/a b."


ODD = ["\x00", "\t", "\n", "\r", "\x0b", "\x0c", "\x1c", "\x7f", "\x80", "\x85", "\x9f", "\xa0", "\u2028", "\u2029",
       "\ufeff", "\ufffe", "\uffff", "\ufdd0", "\ud83dx", "\udc80x", "\U0001f600", "\u6f22", "e\u0301", "\u202e",
       "\u200b", "\U0010fffe", " · ", "---", "\n## Steps\n", "[I · cc-2]", ": ", "- ", "\\u0085", '"']


def any_text(rng: random.Random) -> str:
    return "".join(rng.choice(ODD) if rng.random() < 0.3 else rng.choice(string.printable[:94] + " ")
                   for _ in range(rng.randint(1, 24)))


def one_line(text: str) -> str:
    return " ".join(re.sub("[\ud800-\udfff]", "\ufffd", text).splitlines())


def test_any_text_round_trips_through_a_pursuit_file_and_a_card():
    rng = random.Random(6)
    for _ in range(300):
        name, area, title, left, said, step, intent, where = (any_text(rng) for _ in range(8))
        refs = {"cc-2": _codec.SessionRef("Claude Code", title, date(2026, 11, 18))}
        p = pursuit(name=name, area=area, resume=ResumeNote(
            where=(), left_off=(Inference(left, ("cc-2",)),), decided=(Fact(said, SEEN.source),), open_questions=(),
            reopen=("cc-2",)), next_step=Step("s-20261115-1", Inference(step, ("cc-2",)), date(2026, 11, 15)))
        text = _codec.render("pursuit", _codec.PursuitFile(p, refs), TZ)
        back = _codec.parse("pursuit", text, TZ)
        assert (back.pursuit.name, back.pursuit.area, back.sessions["cc-2"].title) == (name, area, title)
        assert [s.text for s in (*back.pursuit.resume.left_off, *back.pursuit.resume.decided,
                                 back.pursuit.next_step.text)] == [one_line(left), one_line(said), one_line(step)]
        assert _codec.render("pursuit", back, TZ) == text
        card = SessionCard("cc-2", Inference(intent, ("cc-2",)), Inference("b", ("cc-2",)), None,
                           Fact(where, SEEN.source), area)
        read = _codec.parse("card", _codec.render("card", card, TZ), TZ)
        assert (read.intent.text, read.where.text, read.one_off_area) == (one_line(intent), one_line(where), area)

def test_any_label_a_fact_cites_round_trips_through_a_pursuit_file_and_a_card():
    rng = random.Random(7)
    for label in ["Invented ] interface", "a [b] c", "x · y", "\\u005d", "\\", "two\nlines", "\ud83d", *(
            any_text(rng) for _ in range(300))]:
        fact = Fact("Invented location.", Cite("cc-2", label, SEEN.source.at))
        p = pursuit(resume=ResumeNote(where=(fact,), left_off=(), decided=(fact,), open_questions=(), reopen=("cc-2",)))
        refs = {"cc-2": _codec.SessionRef("Claude Code", "invented", date(2026, 11, 18))}
        text = _codec.render("pursuit", _codec.PursuitFile(p, refs), TZ)
        back = _codec.parse("pursuit", text, TZ)
        assert back.pursuit.resume.where == back.pursuit.resume.decided == (fact,)
        assert _codec.render("pursuit", back, TZ) == text
        card = SessionCard("cc-2", Inference("a", ("cc-2",)), Inference("b", ("cc-2",)), None, fact)
        assert _codec.parse("card", _codec.render("card", card, TZ), TZ).where == fact


def test_a_surrogate_pair_in_a_fact_label_is_written_as_the_character_it_stands_for():
    fact = Fact("Invented location.", Cite("cc-2", f"Invented {chr(0xD83D)}{chr(0xDE00)} interface", SEEN.source.at))
    p = pursuit(resume=ResumeNote(where=(fact,), left_off=(), decided=(), open_questions=(), reopen=("cc-2",)))
    refs = {"cc-2": _codec.SessionRef("Claude Code", "invented", date(2026, 11, 18))}
    text = _codec.render("pursuit", _codec.PursuitFile(p, refs), TZ)
    assert "Invented \U0001f600 interface" in text.decode()
    back = _codec.parse("pursuit", text, TZ)
    assert back.pursuit.resume.where[0].source.label == "Invented \U0001f600 interface"
    assert _codec.render("pursuit", back, TZ) == text


def test_a_card_written_before_cards_kept_their_one_off_day_reads_with_none():
    card = SessionCard("cc-2", Inference("a", ("cc-2",)), Inference("b", ("cc-2",)), None, None, "work",
                       date(2026, 11, 15))
    text = _codec.render("card", card, TZ).replace(b"one_off_day: 2026-11-15\n", b"")
    assert _codec.parse("card", text, TZ) == replace(card, one_off_day=None)


def test_a_value_the_format_has_no_place_for_is_refused_not_dropped():
    note = Note("nt-1", datetime(2026, 11, 15, 20, 39, tzinfo=TZ), NoteOrigin.REFUSED_UPDATE, "claude_code",
                "close it")
    with pytest.raises(_codec.Unrepresentable):
        _codec.render("notes", _codec.NotesFile(date(2026, 11, 15), "claude_code", (_codec.NoteEntry(note),)), TZ)


def test_a_refused_update_reads_back_as_the_owner_words_an_agent_passed_with_the_change_kept():
    note = Note("nt-1", datetime(2026, 11, 15, 20, 39, tzinfo=TZ), NoteOrigin.REFUSED_UPDATE, "devin",
                "close it", atlas_reply="Not applied.", pending=SetStatus("Greenhouse logger", Status.DONE),
                derived_from=("dv-1",), cwd="~/home/greenhouse")
    nf = _codec.NotesFile(date(2026, 11, 15), "devin", (_codec.NoteEntry(note),))
    text = _codec.render("notes", nf, TZ)
    assert b"Devin, on your words: close it\nAtlas: Not applied." in text
    assert _codec.parse("notes", text, TZ) == nf


def test_body_times_are_wall_time_in_the_settings_zone_and_read_back_as_the_same_instant():
    tokyo = ZoneInfo("Asia/Tokyo")
    utc_evening = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)
    p = Profile(("work",), (ProfileEntry(Fact("You review late.", Cite("nt-1", "your Note", utc_evening)),
                                         date(2026, 11, 21), Who.OWNER),))
    text = _codec.render("profile", p, tokyo)
    assert "[F your Note · 2026-11-21 00:30 · nt-1]" in text.decode()
    back = _codec.parse("profile", text, tokyo)
    assert back == p and back.entries[0].statement.source.at.tzinfo is tokyo


def test_front_matter_datetimes_carry_their_offset():
    tokyo = ZoneInfo("Asia/Tokyo")
    away = (Pause(datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc), datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc),
                  "away"),)
    text = _codec.render("pauses", away, tokyo).decode()
    assert "start: 2026-11-20T17:00+09:00" in text and "- Fri 20 Nov 17:00 to Sun 22 Nov 09:00." in text
    assert _codec.parse("pauses", text.encode(), tokyo) == away


def test_a_naive_time_is_refused():
    with pytest.raises(ValueError):
        _codec.render("pauses", (Pause(datetime(2026, 11, 20, 8, 0), datetime(2026, 11, 22, 8, 0), "away"),), TZ)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


TOLD = Fact("Named it.", Cite("nt-1", "your Note", at(15, 20, 41)))
SEEN = Fact("Pushed the logger fix.", Cite("cc-2", "Claude Code", at(18, 10, 40)))
REFS = {"cc-2": _codec.SessionRef("Claude Code", "logger fix", date(2026, 11, 18)),
        "cc-3": _codec.SessionRef("Claude Code", "what is left", date(2026, 11, 23))}


def pursuit(**changes) -> Pursuit:
    base = dict(
        id="p-1", name="Greenhouse logger", kind=Kind.PROJECT, area="home", status=Status.ACTIVE, focus=False,
        created=date(2026, 11, 15), continued=None,
        resume=ResumeNote(where=(), left_off=(TOLD,), decided=(), open_questions=(), reopen=()),
        next_step=None, past_steps=(), filing_rules=(), sessions=(Membership("cc-2", at(18, 10, 12), True),),
        proposals=(), history=(HistoryLine("h-0001", at(15, 20, 41), Actor.OWNER, TOLD),))
    return Pursuit(**{**base, **changes})


def pursuit_round_trip(p: Pursuit) -> Pursuit:
    text = _codec.render("pursuit", _codec.PursuitFile(p, REFS), TZ)
    back = _codec.parse("pursuit", text, TZ).pursuit
    assert _codec.render("pursuit", _codec.PursuitFile(back, REFS), TZ) == text
    return back


@pytest.mark.parametrize("proposal", [
    EndingProposal(Status.DROPPED, Inference("Nothing since October.", ("cc-2",)), date(2026, 11, 20), True),
    FocusProposal(False, Inference("Three Steps skipped in a row.", ("cc-2",)), date(2026, 11, 21)),
    ReopenProposal(SEEN, date(2026, 11, 22)),
], ids=["ending", "focus", "reopen"])
def test_each_proposal_kind_round_trips(proposal):
    p = pursuit(proposals=(proposal,))
    assert pursuit_round_trip(p) == p


def test_a_session_that_only_read_atlas_round_trips_and_is_not_last_worked():
    p = pursuit(sessions=(Membership("cc-2", at(18, 10, 12), True), Membership("cc-3", at(23, 9, 12), False)))
    back = pursuit_round_trip(p)
    assert back == p and back.last_worked == date(2026, 11, 18)


def test_an_agent_history_line_round_trips_with_its_harness_label():
    words = Fact('Changed by Claude Code on your instruction: "hold the step"', Cite("cc-3", "Claude Code", at(23, 9, 13)))
    p = pursuit(history=(HistoryLine("h-0001", at(23, 9, 14), Actor.AGENT, words, agent="Claude Code"),))
    text = _codec.render("pursuit", _codec.PursuitFile(p, REFS), TZ).decode()
    assert "- h-0001 · 2026-11-23 09:14 · Claude Code · Changed by Claude Code" in text
    assert pursuit_round_trip(p) == p


def test_a_counted_fact_in_a_pursuit_file_keeps_its_sessions():
    counted = Fact("Worked on it this week.", Count("sessions", ("cc-2", "cx-9")))
    once = Fact("One quiet day.", Count("quiet_days", ("cc-2",)))
    p = pursuit(resume=ResumeNote(where=(), left_off=(counted, once), decided=(), open_questions=(), reopen=()))
    text = _codec.render("pursuit", _codec.PursuitFile(p, REFS), TZ).decode()
    assert "[F counted · sessions · 2 Sessions]" in text and "[F counted · quiet_days · 1 Sessions]" in text
    assert pursuit_round_trip(p) == p


def test_step_end_times_come_from_their_basis_or_midnight_for_silence():
    done = Step("s-20261115-1", Inference("Fix the logger.", ("cc-2",)), date(2026, 11, 15),
                ended=Done("seen", SEEN.source.at, SEEN))
    silent = Step("s-20261116-1", Inference("Check the card.", ("cc-2",)), date(2026, 11, 16),
                  ended=Skipped("silence", at(17, 0), None))
    p = pursuit(past_steps=(done, silent))
    assert pursuit_round_trip(p) == p
    with pytest.raises(_codec.Unrepresentable):
        _codec.render("pursuit", _codec.PursuitFile(pursuit(past_steps=(
            Step(done.id, done.text, done.created, ended=Done("seen", at(18, 12), SEEN)),)), REFS), TZ)


def test_a_pattern_keeps_the_sessions_it_was_drawn_from():
    blocks = Pattern("131 days of Sessions", date(2026, 6, 2), date(2026, 11, 15), ("cx-1", "cc-2"))
    entry = ProfileEntry(Inference("You work in two blocks.", blocks), date(2026, 11, 15), Who.LIBRARIAN)
    again = ProfileEntry(Inference("You start at 07:30.", blocks), date(2026, 11, 15), Who.LIBRARIAN)
    p = Profile(("work",), (entry, again))
    text = _codec.render("profile", p, TZ)
    assert text.decode().count("drawn_from: [cx-1, cc-2]") == 1
    assert _codec.parse("profile", text, TZ) == p


def test_a_pattern_outside_profile_md_is_refused():
    blocks = Pattern("131 days of Sessions", date(2026, 6, 2), date(2026, 11, 15), ("cx-1",))
    p = pursuit(resume=ResumeNote(where=(), left_off=(Inference("Works in blocks.", blocks),), decided=(),
                                  open_questions=(), reopen=()))
    with pytest.raises(_codec.Unrepresentable):
        _codec.render("pursuit", _codec.PursuitFile(p, REFS), TZ)


def notes_round_trip(nf: _codec.NotesFile) -> str:
    text = _codec.render("notes", nf, TZ)
    assert _codec.parse("notes", text, TZ) == nf
    return text.decode()


def said(**fields) -> _codec.NotesFile:
    note = Note("nt-1", at(15, 20, 39), NoteOrigin.OWNER, "telegram", "merge them", **fields)
    return _codec.NotesFile(date(2026, 11, 15), "telegram", (_codec.NoteEntry(note),))


def test_a_note_keeps_its_platform_message_id():
    assert 'nt-1: {platform_ref: "telegram:8812"}' in notes_round_trip(said(platform_ref="telegram:8812"))


def test_a_note_keeps_that_apply_was_offered():
    assert "nt-1: {offered: true}" in notes_round_trip(said(offered=True))


def test_a_note_keeps_the_evidence_its_text_was_derived_from():
    assert "nt-1: {derived_from: [cc-1, cx-2]}" in notes_round_trip(said(derived_from=("cc-1", "cx-2")))


def test_a_note_with_only_default_fields_writes_no_entries():
    assert "entries:" not in notes_round_trip(said())


@pytest.mark.parametrize("command", [
    SetStatus("Greenhouse logger", Status.DONE),
    HoldStep("Greenhouse logger", date(2026, 12, 7)),
    MarkStep("Greenhouse logger", 2, "skipped"),
    MarkStep("Greenhouse logger", "s-20261118-1", "done", "seen in the logs"),
    EditResumeNote("Greenhouse logger", ("card filling up",), add_decided=("Log every 5 minutes",)),
    PauseCmd(datetime(2026, 11, 22, 7, 0, tzinfo=TZ), "until sunday"),
], ids=lambda c: type(c).__name__)
def test_a_pending_change_round_trips_as_its_command(command):
    note = Note("nt-1", at(17, 21, 20), NoteOrigin.AGENT, "claude_code", "close it", pending=command)
    text = notes_round_trip(_codec.NotesFile(date(2026, 11, 17), "claude_code", (_codec.NoteEntry(note),)))
    assert f"nt-1: {{pending: {{command: {type(command).__name__}, " in text


EDITED = """---
atlas: notes/1
---
# Notes

### 20:39 · nt-1
You: a heading inside the file
```diff
- a fence inside the file
```
"""


def hand_edit(text: str) -> _codec.NotesFile:
    note = Note("nt-9", at(20, 12, 47), NoteOrigin.HAND_EDIT, "notes/2026/11/2026-11-15-telegram.md", text,
                diff="-old line\n+new line\n", offered=True, derived_from=("cc-1",))
    return _codec.NotesFile(date(2026, 11, 20), "hand-edits", (_codec.NoteEntry(note),))


def test_a_hand_edit_keeps_the_edited_file_even_when_it_holds_headings_and_fences():
    text = notes_round_trip(hand_edit(EDITED))
    assert "\n````markdown\n" in text and "\n```diff\n-old line\n+new line\n```\n" in text


@pytest.mark.parametrize("edited", ["", "\n"], ids=["emptied", "one blank line"])
def test_a_hand_edit_that_emptied_the_file_round_trips(edited):
    notes_round_trip(hand_edit(edited))


def test_an_edited_file_without_its_final_newline_is_refused():
    with pytest.raises(_codec.Unrepresentable):
        _codec.render("notes", hand_edit("no newline"), TZ)


def brief(**changes) -> SentBrief:
    base = dict(day=date(2026, 11, 16), attempts=(SendAttempt(at(16, 7, 30), SendStatus.SENDING, ()),),
                follows_up=None, followups=(), steps=(BriefStep(1, "p-2x7b", "s-20261115-1"),),
                decision=DecisionAsk("p-7d1c", 1, 28), no_focus_yet=False,
                text="Atlas · Daily Brief · Monday 16 November\n\nSTEPS\n1 · Payments CI flakes")
    return SentBrief(**{**base, **changes})


@pytest.mark.parametrize("b", [
    brief(),
    brief(attempts=(SendAttempt(at(16, 7, 30), SendStatus.SENT, ("8900",)),), decision=None, no_focus_yet=True),
    brief(attempts=(SendAttempt(at(16, 7, 30), SendStatus.ARRIVAL_UNKNOWN, ()),
                    SendAttempt(at(16, 7, 40), SendStatus.SENT, ("8901", "8902")))),
    brief(follows_up=date(2026, 11, 15), steps=(),
          followups=(Followup("p-2x7b", "s-20261115-1", "no_sign", None, ending_proposed=True),)),
    brief(attempts=(SendAttempt(at(16, 7, 30), SendStatus.ARRIVAL_UNKNOWN, (), sent_sha256(brief().text)),
                    SendAttempt(at(16, 7, 40), SendStatus.SENT, ("8901",), sent_sha256(brief().text)))),
    brief(attempts=(SendAttempt(at(16, 7, 30), SendStatus.SENT, ("8901",), chat="telegram:-1001234:7"),
                    SendAttempt(at(16, 7, 40), SendStatus.SENT, ("8902",), chat="telegram:5550001"))),
], ids=["sending", "sent-without-focus", "retried", "followed-up", "hashed", "sent-to-chats"])
def test_a_brief_file_round_trips_its_attempts_ids_and_offered_items(b):
    text = _codec.render("brief", b, TZ)
    assert _codec.parse("brief", text, TZ) == b
    assert text.decode().endswith("\n---\n" + b.text + "\n")


def test_a_brief_file_shows_its_last_attempt_and_names_the_first_only_after_a_retry():
    once = _codec.render("brief", brief(), TZ).decode()
    assert "status: sending\nsent_at: 2026-11-16T07:30:00+01:00\nmessage_ids: []\n" in once
    assert "first_attempt" not in once
    twice = _codec.render("brief", brief(attempts=(SendAttempt(at(16, 7, 30), SendStatus.ARRIVAL_UNKNOWN, ()),
                                                   SendAttempt(at(16, 7, 40), SendStatus.SENT, ("8901",)))), TZ)
    assert ('status: sent\nsent_at: 2026-11-16T07:40:00+01:00\nmessage_ids: ["8901"]\n'
            "first_attempt: {at: 2026-11-16T07:30:00+01:00, status: arrival_unknown, message_ids: []}\n") in twice.decode()


def test_a_brief_file_keeps_an_attempts_hash_only_when_it_is_the_hash_of_the_masked_text_it_holds():
    token = "ghp_" + "a1B2" * 9
    bare, masked = f"{brief().text}\nRotate {token}", f"{brief().text}\nRotate [secret: github token]"
    attempts = (SendAttempt(at(16, 7, 30), SendStatus.ARRIVAL_UNKNOWN, (), sent_sha256(bare)),
                SendAttempt(at(16, 7, 40), SendStatus.SENT, ("8901",), sent_sha256(masked)))
    text = _codec.render("brief", brief(attempts=attempts, text=bare), TZ).decode()
    assert token not in text and sent_sha256(bare) not in text
    assert ('message_ids: ["8901"]\ntext_sha256: "' + sent_sha256(masked) + '"\n'
            "first_attempt: {at: 2026-11-16T07:30:00+01:00, status: arrival_unknown, message_ids: []}\n") in text
    assert _codec.parse("brief", text.encode(), TZ).attempts == (replace(attempts[0], text_sha256=None), attempts[1])


@pytest.mark.parametrize("hashed", [False, True], ids=["unhashed", "hashed"])
def test_a_brief_with_a_surrogate_is_written_as_utf8_can_hold_it_and_keeps_the_hash_of_that_text(hashed):
    odd, written = f"{brief().text}\nInvented \ud83d and \ud83d\ude00", f"{brief().text}\nInvented \ufffd and \U0001f600"
    attempt = SendAttempt(at(16, 7, 40), SendStatus.SENT, ("8901",), sent_sha256(written) if hashed else None)
    back = _codec.parse("brief", _codec.render("brief", brief(attempts=(attempt,), text=odd), TZ), TZ)
    assert back == brief(attempts=(attempt,), text=written)
def test_a_brief_file_names_the_chat_each_attempt_was_sent_to():
    text = _codec.render("brief", brief(attempts=(SendAttempt(at(16, 7, 30), SendStatus.ARRIVAL_UNKNOWN, (), None),
                                                  SendAttempt(at(16, 7, 40), SendStatus.SENT, ("8901",),
                                                              chat="telegram:5550001"))), TZ).decode()
    assert 'message_ids: ["8901"]\nchat: "telegram:5550001"\n' in text
    assert "first_attempt: {at: 2026-11-16T07:30:00+01:00, status: arrival_unknown, message_ids: []}\n" in text


def test_a_third_attempt_and_a_follow_up_line_have_no_place_in_a_brief_file():
    sending = SendAttempt(at(16, 7, 30), SendStatus.SENDING, ())
    with pytest.raises(_codec.Unrepresentable):
        _codec.render("brief", brief(attempts=(sending, sending, sending)), TZ)
    with pytest.raises(_codec.Unrepresentable):
        _codec.render("brief", brief(followups=(Followup("p-2x7b", "s-20261115-1", "no_sign", TOLD),)), TZ)


@pytest.mark.parametrize("where", ["title", "name"])
def test_a_surrogate_pair_is_written_as_the_character_it_stands_for(where):
    pair = f"Invented {chr(0xD83D)}{chr(0xDE00)} work"
    refs = {**REFS, "cc-2": _codec.SessionRef("Claude Code", pair if where == "title" else "logger fix",
                                              date(2026, 11, 18))}
    p = pursuit(name=pair if where == "name" else "Greenhouse logger")
    text = _codec.render("pursuit", _codec.PursuitFile(p, refs), TZ)
    back = _codec.parse("pursuit", text, TZ)
    assert (back.sessions["cc-2"].title if where == "title" else back.pursuit.name) == "Invented \U0001f600 work"
    assert _codec.render("pursuit", back, TZ) == text


@pytest.mark.parametrize("odd, written", [("\ud83d", "\ufffd"), ("\ud83d\ude00", "\U0001f600")], ids=["lone", "pair"])
def test_a_surrogate_in_a_note_is_written_as_utf8_can_hold_it(odd, written):
    note = Note("nt-1", at(15, 20, 39), NoteOrigin.AGENT, "claude_code", f"merge {odd} them", atlas_reply=f"ok {odd}",
                cwd=f"~/work/{odd}")
    nf = _codec.parse("notes", _codec.render("notes", _codec.NotesFile(date(2026, 11, 15), "claude_code",
                                                                       (_codec.NoteEntry(note),)), TZ), TZ)
    assert nf.entries[0].note == replace(note, text=f"merge {written} them", atlas_reply=f"ok {written}",
                                         cwd=f"~/work/{written}")


MONDAY = date(2026, 11, 16)


def journal_day(areas, *, basis=("cc-2",), worked_on=(), decided=(), finished=(), corrections=()) -> JournalDay:
    areas = {a: {k: timedelta(minutes=m) for k, m in split.items()} for a, split in areas.items()}
    total = sum((t for split in areas.values() for t in split.values()), timedelta())
    return JournalDay(MONDAY, DayTime(total, areas, basis), worked_on, decided, finished, corrections)


def journal_round_trip(jf: _codec.JournalFile) -> tuple[_codec.JournalFile, bytes]:
    text = _codec.render("journal", jf, TZ)
    return _codec.parse("journal", text, TZ), text


def test_any_name_line_and_correction_round_trips_through_a_journal_day():
    rng = random.Random(16)
    for _ in range(300):
        a, b, c, area, line, said, shared, machine = (any_text(rng) for _ in range(8))
        day = journal_day({"work": {"p-a": 70, "p-b": 5, ONE_OFF: 10}, area: {"p-c": 120, ONE_OFF: 1}},
                          worked_on=(Fact(line, SEEN.source),), decided=(Inference(said, ("cc-2",)),),
                          corrections=(said,))
        if area == "work":
            continue
        back, text = journal_round_trip(_codec.JournalFile(day, {"p-a": a, "p-b": b, "p-c": c}, shared, machine))
        assert back.names == {"p-a": one_line(a), "p-b": one_line(b), "p-c": one_line(c)}
        assert back.day.time == day.time
        assert [s.text for s in back.day.lines] == [one_line(line), one_line(said)]
        assert _codec.render("journal", back, TZ) == text


@pytest.mark.parametrize("name", ["one-off", "one-off 10 min", "Sprint 2", "Phase 1 h", "a, b", "x 1 h 10", "\\u002c"])
def test_a_pursuit_named_like_a_time_or_a_one_off_round_trips(name):
    day = journal_day({"work": {"p-a": 70, ONE_OFF: 10}, "home": {"p-b": 10, ONE_OFF: 5}})
    back, _ = journal_round_trip(_codec.JournalFile(day, {"p-a": name, "p-b": name}, "", "ubuntu-pc"))
    assert (back.names, back.day.time) == ({"p-a": name, "p-b": name}, day.time)


@pytest.mark.parametrize("minutes, total, pursuit", [(5, "5 min", "5 min"), (60, "1 h", "1 h"),
                                                     (65, "1 h 5 min", "1 h 05"), (130, "2 h 10 min", "2 h 10")])
def test_active_time_is_written_in_hours_and_minutes(minutes, total, pursuit):
    text = _codec.render("journal", _codec.JournalFile(journal_day({"work": {"p-a": minutes}}), {"p-a": "A"}, "",
                                                         "ubuntu-pc"), TZ).decode()
    assert f"Active time {total} [F counted · active_time · 1 Sessions]" in text
    assert f"  A {pursuit}\n" in text


def test_a_journal_day_shows_only_the_sections_it_has_and_one_session_as_one():
    text = _codec.render("journal", _codec.JournalFile(journal_day({"work": {"p-a": 20}}), {"p-a": "A"}, "",
                                                         "ubuntu-pc"), TZ).decode()
    body = text.split("---\n")[2]
    assert "Worked on" not in body and "Decided" not in body and "Finished" not in body
    assert "\nEvidence: 1 Session. [F counted · sessions · 1 Sessions]\nCovers AI Sessions on ubuntu-pc only.\n" in body


def test_a_journal_day_masks_every_secret_it_writes():
    key = "sk-proj-" + "Ab3dE5gH7jK9mN1pQ3sT5vX7zB9dF1hJ"
    day = journal_day({"work": {"p-a": 20}}, worked_on=(Fact(f"A: used {key}.", SEEN.source),), corrections=(key,))
    text = _codec.render("journal", _codec.JournalFile(day, {"p-a": f"A {key}"}, f"Codex 12:30 {key}.", key), TZ)
    assert b"sk-proj-" not in text and text.count(b"[secret: api key]") == 5
    assert _codec.render("journal", _codec.parse("journal", text, TZ), TZ) == text


def test_a_journal_day_without_active_time_or_whole_minutes_is_refused():
    with pytest.raises(_codec.Unrepresentable):
        _codec.render("journal", _codec.JournalFile(journal_day({}, basis=()), {}, "", "ubuntu-pc"), TZ)
    seconds = JournalDay(MONDAY, DayTime(timedelta(seconds=90), {"work": {"p-a": timedelta(seconds=90)}}, ("cc-2",)),
                         (), (), (), ())
    with pytest.raises(_codec.Unrepresentable):
        _codec.render("journal", _codec.JournalFile(seconds, {"p-a": "A"}, "", "ubuntu-pc"), TZ)
