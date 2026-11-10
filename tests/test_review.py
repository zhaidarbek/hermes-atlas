"""The weekly Review (scene 36): each section as a pure function, the text, when it is due, the
record file, and the replies that answer it."""
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from atlas import review, rules, schedule
from atlas.model import (Actor, Area, Cite, Continue, Count, Done, EndingProposal, EvidenceId, Fact, FocusProposal, HarnessId,
                         HistoryId, HistoryLine, Inference, Kind, Membership, Note, NoteOrigin, OwnerMessage, Pause,
                         Profile, ProfileEntry, Pursuit, PursuitId, ReopenProposal, ResumeNote, SendAttempt, SendStatus,
                         SentReview, SentRef, SetStatus, Skipped, Status, Step, StepId, WhichQ, Who)
from atlas.record import ImportLog, Record
from harness import seeds, world
from harness.home import AtlasHome
from harness.scenes import review_items

TZ = world.TZ
SUNDAY = world.REVIEW_AT
WEEK = review.Period(date(2026, 11, 16), date(2026, 11, 22), SUNDAY)
CC = EvidenceId("cc-aaaaaaaaaaaaaaaa")
NOTE = EvidenceId("nt-aaaaaaaa")
WED = datetime(2026, 11, 18, 10, 0, tzinfo=TZ)
WEDNESDAY = WED.date()


def at(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 11, day, hour, 0, tzinfo=TZ)


def said(text: str, when: datetime, actor: Actor = Actor.OWNER, n: int = 2) -> HistoryLine:
    statement = Fact(text, Cite(NOTE, "your Note", when)) if actor is Actor.OWNER else Inference(text, (CC,))
    return HistoryLine(HistoryId(f"h-{n:04d}"), when, actor, statement)


def step(n: int, ended=None) -> Step:
    return Step(StepId(f"s-20261116-{n}"), Inference(f"Step {n}.", (CC,)), date(2026, 11, 16), ended=ended)


def pursuit(name: str = "Invented pursuit", id: str = "p-0001", **kw) -> Pursuit:
    base = dict(id=PursuitId(id), name=name, kind=Kind.PROJECT, area=Area("work"), status=Status.ACTIVE, focus=False,
                created=date(2026, 8, 1), continued=None,
                resume=ResumeNote((), (Fact("left it half done.", Cite(CC, "Claude Code", at(14))),), (), (), (CC,)),
                next_step=None, past_steps=(), filing_rules=(), sessions=(Membership(CC, at(14), True),),
                proposals=(), history=(said("Created from 1 Session", datetime(2026, 8, 1, tzinfo=TZ), Actor.ATLAS, 1),))
    return Pursuit(**{**base, **kw})


# ------------------------------------------------------------------ sections, pure

def test_finished_lists_the_owners_endings_and_counts_the_steps_done_by_word_and_seen():
    p = pursuit(history=(said(rules.STATUS_SAID[Status.DONE][0], at(19)),), past_steps=(
        step(1, Done("word", at(16), Fact("", Cite(NOTE, "your Note", at(16))))),
        step(2, Done("seen", at(17), Fact("", Cite(CC, "Claude Code", at(17))))),
        step(3, Done("seen", at(10), Fact("", Cite(CC, "Claude Code", at(10))))),
        step(4, Skipped("word", at(18), None))))
    assert [i.lines for i in review.finished([p], WEEK).items] == [
        ("Invented pursuit: you marked it done on Thursday. [F]",),
        ("2 Steps done: 1 you told me about, 1 I saw in your Sessions. [F]",)]


def test_decided_reads_the_resume_notes_decisions_of_the_week_by_pursuit_with_a_reply_cited_as_your_reply():
    a = pursuit("B pursuit", resume=ResumeNote((), (), (Fact("use a queue", Cite(CC, "Codex", at(17, 21))),
                                                         Fact("old choice", Cite(CC, "Codex", at(2)))), (), (CC,)))
    b = pursuit("A pursuit", "p-0002", resume=ResumeNote((), (), (Fact("tablet app", Cite(NOTE, "your Note", at(18))),),
                                                         (), (CC,)))
    section = review.decided([a, b], frozenset({NOTE}), WEEK)
    assert [i.lines[0] for i in section.items] == ["A pursuit: tablet app. [F your reply, Wed 10:00]",
                                                   "B pursuit: use a queue. [F Codex, Tue 21:00]"]
    assert [i.cites for i in section.items] == [frozenset(), frozenset({CC})]


def test_changed_groups_parks_then_drops_and_continues_then_each_focus_change():
    ps = [pursuit("A", "p-1", history=(said("Parked it.", at(16)),)),
          pursuit("B", "p-2", history=(said("Dropped it.", at(17)),)),
          pursuit("C", "p-3", history=(said('Said "continue".', at(19)),)),
          pursuit("D", "p-4", history=(said("Added it to Focus on the Board.", at(18)),)),
          pursuit("E", "p-5", history=(said("Parked it.", at(20)), said("Held the Step until Fri 20 Nov.", at(20))))]
    assert [i.lines[0] for i in review.changed(ps, WEEK).items] == [
        "Parked A and E. [F]", "Dropped B and continued C. [F]", "D added to Focus on Wednesday. [F]"]


