"""A Step skipped by word comes back as a new Step in the same words; one skipped by silence does not."""
from datetime import date

from atlas import messages, rules
from atlas.model import BriefStep, MarkStep, Owner, SendAttempt, SendStatus, SentBrief, Skipped
from snapshots import after, at, pursuit, snapshot

FRIDAY, SATURDAY = date(2026, 11, 20), date(2026, 11, 21)
OWNER = Owner("telegram", "nt-0said000")
KNEE = pursuit("p-0kne", "Knee rehab plan", worked=[date(2026, 11, 16)], focus=True,
               step="Ask for a week-3 progression.")


def _brief(day, step):
    return SentBrief(day, (SendAttempt(at(day, "07:30"), SendStatus.SENT, ("1",)),), None, (),
                     (BriefStep(1, KNEE.id, step),), None, False, "Atlas · Daily Brief")


def _skip(snap, step, day):
    return after(rules.decide(snap, MarkStep(KNEE.id, step, "skipped", "not today"), OWNER, at(day, "09:45")))


def test_a_step_skipped_by_word_is_offered_again_the_next_day_as_a_new_step():
    friday = _skip(snapshot([KNEE], briefs=[_brief(FRIDAY, "s-20261115-1")]), "s-20261115-1", FRIDAY)
    knee = friday.pursuit(KNEE.id)
    skipped = knee.past_steps[-1]
    assert isinstance(skipped.ended, Skipped) and skipped.ended.how == "word"
    assert knee.next_step.id == "s-20261120-1" and knee.next_step.created == FRIDAY
    assert knee.next_step.text == skipped.text and knee.next_step.ended is None
    assert rules.offers(friday, KNEE.id, knee.next_step) == 0

    saturday = rules.compose_daily(friday, at(SATURDAY, "07:30")).send
    assert [(f.step, f.state) for f in saturday.followups] == [("s-20261115-1", "skipped_word")]
    assert [s.step for s in saturday.steps] == ["s-20261120-1"]
    text = messages.brief(saturday, friday, messages.Style.TELEGRAM)
    assert "Knee rehab plan: skipped, you said so." in text
    assert "1 · Knee rehab plan" in text and "Ask for a week-3 progression." in text


def test_three_skips_by_word_in_a_row_count_as_three():
    snap = snapshot([KNEE])
    for n, day in enumerate((date(2026, 11, 18), date(2026, 11, 19), FRIDAY)):
        snap = _skip(snap, snap.pursuit(KNEE.id).next_step.id, day)
    knee = snap.pursuit(KNEE.id)
    assert rules.skipped_in_a_row(knee) == 3
    assert len({s.id for s in (*knee.past_steps, knee.next_step)}) == 4


def test_skipping_the_same_step_again_changes_nothing():
    once = _skip(snapshot([KNEE]), "s-20261115-1", FRIDAY)
    assert _skip(once, "s-20261115-1", FRIDAY).pursuit(KNEE.id) == once.pursuit(KNEE.id)


def test_a_step_skipped_by_silence_does_not_come_back():
    briefs = [_brief(date(2026, 11, 18), "s-20261115-1"), _brief(date(2026, 11, 19), "s-20261115-1")]
    friday = after(rules.compose_daily(snapshot([KNEE], briefs=briefs), at(FRIDAY, "07:30")))
    knee = friday.pursuit(KNEE.id)
    assert knee.past_steps[-1].ended.how == "silence"
    assert knee.next_step is None
    assert friday.brief(FRIDAY).steps == ()
