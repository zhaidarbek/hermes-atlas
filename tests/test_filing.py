"""Filing on the tick (scenes 9b, 14, 15, 39): which Sessions wait, and what one Filing changes.
filing.apply is pure; these tests plan on a real record's Snapshot and commit what it plans."""
import logging
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from atlas import filing, messages, rules
from atlas.app import Atlas
from atlas.librarian import Filing, HostLlm, Librarian, PursuitDraft, StepSeen, Unusable
from atlas.model import (LOCAL, Actor, Cite, Done, EndingProposal, Fact, HarnessId, Inference, Kind, Membership,
                         ReopenProposal, ResumeNote, SendStatus, SessionCard, SessionEvidence, Status, Turn,
                         evidence_id)
from atlas.record import ForgetPlan, Record
from harness import model, seeds, world
from harness.fake_llm import FakeLlm
from harness.home import AtlasHome
from snapshots import pursuit

TZ = world.TZ
BUILT = datetime(2026, 11, 15, 20, 10, tzinfo=TZ)
EVENING = datetime(2026, 11, 16, 21, 0, tzinfo=TZ)
TICK = datetime(2026, 11, 16, 22, 50, tzinfo=TZ)
DONE_TURN = "the per-invoice totals match the ledger now"


def session(key: str, start: datetime = EVENING, minutes: int = 40, owner: str = "export october per invoice",
            agent: str = DONE_TURN, atlas: bool = False) -> SessionEvidence:
    end = start + timedelta(minutes=minutes)
    return SessionEvidence(evidence_id(HarnessId.CLAUDE_CODE, LOCAL, key), HarnessId.CLAUDE_CODE, None, LOCAL, key,
                           key, start, end, "ubuntu-pc", "~/work/billing", None, (),
                           (Turn("owner", start, owner, "main", atlas_tool_turn=atlas),
                            Turn("agent", end, agent, "main", atlas_tool_turn=atlas)))


OLD = session("old", datetime(2026, 11, 13, 18, 0, tzinfo=TZ))
BILLING = replace(pursuit("p-4k7q", "Billing export service", focus=True), sessions=(Membership(OLD.id, OLD.last_at, True),))
KEYBOARD = replace(pursuit("p-1k2f", "Keyboard firmware", status=Status.PARKED, kind=Kind.EXPERIMENT),
                   sessions=(Membership(OLD.id, OLD.last_at, True),))


@pytest.fixture
def record(tmp_path):
    r = Record.open(tmp_path / "atlas")
    r.create(seeds.SETTINGS, seeds.PROFILE)
    r.commit(r.snapshot().changes().put_session(OLD, {}))
    r.commit(r.snapshot().changes().put_pursuit(BILLING).put_pursuit(KEYBOARD))
    return r


def put(record: Record, *sessions: SessionEvidence) -> None:
    for s in sessions:
        record.commit(record.snapshot().changes().put_session(s, {}))


def card(s: SessionEvidence) -> SessionCard:
    return SessionCard(s.id, Inference("Export October.", (s.id,)), Inference("Done.", (s.id,)), None, None)


def filed(s: SessionEvidence, into: str | None = "p-4k7q", **kw) -> Filing:
    base = dict(evidence=s.id, last_at=s.last_at, card=card(s), pursuit=into, new_pursuit=None, one_off_area=None,
                advances=True, left_off=(), next_step=None, step_outcome=None, kind_change=None, rule=None)
    return Filing(**{**base, **kw})


def apply(record: Record, f: Filing, now: datetime = TICK):
    record.commit(filing.apply(record.snapshot(), f, now).changes)
    return record.snapshot()


def test_nothing_waits_before_backfill_has_built_the_pursuits(tmp_path):
    r = Record.open(tmp_path / "atlas")
    r.create(seeds.SETTINGS, seeds.PROFILE)
    put(r, session("new"))
    assert filing.queue(r.snapshot(), TICK) == []


