"""Steps across the week (W Q7, W Q8): the hold, offers counted from the brief files, skip by
silence, the follow-up lines, and a brief that never pads. Pure: in-memory Snapshots."""
from dataclasses import replace
from datetime import date, timedelta, timezone

import pytest

from atlas import converse, rules
from atlas.model import (BriefStep, Cite, Done, Fact, HarnessId, HarnessSetting, HoldStep, Membership, OwnerMessage,
                         SendAttempt, SendStatus, SentBrief, SentRef, SetStatus, Skipped, Status)
from snapshots import SETTINGS, after, at, pursuit, snapshot

MON, TUE, WED, THU, FRI = (date(2026, 11, 16) + timedelta(days=n) for n in range(5))
BILLING = pursuit("p-4k7q", "Billing export service", worked=[date(2026, 11, 13)], focus=True)
GREENHOUSE = pursuit("p-6r2t", "Greenhouse logger", worked=[date(2026, 11, 11)], focus=True)
READING = pursuit("p-5g9d", "Reading tracker for the kids", worked=[date(2026, 11, 6)], focus=True,
                  step="Write down paper chart or tablet app, and why.")
BIKE = pursuit("p-3b8e", "Bike route planner", worked=[date(2026, 10, 26)])
WEEK = [BILLING, GREENHOUSE, READING, BIKE]
OWNER_NOTE = Cite("nt-0said000", "your Note", at(TUE, "20:45"))


def brief(day, *pursuits, status=SendStatus.SENT):
    return SentBrief(day, (SendAttempt(at(day, "07:30"), status, (f"88{day.day}",)),), None, (),
                     tuple(BriefStep(n, p.id, p.next_step.id) for n, p in enumerate(pursuits, 1)), None, False, "")


def held(p, until):
    return replace(p, next_step=replace(p.next_step, held_until=until))


def compose(pursuits, briefs, day):
    return rules.compose_daily(snapshot(pursuits, briefs=briefs), at(day, "07:30"))


def names(d):
    by_id = {p.id: p.name for p in WEEK}
    return [by_id[s.pursuit] for s in d.send.steps]


# ------------------------------------------------------------------ offers and the hold

@pytest.mark.parametrize("briefs, step_held, count", [
    ([], None, 0),
    ([brief(MON, GREENHOUSE)], None, 1),
    ([brief(MON, GREENHOUSE), brief(TUE, GREENHOUSE)], None, 2),
    ([brief(MON, GREENHOUSE), brief(TUE, GREENHOUSE, status=SendStatus.ARRIVAL_UNKNOWN)], None, 1),
    ([brief(MON, BILLING)], None, 0),
    ([brief(MON, GREENHOUSE), brief(TUE, GREENHOUSE)], FRI, 0),
    ([brief(TUE, GREENHOUSE), brief(FRI, GREENHOUSE)], FRI, 1),
])
def test_offers_are_counted_from_the_sent_briefs_and_a_hold_starts_a_new_cycle(briefs, step_held, count):
    p = held(GREENHOUSE, step_held) if step_held else GREENHOUSE
    assert rules.offers(snapshot([p], briefs=briefs), p.id, p.next_step) == count


@pytest.mark.parametrize("zone", [None, timezone.utc])
def test_a_step_offered_twice_and_still_open_ends_as_skipped_by_silence(zone):
    now = at(WED, "07:30")
    d = rules.compose_daily(snapshot(WEEK, briefs=[brief(MON, GREENHOUSE), brief(TUE, GREENHOUSE)]),
                            now.astimezone(zone) if zone else now)
    greenhouse = after(d).pursuit(GREENHOUSE.id)
    assert greenhouse.next_step is None
    assert greenhouse.past_steps[-1].ended == Skipped("silence", at(WED, "00:00"), None)
    assert rules.skipped_in_a_row(greenhouse) == 1
    assert "Greenhouse logger" not in names(d)
    assert "    · Greenhouse logger: no sign of it in your Sessions. [F] Offered twice, so I skipped it. [I]" in \
        d.send.text.splitlines()