def test_what_i_did_lists_atlas_lines_by_kind_whatever_the_seen_marks_and_leaves_out_steps_and_proposals():
    rule = Fact('Chores belong here, not in "B".', Cite(NOTE, "your Note", datetime(2026, 11, 15, 20, 41, tzinfo=TZ)))
    filed = [said(f"Filed a Codex Session here by your Filing rule: {rule.text}", at(18), Actor.ATLAS, n) for n in (2, 3)]
    ps = [pursuit("Filed one", "p-1", filing_rules=(rule,), history=tuple(filed)),
          pursuit("Kind one", "p-2", history=(said("Changed Kind from idea to project: it has a repo.", at(18), Actor.ATLAS),
                                              said("Marked the Step done from your Session.", at(18), Actor.ATLAS, 3),
                                              said("Proposed marking it done: finished.", at(18), Actor.ATLAS, 4))),
          pursuit("New one", "p-3", area=Area("health"), history=(said("Created it from your ChatGPT Sessions.", at(18),
                                                                        Actor.ATLAS, 1),))]
    section = review.own(ps, Profile((), ()), WEEK)
    assert [i.lines[0] for i in section.items] == [
        "Created New one (health) from your ChatGPT Sessions. [F]",
        "Changed Kind one from idea to project: it has a repo. [I]",
        "Filed 2 Sessions under Filed one, by Sunday's split. [F]"]
    assert section.footer == review.CORRECT


def test_what_i_did_lists_a_profile_entry_atlas_added_this_week_and_not_one_the_owner_corrected():
    profile = Profile(("work",), (
        ProfileEntry(Inference("You work late on Tuesdays.", (CC,)), date(2026, 11, 17), Who.LIBRARIAN),
        ProfileEntry(Fact("You skip mornings.", Cite(NOTE, "your Note", at(20))), date(2026, 11, 17), Who.OWNER),
        ProfileEntry(Inference("You start projects on Sundays.", (CC,)), date(2026, 11, 2), Who.LIBRARIAN)))
    assert [i.lines[0] for i in review.own([], profile, WEEK).items] == [
        'Added to your Profile: "You work late on Tuesdays." [I]']


def test_what_i_did_lists_a_park_by_silence_with_how_to_undo_it():
    p = pursuit(history=(HistoryLine(HistoryId("h-0002"), at(20), Actor.ATLAS,
                                     Fact(review.neglect.PARKED_BY_SILENCE, Count("quiet_days", (CC,)))),))
    assert review.own([p], Profile((), ()), WEEK).items[0].lines[0] == \
        'Parked Invented pursuit after two unanswered asks; reply "continue" to undo it. [F]'


def test_focus_asks_each_open_proposal_with_the_word_to_answer_it():
    adds = pursuit("Home server backups", proposals=(FocusProposal(True, Inference("A slot is free.", (CC,)), WED.date()),))
    removes = pursuit("Knee rehab plan", "p-2", focus=True,
                      proposals=(FocusProposal(False, Inference("Three skips.", (CC,)), WED.date()),))
    assert [i.lines for i in review.focus([adds, removes]).items] == [
        ('A slot is free. [I] Reply "add backups" or "leave it".',),
        ('Three skips. [I] Reply "remove plan" or "leave it".',)]


def test_three_skipped_steps_in_a_row_by_word_or_silence_propose_taking_the_pursuit_out_of_focus_once():
    two = pursuit(focus=True, past_steps=(step(1, Skipped("word", at(16), None)), step(2, Skipped("silence", at(17), None))))
    assert review.removal(two, WEDNESDAY) == two
    three = replace(two, past_steps=(*two.past_steps, step(3, Skipped("silence", at(18), None))))
    proposed = review.removal(three, WEDNESDAY)
    (proposal,) = proposed.proposals
    assert isinstance(proposal, FocusProposal) and not proposal.add
    assert review.removal(proposed, WEDNESDAY) == proposed
    done_last = replace(three, past_steps=(*three.past_steps, step(4, Done("word", at(19), Fact("", Cite(NOTE, "x", at(19)))))))
    assert review.removal(done_last, WEDNESDAY) == done_last


def test_endings_and_reopens_not_raised_in_a_follow_up_are_asked():
    ending = pursuit("Ends", proposals=(EndingProposal(Status.DONE, Inference("It shipped.", (CC,)), WED.date(), False),))
    raised = pursuit("Raised", "p-2", proposals=(EndingProposal(Status.DONE, Inference("x", (CC,)), WED.date(), True),))
    reopen = pursuit("Reopens", "p-3", status=Status.DONE,
                     proposals=(ReopenProposal(Fact("Your Codex Session worked on it.", Cite(CC, "Codex", at(19))), WED.date()),))
    assert [i.lines for i in review.endings([ending, raised, reopen]).items] == [
        ("Ends: I propose marking it done. It shipped. [I]", 'Reply "close it" or "leave it".')]
    assert [i.lines for i in review.reopening([reopen], WEEK).items] == [
        ("Reopens is done. Your Codex Session worked on it. [F Codex, Thu 10:00]", 'Reply "reopen it" or "leave it".')]


