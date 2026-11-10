"""rules.authorize and the decide registry for the owner's word, rules.converse (one Note and every
change in one ChangeSet), and messages.resume_prompt."""
from dataclasses import replace
from datetime import date, timedelta

import pytest

from atlas import ask, messages, rules
from atlas.model import (Agent, BoundSession, BriefStep, Cite, Continue, Done, Fact, HarnessId, Inference, Instruction,
                         Librarian, MarkStep, OutsideAction, Owner, OwnerMessage, ResumeNote, SendAttempt, SendStatus,
                         SentBrief, SentRef, SetFocus, SetStatus, Skipped, Status, Who)
from snapshots import after, at, pursuit, snapshot

MONDAY = date(2026, 11, 16)
NOW = at(MONDAY, "08:34")
PAYMENTS = pursuit("p-2x7b", "Payments CI flakes", worked=[date(2026, 11, 14)], focus=True)
NEWSLETTER = pursuit("p-7d1c", "Agent-tooling newsletter", worked=[date(2026, 10, 19)])
SNAP = snapshot([PAYMENTS, NEWSLETTER])
OWNER = Owner("telegram", "nt-0said000")
SESSION = BoundSession(HarnessId.CLAUDE_CODE, "fixture", "s-1")


def told(names_ending=None):
    return Agent("claude-code", SESSION, Instruction(SESSION, "park it", NOW, names_ending))


UNTOLD = Agent("claude-code", SESSION, None)
ATLAS = Librarian(("cc-2x7b0000",), 1)
STEP = MarkStep("p-2x7b", "s-20261115-1", "done", None)


@pytest.mark.parametrize("cmd", [STEP, SetStatus("p-7d1c", Status.PARKED)])
@pytest.mark.parametrize("who, refused", [(OWNER, None), (told(), None), (UNTOLD, "NOT_TOLD"), (ATLAS, None)])
def test_who_may_mark_a_step_or_park_is_read_from_the_command_type(cmd, who, refused):
    assert {Who.OWNER, Who.AGENT_TOLD, Who.LIBRARIAN} == type(cmd).allowed
    assert rules.authorize(cmd, who) == refused


@pytest.mark.parametrize("status", [Status.DONE, Status.DROPPED])
@pytest.mark.parametrize("who, refused", [
    (OWNER, None),
    (told(names_ending=Status.DONE), "same"),
    (told(), "OWNER_ONLY"),
    (UNTOLD, "NOT_TOLD"),
    (ATLAS, "OWNER_ONLY"),
])
def test_only_the_owner_or_an_agent_told_to_ends_a_pursuit(status, who, refused):
    if refused == "same":
        refused = None if status is Status.DONE else "OWNER_ONLY"
    assert rules.authorize(SetStatus("p-2x7b", status), who) == refused


def test_owner_only_commands_and_outside_actions_are_refused_to_others():
    assert rules.authorize(SetFocus("p-2x7b", True), told()) == "OWNER_ONLY"
    assert rules.authorize(SetFocus("p-2x7b", True), OWNER) is None
    assert rules.authorize(OutsideAction("send", "Dana"), OWNER) == "OUTSIDE_WORLD"


def test_the_owner_marks_the_step_done_by_word_with_their_words_as_its_basis():
    d = rules.decide(SNAP, replace(STEP, detail="10/10 pass with one worker"), OWNER, NOW)
    assert d.outcome.lines == ("Payments CI flakes: Step done.",)
    p = _committed(d, "p-2x7b")
    said = Cite("nt-0said000", "your Note", NOW)
    assert p.next_step is None
    assert p.past_steps[-1].ended == Done("word", NOW, Fact("10/10 pass with one worker", said))
    assert p.history[-1].statement == Fact("Marked the Step done.", said)


def test_a_skip_by_word_counts_toward_three_skips_in_a_row():
    d = rules.decide(SNAP, replace(STEP, outcome="skipped", detail="not today"), OWNER, NOW)
    assert d.outcome.lines == ("Payments CI flakes: Step skipped.",)
    p = _committed(d, "p-2x7b")
    assert isinstance(p.past_steps[-1].ended, Skipped) and p.past_steps[-1].ended.how == "word"
    assert rules.skipped_in_a_row(p) == 1