def test_a_step_offered_once_is_offered_again_after_a_day_the_owner_acted():
    done = replace(BILLING, next_step=None, past_steps=(
        replace(BILLING.next_step, ended=Done("word", OWNER_NOTE.at, Fact("", OWNER_NOTE))),))
    d = compose([done, GREENHOUSE, READING], [brief(MON, BILLING, GREENHOUSE)], TUE)
    assert names(d) == ["Greenhouse logger", "Reading tracker for the kids"]
    assert "    · Greenhouse logger: no sign of it in your Sessions. [F] It is still the next Step. [I]" in \
        d.send.text.splitlines()
    assert after(d).pursuit(GREENHOUSE.id).next_step.ended is None


def test_a_held_step_is_not_offered_before_its_day_and_comes_first_on_it():
    hold = held(READING, FRI)
    assert names(compose([hold, GREENHOUSE, BILLING], [], THU)) == ["Billing export service", "Greenhouse logger"]
    assert names(compose([hold, GREENHOUSE, BILLING], [], FRI))[0] == "Reading tracker for the kids"


def test_after_its_first_offer_a_held_step_takes_its_place_by_its_age_then_recent_work():
    hold = held(READING, TUE)
    done = replace(GREENHOUSE, next_step=replace(GREENHOUSE.next_step, id="s-20261117-1", created=TUE), past_steps=(
        replace(GREENHOUSE.next_step, ended=Done("word", OWNER_NOTE.at, Fact("", OWNER_NOTE))),))
    d = compose([hold, done, BILLING], [brief(TUE, hold, GREENHOUSE)], WED)
    assert names(d) == ["Billing export service", "Reading tracker for the kids", "Greenhouse logger"]


def test_an_older_step_comes_before_a_newer_one_however_recent_its_work():
    """Tuesday's brief: the billing Step made from Monday evening's Session comes after the Steps
    carried from Sunday, though billing was worked on last."""
    newer = replace(BILLING, next_step=replace(BILLING.next_step, id="s-20261116-1", created=MON))
    d = compose([newer, GREENHOUSE, READING], [], TUE)
    assert names(d) == ["Greenhouse logger", "Reading tracker for the kids", "Billing export service"]


def test_the_brief_never_pads_to_three_steps():
    no_step = replace(GREENHOUSE, next_step=None)
    d = compose([held(BILLING, FRI), no_step, READING, BIKE], [], WED)
    assert names(d) == ["Reading tracker for the kids"]


# ------------------------------------------------------------------ the follow-up

def test_a_held_step_is_followed_up_as_held_until_its_day():
    hold = held(BILLING, FRI)
    d = compose([hold, READING], [brief(TUE, hold)], WED)
    assert [f.state for f in d.send.followups] == ["held"]
    assert "    · Billing export service: held until Friday, as you asked. [F]" in d.send.text.splitlines()


def test_steps_with_no_sign_rest_a_day_when_the_owner_acted_on_none():
    d = compose([GREENHOUSE, READING, BILLING], [brief(THU, READING, GREENHOUSE)], FRI)
    assert names(d) == ["Billing export service"]
    lines = d.send.text.splitlines()
    assert "    · Reading tracker for the kids, Greenhouse logger: no sign of them in your Sessions. [F]" in lines
    assert "      Both stay open; I'll offer each once more. [I]" in lines
    friday = replace(d.send, attempts=(SendAttempt(at(FRI, "07:30"), SendStatus.SENT, ("8840",)),))
    sat = compose([GREENHOUSE, READING, BILLING], [brief(THU, READING, GREENHOUSE), friday], FRI + timedelta(days=1))
    assert {"Reading tracker for the kids", "Greenhouse logger"} <= set(names(sat))


def test_a_pursuit_last_worked_in_a_late_harness_is_not_visible_yet():
    late = replace(SETTINGS, harnesses={HarnessId.CHATGPT: HarnessSetting(True, "export", (), "owner")})
    knee = replace(pursuit("p-8h1n", "Knee rehab plan", focus=True),
                   sessions=(Membership("gpt-5k2m9q1d", at(MON, "19:10"), True),))
    snap = snapshot([knee, GREENHOUSE], briefs=[brief(THU, knee, GREENHOUSE)], settings=late)
    d = rules.compose_daily(snap, at(FRI, "07:30"))
    assert [f.state for f in d.send.followups] == ["not_visible_yet", "no_sign"]
    assert [s.pursuit for s in d.send.steps] == [knee.id]
    assert "    · Knee rehab plan: no sign yet. You work on it in ChatGPT, whose Sessions reach me late. [F]" in \
        d.send.text.splitlines()
    assert rules.compose_daily(snapshot([knee], briefs=[brief(THU, knee)]), at(FRI, "07:30")).send.followups[0].state \
        == "no_sign"