def test_parked_shows_where_the_owner_left_off_and_asks_once():
    p = pursuit("Pi-hole DNS loop", kind=Kind.PROBLEM, status=Status.PARKED)
    assert review.parked(p, 100, WEEK).items[0].lines == (
        "Pi-hole DNS loop · problem · quiet 100 days [F]", "Left off: left it half done. [F Claude Code, 14 Nov]",
        "Continue, keep parked, or drop?")
    assert review.parked(None, 0, WEEK).items == ()


def test_waiting_lists_hand_edits_and_refused_updates_not_yet_applied():
    notes = [Note(NOTE, at(20), NoteOrigin.HAND_EDIT, "profile.md", "x"),
             Note(EvidenceId("nt-bbbbbbbb"), at(21), NoteOrigin.REFUSED_UPDATE, "devin", "x", about=PursuitId("p-0001"),
                  derived_from=(CC,))]
    section = review.waiting(notes, [pursuit()], WEEK)
    assert [i.lines[0] for i in section.items] == [
        "You edited profile.md by hand on Friday. I put it back and kept your text as a Note. [F]",
        "Invented pursuit: a change Devin asked for on Saturday. I could not check your words there, so I kept it "
        "as a Note with your words. [F]"]
    assert section.items[1].cites == frozenset({CC})
    assert section.footer == 'Reply "apply" to make the latest change.'


SETTLED = Cite(CC, "Claude Code", at(17, 21))


def settled(p: Pursuit) -> Pursuit:
    """An atlas_update that closes the one Open question and adds the owner's words as a decision."""
    p = replace(p, resume=replace(p.resume, open_questions=(Inference("does the card fill up?", (CC,)),)))
    cmd = rules.EditResumeNote(p.id, ("does the card fill up?",), ("The question is settled: it lasts.",))
    p, removed = rules.edit_resume(p, cmd, SETTLED, Actor.AGENT, "Claude Code")
    assert removed
    return p


@pytest.mark.parametrize("note_hour, closing_first", [(20, False), (22, True)])
def test_decided_leaves_out_a_decision_that_closed_an_open_question_whichever_came_first(note_hour, closing_first):
    note = Fact("log every 5 minutes", Cite(NOTE, "Note from Claude Code", at(17, note_hour)))
    p = settled(pursuit("Greenhouse"))
    decided = (note, *p.resume.decided) if not closing_first else (*p.resume.decided, note)
    p = replace(p, resume=replace(p.resume, decided=decided))
    assert [i.lines[0] for i in review.decided([p], frozenset(), WEEK).items] == [
        f"Greenhouse: log every 5 minutes. [F Note from Claude Code, Tue {note_hour}:00]"]


@pytest.mark.parametrize("hours", [(20, 22), (22, 20)])
def test_decided_lists_one_line_per_pursuit_per_day_the_earliest(hours):
    facts = tuple(Fact(f"choice at {h}", Cite(CC, "Codex", at(17, h))) for h in hours)
    p = pursuit(resume=replace(pursuit().resume, decided=(*facts, Fact("next day", Cite(CC, "Codex", at(18))))))
    assert [i.lines[0] for i in review.decided([p], frozenset(), WEEK).items] == [
        "Invented pursuit: choice at 20. [F Codex, Tue 20:00]", "Invented pursuit: next day. [F Codex, Wed 10:00]"]


def test_an_update_that_closes_no_open_question_lists_its_decision():
    cmd = rules.EditResumeNote(PursuitId("p-0001"), (), ("Keep the card.",))
    p, _ = rules.edit_resume(pursuit(), cmd, SETTLED, Actor.AGENT, "Claude Code")
    assert [i.lines[0] for i in review.decided([p], frozenset(), WEEK).items] == [
        "Invented pursuit: Keep the card. [F Claude Code, Tue 21:00]"]


def ended_in_focus(how: str = rules.STATUS_SAID[Status.DONE][0], when: datetime = at(19, 7)) -> Pursuit:
    return pursuit("Payments CI flakes", "p-0009", status=Status.DONE, history=(
        said(how, when, n=2), said(rules.LEFT_FOCUS, when, n=3)))


def candidate(name: str, id: str, *lines: HistoryLine, **kw) -> Pursuit:
    return pursuit(name, id, history=lines, **kw)


def test_a_slot_freed_this_week_proposes_the_active_pursuit_the_owner_said_continue_on_with_the_reason_from_the_record():
    backups = candidate("Home server backups", "p-0002", said(rules.CONTINUE_SAID[0], at(19, 7)))
    others = [candidate("Bike route planner", "p-0003", said(rules.STATUS_SAID[Status.DROPPED][0], at(17)),
                        status=Status.DROPPED),
              candidate("Newsletter", "p-0004", said(rules.CONTINUE_SAID[0], at(18)), status=Status.PARKED),
              candidate("Knee rehab plan", "p-0005", said(rules.CONTINUE_SAID[0], at(18)), focus=True),
              candidate("Quiet one", "p-0006")]
    proposed = rules.free_slot([ended_in_focus(), backups, *others], WEEK.start, WEEK.end, TZ)
    assert proposed.id == backups.id
    [x] = proposed.proposals
    assert x.add and x.since == WEEK.end and x.reason.text == (
        'Payments CI flakes left Focus when you closed it, so there is a free slot. Proposed: Home server backups. '
        'You said "continue" on Thursday.')
    assert [i.lines[0] for i in review.focus([proposed]).items] == [
        f'{x.reason.text} [I] Reply "add backups" or "leave it".']


