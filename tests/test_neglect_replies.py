"""The owner's answers to the decision question, end to end: a reply to Thursday's brief, which asks
about the backups a second time, then Friday's and Saturday's ticks."""
from datetime import timedelta

import pytest

from atlas import neglect
from atlas.model import Actor, Status
from atlas.record import Record
from harness import seeds, world
from harness.home import AtlasHome

FRIDAY = world.THURSDAY_BRIEF_AT + timedelta(days=1)
SATURDAY = FRIDAY + timedelta(days=1)


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(world.CLOSE_AT)
        seeds.after_scene_20(home.record_root)
        yield home


def backups(home):
    return Record.open(home.record_root).snapshot().pursuit(seeds.BACKUPS)


def tick(home, at):
    home.set_clock(at)
    home.cli("atlas", "tick", "--quiet")
    return Record.open(home.record_root).snapshot().brief(at.date())


SAID = {"continue": "Home server backups: stays active. Its quiet days count from today.",
        "park": "Home server backups: parked.",
        "drop": "Home server backups: dropped. I won't push it again. You can still find it."}


@pytest.mark.parametrize("text", ["continue", "Continue", "continue it", "Continue it.", "park", "park it", "drop",
                                  "drop it", "Drop that!"])
def test_a_bare_answer_to_the_brief_answers_its_question_as_the_named_form_does(home, text):
    assert home.telegram(text, reply_to=seeds.THURSDAY_BRIEF_ID) == SAID[text.split()[0].strip(".!").lower()]
    assert backups(home).history[-1].actor is Actor.OWNER
    tick(home, FRIDAY)
    assert backups(home).history[-1].actor is Actor.OWNER


@pytest.mark.parametrize("text", ["keep the backups", "yes, continue the backups please"])
def test_words_atlas_cannot_read_still_answer_so_the_question_is_asked_again_and_nothing_is_parked(home, text):
    assert home.telegram(text, reply_to=seeds.THURSDAY_BRIEF_ID) is None
    friday = tick(home, FRIDAY)
    assert backups(home).status is Status.ACTIVE
    assert (friday.decision.pursuit, friday.decision.ask) == (seeds.BACKUPS, 2)
    tick(home, SATURDAY)
    assert backups(home).history[-1].statement.text == neglect.PARKED_BY_SILENCE


def test_continue_after_a_park_by_silence_undoes_it_as_the_history_line_promises(home):
    tick(home, FRIDAY)
    assert backups(home).history[-1].statement.text == neglect.PARKED_BY_SILENCE
    home.set_clock(FRIDAY + timedelta(hours=2))
    assert home.telegram("continue", reply_to=seeds.THURSDAY_BRIEF_ID) == \
        "Home server backups: active again. Its quiet days count from today."
    assert backups(home).status is Status.ACTIVE
