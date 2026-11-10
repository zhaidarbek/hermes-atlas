from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from atlas import converse, pausing, rules, schedule
from atlas.model import BriefStep, EndPause, OwnerMessage, Pause, PauseCmd, SendAttempt, SendStatus, SentBrief
from atlas.record import Record, _codec
from harness import seeds, world
from harness.home import AtlasHome
from snapshots import BERLIN, SETTINGS, after, at, pursuit, snapshot

FRIDAY, SATURDAY, SUNDAY = date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22)
WEEKEND = Pause(at(FRIDAY, "16:40"), at(SUNDAY, "07:00"), "until sunday morning, we're at my parents'")


@pytest.mark.parametrize("words, end", [
    ("sunday morning", at(SUNDAY, "07:00")),
    ("sunday", at(SUNDAY, "00:00")),
    ("Sunday  evening", at(SUNDAY, "18:00")),
    ("friday", at(date(2026, 11, 27), "00:00")),
    ("next friday", at(date(2026, 11, 27), "00:00")),
    ("next sunday", at(SUNDAY, "00:00")),
    ("tomorrow at 9am", at(SATURDAY, "09:00")),
    ("monday at 08:15", at(date(2026, 11, 23), "08:15")),
    ("30 nov", at(date(2026, 11, 30), "00:00")),
    ("nov 30 at 6pm", at(date(2026, 11, 30), "18:00")),
    ("18:00", at(FRIDAY, "18:00")),
    ("9:00", at(SATURDAY, "09:00")),
    ("3 nov", None),
    ("31 feb", None),
    ("25:00", None),
    ("the kids are back", None),
], ids=lambda v: str(v))
def test_the_end_of_a_pause_is_read_from_the_owners_words(words, end):
    assert pausing.until(words, at(FRIDAY, "16:40"), BERLIN) == end


def test_a_date_early_next_year_is_next_years():
    assert pausing.until("3 jan", at(date(2026, 12, 20), "10:00"), BERLIN) == at(date(2027, 1, 3), "00:00")


@pytest.mark.parametrize("words, days", [("3 days", 3), ("a week", 7), ("two weeks", 14), ("0 days", None),
                                         ("a fortnight", None)])
def test_a_pause_for_a_number_of_days_or_weeks(words, days):
    now = at(FRIDAY, "16:40")
    assert pausing.lasting(words, now) == (now + timedelta(days=days) if days else None)


def _read(text: str, now: datetime = at(FRIDAY, "16:40")) -> converse.Interpretation:
    return converse.interpret(OwnerMessage(text, now, "telegram", None, None, addressed=True), snapshot(), None)


@pytest.mark.parametrize("text, cmd", [
    ("pause until sunday morning, we're at my parents'",
     PauseCmd(at(SUNDAY, "07:00"), "until sunday morning, we're at my parents'")),
    ("pause", PauseCmd(None, "")),
    ("Pause.", PauseCmd(None, "")),
    ("pause, I'm ill", PauseCmd(None, "I'm ill")),
    ("pause for 3 days", PauseCmd(at(FRIDAY, "16:40") + timedelta(days=3), "for 3 days")),
    ("I'm back", EndPause()),
    ("back!", EndPause()),
    ("unpause", EndPause()),
])
def test_pause_and_back_are_read_without_a_model(text, cmd):
    assert _read(text).commands == (cmd,)


@pytest.mark.parametrize("text", ["pause until the kids are back", "pause the newsletter", "pause until 3 nov"])
def test_a_pause_whose_end_atlas_cannot_read_is_left_to_hermes(text):
    assert _read(text).unclear == converse.UNREAD


def test_the_owner_pauses_with_their_words_kept():
    pauses, said = pausing.pause((), PauseCmd(at(SUNDAY, "07:00"), WEEKEND.said), at(FRIDAY, "16:40"), SETTINGS)
    assert pauses == (WEEKEND,)
    assert said == ("Paused until Sun 22 Nov 07:00. No Daily Brief on Saturday, and no Pursuit becomes Neglected "
                    "while Atlas is paused. Write to me to come back early.")


def test_a_pause_with_no_end_lasts_a_week_and_says_so():
    pauses, said = pausing.pause((), PauseCmd(None, ""), at(FRIDAY, "16:40"), SETTINGS)
    assert pauses == (Pause(at(FRIDAY, "16:40"), at(date(2026, 11, 27), "16:40"), ""),)
    assert said.startswith("Paused for one week, until Fri 27 Nov 16:40, since you gave no end. "
                           "No Daily Brief from Saturday to Thursday and no Review on Sunday,")
    assert said.endswith("The next Daily Brief comes on Fri 27 Nov at 16:40, when the Pause ends. "
                         "Write to me to come back early.")