def test_a_new_session_waits_and_a_filed_or_one_off_session_does_not(record):
    new, later, one_off = session("new"), session("later", EVENING + timedelta(hours=1)), session("one-off")
    put(record, later, new, one_off)
    record.commit(record.snapshot().changes().put_card(card(one_off)))
    record.commit(record.snapshot().changes().mark_one_off(one_off.id, "work", TICK.date()))
    assert filing.queue(record.snapshot(), TICK) == [new.id, later.id]


def test_a_filed_session_that_grew_waits_again_and_stays_where_it_is_filed(record):
    put(record, replace(OLD, last_at=OLD.last_at + timedelta(hours=1),
                        turns=(*OLD.turns, Turn("owner", OLD.last_at + timedelta(hours=1), "one more thing", "main"))))
    snap = record.snapshot()
    assert filing.queue(snap, TICK) == [OLD.id]
    grown = snap.session(OLD.id)
    after = apply(record, filed(grown, into="p-1k2f"))
    assert after.pursuit("p-4k7q").sessions == (Membership(OLD.id, grown.last_at, True),)
    assert after.pursuit("p-1k2f").status is Status.PARKED
    assert filing.queue(after, TICK) == []


def test_a_step_seen_done_ends_as_seen_with_a_history_line_and_a_next_step(record):
    s = session("seen")
    put(record, s)
    step = record.snapshot().pursuit("p-4k7q").next_step
    seen = Fact("per-invoice rounding matches the ledger", Cite(s.id, "Claude Code", s.last_at))
    after = apply(record, filed(s, step_outcome=StepSeen(step.id, seen), left_off=(seen,),
                                next_step=Inference("Switch the PDF export.", (s.id,))))
    p = after.pursuit("p-4k7q")
    assert p.past_steps[-1] == replace(step, ended=Done("seen", s.last_at, seen))
    assert p.next_step.text.text == "Switch the PDF export." and p.next_step.id == "s-20261116-1"
    assert p.resume.left_off == (seen,) and p.resume.reopen[0] == s.id
    assert p.history[-1].actor is Actor.ATLAS and p.history[-1].statement.source.evidence == s.id
    assert rules.marks(p.history, p.history[-2].id) == (False, True)
    assert after.card(s.id) == card(s)
    assert filing.queue(after, TICK) == []


def test_a_seen_step_already_ended_by_the_owner_is_left_as_it_is(record):
    s = session("late")
    put(record, s)
    p = record.snapshot().pursuit("p-4k7q")
    said = Fact("", Cite("nt-0said000", "your Note", EVENING))
    ended = replace(p.next_step, ended=Done("word", EVENING, said))
    record.commit(record.snapshot().changes().put_pursuit(replace(p, next_step=None, past_steps=(ended,))))
    after = apply(record, filed(s, step_outcome=StepSeen(p.next_step.id, Fact("x", Cite(s.id, "Claude Code", s.last_at)))))
    assert after.pursuit("p-4k7q").past_steps == (ended,)


def test_a_parked_pursuit_worked_on_again_is_active_outside_focus(record):
    s = session("debounce", datetime(2026, 11, 22, 9, 30, tzinfo=TZ))
    put(record, s)
    p = apply(record, filed(s, into="p-1k2f"), datetime(2026, 11, 22, 17, 0, tzinfo=TZ)).pursuit("p-1k2f")
    assert (p.status, p.focus, p.last_worked) == (Status.ACTIVE, False, s.last_at.date())
    assert p.history[-1].statement == Fact("Active again: your Claude Code Session worked on it.",
                                           Cite(s.id, "Claude Code", s.last_at))


def test_an_older_session_filed_late_or_one_that_only_read_does_not_reactivate(record):
    old, reading = session("older", datetime(2026, 11, 1, 9, 0, tzinfo=TZ)), session("read")
    put(record, old, reading)
    apply(record, filed(old, into="p-1k2f"))
    after = apply(record, filed(reading, into="p-1k2f", advances=False))
    assert after.pursuit("p-1k2f").status is Status.PARKED