def test_the_owner_word_settles_a_step_ended_otherwise_and_again_changes_nothing():
    skipped = rules.decide(SNAP, replace(STEP, outcome="skipped"), OWNER, NOW)
    after = snapshot([_committed(skipped, "p-2x7b"), NEWSLETTER])
    done = rules.decide(after, STEP, OWNER, NOW)
    assert isinstance(_committed(done, "p-2x7b").past_steps[-1].ended, Done)
    assert rules.decide(snapshot([_committed(done, "p-2x7b")]), STEP, OWNER, NOW).changes.is_empty()


def test_parking_or_dropping_leaves_focus_and_says_so_in_the_tutorials_words():
    parked = rules.decide(SNAP, SetStatus("p-2x7b", Status.PARKED), OWNER, NOW)
    assert parked.outcome.lines == ("Payments CI flakes: parked. It leaves Focus.",)
    assert (_committed(parked, "p-2x7b").status, _committed(parked, "p-2x7b").focus) == (Status.PARKED, False)
    dropped = rules.decide(SNAP, SetStatus("p-7d1c", Status.DROPPED), OWNER, NOW)
    assert dropped.outcome.lines == ("Agent-tooling newsletter: dropped. I won't push it again. You can still find it.",)
    assert rules.decide(snapshot([_committed(parked, "p-2x7b")]), SetStatus("p-2x7b", Status.PARKED), OWNER,
                        NOW).changes.is_empty()


def test_continue_restarts_the_quiet_clock_once_a_day():
    d = rules.decide(SNAP, Continue("p-7d1c"), OWNER, NOW)
    assert d.outcome.lines == ("Agent-tooling newsletter: stays active. Its quiet days count from today.",)
    assert _committed(d, "p-7d1c").continued == MONDAY
    assert rules.decide(snapshot([_committed(d, "p-7d1c")]), Continue("p-7d1c"), OWNER, NOW).changes.is_empty()


def test_a_refused_command_changes_nothing():
    d = rules.decide(SNAP, SetStatus("p-2x7b", Status.DONE), ATLAS, NOW)
    assert (d.outcome.ok, d.outcome.refused, d.changes.is_empty()) == (False, "OWNER_ONLY", True)


MSG = OwnerMessage("1 done, 10/10 pass with one worker. park the newsletter", NOW, "telegram", "telegram:5550001",
                   None, "telegram:5550001:9001")
SAID = (replace(STEP, detail="10/10 pass with one worker"), SetStatus("p-7d1c", Status.PARKED))


def test_one_changeset_holds_the_note_with_atlas_reply_and_every_change():
    d = rules.converse(SNAP, MSG, SAID, ())
    assert d.outcome.lines == ("Payments CI flakes: Step done.", "Agent-tooling newsletter: parked.")
    snap = after(d)
    [note] = snap.notes()
    assert (note.text, note.atlas_reply, note.platform_ref) == (MSG.text, "\n".join(d.outcome.lines), MSG.platform_ref)
    assert note.id == rules.note_id(MSG)
    assert snap.pursuit("p-2x7b").past_steps[-1].ended.basis.source.evidence == note.id
    assert snap.pursuit("p-7d1c").status is Status.PARKED


def test_a_redelivered_platform_message_changes_nothing_and_gets_the_same_reply():
    snap = after(rules.converse(SNAP, MSG, SAID, ()))
    again = rules.converse(snap, MSG, SAID, ())
    assert again.changes.is_empty()
    assert again.outcome.lines == ("Payments CI flakes: Step done.\nAgent-tooling newsletter: parked.",)


def test_two_changes_to_one_pursuit_in_one_message_both_land():
    d = rules.converse(SNAP, replace(MSG, platform_ref="telegram:5550001:9002"),
                       (STEP, SetStatus("p-2x7b", Status.PARKED)), ())
    p = after(d).pursuit("p-2x7b")
    assert (p.next_step, p.status) == (None, Status.PARKED)


def test_an_outside_action_is_refused_in_the_reply_and_changes_no_pursuit():
    d = rules.converse(SNAP, MSG, (OutsideAction("send", None),), ())
    assert (d.outcome.ok, d.outcome.lines) == (True, (ask.CANNOT_SEND,))
    left = after(d)
    assert list(left.pursuits()) == list(SNAP.pursuits())
    assert [n.atlas_reply for n in left.notes()] == [ask.CANNOT_SEND]