def test_a_second_pause_ends_the_first_where_it_begins():
    pauses, _ = pausing.pause((WEEKEND,), PauseCmd(at(date(2026, 11, 25), "07:00"), "until wednesday"),
                              at(SATURDAY, "10:00"), SETTINGS)
    assert [(p.start, p.end) for p in pauses] == [(at(FRIDAY, "16:40"), at(SATURDAY, "10:00")),
                                                  (at(SATURDAY, "10:00"), at(date(2026, 11, 25), "07:00"))]


@pytest.mark.parametrize("now, briefed, said", [
    (at(SATURDAY, "06:00"), False, "Your Daily Brief comes today at 07:30."),
    (at(SATURDAY, "10:00"), False, "Today's Daily Brief comes within 10 minutes."),
    (at(SATURDAY, "10:00"), True, "Your next Daily Brief comes tomorrow at 07:30."),
])
def test_coming_back_early_ends_the_pause_now(now, briefed, said):
    pauses, reply = pausing.end_pause((WEEKEND,), now, SETTINGS, briefed)
    assert pauses == (Pause(WEEKEND.start, now, WEEKEND.said),)
    assert reply == f"Welcome back. The Pause has ended. {said}"


def test_back_when_not_paused_changes_nothing():
    assert pausing.end_pause((WEEKEND,), at(SUNDAY, "09:00"), SETTINGS, True) == (
        (WEEKEND,), "Atlas is not paused.")


@pytest.mark.parametrize("pause, now, expected", [
    (WEEKEND, at(SATURDAY, "07:30"), True),
    (WEEKEND, at(FRIDAY, "16:39"), False),
    (WEEKEND, at(FRIDAY, "16:40"), True),
    (WEEKEND, at(SUNDAY, "07:00"), False),
], ids=["covering-the-brief-time", "before-it-starts", "from-its-first-minute", "at-its-end"])
def test_paused(pause, now, expected):
    assert rules.paused((pause,), now) is expected


@pytest.mark.parametrize("pause, days", [
    (WEEKEND, 1),
    (Pause(at(FRIDAY, "16:40"), at(SUNDAY, "07:45"), "x"), 0),
    (Pause(at(FRIDAY, "16:40"), at(SATURDAY, "07:00"), "x"), 2),
], ids=["covering-saturday", "covering-both-brief-times", "ending-before-the-brief-time"])
def test_a_day_counts_on_a_clock_only_if_no_pause_covered_its_brief_time(pause, days):
    assert rules.clock_days(FRIDAY, SUNDAY, (pause,), SETTINGS) == days


def test_paused_days_do_not_count_toward_neglected():
    quiet = pursuit("p-0qui", "Quiet thirteen days", worked=[date(2026, 11, 8)])
    assert [p.name for p in rules.neglected(snapshot([quiet]), SUNDAY)] == ["Quiet thirteen days"]
    assert rules.neglected(snapshot([quiet], pauses=[WEEKEND]), SUNDAY) == []


def _brief(day: date, step_of: str) -> SentBrief:
    return SentBrief(day, (SendAttempt(at(day, "07:30"), SendStatus.SENT, ("1",)),), None, (),
                     (BriefStep(1, step_of, "s-20261115-1"),), None, False, "Atlas · Daily Brief")


def test_no_offer_is_counted_while_paused_so_the_step_is_not_skipped_by_silence():
    reading = pursuit("p-0rea", "Reading tracker", worked=[date(2026, 11, 18)], focus=True)
    snap = snapshot([reading], pauses=[WEEKEND], briefs=[_brief(FRIDAY, "p-0rea")])
    sunday = after(rules.compose_daily(snap, at(SUNDAY, "07:30")))
    assert rules.offers(sunday, reading.id, reading.next_step) == 1
    assert sunday.pursuit("p-0rea").next_step.ended is None
    assert sunday.brief(SUNDAY).follows_up == FRIDAY


def test_a_held_step_whose_day_fell_in_the_pause_comes_first_after_it():
    held = pursuit("p-0hel", "Held one", worked=[date(2026, 11, 18)], focus=True, held_until=SATURDAY)
    other = pursuit("p-0oth", "Other", worked=[date(2026, 11, 19)], focus=True)
    sunday = rules.compose_daily(snapshot([other, held], pauses=[WEEKEND]), at(SUNDAY, "07:30")).send
    assert [s.pursuit for s in sunday.steps] == ["p-0hel", "p-0oth"]


def test_nothing_is_composed_while_paused():
    snap = snapshot([pursuit("p-0rea", "Reading tracker", worked=[FRIDAY], focus=True)], pauses=[WEEKEND])
    decision = rules.compose_daily(snap, at(SATURDAY, "07:30"))
    assert decision.send is None and after(decision).brief(SATURDAY) is None


