"""Scene 5's corrections by chat: converse's model tier reads Merge, Split, AddArea and SetArea, and
rules.converse applies them in one ChangeSet with the owner's Note."""
import json
from dataclasses import fields, replace
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas import board_api, converse, rules
from atlas.librarian import Librarian, PursuitDraft
from atlas.model import (AddArea, Fact, Inference, Kind, Merge, NoteOrigin, OwnerMessage, Profile, ResumeNote, SetArea,
                         Skipped, Split, Status)
from atlas.record import Record
from harness import scenes, world
from harness.home import AtlasHome
from harness.model import STAGING as SPLIT_NAME, corrections_model
from snapshots import after, at, pursuit, snapshot

SUNDAY = date(2026, 11, 15)
NOW = at(SUNDAY, "20:39")
FIRMWARE = pursuit("p-6r2t", "Greenhouse logger firmware", worked=[date(2026, 11, 11)])
DASHBOARD = replace(pursuit("p-6r2u", "Greenhouse dashboard", worked=[date(2026, 11, 2), date(2026, 11, 9)],
                            kind=Kind.EXPERIMENT, focus=True), area="family")
BILLING = pursuit("p-4k7q", "Billing export service",
                  worked=[date(2026, 7, 30), date(2026, 11, 12), date(2026, 11, 13)])
PROFILE = Profile(("work", "health", "family"), ())
SNAP = snapshot([FIRMWARE, DASHBOARD, BILLING], profile=PROFILE)


def said(text, ref="telegram:5550001:8801"):
    return OwnerMessage(text, NOW, "telegram", "telegram:5550001", None, ref)


class Model:
    """A model that answers each call with the next scripted output."""

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def structured(self, task, instructions, input, schema):
        self.calls.append((task, input, schema))
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def read(text, *outputs, snap=SNAP):
    model = Model(*outputs)
    return converse.interpret(said(text), snap, Librarian(model, None)), model


MERGE = {"command": "Merge", "a": "Greenhouse logger firmware", "b": "Greenhouse dashboard",
         "into_name": "Greenhouse logger", "rule": "two folders, one thing"}


def test_the_model_reads_words_the_grammar_cannot_and_names_pursuits_by_id():
    reading, model = read("merge the two greenhouse ones. two folders, one thing", {"commands": [MERGE]})
    assert reading == converse.Interpretation(
        (Merge("p-6r2t", "p-6r2u", "Greenhouse logger", "two folders, one thing"),), (), False, None)
    [(task, input, _)] = model.calls
    assert task == "atlas_converse"
    assert "- Greenhouse dashboard · experiment · family · active" in input
    assert input.endswith("Message: merge the two greenhouse ones. two folders, one thing")


def test_the_grammar_reads_first_and_the_model_is_not_asked():
    reading, model = read("park the greenhouse dashboard")
    assert reading.commands and model.calls == []


def test_an_area_is_one_the_owner_has_or_adds_in_the_same_message():
    reading, _ = read("put the greenhouse logger in a new Area, home", {"commands": [
        {"command": "AddArea", "area": "home"}, {"command": "SetArea", "pursuit": "Greenhouse logger firmware",
                                                 "area": "Home"}]})
    assert reading.commands == (AddArea("home"), SetArea("p-6r2t", "home"))
    reading, _ = read("put the logger in home", {"commands": [
        {"command": "SetArea", "pursuit": "Greenhouse logger firmware", "area": "home"}]})
    assert reading.unclear == converse.UNREAD


@pytest.mark.parametrize("output", [
    {"commands": []},
    {"commands": [{**MERGE, "a": "Zeppelin"}]},
    {"commands": [{**MERGE, "b": MERGE["a"]}]},
    {"commands": [{**MERGE, "into_name": None}]},
    {"commands": [{"command": "Forget"}]},
    {"nothing": "here"},
    TimeoutError("the model took too long"),
])
def test_an_answer_atlas_cannot_use_leaves_the_message_unread(output):
    reading, _ = read("merge the two greenhouse ones", output)
    assert reading == converse.Interpretation((), (), False, converse.UNREAD)


def test_a_merge_with_a_null_reason_reads_as_one_with_none():
    reading, _ = read("merge the two greenhouse ones", {"commands": [{**MERGE, "rule": None}]})
    assert reading.commands == (Merge("p-6r2t", "p-6r2u", "Greenhouse logger", ""),)