@pytest.mark.parametrize("how, words", [(rules.STATUS_SAID[Status.DROPPED][0], "when you dropped it"),
                                        (rules.STATUS_SAID[Status.PARKED][0], "when you parked it")])
def test_the_reason_says_how_the_pursuit_left_focus(how, words):
    backups = candidate("Home server backups", "p-0002", said(rules.STATUS_SAID[Status.ACTIVE][0], at(20)))
    proposed = rules.free_slot([ended_in_focus(how), backups], WEEK.start, WEEK.end, TZ)
    assert proposed.proposals[0].reason.text == (
        f"Payments CI flakes left Focus {words}, so there is a free slot. Proposed: Home server backups. "
        "You made it active on Friday.")


def test_the_latest_owner_act_wins_and_a_leave_it_in_the_last_four_weeks_rules_one_out():
    early = candidate("A early", "p-0002", said(rules.CONTINUE_SAID[0], at(17)))
    late = candidate("B late", "p-0003", said(rules.CONTINUE_SAID[0], at(20)))
    assert rules.free_slot([ended_in_focus(), early, late], WEEK.start, WEEK.end, TZ).id == late.id
    declined = replace(late, history=(said(review.LEFT_AS_IS, datetime(2026, 10, 30, tzinfo=TZ), n=1), *late.history))
    assert rules.free_slot([ended_in_focus(), early, declined], WEEK.start, WEEK.end, TZ).id == early.id
    long_ago = replace(late, history=(said(review.LEFT_AS_IS, datetime(2026, 10, 20, tzinfo=TZ), n=1), *late.history))
    assert rules.free_slot([ended_in_focus(), early, long_ago], WEEK.start, WEEK.end, TZ).id == late.id


def test_no_proposal_without_a_slot_freed_this_week_or_once_the_owner_filled_it():
    backups = candidate("Home server backups", "p-0002", said(rules.CONTINUE_SAID[0], at(19, 8)))
    assert rules.free_slot([backups], WEEK.start, WEEK.end, TZ) is None
    assert rules.free_slot([ended_in_focus(when=at(13)), backups], WEEK.start, WEEK.end, TZ) is None
    filled = candidate("Knee", "p-0005", said(rules.FOCUS_SAID[True][0], at(20)), focus=True)
    assert rules.free_slot([ended_in_focus(), backups, filled], WEEK.start, WEEK.end, TZ) is None
    assert rules.free_slot([ended_in_focus(), candidate("Quiet", "p-0006")], WEEK.start, WEEK.end, TZ) is None


def test_closing_a_focus_pursuit_records_that_it_left_focus_and_closing_another_does_not():
    said_ = Cite(NOTE, "your Note", at(19, 7))
    closed, reply = rules._set_status(pursuit(focus=True), SetStatus(PursuitId("p-0001"), Status.DONE), said_)
    assert [h.statement.text for h in closed.history[-2:]] == [rules.STATUS_SAID[Status.DONE][0], rules.LEFT_FOCUS]
    assert reply.endswith(rules.LEAVES_FOCUS)
    closed, _ = rules._set_status(pursuit(), SetStatus(PursuitId("p-0001"), Status.DONE), said_)
    assert closed.history[-1].statement.text == rules.STATUS_SAID[Status.DONE][0]


def logged(deleted: bool, when: datetime, harness=HarnessId.CHATGPT, broken: int = 0) -> ImportLog:
    return ImportLog(harness, when, 3, deleted, None if deleted or broken else "BadZipFile", "1:2:3:4", broken)


def test_imports_lists_only_the_exports_kept_after_a_failure():
    assert [i.lines[0] for i in review.imports([logged(True, at(18)), logged(False, at(19)), logged(False, at(2)),
                                                logged(False, at(20), None)], WEEK).items] == [
        "Kept a ChatGPT export in your inbox on Thursday: I could not read all of it. [F]",
        "Kept a file in your inbox on Friday: I could not read all of it. [F]"]


def test_an_empty_week_is_a_short_review_and_draws_on_no_session():
    text, drawn = review.render(WEEK.start, WEEK.end, [review.finished([], WEEK), review.own([], Profile((), ()), WEEK)])
    assert text == f"Atlas · Review · week of 16 to 22 November\n\n{review.QUIET}" and drawn == ()


def test_the_text_records_which_lines_each_session_draws_on_and_forget_removes_exactly_those():
    sections = [review.Section("ONE", (review.Item(("a",), frozenset({CC})), review.Item(("b", "c")))),
                review.Section("TWO", (review.Item(("d", "e"), frozenset({CC, EvidenceId("cx-b")})),))]
    text, drawn = review.render(WEEK.start, WEEK.end, sections)
    assert drawn == ((CC, (4, 9, 10)), (EvidenceId("cx-b"), (9, 10)))
    r = SentReview("2026-W47", (WEEK.start, WEEK.end), (), None, (), None, text, drawn)
    left = review.without(r, frozenset({CC}))
    assert left.text.split("\n") == ["Atlas · Review · week of 16 to 22 November", "", "ONE", "- b", "  c", "",
                                     "TWO"]
    assert left.drawn_from == ()