@pytest.mark.parametrize("now, expected", [
    (at(SATURDAY, "07:30"), []),
    (at(SATURDAY, "23:50"), []),
    (at(SUNDAY, "06:59"), []),
    (at(SUNDAY, "07:30"), [SUNDAY]),
])
def test_nothing_is_due_through_the_pause(now, expected):
    assert schedule.due(snapshot(pauses=[WEEKEND]), now) == [schedule.BriefDue(d) for d in expected]


def test_the_brief_is_due_after_coming_back_the_same_day():
    back, _ = pausing.end_pause((WEEKEND,), at(SATURDAY, "10:00"), SETTINGS, False)
    assert schedule.due(snapshot(pauses=back), at(SATURDAY, "10:10")) == [schedule.BriefDue(SATURDAY)]


def test_a_pause_that_ends_after_the_brief_time_sends_the_brief_that_day():
    late = Pause(at(FRIDAY, "16:40"), at(SUNDAY, "09:00"), "x")
    assert schedule.due(snapshot(pauses=[late]), at(SUNDAY, "08:50")) == []
    assert schedule.due(snapshot(pauses=[late]), at(SUNDAY, "09:00")) == [schedule.BriefDue(SUNDAY)]


def _said(text: str, now: datetime, ref: str = "5550001:77"):
    msg = OwnerMessage(text, now, "telegram", "telegram:5550001", None, ref, addressed=True)
    return msg, converse.interpret(msg, snapshot(), None)


def test_a_pause_is_written_to_pauses_md_with_the_owners_note():
    msg, read = _said("pause until sunday morning, we're at my parents'", at(FRIDAY, "16:40"))
    decision = rules.converse(snapshot(), msg, read.commands, read.asks)
    written = after(decision)
    assert written.pauses() == (WEEKEND,)
    assert written.notes()[-1].atlas_reply.startswith("Paused until Sun 22 Nov 07:00.")


def test_a_redelivered_pause_changes_nothing():
    msg, read = _said("pause until sunday morning", at(FRIDAY, "16:40"))
    once = after(rules.converse(snapshot(), msg, read.commands, read.asks))
    again = rules.converse(once, msg, read.commands, read.asks)
    assert after(again).pauses() == once.pauses() and len(after(again).notes()) == len(once.notes())
    assert again.outcome.lines == (once.notes()[-1].atlas_reply,)


def test_the_pauses_md_codec_round_trips():
    pauses = (WEEKEND, Pause(at(SUNDAY, "18:00"), at(date(2026, 11, 29), "18:00"), ""))
    assert _codec.parse("pauses", _codec.render("pauses", pauses, BERLIN), BERLIN) == pauses



ASKED_OR_TAKEN_BACK = ["pause... actually no", "pause!! no wait", "pause, nope", "pause, nah", "pause, scratch that",
                       "pause, forget it", "pause, kidding", "pause - what does that do?",
                       "pause, what does that do?", "pause. actually no, don't",
                       "pause: no wait, never mind", "pause until sunday, ok?", "pause, cancel that",
                       "pause, don’t", "pause?", "don't pause"]


@pytest.mark.parametrize("text", ASKED_OR_TAKEN_BACK)
def test_a_pause_asked_about_or_taken_back_is_left_to_hermes(text):
    read = _read(text)
    assert read.commands == () and read.unclear == converse.UNREAD


@pytest.mark.parametrize("text", ["pause, nothing tonight", "pause until sunday morning, nowhere near a laptop"])
def test_own_words_that_only_begin_like_a_retraction_still_pause(text):
    assert isinstance(_read(text).commands[0], PauseCmd)


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(world.PROMPT_AT)
        seeds.after_scene_7(home.record_root)
        yield home


def _record(home):
    return {path: data for path, data in home.files().items() if path.startswith("atlas/")}


@pytest.mark.parametrize("text", ASKED_OR_TAKEN_BACK[:4])
@pytest.mark.parametrize("how", ["addressed", "in-the-window", "reply-to-a-brief"])
def test_no_pause_is_written_for_a_question_or_a_retraction_however_it_reaches_atlas(home, text, how):
    if how == "in-the-window":
        assert home.telegram("atlas, 1 prompt") is not None
    before, was = _record(home), Record.open(home.record_root).snapshot()
    said = f"atlas, {text}" if how == "addressed" else text
    assert home.telegram(said, reply_to=seeds.MONDAY_BRIEF_ID if how == "reply-to-a-brief" else None) is None
    if how != "reply-to-a-brief":
        assert _record(home) == before
        return
    now, asked = Record.open(home.record_root).snapshot(), was.brief(seeds.MONDAY)
    assert (now.pauses(), now.notes(), now.pursuits()) == (was.pauses(), was.notes(), was.pursuits())
    assert now.brief(seeds.MONDAY) == replace(asked, decision=replace(asked.decision, heard=True))