@pytest.mark.parametrize("ended", [Status.DONE, Status.DROPPED])
def test_a_done_or_dropped_pursuit_worked_on_again_gets_a_reopen_proposal_and_stays_ended(record, ended):
    record.commit(record.snapshot().changes().put_pursuit(replace(KEYBOARD, status=ended)))
    s = session("again")
    put(record, s)
    p = apply(record, filed(s, into="p-1k2f")).pursuit("p-1k2f")
    assert p.status is ended
    assert [type(x) for x in p.proposals] == [ReopenProposal] and p.proposals[0].basis.source.evidence == s.id
    assert apply(record, filed(s, into="p-1k2f")).pursuit("p-1k2f").proposals == p.proposals


def test_an_ending_the_session_shows_becomes_one_proposal_on_an_active_pursuit_only(record):
    s = session("finished")
    put(record, s)
    reason = Inference("Its purpose is met.", (s.id,))
    p = apply(record, filed(s, ending=(Status.DONE, reason))).pursuit("p-4k7q")
    assert p.status is Status.ACTIVE and p.focus
    assert p.proposals == (EndingProposal(Status.DONE, reason, TICK.date(), False),)
    assert (p.history[-1].actor, p.history[-1].statement) == (
        Actor.ATLAS, Inference("Proposed marking it done: Its purpose is met.", (s.id,)))
    assert apply(record, filed(s, ending=(Status.DROPPED, reason))).pursuit("p-4k7q").proposals == p.proposals
    assert apply(record, filed(s, into="p-1k2f", ending=(Status.DONE, reason))).pursuit("p-1k2f").proposals == ()


@pytest.mark.parametrize("advances", [True, False])
def test_the_filing_call_reads_an_ending_only_for_a_session_that_advanced_its_pursuit(record, advances):
    s = session("finished")
    put(record, s)
    llm = FakeLlm({"atlas_file": lambda call: {
        "intent": "Finish it.", "outcome": "It works.", "pursuit": "p-4k7q", "new_pursuit": None,
        "one_off_area": None, "rule": None, "advances": advances, "left_off": [], "step_done": None, "next_step": None,
        "kind": None, "ending": {"to": "done", "reason": "Its purpose is met."}}})
    f = Librarian(HostLlm(type("Ctx", (), {"llm": llm})()), record).file(record.snapshot(),
                                                                       record.snapshot().session(s.id))
    assert f.ending == ((Status.DONE, Inference("Its purpose is met.", (s.id,))) if advances else None)


def test_a_kind_change_keeps_one_history_and_shows_on_the_card(record):
    s = session("repo")
    put(record, s)
    reason = Inference("it has its own repository now", (s.id,))
    record.commit(record.snapshot().changes().put_pursuit(replace(BILLING, kind=Kind.IDEA)))
    after = apply(record, filed(s, kind_change=(Kind.PROJECT, reason)), datetime(2026, 11, 18, 15, 0, tzinfo=TZ))
    p = after.pursuit("p-4k7q")
    assert p.kind is Kind.PROJECT and p.id == BILLING.id
    assert p.history[-1].statement == Inference("Changed Kind from idea to project: it has its own repository now",
                                                (s.id,))
    assert rules.kind_line(p, "Europe/Berlin") == "project (an idea until Wed 18 Nov)"


def test_a_session_filed_by_a_filing_rule_says_so_in_the_history(record):
    s = session("rotate")
    put(record, s)
    rule = Fact('Staging deploy chores belong here, not in "Billing export service".',
                Cite("nt-0split00", "your Note", BUILT))
    after = apply(record, filed(s, rule=rule))
    assert after.pursuit("p-4k7q").history[-1].statement.text.endswith(rule.text)