def test_a_review_covers_the_days_since_the_last_one_sent_so_the_first_after_a_pause_covers_the_gap():
    sent = SentReview("2026-W45", (date(2026, 11, 2), date(2026, 11, 8)),
                      (SendAttempt(at(8, 18) - timedelta(days=0), SendStatus.SENT, ("1",)),), None, (), None, "x")
    lost = replace(sent, week="2026-W46", covers=(date(2026, 11, 9), date(2026, 11, 15)),
                   attempts=(SendAttempt(at(15, 18), SendStatus.ARRIVAL_UNKNOWN, ()),))
    assert review.covers([], date(2026, 11, 22)) == (date(2026, 11, 16), date(2026, 11, 22))
    assert review.covers([sent, lost], date(2026, 11, 22)) == (date(2026, 11, 9), date(2026, 11, 22))
    assert review.heading(date(2026, 11, 9), date(2026, 11, 22)) == "Atlas · Review · 9 to 22 November"
    assert review.heading(date(2026, 10, 26), date(2026, 11, 1)) == "Atlas · Review · week of 26 October to 1 November"


# ------------------------------------------------------------------ G4: the brief time proposal

def starts(off: int, of: int = 14, at_: time = time(9, 40)) -> dict[date, time]:
    days = [date(2026, 11, 1) + timedelta(days=n) for n in range(of)]
    return {d: at_ if n < off else time(7, 45) for n, d in enumerate(days)}


def test_nine_of_fourteen_days_off_propose_nothing_and_ten_propose_the_median_rounded_down():
    assert rules.proposed_time(starts(9), set(), time(7, 30)) == (None, 9)
    spread = starts(10) | {date(2026, 11, 1): time(9, 0), date(2026, 11, 2): time(10, 20)}
    assert rules.proposed_time(spread, set(), time(7, 30)) == (time(9, 30), 10)


def test_starts_sixty_minutes_earlier_count_and_fifty_nine_do_not():
    assert rules.proposed_time(starts(10, at_=time(6, 30)), set(), time(7, 30)) == (time(6, 30), 10)
    assert rules.proposed_time(starts(10, at_=time(6, 31)), set(), time(7, 30))[0] is None


def test_paused_days_are_left_out_and_the_window_reaches_back_to_fourteen_unpaused_days():
    days = starts(10, of=16)
    paused = {date(2026, 11, 1), date(2026, 11, 2)}
    assert rules.proposed_time(days, paused, time(7, 30)) == (None, 8)
    assert rules.proposed_time(days, set(), time(7, 30)) == (None, 8)


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(SUNDAY)
        seeds.sunday_review(home.record_root)
        yield home


def snap(home):
    return Record.open(home.record_root).snapshot()


def test_a_proposal_made_three_weeks_ago_means_no_new_one(home):
    record = Record.open(home.record_root)
    earlier = SentReview("2026-W44", (date(2026, 10, 26), date(2026, 11, 1)),
                         (SendAttempt(datetime(2026, 11, 1, 18, tzinfo=TZ), SendStatus.SENT, ("1",)),), None, (),
                         time(9, 30), "Atlas · Review · week of 26 October to 1 November")
    record.commit(record.snapshot().changes().put_review(earlier))
    assert rules.time_proposal_of(snap(home), SUNDAY.date()) == (None, 0)


# ------------------------------------------------------------------ the parked raise

def parked_since(days: int, raised: date | None = None) -> Pursuit:
    last = SUNDAY - timedelta(days=days)
    site = seeds.evidence_id(world.by_title("move posts to astro"))
    return pursuit("Quiet one", "p-0q01", status=Status.PARKED, sessions=(Membership(site, last, True),),
                   resume=ResumeNote((), (), (), (), (site,)), raised_at=raised)


def put(home, p: Pursuit) -> None:
    record = Record.open(home.record_root)
    record.commit(seeds._lent(record).changes().put_pursuit(p))


def test_a_parked_pursuit_is_raised_at_ninety_days_quiet_and_not_at_eighty_four(home):
    put(home, parked_since(84))
    assert rules.parked_to_raise(snap(home), SUNDAY.date()).id == seeds.PIHOLE
    put(home, parked_since(110))
    assert rules.parked_to_raise(snap(home), SUNDAY.date()).id == "p-0q01"


def test_keep_parked_or_a_raise_in_a_review_restarts_the_ninety_days(home):
    put(home, parked_since(200, raised=SUNDAY.date() - timedelta(days=30)))
    assert rules.parked_to_raise(snap(home), SUNDAY.date()).id == seeds.PIHOLE
    record = Record.open(home.record_root)
    raised = SentReview("2026-W38", (date(2026, 9, 14), date(2026, 9, 20)),
                        (SendAttempt(datetime(2026, 9, 20, 18, tzinfo=TZ), SendStatus.SENT, ("1",)),), seeds.PIHOLE, (),
                        None, "x")
    record.commit(record.snapshot().changes().put_review(raised))
    assert rules.parked_to_raise(snap(home), SUNDAY.date()) is None