def test_a_step_done_by_word_with_a_choice_is_followed_up_with_it():
    d = rules.decide(snapshot([READING]), converse.MarkStep(READING.id, READING.next_step.id, "done",
                                                            "tablet app, nothing fancy"),
                     rules.Owner("telegram", "nt-0said000"), at(TUE, "20:45"))
    reading = after(d).pursuit(READING.id)
    assert reading.resume.decided == (Fact("tablet app", OWNER_NOTE),)
    wed = rules.compose_daily(snapshot([reading], briefs=[brief(TUE, READING)]), at(WED, "07:30"))
    assert "    ✓ Reading tracker for the kids: done, you said so: tablet app. [F]" in wed.send.text.splitlines()


def test_a_brief_after_a_day_without_one_says_which_day_it_follows_up():
    d = compose([GREENHOUSE], [brief(FRI, GREENHOUSE)], FRI + timedelta(days=2))
    assert "YESTERDAY'S STEPS (from Friday)" in d.send.text.splitlines()


# ------------------------------------------------------------------ the owner's words

@pytest.mark.parametrize("step, detail, picked", [
    ("Write down paper chart or tablet app, and why.", "tablet app, nothing fancy", "tablet app"),
    ("Write down paper chart or tablet app, and why.", "paper chart.", "paper chart"),
    ("Write down paper chart or tablet app, and why.", "neither, a notebook", None),
    ("Run tests/test_retry.py with one worker, ten times in a row.", "with one worker", None),
    ("Write down paper chart or tablet app, and why.", None, None),
])
def test_a_done_detail_names_the_choice_the_step_asked_for(step, detail, picked):
    assert rules.chosen(step, detail) == picked


def read(text, today=TUE):
    snap = snapshot(WEEK, briefs=[brief(TUE, GREENHOUSE, READING, BILLING)])
    msg = OwnerMessage(text, at(today, "08:10"), "telegram", None, SentRef("brief", TUE.isoformat()))
    return converse.interpret(msg, snap, None)


@pytest.mark.parametrize("text, commands", [
    ("3 remind me friday", (HoldStep(BILLING.id, FRI),)),
    ("3 remind me on Friday.", (HoldStep(BILLING.id, FRI),)),
    ("3 remind me tuesday", (HoldStep(BILLING.id, TUE + timedelta(days=7)),)),
    ("3 remind me friday. drop the bike planner", (HoldStep(BILLING.id, FRI), SetStatus(BIKE.id, Status.DROPPED))),
    ("1 done, 3 remind me thursday", (converse.MarkStep(GREENHOUSE.id, GREENHOUSE.next_step.id, "done", None),
                                      HoldStep(BILLING.id, THU))),
])
def test_the_grammar_reads_a_hold(text, commands):
    assert read(text) == converse.Interpretation(commands, (), False, None)


@pytest.mark.parametrize("text", ["remind me friday", "3 remind me someday", "4 remind me friday"])
def test_a_hold_without_its_step_or_day_is_unread(text):
    assert read(text).unclear == converse.UNREAD


def test_the_hold_answers_in_the_tutorials_words_and_is_neither_an_offer_nor_a_skip():
    snap = snapshot(WEEK, briefs=[brief(TUE, GREENHOUSE, READING, BILLING)])
    d = rules.decide(snap, HoldStep(BILLING.id, FRI), rules.Owner("telegram", "nt-0said000"), at(TUE, "08:10"))
    assert d.outcome.lines == ("Billing export service: I'll suggest this Step again on Friday.",)
    billing = after(d).pursuit(BILLING.id)
    assert billing.next_step.held_until == FRI and billing.next_step.ended is None
    assert rules.offers(after(d), billing.id, billing.next_step) == 0
    assert rules.skipped_in_a_row(billing) == 0
    again = rules.decide(after(d), HoldStep(BILLING.id, FRI), rules.Owner("telegram", "nt-0said001"), at(TUE, "09:00"))
    assert again.changes.is_empty()