@pytest.mark.parametrize("text, kinds", [
    ("atlas, merge the two greenhouse ones. two folders, one thing", {Merge}),
    ("merge the greenhouse dashboard into the firmware one", {Merge}),
    ("split the staging deploy chores out of Billing export service", {Split}),
    ("split the deploy work out of Billing export service", {Split}),
    ("put the greenhouse logger in a new Area, home", {AddArea, SetArea}),
    ("atlas, tidy up my greenhouse things", set()),
    ("thanks. can you find me a train to Lisbon on Saturday?", set()),
])
def test_the_owners_words_name_the_kinds_of_correction_they_ask_for(text, kinds):
    assert converse.asked(text) == kinds


SPLIT_ANSWER = {"command": "Split", "pursuit": "Billing export service", "new_name": "Billing export: deploy",
                "criterion": "deploy work"}
AREA_ANSWER = [{"command": "AddArea", "area": "bay"},
               {"command": "SetArea", "pursuit": "Greenhouse logger firmware", "area": "bay"}]
ORDINARY = [
    ("same here, thanks", [MERGE]),
    ("one thing, can you check the weather?", [MERGE]),
    ("put that in the same folder", [MERGE]),
    ("can you join me for lunch?", [MERGE]),
    ("is the bay area warm in May?", AREA_ANSWER),
    ("separate question: what time is it?", [SPLIT_ANSWER]),
    ("let's split the bill", [SPLIT_ANSWER]),
]


@pytest.mark.parametrize("text, answer", ORDINARY)
def test_ordinary_words_near_a_correction_word_make_no_model_call(text, answer):
    assert converse.asked(text) == set()
    reading, model = read(text, {"commands": answer})
    assert reading == converse.Interpretation((), (), False, converse.UNREAD)
    assert model.calls == []


def test_a_plain_message_makes_no_model_call():
    reading, model = read("thanks. can you find me a train to Lisbon on Saturday?",
                          {"commands": [{"command": "AddArea", "area": "pwned"}]})
    assert reading.unclear == converse.UNREAD and reading.commands == ()
    assert model.calls == []


def test_words_that_name_no_correction_are_not_given_to_the_model():
    two = {"commands": [MERGE, {**MERGE, "a": "Greenhouse logger firmware", "b": "Billing export service"}]}
    reading, model = read("atlas, tidy up my greenhouse things", two)
    assert reading.unclear == converse.UNREAD and model.calls == []


def test_the_model_cannot_answer_with_a_kind_the_words_did_not_ask_for():
    reading, model = read("atlas, add a new area called garden", {"commands": [MERGE]})
    assert reading == converse.Interpretation((), (), False, converse.UNREAD)
    assert len(model.calls) == 1


def test_the_model_cannot_chain_two_merges():
    two = {"commands": [MERGE, {**MERGE, "a": "Greenhouse logger firmware", "b": "Billing export service"}]}
    reading, _ = read("atlas, merge my greenhouse things", two)
    assert reading == converse.Interpretation((), (), False, converse.UNREAD)


def test_the_schema_is_generated_from_the_commands_the_model_reads():
    item = converse.filing_schema()["properties"]["commands"]["items"]["properties"]
    assert item["command"]["enum"] == ["Merge", "Split", "AddArea", "SetArea"]
    assert set(item) - {"command"} == {f.name for cls in (Merge, Split, AddArea, SetArea) for f in fields(cls)}


def test_a_merge_keeps_both_histories_every_session_and_the_reason():
    d = rules.converse(SNAP, said("merge the two greenhouse ones"), (Merge("p-6r2u", "p-6r2t", "Greenhouse logger",
                                                                          "two folders, one thing"),), ())
    assert d.outcome.lines == ('Merged into "Greenhouse logger" (project). It keeps both histories, 3 Sessions.',)
    snap = after(d)
    [logger, billing] = sorted(snap.pursuits(), key=lambda p: p.name, reverse=True)
    assert (logger.id, logger.kind, logger.area, logger.focus) == ("p-6r2t", Kind.PROJECT, "work", True)
    assert [m.evidence for m in logger.sessions] == ["cc-6r2u0000", "cc-6r2u0001", "cc-6r2t0000"]
    assert logger.resume == FIRMWARE.resume and logger.next_step == FIRMWARE.next_step
    assert [h.id for h in logger.history] == ["h-0001", "h-0002", "h-0003", "h-0004"]
    assert [h.statement.text for h in logger.history[1:]] == [
        "Created it from your Sessions.", "Added it to Focus on the Board.",
        'Merged "Greenhouse logger firmware" and "Greenhouse dashboard" into one Pursuit.']
    [replaced] = logger.past_steps
    assert isinstance(replaced.ended, Skipped) and replaced.ended.how == "replaced"
    assert replaced.id != logger.next_step.id
    [rule] = logger.filing_rules
    assert rule == Fact('Work filed as "Greenhouse dashboard" belongs here: two folders, one thing.',
                        logger.history[-1].statement.source)
    assert rule.source.evidence == rules.note_id(said("merge the two greenhouse ones"))
    assert billing.id == "p-4k7q"