# ------------------------------------------------------------------ due, sent once, the file

def test_the_review_is_due_on_its_day_at_its_time_once_a_week_and_never_while_paused(home):
    s = snap(home)
    assert schedule.due(s, SUNDAY - timedelta(minutes=1)) == []
    assert schedule.due(s, SUNDAY) == [schedule.ReviewDue("2026-W47")]
    assert schedule.due(s, SUNDAY + timedelta(days=1, hours=1)) == [schedule.BriefDue(date(2026, 11, 23))]
    record = Record.open(home.record_root)
    record.commit(s.changes().put_pauses((Pause(SUNDAY - timedelta(hours=1), SUNDAY + timedelta(hours=1), "x"),)))
    assert schedule.due(snap(home), SUNDAY) == []


def test_no_review_before_a_brief_was_sent_on_an_earlier_day(tmp_path):
    with AtlasHome(tmp_path / "fresh") as home:
        home.set_clock(SUNDAY)
        seeds.after_scene_6(home.record_root)
        assert schedule.due(snap(home), SUNDAY) == [schedule.BriefDue(SUNDAY.date())]


def test_the_tick_sends_it_once_masked_under_four_thousand_units_and_a_second_tick_sends_nothing(home):
    home.cli("atlas", "tick", "--quiet")
    home.set_clock(SUNDAY + timedelta(hours=2))
    home.cli("atlas", "tick", "--quiet")
    (sent,) = home.sent()
    assert len(sent["text"].encode("utf-16-le")) // 2 <= 4000
    kept = snap(home).review("2026-W47")
    assert kept.status is SendStatus.SENT and kept.attempts[-1].message_ids == (sent["message_id"],)


def test_a_send_that_fails_is_sent_once_more_that_day_and_never_a_third_time(home):
    home.script_hermes("send", exit=1, stdout=json.dumps({"error": "Telegram send timed out"}, indent=2))
    home.cli("atlas", "tick", "--quiet")
    assert snap(home).review("2026-W47").status is SendStatus.ARRIVAL_UNKNOWN
    for minutes in (10, 20):
        home.set_clock(SUNDAY + timedelta(minutes=minutes))
        assert home.cli("atlas", "tick", "--quiet").code == 0
    assert len(snap(home).review("2026-W47").attempts) == 2


def test_the_review_file_round_trips_and_the_sweep_takes_it_for_atlas_own_but_puts_back_a_hand_edit(home):
    home.cli("atlas", "tick", "--quiet")
    record = Record.open(home.record_root)
    assert not record.reconcile()
    path = home.record_root / "reviews/2026/2026-W47.md"
    kept = snap(home).review("2026-W47")
    path.write_text(path.read_text().replace("FINISHED", "FINISHED by hand"))
    (edit,) = record.reconcile()
    assert edit.path == "reviews/2026/2026-W47.md" and snap(home).review("2026-W47") == kept
    assert "by hand" not in path.read_text()


def test_forget_removes_the_review_lines_a_session_drew_and_its_id_and_keeps_the_rest(home):
    home.cli("atlas", "tick", "--quiet")
    loop = seeds.evidence_id(world.by_title("pi-hole dns loop"))
    record = Record.open(home.record_root)
    s = snap(home)
    before = s.review("2026-W47")
    from atlas import forget
    record.commit(forget.execute(s, (loop,), SUNDAY + timedelta(hours=1)))
    after = snap(home).review("2026-W47")
    assert "Left off: found the loop" not in after.text and "Pi-hole" not in after.text
    assert loop not in (home.record_root / "reviews/2026/2026-W47.md").read_text()
    assert review_items(after.text)["FOCUS"] == review_items(before.text)["FOCUS"]
    assert after.text.count("\n") == before.text.count("\n") - 3


def test_the_forget_preview_counts_the_review_lines(home):
    home.cli("atlas", "tick", "--quiet")
    from atlas import forget
    s = seeds._lent(Record.open(home.record_root))
    msg = OwnerMessage("atlas, forget the Claude Code Session from 14 Aug 22:00", SUNDAY, "telegram", None, None)
    preview = forget.preview(s, [s.session(seeds.evidence_id(world.by_title("pi-hole dns loop")))], msg)
    assert "- 3 lines in the Review of 16 Nov to 22 Nov" in preview.splitlines()


# ------------------------------------------------------------------ replies

def sent_review(home) -> str:
    home.cli("atlas", "tick", "--quiet")
    home.set_clock(SUNDAY + timedelta(minutes=5))
    return home.sent()[0]["message_id"]


def test_add_backups_replied_to_the_review_adds_them_to_focus_and_leave_it_drops_the_proposal(home):
    id = sent_review(home)
    assert home.telegram("leave it", reply_to=id) == "Home server backups: I'll leave it as it is."
    backups = snap(home).pursuit(seeds.BACKUPS)
    assert not backups.focus and not backups.proposals


