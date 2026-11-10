"""The Llm port, cards, grounding and the backfill grouping, with the fake model and no record."""
import json
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import atlas
from atlas.librarian import CARD_SCHEMA, SESSION_BUDGET, HostLlm, Librarian, ModelError, ground
from atlas.model import (LOCAL, Cite, Fact, HarnessId, Inference, Membership, Profile, SessionCard, SessionEvidence,
                         Turn)
import snapshots
from harness.fake_llm import FakeLlm
from harness.home import FakePluginContext

TZ = ZoneInfo("Europe/Berlin")
SETTINGS = snapshots.SETTINGS
AT = datetime(2026, 11, 14, 21, 30, tzinfo=TZ)
KEY = "sk-proj-" + "Ab3dE5gH7jK9mN1pQ3sT5vX7zB9dF1hJ"


def session(id="cc-aaaaaaaaaaaaaaaa", turns=None, cwd="~/work/payments-api", interface=None) -> SessionEvidence:
    turns = turns if turns is not None else (
        Turn("owner", AT, "why does test_retry flake?", "main"),
        Turn("agent", AT + timedelta(minutes=40), "It fails only when two workers share Redis db 0.", "main"))
    return SessionEvidence(id, HarnessId.CLAUDE_CODE, interface, LOCAL, "native", "test_retry flake", AT,
                           AT + timedelta(minutes=40), "ubuntu-pc", cwd, "fix/retry-flake", (), turns)


class Cache:
    def __init__(self):
        self.entries = {}

    def cache_get(self, key):
        return self.entries.get(key, (None,))[0]

    def cache_put(self, key, value, evidence):
        self.entries[key] = (value, tuple(evidence))


def librarian(script) -> tuple[Librarian, FakeLlm, Cache]:
    ctx = FakePluginContext(fake_llm=FakeLlm(script))
    cache = Cache()
    return Librarian(HostLlm(ctx), cache), ctx.fake_llm, cache


def test_register_registers_one_auxiliary_task_per_job():
    ctx = FakePluginContext()
    atlas.register(ctx)
    assert set(ctx.auxiliary_tasks) == {"atlas_file", "atlas_backfill", "atlas_journal", "atlas_converse",
                                        "atlas_answer"}


def test_the_llm_port_asks_ctx_llm_through_the_jobs_own_task_and_only_when_called():
    ctx = FakePluginContext(fake_llm=FakeLlm({"atlas_backfill": [{"answer": 1}]}))
    port = HostLlm(ctx)
    assert ctx.lazy_reads == []
    assert port.structured("atlas_backfill", "Answer.", "the input", {"title": "t"}) == {"answer": 1}
    assert ctx.fake_llm.calls == [{"task": "atlas_backfill", "instructions": "Answer.", "input": "the input",
                                   "schema": {"title": "t"}}]


def test_an_answer_that_is_no_json_object_is_a_model_error():
    port = HostLlm(FakePluginContext(fake_llm=FakeLlm({"atlas_backfill": lambda call: None})))
    with pytest.raises(ModelError):
        port.structured("atlas_backfill", "Answer.", "the input", {"title": "t"})


def test_a_card_is_three_inferences_and_where_the_session_ran():
    lib, _, _ = librarian({"atlas_backfill": [{"intent": "Find the flake.", "outcome": "Found it.",
                                               "unfinished": "Not fixed."}]})
    s = session(interface="T3 Code")
    assert lib.card(s) == SessionCard(
        s.id, Inference("Find the flake.", (s.id,)), Inference("Found it.", (s.id,)), Inference("Not fixed.", (s.id,)),
        Fact("~/work/payments-api, branch fix/retry-flake.", Cite(s.id, "Claude Code via T3 Code", AT)))


def test_the_model_reads_no_secret_and_a_second_card_is_the_cached_one():
    lib, llm, cache = librarian({"atlas_backfill": [{"intent": "a", "outcome": "b", "unfinished": None}]})
    s = session(turns=(Turn("owner", AT, f"use {KEY} for the api", "main"),))
    first = lib.card(s)
    assert KEY not in llm.calls[0]["input"] and "[secret: api key]" in llm.calls[0]["input"]
    assert lib.card(s) == first and len(llm.calls) == 1
    assert [evidence for _, evidence in cache.entries.values()] == [(s.id,)]