def test_a_merge_with_no_reason_keeps_a_filing_rule_all_the_same():
    one = snapshot([FIRMWARE, pursuit("p-6r2v", "Greenhouse notes", worked=[date(2026, 11, 1)])], profile=PROFILE)
    d = rules.converse(one, said("merge"), (Merge("p-6r2t", "p-6r2v", "Greenhouse logger", ""),), ())
    assert d.outcome.lines == ('Merged into "Greenhouse logger" (project). It keeps both histories, 2 Sessions.',)
    assert after(d).pursuit("Greenhouse logger").filing_rules[0].text == 'Work filed as "Greenhouse notes" belongs here.'


STAGING = PursuitDraft("ignored", Kind.PROJECT, "family", (BILLING.sessions[1],),
                       ResumeNote((), (Inference("The deploy reads env vars.", ("cc-4k7q0001",)),), (), (),
                                  ("cc-4k7q0001",)),
                       Inference("Rotate the staging database password.", ("cc-4k7q0001",)))
SPLIT = Split("p-4k7q", "Billing export: staging deploy", "staging deploy chores")


def test_a_split_moves_the_sessions_the_owner_named_into_a_new_pursuit():
    d = rules.converse(SNAP, said("split the staging deploy chores out"), (SPLIT,), (), prework={SPLIT: STAGING})
    assert d.outcome.lines == ('Split. "Billing export: staging deploy" (project) has 1 Session.\n'
                               "Next Step: rotate the staging database password [I]\n"
                               '"Billing export service" keeps 2 Sessions.',)
    snap = after(d)
    new, billing = snap.pursuit("Billing export: staging deploy"), snap.pursuit("p-4k7q")
    assert [m.evidence for m in new.sessions] == ["cc-4k7q0001"]
    assert [m.evidence for m in billing.sessions] == ["cc-4k7q0000", "cc-4k7q0002"]
    assert (new.area, new.status, new.focus, new.created) == ("work", Status.ACTIVE, False, SUNDAY)
    assert new.next_step.text.text == "Rotate the staging database password."
    assert new.filing_rules[0].text == 'Staging deploy chores belong here, not in "Billing export service".'
    assert billing.filing_rules[0].text == 'Staging deploy chores belong in "Billing export: staging deploy", not here.'
    assert new.history[0].statement.text == 'Split out of "Billing export service" with 1 Session.'
    assert billing.history[-1].statement.text == 'Split "Billing export: staging deploy" out of it with 1 Session.'


def test_a_split_that_would_leave_one_side_empty_is_refused():
    every = replace(STAGING, sessions=BILLING.sessions)
    d = rules.converse(SNAP, said("split it all out"), (SPLIT,), (), prework={SPLIT: every})
    assert not d.outcome.ok and d.changes._ops == []


def test_a_repeated_split_or_merge_writes_nothing_and_leaves_the_message_to_hermes():
    split = after(rules.converse(SNAP, said("split the staging deploy chores out"), (SPLIT,), (),
                                 prework={SPLIT: STAGING}))
    again = rules.converse(split, said("split the staging deploy chores out", ref="telegram:5550001:8802"),
                           (SPLIT,), (), prework={SPLIT: STAGING})
    assert not again.outcome.ok and again.changes._ops == []
    merge = Merge("p-6r2t", "p-6r2u", "Greenhouse logger", "")
    merged = after(rules.converse(SNAP, said("merge the two greenhouse ones"), (merge,), ()))
    again = rules.converse(merged, said("merge the two greenhouse ones", ref="telegram:5550001:8802"), (merge,), ())
    assert not again.outcome.ok and again.changes._ops == []