def test_keep_parked_restarts_the_raise_clock_and_says_when_it_comes_back(home):
    id = sent_review(home)
    assert home.telegram("keep parked", reply_to=id) == "Pi-hole DNS loop: stays parked. I'll ask again in 90 days."
    s = snap(home)
    assert s.pursuit(seeds.PIHOLE).raised_at == SUNDAY.date() and s.pursuit(seeds.PIHOLE).status is Status.PARKED
    assert review.parked_since(s.reviews(), s.pursuit(seeds.PIHOLE)) == SUNDAY.date()


@pytest.mark.parametrize("word,status", [("continue", Status.ACTIVE), ("drop", Status.DROPPED)])
def test_a_bare_continue_or_drop_answers_only_the_parked_pursuit_the_review_raised(home, word, status):
    id = sent_review(home)
    assert home.telegram(word, reply_to=id) is not None
    s = snap(home)
    assert s.pursuit(seeds.PIHOLE).status is status
    assert s.pursuit(seeds.BACKUPS).proposals and s.pursuit(seeds.BACKUPS).status is Status.ACTIVE


def test_a_bare_word_that_fits_two_questions_asks_which_and_changes_nothing(home):
    staging = snap(home).pursuit(seeds.STAGING)
    for p in (replace(staging, status=Status.ACTIVE), snap(home).pursuit(seeds.BACKUPS)):
        put(home, replace(p, proposals=(FocusProposal(True, Inference("A free slot.", (CC,)), WED.date()),)))
    id = sent_review(home)
    pursuits = lambda: {k: v for k, v in home.files().items() if "pursuits/" in k}
    files = pursuits()
    assert home.telegram("leave it", reply_to=id) == \
        'Which one: Billing export: staging deploy or Home server backups? Reply "leave" with its name.'
    assert pursuits() == files
    assert home.telegram("leave backups", reply_to=id) == "Home server backups: I'll leave it as it is."


def test_review_words_outside_a_review_go_to_hermes_and_a_brief_reply_keeps_the_brief_meaning(tmp_path):
    with AtlasHome(tmp_path / "thursday") as home:
        home.set_clock(world.CLOSE_AT)
        seeds.after_scene_20(home.record_root)
        for words in ("leave it", "keep parked", "add backups", "move it"):
            assert home.telegram(f"atlas, {words}") is None
        assert home.telegram("continue", reply_to=seeds.THURSDAY_BRIEF_ID) == \
            "Home server backups: stays active. Its quiet days count from today."


def test_a_bare_word_after_the_review_follows_it_and_after_the_next_brief_follows_the_brief(home):
    sent_review(home)
    assert home.telegram("atlas, keep parked") == "Pi-hole DNS loop: stays parked. I'll ask again in 90 days."
    home.set_clock(SUNDAY + timedelta(hours=14))
    home.cli("atlas", "tick", "--quiet")
    home.set_clock(SUNDAY + timedelta(hours=14, minutes=30))
    assert home.telegram("atlas, leave it") is None


def test_move_it_and_keep_it_answer_only_a_review_that_proposed_a_time(home):
    record = Record.open(home.record_root)
    s = snap(home)
    msg = OwnerMessage("move it", SUNDAY + timedelta(minutes=5), "telegram", seeds.HOME_CHAT, SentRef("review", "2026-W47"))
    assert review.answer_time("move", msg, s) is None
    record.commit(s.changes().put_review(SentReview(
        "2026-W47", (WEEK.start, WEEK.end), (SendAttempt(SUNDAY, SendStatus.SENT, ("1",), chat=seeds.HOME_CHAT),),
        None, (), time(9, 30), "x")))
    s = snap(home)
    moved = review.answer_time("move", msg, s)
    decision = rules.converse(s, msg, (moved,), ())
    assert decision.outcome.lines == ("Moved the Daily Brief to 09:30.",)
    record.commit(decision.changes)
    assert snap(home).settings().brief_at == time(9, 30)
    kept = rules.converse(snap(home), replace(msg, text="keep it", platform_ref="x"),
                          (review.answer_time("keep", msg, snap(home)),), ())
    assert kept.outcome.lines == ("The Daily Brief stays at 09:30.",)


def test_two_parks_by_silence_and_a_raise_make_a_bare_continue_ask_which(home):
    s = snap(home)
    r = SentReview("2026-W47", (WEEK.start, WEEK.end), (SendAttempt(SUNDAY, SendStatus.SENT, ("1",)),),
                   seeds.PIHOLE, (seeds.PIHOLE, PursuitId("p-1k2f")), None, "x")
    keyboard = s.pursuit("p-1k2f")
    record = Record.open(home.record_root)
    silenced = replace(keyboard, history=(*keyboard.history, HistoryLine(
        rules._next_history_id(keyboard), at(20), Actor.ATLAS,
        Fact(review.neglect.PARKED_BY_SILENCE, Count("quiet_days", ())))))
    record.commit(s.changes().put_review(r).put_pursuit(silenced))
    msg = OwnerMessage("continue", SUNDAY + timedelta(minutes=5), "telegram", None, SentRef("review", "2026-W47"))
    assert review.answer("continue", None, msg, snap(home)) == WhichQ(("Keyboard firmware", "Pi-hole DNS loop"),
                                                                       "continue")
    assert review.answer("continue", "keyboard", msg, snap(home)) == Continue(PursuitId("p-1k2f"))
    assert review.answer("drop", None, msg, snap(home)) == SetStatus(seeds.PIHOLE, Status.DROPPED)


