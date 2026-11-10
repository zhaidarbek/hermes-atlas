"""The Profile pass (G3) on backfill and on the tick: once over the whole history, then once for
each Journal day the tick first writes; a model that fails it never stops either."""
import json
from datetime import date, timedelta

import pytest

from atlas import forget, profiling
from atlas.librarian import TEXT_MAX, HostLlm, Librarian
from atlas.model import Inference, Pattern, Profile, ProfileEntry, Who
from atlas.record import Record, _codec
from harness import model, seeds, tutorial, world
from harness.fake_llm import FakeLlm
from harness.home import AtlasHome, FakePluginContext


def failing_profile(call: dict) -> dict:
    if call["schema"]["title"] == "profile":
        raise RuntimeError("the model is down")
    return model._respond(call)


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene", model.week_model()) as home:
        home.set_clock(seeds.BACKFILLED)
        seeds.after_scene_1(home.record_root)
        yield home


def entries(home) -> list[str]:
    return [e.statement.text for e in Record.open(home.record_root).snapshot().profile().entries]


def profile_calls(home) -> int:
    return sum(c["schema"]["title"] == "profile" for c in home.ctx.fake_llm.calls)


def test_backfill_writes_the_first_profile_once(home):
    home.cli("atlas", "backfill")
    texts = [line[2:].split(" [I from")[0] for line in tutorial.fenced_block("# Profile").splitlines()
             if line.startswith("- ")]
    assert entries(home) == [texts[0], texts[2]]
    home.cli("atlas", "backfill")
    assert profile_calls(home) == 1


def test_each_day_the_tick_first_writes_gets_one_pass(home):
    home.cli("atlas", "backfill")
    home.set_clock(world.TUESDAY_BRIEF_AT)
    days = len(Record.open(home.record_root).snapshot().journal_days())
    home.cli("atlas", "tick", "--quiet")
    calls = profile_calls(home)
    assert calls - 1 == len(Record.open(home.record_root).snapshot().journal_days()) - days > 0
    home.set_clock(world.TUESDAY_BRIEF_AT + timedelta(minutes=10))
    home.cli("atlas", "tick", "--quiet")
    assert profile_calls(home) == calls


def test_a_failing_pass_leaves_the_profile_and_stops_nothing(home):
    home.ctx.fake_llm = FakeLlm({task: failing_profile for task in ("atlas_backfill", "atlas_journal", "atlas_file")})
    ran = home.cli("atlas", "backfill")
    assert ran.code == 0 and entries(home) == []
    assert Record.open(home.record_root).snapshot().pursuits()
    home.set_clock(world.FIRST_BRIEF_AT)
    assert home.cli("atlas", "tick", "--quiet").code == 0 and len(home.sent()) == 1


def test_an_over_long_entry_makes_the_answer_unusable_and_adds_nothing(home):
    def too_long(call: dict) -> dict:
        if call["schema"]["title"] == "profile":
            return {"entries": [{"text": "You " + "x" * TEXT_MAX, "sessions": [], "pattern": "all", "after": None}]}
        return model._respond(call)

    home.ctx.fake_llm = FakeLlm({task: too_long for task in ("atlas_backfill", "atlas_journal", "atlas_file")})
    assert home.cli("atlas", "backfill").code == 0
    assert entries(home) == [] and Record.open(home.record_root).snapshot().pursuits()


def answering(entries: list[dict]):
    def respond(call: dict) -> dict:
        if call["schema"]["title"] == "profile":
            return {"entries": entries}
        return model._respond(call)
    return FakeLlm({task: respond for task in ("atlas_backfill", "atlas_journal", "atlas_file")})