def test_an_answer_the_card_cannot_use_is_not_cached():
    lib, llm, cache = librarian({"atlas_backfill": [{"intent": "a"}, {"intent": "a", "outcome": "b",
                                                                      "unfinished": None}]})
    with pytest.raises(ModelError):
        lib.card(session())
    assert cache.entries == {}
    assert lib.card(session()).outcome.text == "b" and len(llm.calls) == 2


def test_a_long_session_keeps_its_first_and_last_turns_within_the_budget():
    turns = tuple(Turn("owner" if n % 2 else "agent", AT + timedelta(minutes=n), f"turn {n} " + "x" * 1500, "main")
                  for n in range(40))
    lib, llm, _ = librarian({"atlas_backfill": [{"intent": "a", "outcome": "b", "unfinished": None}]})
    lib.card(session(turns=turns))
    sent = llm.calls[0]["input"]
    assert "turn 0 " in sent and "turn 39 " in sent and "earlier turns left out" in sent
    assert len(sent) < SESSION_BUDGET + 1000
    assert llm.calls[0]["schema"] == CARD_SCHEMA


def statement(text, quote, *ids):
    return {"text": text, "quote": quote, "sessions": list(ids)}


def test_grounding_keeps_a_fact_whose_quote_is_in_the_transcript_at_that_turns_time():
    s = session()
    fact = ground(statement("It fails with a shared Redis db.", "two workers   share Redis db 0", s.id), {s.id: s})
    assert fact == Fact("It fails with a shared Redis db.", Cite(s.id, "Claude Code", AT + timedelta(minutes=40)))


@pytest.mark.parametrize("quote", ["two workers share Redis db 7", "Redis db 0", None])
def test_grounding_demotes_a_fact_whose_quote_is_not_found_to_an_inference_with_the_same_basis(quote):
    s = session()
    assert ground(statement("It fails with a shared Redis db.", quote, s.id), {s.id: s}) == Inference(
        "It fails with a shared Redis db.", (s.id,))


def test_grounding_reads_a_quote_against_the_masked_transcript():
    s = session(turns=(Turn("agent", AT, f"the key {KEY} was rotated today", "main"),))
    assert isinstance(ground(statement("Rotated.", "the key [secret: api key] was rotated", s.id), {s.id: s}), Fact)


def test_a_statement_on_two_sessions_or_on_none_it_may_cite():
    a, b = session(), session("cc-bbbbbbbbbbbbbbbb")
    both = ground(statement("Same flake.", "two workers share Redis db 0", a.id, b.id, "cc-elsewhere"),
                  {a.id: a, b.id: b})
    assert both == Inference("Same flake.", (a.id, b.id))
    assert ground(statement("Elsewhere.", None, "cc-elsewhere"), {a.id: a}) is None


class Snap:
    """What group_backfill reads of a Snapshot."""
    def __init__(self, sessions, areas=("work", "health", "family")):
        self._sessions = {s.id: s for s in sessions}
        self._areas = areas

    def session(self, id):
        return self._sessions[id]

    def card(self, id):
        return SessionCard(id, Inference("a", (id,)), Inference("b", (id,)), None, None)

    def profile(self):
        return Profile(self._areas, ())


def group(*pursuits, one_offs=()):
    return {"pursuits": list(pursuits), "one_offs": list(one_offs)}


def draft(name, *ids, area="work"):
    return {"name": name, "kind": "problem", "area": area, "sessions": list(ids),
            "left_off": [statement("Shared Redis db.", "two workers share Redis db 0", ids[-1])], "decided": [],
            "open_questions": [], "reopen": [ids[-1]], "next_step": statement("Run it with one worker.", None, ids[-1])}