def test_a_new_pursuit_is_created_active_outside_focus_and_marked_new(record):
    s = session("knee")
    put(record, s)
    draft = PursuitDraft("Knee rehab plan", Kind.PROJECT, "health", (Membership(s.id, s.last_at, True),),
                         ResumeNote((), (), (), (), (s.id,)), Inference("Ask for week 3.", (s.id,)))
    after = apply(record, filed(s, into=None, new_pursuit=draft))
    p = after.pursuit("Knee rehab plan")
    assert (p.status, p.focus, p.next_step.text.text) == (Status.ACTIVE, False, "Ask for week 3.")
    assert rules.marks(p.history, None) == (True, False)


def test_a_one_off_marks_its_card_with_its_area(record):
    s = session("question")
    put(record, s)
    after = apply(record, filed(s, into=None, one_off_area="health"))
    assert (after.card(s.id).one_off_area, after.card(s.id).one_off_day) == ("health", TICK.date())
    assert filing.queue(after, TICK) == []


def test_filing_a_session_into_a_pursuit_cancels_a_forget_that_waits(record):
    """issue #119: Forget works out what goes when the owner confirms, so a Session filed after the
    preview could go with it unseen. The owner is asked again instead."""
    s, other = session("filed"), session("one-off", EVENING + timedelta(hours=1))
    put(record, s, other)
    plan = ForgetPlan((OLD.id,), EVENING)
    record.commit(record.snapshot().changes().set_pending_forget(plan))
    assert apply(record, filed(other, into=None, one_off_area="work")).pending_forget() == plan
    assert apply(record, filed(s)).pending_forget() is None


def test_a_session_that_changed_since_the_model_read_it_is_left_to_the_next_tick(record):
    s = session("growing")
    put(record, s)
    stale = filed(s, last_at=s.last_at - timedelta(minutes=10))
    assert filing.apply(record.snapshot(), stale, TICK).changes.is_empty()


def answer(**kw) -> dict:
    return {"intent": "Export October.", "outcome": "Done.", "pursuit": "p-4k7q", "new_pursuit": None,
            "one_off_area": None, "rule": None, "advances": True, "left_off": [], "step_done": None,
            "next_step": None, "kind": None, **kw}


def test_the_filing_call_reads_masked_text_and_needs_a_quote_to_see_a_step_done(record):
    s = session("secret", owner=f"the staging db password is {world.PASTED_PASSWORD}")
    put(record, s)
    llm = FakeLlm({"atlas_file": [answer(step_done={"text": "done", "quote": "words the Session never had",
                                                    "sessions": []})]})
    made = Librarian(HostLlm(type("Ctx", (), {"llm": llm})()), record).file(record.snapshot(), record.snapshot().session(s.id))
    assert world.PASTED_PASSWORD not in llm.calls[0]["input"]
    assert "Billing export service" in llm.calls[0]["input"] and "Open Step:" in llm.calls[0]["input"]
    assert made.pursuit == "p-4k7q" and made.step_outcome is None


def test_a_step_done_is_seen_at_the_turn_its_quote_is_read_from(record):
    s = session("quoted")
    put(record, s)
    llm = FakeLlm({"atlas_file": [answer(step_done={"text": "per-invoice rounding matches", "quote": DONE_TURN,
                                                    "sessions": []})]})
    made = Librarian(HostLlm(type("Ctx", (), {"llm": llm})()), record).file(record.snapshot(), record.snapshot().session(s.id))
    assert made.step_outcome.basis == Fact("per-invoice rounding matches", Cite(s.id, "Claude Code", s.last_at))


def test_an_exchange_with_atlas_never_advances(record):
    s = session("asked atlas", atlas=True)
    put(record, s)
    llm = FakeLlm({"atlas_file": [answer()]})
    made = Librarian(HostLlm(type("Ctx", (), {"llm": llm})()), record).file(record.snapshot(), record.snapshot().session(s.id))
    assert made.advances is False