HOME = "telegram:5550001"


def sent_brief(*attempts):
    return SentBrief(MONDAY, attempts, None, (), (BriefStep(1, "p-2x7b", "s-20261115-1"),), None, False, "")


def test_a_message_id_names_a_brief_only_in_the_chat_it_was_sent_to():
    snap = snapshot([PAYMENTS], briefs=[sent_brief(SendAttempt(NOW, SendStatus.ARRIVAL_UNKNOWN, (), chat=HOME),
                                                   SendAttempt(NOW, SendStatus.SENT, ("8812",), chat=HOME))])
    assert rules.replied_to(snap, HOME, "8812") == SentRef("brief", "2026-11-16", ("8812",))
    assert rules.replied_to(snap, "telegram:-100777", "8812") is None
    assert rules.replied_to(snap, "telegram:5550001:7", "8812") is None


def test_a_brief_sent_before_its_chat_was_kept_is_named_in_no_chat():
    snap = snapshot([PAYMENTS], briefs=[sent_brief(SendAttempt(NOW, SendStatus.SENT, ("8812",)))])
    assert rules.replied_to(snap, HOME, "8812") is None
    assert rules.replied_to(snap, "telegram:-100777", "8812") is None


def test_no_message_id_names_an_atlas_answer():
    snap = after(rules.converse(SNAP, MSG, SAID, ()))
    assert rules.replied_to(snap, HOME, "9050") is None


def test_a_message_with_no_platform_id_finds_its_note_by_its_id():
    local = OwnerMessage("park the newsletter", NOW, "slash", None, None)
    snap = after(rules.converse(SNAP, local, (SetStatus("p-7d1c", Status.PARKED),), ()))
    assert rules.noted(snap, local).atlas_reply == "Agent-tooling newsletter: parked."
    assert rules.noted(snap, replace(local, at=NOW.replace(second=1))) is None


@pytest.mark.parametrize("status, minutes_ago, sending", [
    (SendStatus.SENDING, 0, True),
    (SendStatus.SENDING, 2, False),
    (SendStatus.SENT, 0, False),
])
def test_a_brief_is_in_flight_while_its_last_attempt_is_sending_and_recent(status, minutes_ago, sending):
    snap = snapshot([PAYMENTS], briefs=[sent_brief(SendAttempt(NOW - timedelta(minutes=minutes_ago), status, ()))])
    assert rules.in_flight(snap, NOW) is sending


def test_resume_prompt_is_deterministic_from_the_resume_note():
    left_off = Fact("test_retry fails only when two pytest-xdist workers share Redis db 0.",
                    Cite("cc-2x7b0000", "Claude Code", at(date(2026, 11, 14), "22:10")))
    p = replace(PAYMENTS, resume=ResumeNote(
        where=(Fact("ubuntu-pc, ~/work/payments-api, branch fix/retry-flake", left_off.source),),
        left_off=(left_off,), decided=(Fact("keep xdist; no sleeps in tests.", left_off.source),),
        open_questions=(Inference("does the conftest fixture flush Redis between tests?", ("cc-2x7b0000",)),),
        reopen=("cc-2x7b0000",)))
    p = replace(p, next_step=replace(p.next_step, text=Inference("Run it ten times.", ("cc-2x7b0000",))))
    snap = snapshot([p])
    assert messages.resume_prompt(p, snap) == messages.resume_prompt(p, snapshot([p])) == "\n".join([
        'Resuming "Payments CI flakes".',
        "Where: ubuntu-pc, ~/work/payments-api, branch fix/retry-flake",
        "Left off: test_retry fails only when two pytest-xdist workers share Redis db 0. [F]",
        "Decided: keep xdist; no sleeps in tests. [F]",
        "Open question: does the conftest fixture flush Redis between tests? [I]",
        'Sessions to reopen: Claude Code "cc-2x7b0000" (Sat 14 Nov)',
        "Next Step: run it ten times.",
    ])


def test_resume_prompt_leaves_out_what_the_resume_note_does_not_hold():
    bare = replace(NEWSLETTER, next_step=None)
    assert messages.resume_prompt(bare, snapshot([bare])) == 'Resuming "Agent-tooling newsletter".\nLeft off: Left it here. [F]'


def _committed(d, id):
    return after(d).pursuit(id)