def test_grouping_asks_once_per_folder_then_once_to_reconcile():
    a, b = session(), session("cc-bbbbbbbbbbbbbbbb", cwd="~/work/payments-ci")
    calls = []

    def respond(call):
        calls.append(call["schema"]["title"])
        if call["schema"]["title"] == "reconcile":
            return {"merges": [{"drafts": ["d1", "d2"], "name": "Payments CI flakes"}]}
        id = a.id if a.id in call["input"] else b.id
        return group(draft(f"flake {id}", id))

    lib, _, _ = librarian({"atlas_backfill": respond})
    grouping = lib.group_backfill(Snap([a, b]), [a.id, b.id])
    assert calls == ["group", "group", "reconcile"]
    (merged,) = grouping.drafts
    assert merged.name == "Payments CI flakes" and [m.evidence for m in merged.sessions] == [a.id, b.id]
    assert isinstance(merged.resume.left_off[0], Fact)
    assert merged.resume.where[0].text == "ubuntu-pc, ~/work/payments-ci, branch fix/retry-flake"


def test_a_one_off_keeps_its_area_and_a_session_claimed_twice_stays_in_its_first_pursuit():
    a, b = session(), session("cc-bbbbbbbbbbbbbbbb")
    lib, _, _ = librarian({"atlas_backfill": [group(draft("flakes", a.id), draft("again", a.id),
                                                    one_offs=[{"session": b.id, "area": "family"},
                                                              {"session": a.id, "area": "health"}])]})
    grouping = lib.group_backfill(Snap([a, b]), [a.id, b.id])
    assert [d.name for d in grouping.drafts] == ["flakes"] and grouping.one_offs == {b.id: "family"}


def test_an_area_the_owner_does_not_have_is_a_model_error():
    a = session()
    lib, _, _ = librarian({"atlas_backfill": [group(draft("flakes", a.id, area="hobbies"))]})
    with pytest.raises(ModelError):
        lib.group_backfill(Snap([a]), [a.id])


@pytest.mark.parametrize("quote", ["[secret: api key]", "Rotate [secret: api key] now"])
def test_a_quote_that_is_short_once_its_masked_secrets_are_left_out_grounds_no_fact(quote):
    s = session(turns=(Turn("owner", AT, f"Rotate {KEY} now", "main"),))
    assert ground(statement("The key was rotated.", quote, s.id), {s.id: s}) == Inference("The key was rotated.",
                                                                                           (s.id,))


def test_the_answer_is_masked_before_it_is_cached():
    lib, _, cache = librarian({"atlas_backfill": [{"intent": f"Rotate {KEY}.", "outcome": "Done.",
                                                   "unfinished": None}]})
    assert lib.card(session()).intent.text == "Rotate [secret: api key]."
    assert not [value for value, _ in cache.entries.values() if KEY.encode() in value]


def test_the_model_reads_the_areas_masked_and_its_answer_files_under_the_owners_area():
    a = session()
    lib, llm, _ = librarian({"atlas_backfill": [group(one_offs=[{"session": a.id, "area": "keys [secret: api key]"}])]})
    grouping = lib.group_backfill(Snap([a], areas=("work", f"keys {KEY}")), [a.id])
    assert KEY not in json.dumps(llm.calls) and grouping.one_offs == {a.id: f"keys {KEY}"}


@pytest.mark.parametrize("answer", [group(draft("flakes", "cc-aaaaaaaaaaaaaaaa")), group()], ids=["some", "none"])
def test_a_group_answer_that_leaves_a_session_unfiled_is_not_used_or_kept(answer):
    a, b = session(), session("cc-bbbbbbbbbbbbbbbb")
    lib, _, cache = librarian({"atlas_backfill": [answer]})
    with pytest.raises(ModelError):
        lib.group_backfill(Snap([a, b]), [a.id, b.id])
    assert cache.entries == {}


@pytest.mark.parametrize("extra", [{KEY: "invented"}, {"more": {KEY: "invented"}}], ids=["key", "nested-key"])
def test_an_answer_is_cut_to_what_its_schema_names_before_it_is_cached(extra):
    lib, _, cache = librarian({"atlas_backfill": [{"intent": "a", "outcome": "b", "unfinished": None, **extra}]})
    lib.card(session())
    ((value, _),) = cache.entries.values()
    assert json.loads(value) == {"intent": "a", "outcome": "b", "unfinished": None}