def test_with_no_usable_filing_the_tick_logs_skips_and_still_sends(tmp_path, caplog):
    with AtlasHome(tmp_path / "scene", FakeLlm({"atlas_file": [{"intent": ""}]})) as home:
        seeds.collecting(seeds.after_scene_6)(home.record_root)
        home.set_clock(world.FIRST_BRIEF_AT)
        with caplog.at_level(logging.WARNING, logger="atlas"):
            ran = home.cli("atlas", "tick", "--quiet")
        assert (ran.code, ran.out) == (0, "")
        assert "the model's filing was unusable" in caplog.text
        assert len(home.sent()) == 1


def test_the_tick_sends_after_it_lets_go_of_the_tick_lease(tmp_path, monkeypatch):
    from atlas import schedule
    held = []

    def send_due(self, now):
        with schedule.TickLease(self._record.root) as lease:
            held.append(lease.held)
        return ()
    monkeypatch.setattr(Atlas, "_send_due", send_due)
    root = tmp_path / "atlas"
    seeds.after_scene_6(root)
    Atlas.open(root).tick(world.FIRST_BRIEF_AT)
    assert held == [True]


def file(record: Record, s: SessionEvidence, out: dict) -> Filing:
    llm = FakeLlm({"atlas_file": [out]})
    return Librarian(HostLlm(type("Ctx", (), {"llm": llm})()), record).file(record.snapshot(), record.snapshot().session(s.id))


@pytest.mark.parametrize("out", [
    answer(intent="x" * 401),
    answer(next_step={"text": "x" * 100_000, "quote": None, "sessions": []}),
    answer(pursuit=None, new_pursuit={"name": "x" * 81, "kind": "project", "area": "work"}),
    answer(kind={"to": "idea", "reason": "x" * 301}),
    answer(pursuit=None, new_pursuit={"name": "billing export service", "kind": "project", "area": "work"}),
    answer(kind={"to": "trip", "reason": "a Kind there is not"}),
    {"intent": ""},
])
def test_an_answer_with_a_field_over_its_maximum_or_unfit_is_unusable_and_never_kept(record, out):
    s = session("long")
    put(record, s)
    with pytest.raises(Unusable):
        file(record, s, out)


def test_an_exchange_with_atlas_neither_ends_a_step_nor_changes_the_kind(record):
    s = session("asked atlas again", atlas=True)
    put(record, s)
    made = file(record, s, answer(step_done={"text": "done", "quote": DONE_TURN, "sessions": []},
                                  kind={"to": "idea", "reason": "it is only an idea"}))
    assert (made.advances, made.step_outcome, made.kind_change) == (False, None, None)


def test_a_grown_session_sees_a_step_done_only_in_turns_its_pursuit_had_not_read(record):
    at = OLD.last_at + timedelta(hours=1)
    put(record, replace(OLD, last_at=at, turns=(*OLD.turns, Turn("owner", at, "one more thing", "main"))))
    made = file(record, OLD, answer(step_done={"text": "matches", "quote": DONE_TURN, "sessions": []}))
    assert made.step_outcome is None
    after = apply(record, made)
    assert after.pursuit("p-4k7q").history == BILLING.history


def test_a_forget_waiting_is_cancelled_only_by_filing_into_a_pursuit_its_sessions_belong_to(record):
    s, t = session("elsewhere"), session("same", EVENING + timedelta(hours=1))
    put(record, s, t)
    plan = ForgetPlan((OLD.id,), EVENING)
    record.commit(record.snapshot().changes().put_pursuit(replace(KEYBOARD, sessions=())).set_pending_forget(plan))
    assert apply(record, filed(s, into="p-1k2f")).pending_forget() == plan
    assert apply(record, filed(t)).pending_forget() is None


def _waiting(record: Record) -> list[SessionEvidence]:
    three = [session(k, EVENING + timedelta(minutes=n)) for n, k in enumerate(("first", "second", "third"))]
    put(record, *three)
    return three