@pytest.mark.parametrize("word", ["drop", "continue", "leave it", "keep parked"])
def test_a_bare_word_after_atlas_answered_something_else_changes_nothing_and_goes_to_hermes(home, word):
    id = sent_review(home)
    home.set_clock(SUNDAY + timedelta(minutes=45))
    assert home.telegram("atlas, where did I leave off on the keyboard firmware?") is not None
    before = snap(home)
    home.set_clock(SUNDAY + timedelta(minutes=46))
    assert home.telegram(word) is None
    after = snap(home)
    assert after.pursuit(seeds.PIHOLE) == before.pursuit(seeds.PIHOLE)
    assert after.pursuit(seeds.BACKUPS) == before.pursuit(seeds.BACKUPS)
    assert home.telegram("leave it", reply_to=id) == "Home server backups: I'll leave it as it is."


def test_a_bare_word_straight_after_the_review_still_answers_it(home):
    sent_review(home)
    assert home.telegram("atlas, drop").startswith("Pi-hole DNS loop: dropped.")


def test_no_time_is_proposed_that_equals_the_brief_time_or_midnight():
    days = [date(2026, 11, 2) + timedelta(days=n) for n in range(14)]
    split = {d: time(6, 30) if n % 2 else time(8, 30) for n, d in enumerate(days)}
    assert rules.proposed_time(split, set(), time(7, 30)) == (None, 14)
    assert rules.proposed_time({d: time(0, 5) for d in days}, set(), time(7, 30)) == (None, 14)


def test_move_it_quoting_a_review_that_is_not_the_latest_is_left_unread(home):
    record = Record.open(home.record_root)
    older = SentReview("2026-W46", (date(2026, 11, 9), date(2026, 11, 15)),
                       (SendAttempt(SUNDAY - timedelta(days=7), SendStatus.SENT, ("1",)),), None, (), time(9, 30), "x")
    latest = SentReview("2026-W47", (WEEK.start, WEEK.end), (SendAttempt(SUNDAY, SendStatus.SENT, ("2",)),), None, (),
                        None, "x")
    record.commit(snap(home).changes().put_review(older).put_review(latest))
    msg = OwnerMessage("move it", SUNDAY + timedelta(minutes=5), "telegram", seeds.HOME_CHAT, SentRef("review", "2026-W46"))
    assert review.answer_time("move", msg, snap(home)) is review.UNREAD


def test_keep_parked_on_a_parked_pursuit_with_an_open_proposal_restarts_the_clock_and_keeps_the_proposal(home):
    pi = snap(home).pursuit(seeds.PIHOLE)
    proposal = EndingProposal(Status.DROPPED, Inference("Quiet for months.", pi.resume.reopen), WEDNESDAY, False)
    put(home, replace(pi, proposals=(proposal,)))
    id = sent_review(home)
    assert home.telegram("keep parked", reply_to=id) == "Pi-hole DNS loop: stays parked. I'll ask again in 90 days."
    kept = snap(home).pursuit(seeds.PIHOLE)
    assert kept.raised_at == SUNDAY.date() and kept.proposals == (proposal,)


def test_parked_counts_calendar_days_since_the_last_session_while_the_raise_clock_skips_the_pause(home):
    s = snap(home)
    assert len(s.pauses()) == 1
    pi = s.pursuit(seeds.PIHOLE)
    assert rules.clock_days(pi.last_worked, SUNDAY.date(), s.pauses(), s.settings()) == 99
    home.cli("atlas", "tick", "--quiet")
    assert review_items(home.sent()[0]["text"])["PARKED"][0].startswith("- Pi-hole DNS loop · problem · quiet 100 days")


def test_no_review_while_paused_by_message_and_it_goes_out_once_the_pause_ends_that_day(home):
    home.set_clock(SUNDAY - timedelta(minutes=10))
    assert home.telegram("atlas, pause until 19:00").startswith("Paused until Sun 22 Nov 19:00.")
    home.set_clock(SUNDAY)
    home.cli("atlas", "tick", "--quiet")
    home.set_clock(SUNDAY + timedelta(minutes=50))
    home.cli("atlas", "tick", "--quiet")
    assert home.sent() == []
    home.set_clock(SUNDAY + timedelta(hours=1))
    home.cli("atlas", "tick", "--quiet")
    (sent,) = home.sent()
    assert sent["text"].startswith("Atlas · Review · week of 16 to 22 November")


def test_the_review_lists_a_partly_read_export_from_the_import_log(home):
    record = Record.open(home.record_root)
    record.commit(snap(home).changes().put_imports((logged(False, at(21, 9), HarnessId.CLAUDE_AI, broken=2),)))
    home.cli("atlas", "tick", "--quiet")
    assert review_items(home.sent()[0]["text"])["IMPORTS"] == [
        "- Kept a claude.ai export in your inbox on Saturday: I could not read all of it. [F]"]