def test_a_free_form_map_in_an_answer_is_kept_with_its_keys_masked():
    lib, _, cache = librarian({"atlas_backfill": [{"notes": {KEY: f"use {KEY}"}}]})
    lib._ask("atlas_backfill", "Answer.", "the input", {"title": "t", "properties": {"notes": {"type": "object"}}},
             (), lambda out: out)
    ((value, _),) = cache.entries.values()
    assert json.loads(value) == {"notes": {"[secret: api key]": "use [secret: api key]"}}


def test_an_answer_is_written_without_control_characters_noncharacters_or_lone_surrogates():
    lib, _, cache = librarian({"atlas_backfill": [{"intent": "Find\x85the\x9fflake￾\ud83d.", "outcome": "Café 漢 ✓.",
                                                   "unfinished": None}]})
    card = lib.card(session())
    assert (card.intent.text, card.outcome.text) == ("Find the flake.", "Café 漢 ✓.")
    ((value, _),) = cache.entries.values()
    assert json.loads(value)["intent"] == "Find the flake."


@pytest.mark.parametrize("intent, written", [
    ("password is Invented7![secret: token]", "password is [secret: password][secret: token]"),
    ("api  key: Zq8Xw2Lp9Rt4Vb7Nm3Kc6Jh1Gf5Ds0Ae8Yu", "api key: [secret: token]"),
    ("api\tkey: Zq8Xw2Lp9Rt4Vb7Nm3Kc6Jh1Gf5Ds0Ae8Yu", "api key: [secret: token]"),
], ids=["before-a-mask", "two-spaces", "tab"])
def test_an_answer_is_written_and_cached_with_every_secret_it_holds_masked(intent, written):
    lib, _, cache = librarian({"atlas_backfill": [{"intent": intent, "outcome": "Done.", "unfinished": None}]})
    assert lib.card(session()).intent.text == written
    ((value, _),) = cache.entries.values()
    assert json.loads(value)["intent"] == written


def test_a_quote_copied_exactly_from_what_the_model_read_is_a_fact():
    s = session(turns=(Turn("agent", AT, "The new password is :Hx9!rt7Q and the deploy waits on it", "main"),))
    shown = []

    def respond(call):
        shown.append(call["input"])
        line = next(line for line in call["input"].splitlines() if "deploy waits" in line)
        row = draft("deploy", s.id)
        row["left_off"] = [statement("The deploy waits on a password.", line[:line.index(" waits")], s.id)]
        return group(row)

    lib, _, _ = librarian({"atlas_backfill": respond})
    (built,) = lib.group_backfill(Snap([s]), [s.id]).drafts
    assert "password is :[secret: password] and the deploy" in shown[0]
    assert isinstance(built.resume.left_off[0], Fact)


def test_the_merge_takes_the_resume_note_of_the_session_worked_on_last_across_a_dst_fallback():
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 45, tzinfo=zone)
    then = datetime(2026, 11, 1, 1, 15, fold=1, tzinfo=zone)
    assert then.utcoffset() == timedelta(hours=-5) and then.timestamp() > first.timestamp()
    a = SessionEvidence("cc-aaaaaaaaaaaaaaaa", HarnessId.CLAUDE_CODE, None, LOCAL, "a", "invented", first, first,
                        "ubuntu-pc", "~/work/a", None, (), (Turn("owner", first, "invented first", "main"),))
    b = SessionEvidence("cc-bbbbbbbbbbbbbbbb", HarnessId.CLAUDE_CODE, None, LOCAL, "b", "invented", then, then,
                        "ubuntu-pc", "~/work/b", None, (), (Turn("owner", then, "invented then", "main"),))

    def respond(call):
        if call["schema"]["title"] == "reconcile":
            return {"merges": [{"drafts": ["d1", "d2"], "name": "Invented work"}]}
        (id,) = (i for i in (a.id, b.id) if i in call["input"])
        return group(draft(f"invented {id}", id))

    lib, _, _ = librarian({"atlas_backfill": respond})
    (merged,) = lib.group_backfill(Snap([a, b]), [a.id, b.id]).drafts
    assert [m.evidence for m in merged.sessions] == [a.id, b.id]
    assert merged.resume.reopen == (b.id,) and merged.resume.where[0].text == "ubuntu-pc, ~/work/b"