def test_a_session_the_model_answers_unusably_for_is_skipped_and_the_others_are_filed(record, caplog):
    first, second, third = _waiting(record)
    llm = FakeLlm({"atlas_file": [{"intent": ""}, answer(), answer()]})
    atlas = Atlas.open(record.root, llm=HostLlm(type("Ctx", (), {"llm": llm})()))
    with caplog.at_level(logging.WARNING, logger="atlas"):
        assert atlas._file_quietly(TICK) == 2
    snap = record.snapshot()
    assert {m.evidence for m in snap.pursuit("p-4k7q").sessions} == {OLD.id, second.id, third.id}
    assert filing.queue(snap, TICK) == [] and set(snap.filing_retries()) == {first.id}
    assert "1 Sessions to file later" in caplog.text and "export" not in caplog.text.lower()
    retry = snap.filing_retries()[first.id]
    assert filing.queue(snap, retry.retry_at) == [first.id]
    record.commit(filing.unusable(snap, first.id, retry.retry_at).changes)
    again = record.snapshot().filing_retries()[first.id]
    assert (again.failures, again.retry_at - retry.retry_at) == (2, 2 * filing.RETRY[0])


def test_a_model_call_that_fails_stops_filing_for_the_tick(record):
    _waiting(record)
    llm = FakeLlm({})
    assert Atlas.open(record.root, llm=HostLlm(type("Ctx", (), {"llm": llm})()))._file_quietly(TICK) == 0
    assert len(llm.calls) == 1 and record.snapshot().filing_retries() == {}


def test_a_brief_too_long_for_one_message_is_sent_as_one():
    lines = ["Atlas · Daily Brief", "", "1 · Billing  " + "x" * 100_000, "    Left off: " + "é" * 9000, "STEPS"]
    sent = messages.fit("\n".join(lines))
    assert len(sent.encode("utf-16-le")) // 2 <= messages.MESSAGE_MAX
    assert sent.split("\n")[0] == lines[0] and sent.split("\n")[-1] == "STEPS" and len(sent.split("\n")) == 5
    assert messages.fit("short\ntext") == "short\ntext"


def _overlong_next_step(call: dict) -> dict:
    out = model._file(call)
    return {**out, "next_step": {**out["next_step"], "text": "x" * 100_000}} if out.get("next_step") else out


def test_an_overlong_answer_never_stops_the_brief(tmp_path):
    """The adversary's case: scene 9b's seed, the billing Session's next Step 100,000 characters
    long, the night tick, then ticks at Tuesday's brief time."""
    llm = FakeLlm({"atlas_backfill": model._respond, "atlas_journal": model._journal, "atlas_file": _overlong_next_step})
    with AtlasHome(tmp_path / "scene", llm) as home:
        seeds.collecting(seeds.after_scene_9)(home.record_root)
        for at in (world.SEEN_TICK_AT, *(world.TUESDAY_BRIEF_AT + timedelta(minutes=10 * n) for n in range(3))):
            home.set_clock(at)
            assert (home.cli("atlas", "tick", "--quiet").code) == 0
        assert len(home.sent()) == 1
        assert all(len(p.next_step.text.text) < 1000 for p in Record.open(home.record_root).snapshot().pursuits()
                   if p.next_step)


def test_a_send_that_cannot_start_never_raises_out_of_the_tick_and_is_tried_twice(tmp_path, monkeypatch):
    from atlas import _hermes
    with AtlasHome(tmp_path / "scene", FakeLlm({})) as home:
        seeds.after_scene_6(home.record_root)
        real = _hermes.run

        def run(*args, **kw):
            if args[0] == "send" and "-l" not in args:
                raise OSError(7, "Argument list too long")
            return real(*args, **kw)
        monkeypatch.setattr(_hermes, "run", run)
        for n in range(5):
            home.set_clock(world.FIRST_BRIEF_AT + timedelta(minutes=10 * n))
            assert home.cli("atlas", "tick", "--quiet").code == 0
        attempts = Record.open(home.record_root).snapshot().brief(world.FIRST_BRIEF_AT.date()).attempts
        assert len(attempts) == 2 and attempts[0].status is SendStatus.ARRIVAL_UNKNOWN