def test_back_as_a_reply_to_the_pause_reply_outside_the_window_is_left_to_hermes(home):
    reply = home.telegram("atlas, pause until sunday morning")
    assert reply.startswith("Paused until Sun 22 Nov 07:00.")
    home.set_clock(world.PROMPT_AT + timedelta(minutes=30))
    assert home.telegram("I'm back", reply_to=home.last_reply_id) is None
    assert rules.paused(Record.open(home.record_root).snapshot().pauses(), home.now)
    assert home.telegram("atlas, I'm back").startswith("Welcome back.")


def test_a_bare_pause_writes_no_you_said_line():
    body = _codec.render("pauses", (Pause(at(FRIDAY, "16:40"), at(date(2026, 11, 27), "16:40"), ""),), BERLIN).decode()
    assert "You said" not in body and "- Fri 20 Nov 16:40 to Fri 27 Nov 16:40.\n" in body


@pytest.mark.parametrize("words, now, end", [
    ("5", at(FRIDAY, "16:40"), at(FRIDAY, "17:00")),
    ("11", at(FRIDAY, "16:40"), at(FRIDAY, "23:00")),
    ("4", at(FRIDAY, "16:40"), at(SATURDAY, "04:00")),
    ("5", at(FRIDAY, "03:00"), at(FRIDAY, "05:00")),
    ("5am", at(FRIDAY, "16:40"), at(SATURDAY, "05:00")),
    ("5:00", at(FRIDAY, "16:40"), at(SATURDAY, "05:00")),
], ids=str)
def test_a_bare_hour_already_gone_today_is_the_evening_hour_when_that_is_ahead(words, now, end):
    assert pausing.until(words, now, BERLIN) == end


def test_the_reply_to_pause_till_5_states_the_end():
    read = _read("pause till 5", at(FRIDAY, "16:40"))
    _, said = pausing.pause((), read.commands[0], at(FRIDAY, "16:40"), SETTINGS)
    assert said.startswith("Paused until Fri 20 Nov 17:00.")


def test_a_pause_ending_after_the_brief_time_names_when_that_days_brief_comes():
    pauses, said = pausing.pause((), PauseCmd(at(SUNDAY, "09:00"), "until sunday 9am"), at(FRIDAY, "16:40"), SETTINGS)
    assert said == ("Paused until Sun 22 Nov 09:00. No Daily Brief on Saturday, and no Pursuit becomes Neglected "
                    "while Atlas is paused. The next Daily Brief comes on Sun 22 Nov at 09:00, when the Pause ends. "
                    "Write to me to come back early.")
    assert pausing.brief_from(SUNDAY, pauses, SETTINGS) == at(SUNDAY, "09:00")
    assert schedule.due(snapshot(pauses=pauses), at(SUNDAY, "09:00")) == [schedule.BriefDue(SUNDAY)]


def test_a_pause_over_the_brief_time_this_morning_names_the_brief_when_it_ends():
    _, said = pausing.pause((), PauseCmd(at(FRIDAY, "09:00"), "until 9"), at(FRIDAY, "06:00"), SETTINGS)
    assert said == ("Paused until Fri 20 Nov 09:00. No Pursuit becomes Neglected while Atlas is paused. "
                    "The next Daily Brief comes on Fri 20 Nov at 09:00, when the Pause ends. "
                    "Write to me to come back early.")


class _Unreadable:
    def __init__(self, snap, failure):
        self._snap, self._failure = snap, failure

    def pauses(self):
        raise self._failure

    def __getattr__(self, name):
        return getattr(self._snap, name)


@pytest.mark.parametrize("failure", [ValueError("bad"), KeyError("end"), FileNotFoundError("pauses.md")],
                         ids=lambda f: type(f).__name__)
def test_an_unreadable_pauses_md_reads_as_no_pause_and_logs_its_error_type(failure, caplog):
    snap = _Unreadable(snapshot([pursuit("p-0rea", "Reading tracker", worked=[FRIDAY], focus=True)]), failure)
    assert schedule.due(snap, at(SATURDAY, "07:30")) == [schedule.BriefDue(SATURDAY)]
    assert rules.compose_daily(snap, at(SATURDAY, "07:30")).send is not None
    assert type(failure).__name__ in caplog.text and "bad" not in caplog.text


def test_a_pause_longer_than_any_atlas_writes_is_ignored(caplog):
    forever = Pause(at(FRIDAY, "16:40"), datetime(9999, 12, 31, tzinfo=BERLIN), "")
    assert schedule.due(snapshot(pauses=[forever]), at(SATURDAY, "07:30")) == [schedule.BriefDue(SATURDAY)]
    assert f"{pausing.LONGEST.days} days" in caplog.text
    longest = Pause(at(FRIDAY, "16:40"), at(FRIDAY, "16:40") + pausing.LONGEST, "")
    assert schedule.due(snapshot(pauses=[longest]), at(SATURDAY, "07:30")) == []