def test_an_area_added_and_given_in_one_message_answers_with_one_line():
    d = rules.converse(SNAP, said("put the greenhouse logger in a new Area, home"),
                       (AddArea("home"), SetArea("p-6r2t", "home")), ())
    assert d.outcome.lines == ('Added the Area "home". Your Areas: work, health, family, home.',)
    snap = after(d)
    assert snap.profile().areas == ("work", "health", "family", "home")
    assert snap.pursuit("p-6r2t").area == "home"
    assert snap.pursuit("p-6r2t").history[-1].statement.text == 'Moved it to the Area "home".'


def test_an_area_given_alone_or_added_twice_says_so():
    d = rules.converse(SNAP, said("put the logger in family"), (SetArea("p-6r2t", "family"),), ())
    assert d.outcome.lines == ('Greenhouse logger firmware: now in the Area "family".',)
    d = rules.converse(SNAP, said("add an area, Family"), (AddArea("Family"),), ())
    assert d.outcome.lines == ('You already have the Area "family". Your Areas: work, health, family.',)
    assert [op.path for op in d.changes._ops] == ["notes/2026/11/2026-11-15-telegram.md"]


def test_the_corrections_of_one_message_see_each_other():
    d = rules.converse(SNAP, said("merge the greenhouse ones and put them in home"),
                       (Merge("p-6r2t", "p-6r2u", "Greenhouse logger", ""), AddArea("home"),
                        SetArea("p-6r2u", "home")), ())
    snap = after(d)
    assert [(p.name, p.area) for p in snap.pursuits() if p.name.startswith("Greenhouse")] == [("Greenhouse logger", "home")]


def test_a_tick_after_scene_5_puts_back_no_merged_away_pursuit_and_keeps_no_hand_edit(tmp_path):
    with AtlasHome(tmp_path / "scene", corrections_model()) as home:
        home.set_clock(world.CORRECTED_AT)
        scenes._after_backfill(home)
        before = sorted(p.name for p in (home.record_root / "pursuits").iterdir())
        scenes._corrections(home)
        corrected = sorted(p.name for p in (home.record_root / "pursuits").iterdir())
        assert set(before) - set(corrected) == {"greenhouse-logger-firmware.md", "greenhouse-dashboard.md"}
        assert set(corrected) - set(before) == {"greenhouse-logger.md", "billing-export-staging-deploy.md"}
        home.set_clock(world.CORRECTED_AT + timedelta(minutes=10))
        assert home.cli("atlas", "tick", "--quiet").code == 0
        record = Record.open(home.record_root)
        assert record.reconcile() == []
        assert sorted(p.name for p in (home.record_root / "pursuits").iterdir()) == corrected
        assert [n for n in record.snapshot().notes() if n.origin is NoteOrigin.HAND_EDIT] == []


def test_the_board_after_scene_5_shows_the_merged_pursuit_once_as_new_and_both_sides_of_the_split(tmp_path,
                                                                                                    monkeypatch):
    with AtlasHome(tmp_path / "scene", corrections_model()) as home:
        home.set_clock(world.CORRECTED_AT)
        scenes._after_backfill(home)
        held = {p.name: p.id for p in Record.open(home.record_root).snapshot().pursuits()}
        scenes._corrections(home)
        after_ids = {p.name: p.id for p in Record.open(home.record_root).snapshot().pursuits()}
        [gone] = {held["Greenhouse logger firmware"], held["Greenhouse dashboard"]} - set(after_ids.values())
        (home.record_root / ".atlas" / "seen.json").write_text(json.dumps({gone: "h-0003"}))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "no-hermes"))
        monkeypatch.setenv("ATLAS_RECORD", str(home.record_root))
        monkeypatch.setenv("ATLAS_NOW", (world.CORRECTED_AT + timedelta(minutes=10)).isoformat())
        app = FastAPI()
        app.include_router(board_api.router, prefix="/api/plugins/atlas")
        board = TestClient(app).get("/api/plugins/atlas/board")
        assert board.status_code == 200
        cards = [c for cards in board.json()["columns"].values() for c in cards]
        assert sorted(c["id"] for c in cards) == sorted(after_ids.values())
        [logger] = [c for c in cards if c["name"].startswith("Greenhouse")]
        assert (logger["name"], logger["new"]) == ("Greenhouse logger", True)
        assert {"Billing export service", SPLIT_NAME} <= {c["name"] for c in cards}