class DaySnap:
    """What journal_lines reads of a Snapshot."""
    def __init__(self, sessions, pursuits):
        self._sessions = {s.id: s for s in sessions}
        self._pursuits = pursuits

    def session(self, id):
        return self._sessions[id]

    def pursuits(self):
        return self._pursuits

    def settings(self):
        return SETTINGS


def listing(id, name, *ids):
    return replace(snapshots.pursuit(id, name), sessions=tuple(Membership(i, AT, True) for i in ids))


def line(pursuit, text, quote=None):
    return {"pursuit": pursuit, "text": text, "quote": quote}


def lines(worked_on=(), decided=(), finished=()):
    return {"worked_on": list(worked_on), "decided": list(decided), "finished": list(finished)}


YESTERDAY = AT - timedelta(days=1)


def test_journal_lines_ask_once_per_session_a_pursuit_lists_reading_its_turns_of_that_day_only():
    worked = session(turns=(Turn("owner", YESTERDAY, "an older question about invented flags", "main"),
                            Turn("owner", AT, "why does test_retry flake?", "main"),
                            Turn("agent", AT + timedelta(minutes=5), "It fails only when two workers share Redis db 0.",
                                 "main")))
    alone = session("cc-bbbbbbbbbbbbbbbb")
    lib, llm, cache = librarian({"atlas_journal": [lines(
        worked_on=[line("Payments CI flakes", "found the shared Redis db.", "two workers share Redis db 0")],
        decided=[line("Payments CI flakes", "keep xdist.")])]})
    got = lib.journal_lines(DaySnap([worked, alone], [listing("p-2x7b", "Payments CI flakes", worked.id)]), AT.date(),
                            [worked.id, alone.id])
    (call,) = llm.calls
    assert "why does test_retry flake?" in call["input"] and "older question" not in call["input"]
    assert call["schema"]["properties"]["worked_on"]["items"]["properties"]["pursuit"] == {"enum": ["Payments CI flakes"]}
    assert got.worked_on == (Fact("Payments CI flakes: found the shared Redis db.",
                                  Cite(worked.id, "Claude Code", AT + timedelta(minutes=5))),)
    assert got.decided == (Inference("Payments CI flakes: keep xdist.", (worked.id,)),) and got.finished == ()
    assert [evidence for _, evidence in cache.entries.values()] == [(worked.id,)]


def test_a_journal_line_about_a_pursuit_not_shown_is_left_out_and_the_answer_is_kept_masked_and_cut():
    s = session()
    lib, llm, cache = librarian({"atlas_journal": [{**lines(worked_on=[
        line("Billing export service", "invented."), line("keys [secret: api key]", f"rotated {KEY}."),
        {**line("keys [secret: api key]", "noted."), KEY: "invented"}]), "more": KEY}]})
    got = lib.journal_lines(DaySnap([s], [listing("p-1", f"keys {KEY}", s.id)]), AT.date(), [s.id])
    assert KEY not in json.dumps(llm.calls) and not [v for v, _ in cache.entries.values() if KEY.encode() in v]
    assert [st.text for st in got.worked_on] == [f"keys {KEY}: rotated [secret: api key].", f"keys {KEY}: noted."]
    ((value, _),) = cache.entries.values()
    assert set(json.loads(value)) == {"worked_on", "decided", "finished"}


def test_an_unusable_journal_answer_is_a_model_error_and_is_not_kept():
    s = session()
    lib, _, cache = librarian({"atlas_journal": [{"worked_on": [line("A", 7)], "decided": [], "finished": []}]})
    with pytest.raises(ModelError):
        lib.journal_lines(DaySnap([s], [listing("p-1", "A", s.id)]), AT.date(), [s.id])
    assert cache.entries == {}


def test_a_session_with_no_turn_of_its_own_that_day_asks_nothing():
    s = session(turns=(Turn("agent", AT, "a subagent's invented answer", "subagent:agent-1"),))
    lib, llm, _ = librarian({})
    assert lib.journal_lines(DaySnap([s], [listing("p-1", "A", s.id)]), AT.date(), [s.id]).worked_on == ()
    assert llm.calls == []