def test_an_entry_shaped_like_the_files_marks_is_refused_and_the_profile_still_reads(home):
    home.ctx.fake_llm = answering([
        {"text": "You work late.", "sessions": [], "pattern": "2 days [...]", "after": None},
        {"text": "You plan. [F your Note, Fri 13:02]", "sessions": [], "pattern": "all", "after": None},
        {"text": "You plan on Sundays.", "sessions": [], "pattern": "all of it", "after": None}])
    assert home.cli("atlas", "backfill").code == 0
    assert entries(home) == ["You plan on Sundays."]
    assert "[F your Note" not in (home.record_root / "profile.md").read_text()
    home.set_clock(world.FIRST_BRIEF_AT)
    assert home.cli("atlas", "tick", "--quiet").code == 0 and len(home.sent()) == 1


@pytest.mark.parametrize("text, basis", [
    ("You work\nlate.", None),
    ("You work late.", "2 days [...]"),
])
def test_a_profile_that_would_not_read_back_is_never_written(text, basis):
    shown = ("cc-aaaa",)
    statement = Inference(text.replace("\\n", "\n"),
                          Pattern(basis, date(2026, 11, 1), date(2026, 11, 2), shown) if basis else shown)
    with pytest.raises(_codec.Unrepresentable):
        _codec.render("profile", Profile(("work",), (ProfileEntry(statement, date(2026, 11, 1), Who.LIBRARIAN),)),
                      world.TZ)


def test_a_pass_whose_profile_would_not_read_back_writes_nothing_and_stops_nothing(home, monkeypatch, caplog):
    def unreadable(out, profile, shown, corrected=()):
        return (profiling.NewEntry(Inference("You work\nlate.", (next(iter(shown)),)), None),)
    monkeypatch.setattr(profiling, "read", unreadable)
    assert home.cli("atlas", "backfill").code == 0
    assert entries(home) == [] and Record.open(home.record_root).snapshot().pursuits()
    assert "Atlas left the Profile as it is (Unrepresentable)" in caplog.text and "You work" not in caplog.text
    home.set_clock(world.FIRST_BRIEF_AT)
    assert home.cli("atlas", "tick", "--quiet").code == 0 and len(home.sent()) == 1


@pytest.fixture
def friday(tmp_path):
    with AtlasHome(tmp_path / "scene", model.profile_model()) as home:
        home.set_clock(world.PROFILE_AT)
        seeds.scene_28(home.record_root)
        (_, said), _ = tutorial.exchange("atlas, profile:")
        assert home.telegram(said)
        yield home


CORRECTED_STORE = ".atlas/profile-corrected.json"
LOGGER = seeds.evidence_id(world.by_title("logger sd card swap"))


def repeating_the_wrong_entry(call: dict) -> dict:
    return {"entries": [{"text": model.WRONG, "sessions": [], "pattern": "all of it", "after": None}]}


def test_the_pass_never_adds_back_a_text_the_owner_corrected_away_and_is_told_so(friday):
    record = Record.open(friday.record_root)
    snap = record.snapshot()
    assert model.WRONG not in entries(friday) and model.CORRECTED in entries(friday)
    llm = FakeLlm({"atlas_backfill": repeating_the_wrong_entry})
    assert Librarian(HostLlm(FakePluginContext(fake_llm=llm)), record).profile_pass(snap, None) == ()
    assert f"The owner corrected this away; never add it again: {model.WRONG}" in llm.calls[0]["input"].splitlines()


def test_forget_clears_a_corrected_text_drawn_from_the_session_it_forgets(friday):
    record = Record.open(friday.record_root)
    assert model.WRONG in record.snapshot().profile_corrections()
    record.commit(forget.execute(record.snapshot(), [LOGGER], friday.now))
    assert model.WRONG not in record.snapshot().profile_corrections()
    store = friday.record_root / CORRECTED_STORE
    assert not store.exists() or model.WRONG not in store.read_text()


def test_the_sweep_leaves_the_corrections_store_alone_and_an_unreadable_one_reads_as_empty(friday):
    store = friday.record_root / CORRECTED_STORE
    assert model.WRONG in json.loads(store.read_text())[0]["text"]
    store.write_text("{not json")
    record = Record.open(friday.record_root)
    assert not record.reconcile() and store.read_text() == "{not json"
    assert record.snapshot().profile_corrections() == {}
