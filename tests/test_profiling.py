from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from atlas import profiling
from atlas.model import (LOCAL, EvidenceId, Fact, Cite, HarnessId, Inference, Pattern, Profile, ProfileEntry,
                         SessionEvidence, Who, cites)

TZ = ZoneInfo("Europe/Berlin")
A, B = EvidenceId("cc-aaaa"), EvidenceId("cx-bbbb")
SHOWN = {id: SessionEvidence(id, HarnessId.CLAUDE_CODE, None, LOCAL, id, "t", datetime(2026, 11, d, 9, tzinfo=TZ),
                             datetime(2026, 11, d, 10, tzinfo=TZ), "pc", "~/x", None, (), ())
         for id, d in ((A, 2), (B, 9))}
OWN = ProfileEntry(Fact("You plan on Sundays.", Cite(EvidenceId("nt-1"), "your Note", datetime(2026, 11, 1, tzinfo=TZ))),
                   date(2026, 11, 1), Who.OWNER)
PROFILE = Profile(("work",), (OWN,))


def row(text="You work late.", sessions=(A,), pattern=None, after=None):
    return {"text": text, "sessions": list(sessions), "pattern": pattern, "after": after}


def test_an_entry_rests_on_the_sessions_it_names():
    (entry,) = profiling.read({"entries": [row(after=1)]}, PROFILE, SHOWN)
    assert entry == profiling.NewEntry(Inference("You work late.", (A,)), 1)


def test_a_pattern_is_drawn_from_every_session_shown():
    (entry,) = profiling.read({"entries": [row(sessions=(), pattern="2 days of Sessions")]}, PROFILE, SHOWN)
    assert entry.statement.basis == Pattern("2 days of Sessions", date(2026, 11, 2), date(2026, 11, 9), (A, B))


@pytest.mark.parametrize("out", [
    {"entries": [row(text=f"You {n}.") for n in range(profiling.MAX_NEW + 1)]},
    {"entries": [row(sessions=(EvidenceId("cc-unseen"),))]},
    {"entries": [row(after=2)]},
    {"entries": [row(after=True)]},
    {"entries": [row(sessions=())]},
    {"entries": [row(text=" ")]},
])
def test_an_answer_beyond_what_the_input_holds_is_unusable(out):
    with pytest.raises(ValueError):
        profiling.read(out, PROFILE, SHOWN)


def test_an_entry_the_profile_holds_is_not_added_again():
    assert profiling.read({"entries": [row(text="You plan on Sundays."), row(), row()]}, PROFILE, SHOWN) == (
        profiling.NewEntry(Inference("You work late.", (A,)), None),)


def test_new_entries_go_after_the_entry_they_name_as_the_librarians():
    top, end, middle = (profiling.NewEntry(Inference(t, (A,)), at) for t, at in (("Top.", 0), ("End.", None), ("Mid.", 1)))
    two = profiling.added(PROFILE, [end], date(2026, 11, 15))
    got = profiling.added(two, [top, middle, profiling.NewEntry(Inference("End.", (B,)), 0)], date(2026, 11, 17))
    assert [e.statement.text for e in got.entries] == ["Top.", "You plan on Sundays.", "Mid.", "End."]
    assert [(e.added, e.by) for e in got.entries] == [(date(2026, 11, 17), Who.LIBRARIAN), (OWN.added, Who.OWNER),
                                                      (date(2026, 11, 17), Who.LIBRARIAN),
                                                      (date(2026, 11, 15), Who.LIBRARIAN)]


def test_the_day_input_names_each_step_the_brief_offered_and_how_it_ended():
    text = profiling.day_input(PROFILE, date(2026, 11, 16), None, {}, ["Worked on x."], SHOWN, zone=TZ)
    assert text.splitlines()[:3] == ["Profile entry 1 (the owner's): You plan on Sundays.", "", "Day: Monday 16 November 2026"]
    assert "  Worked on x." in text


def test_forget_reaches_each_entry_through_the_sessions_it_rests_on():
    named, drawn = profiling.read({"entries": [row(), row(text="You plan.", sessions=(), pattern="2 days")]},
                                  PROFILE, SHOWN)
    assert cites(named.statement) == {A} and cites(drawn.statement) == {A, B}


@pytest.mark.parametrize("text, pattern", [
    ("You plan [...] on Fridays.", None),
    ("You work late. [F your Note, Fri 13:02]", None),
    ("You work late [I · cc-aaaa]", None),
    ("You work late \\ often.", None),
    ("You work · late.", None),
    ("You work late <!-- added 2026-11-01 by you -->", None),
    ("You work late.", "2 days [...]"),
    ("You work late.", "2 days, [F your Note, Fri 13:02]"),
    ("You work late.", "2 days -->"),
])
def test_an_entry_shaped_like_the_files_marks_is_refused_and_the_rest_kept(text, pattern):
    out = {"entries": [row(text=text, sessions=() if pattern else (A,), pattern=pattern), row(text="You plan.")]}
    assert profiling.read(out, PROFILE, SHOWN) == (profiling.NewEntry(Inference("You plan.", (A,)), None),)


@pytest.mark.parametrize("bad", [
    {"pattern": 7, "sessions": []},
    {"pattern": ["2 days"], "sessions": []},
    {"sessions": [7]},
    {"sessions": "cc-aaaa"},
    {"text": 7},
])
def test_an_entry_with_a_value_of_the_wrong_type_makes_the_answer_unusable(bad):
    with pytest.raises((ValueError, TypeError)):
        profiling.read({"entries": [{**row(), **bad}]}, PROFILE, SHOWN)


@pytest.mark.parametrize("again", ["you skip  invented steps", "You skip invented Steps?", "You skip invented Steps…",
                                   "You skip, invented Steps;", '"You skip invented Steps"', "You skip invented Steps ."])
def test_a_text_the_owner_corrected_away_is_never_added_again(again):
    out = {"entries": [row(text=again), row(text="You work late.")]}
    assert profiling.read(out, PROFILE, SHOWN, corrected=["You skip invented Steps."]) == (
        profiling.NewEntry(Inference("You work late.", (A,)), None),)


def test_the_model_is_shown_whose_each_entry_is_and_what_the_owner_corrected_away():
    for text in (profiling.day_input(PROFILE, date(2026, 11, 16), None, {}, [], SHOWN, ["You skip invented Steps."], zone=TZ),
                 profiling.history_input(PROFILE, [], SHOWN, 10_000, ["You skip invented Steps."])):
        lines = text.splitlines()
        assert "Profile entry 1 (the owner's): You plan on Sundays." in lines
        assert "The owner corrected this away; never add it again: You skip invented Steps." in lines


def test_a_day_holds_the_sessions_of_that_day_in_the_settings_timezone():
    late = SessionEvidence(EvidenceId("cc-late"), HarnessId.CLAUDE_CODE, None, LOCAL, "late", "t",
                           datetime(2026, 11, 16, 23, 30, tzinfo=ZoneInfo("UTC")),
                           datetime(2026, 11, 16, 23, 50, tzinfo=ZoneInfo("UTC")), "pc", "~/x", None, (), ())
    assert profiling.on_day(late, date(2026, 11, 17), TZ) and not profiling.on_day(late, date(2026, 11, 16), TZ)
    text = profiling.day_input(PROFILE, date(2026, 11, 17), None, {}, [], {late.id: late}, zone=TZ)
    assert text.splitlines()[-1].startswith("  Session cc-late")
