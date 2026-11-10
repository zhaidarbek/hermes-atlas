"""Backfill builds the Pursuits: rules.start_state, cards, the cache, and what a rerun, a crash, a
missing model and a Forget in the middle do."""
import json
import os
import re
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from atlas import rules
from atlas.app import NO_MODEL, Atlas
from atlas.librarian import FIELD_MAX, HostLlm, Librarian
from atlas.model import (LOCAL, Forgotten, HarnessId, Inference, RawPart, SessionCard, SessionEvidence, Status, Turn,
                         evidence_id, part_file, part_origin)
from atlas.record import Record, _txn
from harness import seeds, world
from harness.fake_llm import FakeLlm
from harness.home import AtlasHome, FakePluginContext
from harness.model import world_model

NOW = seeds.BACKFILLED
TODAY = NOW.date()
REMOVED_WORDS = "violet lantern"
SESSION = re.compile(r"^Session (\S+) · ", re.M)


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene", world_model()) as home:
        home.set_clock(NOW)
        seeds.after_scene_1(home.record_root)
        yield home


def atlas(home) -> Atlas:
    return Atlas.open(home.record_root, HostLlm(home.ctx))


def snap(home):
    return Record.open(home.record_root).snapshot()


def machinery(home) -> dict:
    return {k: v for k, v in home.files().items() if k.startswith("atlas/")}


@pytest.mark.parametrize("days, status", [(0, Status.ACTIVE), (30, Status.ACTIVE), (31, Status.PARKED),
                                          (400, Status.PARKED)])
def test_a_pursuit_starts_active_when_worked_on_in_the_last_30_days(days, status):
    assert rules.starting_status(TODAY - timedelta(days=days), TODAY) is status


def test_a_pursuit_never_starts_done_or_dropped():
    assert {rules.starting_status(date(2020, 1, 1) + timedelta(days=n), TODAY) for n in range(3000)} == {
        Status.ACTIVE, Status.PARKED}


def test_backfill_reports_the_pursuits_it_built(home):
    report = atlas(home).backfill(NOW)
    assert report.render().split("\n\n")[1] == (
        "Pursuits        11\n"
        "  active         8   touched since 15 Oct\n"
        "  parked         3   quiet for more than a month\n"
        "Not a Pursuit    3 one-off Sessions, kept as Evidence\n"
        "Journal         21 days, 2 Jun to 14 Nov\n"
        "Board          http://127.0.0.1:9119/atlas")


def test_a_backfill_that_crashed_before_its_pursuits_were_committed_asks_the_model_nothing_again(home, monkeypatch):
    real = Record.commit

    def crash_on_pursuits(record, cs):
        if any(op.path.startswith("pursuits/") for op in cs._ops):
            raise KeyboardInterrupt
        return real(record, cs)

    monkeypatch.setattr(Record, "commit", crash_on_pursuits)
    with pytest.raises(KeyboardInterrupt):
        atlas(home).backfill(NOW)
    asked = len(home.ctx.fake_llm.calls)
    assert snap(home).pursuits() == [] and asked > 0
    monkeypatch.setattr(Record, "commit", real)
    home.ctx.fake_llm = FakeLlm()
    assert atlas(home).backfill(NOW).filed.pursuits == len(snap(home).pursuits()) == 11


def test_cards_written_before_a_failed_model_call_are_kept_and_the_rest_follow_on_the_next_run(home, caplog):
    answers = world_model()
    calls = []

    def fails_on_the_fifth(call):
        calls.append(call)
        if len(calls) == 5:
            raise TimeoutError("the model took too long")
        return answers._script["atlas_backfill"](call)

    home.ctx.fake_llm = FakeLlm({"atlas_backfill": fails_on_the_fifth})
    report = atlas(home).backfill(NOW)
    assert report.filed is None and "stopped writing cards" in caplog.text
    held = snap(home).held().sessions()
    assert sum(snap(home).card(h.id) is not None for h in held) == 4
    atlas(home).backfill(NOW)
    assert all(snap(home).card(h.id) is not None for h in held) and len(snap(home).pursuits()) == 11


def test_with_no_model_backfill_reads_the_sessions_logs_and_builds_nothing(home, caplog):
    report = Atlas.open(home.record_root).backfill(NOW)
    assert report.sessions > 0 and report.filed is None and snap(home).pursuits() == []
    assert "no model" in caplog.text


@pytest.mark.parametrize("model", [FakeLlm(), FakeLlm({"atlas_backfill": lambda call: {"wrong": "shape"}})],
                         ids=["unanswered", "unusable"])
def test_hermes_atlas_backfill_with_a_failing_model_logs_and_builds_nothing(home, model, caplog):
    home.ctx.fake_llm = model
    ran = home.cli("atlas", "backfill")
    assert ran.code == 0 and ran.out.startswith("Read ") and "Board" not in ran.out
    assert ran.out.rstrip().endswith(NO_MODEL)
    assert snap(home).pursuits() == [] and "stopped writing cards" in caplog.text


def test_a_failing_grouping_call_builds_nothing_and_the_next_backfill_builds_all(home, caplog):
    answers = world_model()._script["atlas_backfill"]

    def no_grouping(call):
        if call["schema"]["title"] == "reconcile":
            raise ConnectionError("provider down")
        return answers(call)

    home.ctx.fake_llm = FakeLlm({"atlas_backfill": no_grouping})
    assert atlas(home).backfill(NOW).filed is None and "gave no grouping" in caplog.text
    home.ctx.fake_llm = world_model()
    atlas(home).backfill(NOW)
    assert len(snap(home).pursuits()) == 11


def forget(root, id):
    """What a Forget commits: the Session's skip key in forgotten.md, and its evidence removed."""
    record = Record.open(root)
    s = record.snapshot()
    key = next(h.skip_key for h in s.held().sessions() if h.id == id)
    f = s.forgotten()
    record.commit(s.changes().put_forgotten(Forgotten(f.entries + ((NOW, "a Session"),), f.skip_keys | {key}))
                  .remove_evidence(id))


@pytest.mark.parametrize("when", ["grouped", "planned"])
def test_a_session_forgotten_while_the_pursuits_are_planned_leaves_nothing_read_from_it(home, monkeypatch, when):
    gone = seeds.evidence_id(world.by_title("redis db per worker"))
    kept = [seeds.evidence_id(world.by_title(t)) for t in ("payments api retries", "ci matrix", "retry flake")]
    answers = world_model()._script["atlas_backfill"]

    def respond(call):
        out = answers(call)
        if call["schema"]["title"] == "group" and gone in call["input"]:
            row = next(r for r in out["pursuits"] if r["name"] == "Payments CI flakes")
            row.update(name=f"Payments {REMOVED_WORDS}", kind="experiment",
                       decided=[{"text": f"One Redis db per worker, the {REMOVED_WORDS} way.", "quote": None,
                                 "sessions": [gone, kept[-1]]}],
                       next_step={"text": f"Run the {REMOVED_WORDS} test again.", "quote": None,
                                  "sessions": [gone, kept[-1]]})
        return out

    home.ctx.fake_llm = FakeLlm({"atlas_backfill": respond})
    target, name = (Librarian, "group_backfill") if when == "grouped" else (rules, "start_state")
    real = getattr(target, name)

    def then_forget(*args):
        out = real(*args)
        if gone in {h.id for h in snap(home).held().sessions()}:
            forget(home.record_root, gone)
        return out

    monkeypatch.setattr(target, name, then_forget)
    atlas(home).backfill(NOW)
    payments = snap(home).pursuit("Payments CI flakes")
    assert [m.evidence for m in payments.sessions] == kept
    assert (payments.kind.value, payments.resume.decided, payments.history[0].statement.text) == (
        "problem", (), "Created from 3 Sessions")
    assert not [path for path, (data, _) in home.files().items() if REMOVED_WORDS.encode() in data]


def test_a_card_is_not_written_for_a_session_removed_meanwhile(home, monkeypatch):
    gone = seeds.evidence_id(world.by_title("shelf bracket sizes"))
    card = Librarian.card

    def remove_then_card(self, s):
        if s.id == gone:
            record = Record.open(home.record_root)
            record.commit(record.snapshot().changes().remove_evidence(gone))
        return card(self, s)

    monkeypatch.setattr(Librarian, "card", remove_then_card)
    atlas(home).backfill(NOW)
    assert not list((home.record_root / "evidence").glob(f"*/*/*/{gone}"))
    assert not any(gone in path for path in machinery(home) if "/.atlas/cache/" in f"/{path}")


def test_removing_a_session_drops_the_model_outputs_read_from_it(home):
    atlas(home).backfill(NOW)
    gone = seeds.evidence_id(world.by_title("retry flake"))
    cache = home.record_root / ".atlas" / "cache"
    tagged = [p for p in cache.glob("*.json") if gone in p.read_text()]
    assert len(tagged) == 4                  # its card, its folder's group, the reconciliation, its Journal lines
    record = Record.open(home.record_root)
    record.commit(record.snapshot().changes().remove_evidence(gone))
    assert not any(gone in p.read_text() for p in cache.glob("*.json"))
    record.cache_put("k" * 64, b"{}", [gone])
    assert record.cache_get("k" * 64) is None


def collected(home) -> Record:
    """The record after a backfill with no model: every Session held, none with a card."""
    Atlas.open(home.record_root).backfill(NOW)
    return Record.open(home.record_root)


def invented_card(id) -> SessionCard:
    return SessionCard(id, Inference("Invented intent.", (id,)), Inference("Invented outcome.", (id,)), None, None)


@pytest.mark.parametrize("removal", ["forget", "fold"])
def test_a_card_committed_after_a_removal_was_planned_goes_with_its_session(home, removal):
    record = collected(home)
    gone = seeds.evidence_id(world.by_title("redis db per worker"))
    s = record.snapshot()
    if removal == "forget":
        planned = s.changes().remove_evidence(gone)
    else:
        parent = s.session(seeds.evidence_id(world.by_title("retry flake")))
        planned = s.changes().put_session(parent, {p.name: s.copy(p.origin) for p in parent.parts},
                                          folds={gone: "t3-child:invented"})
    record.commit(record.snapshot().changes().put_card(invented_card(gone)))
    record.commit(planned)
    assert gone not in {h.id for h in record.snapshot().held().sessions()}
    assert [p for p in home.record_root.rglob("*") if gone in str(p)] == []


@pytest.mark.parametrize("then", ["opened", "removed"])
def test_a_cache_write_a_crash_cut_short_is_deleted(home, monkeypatch, then):
    record = collected(home)
    gone = seeds.evidence_id(world.by_title("redis db per worker"))
    real = os.replace

    def crash_before_rename(src, dst):
        if str(src).endswith(".tmp"):
            raise KeyboardInterrupt
        return real(src, dst)

    monkeypatch.setattr(os, "replace", crash_before_rename)
    with pytest.raises(KeyboardInterrupt):
        record.cache_put("a" * 64, json.dumps({"intent": REMOVED_WORDS}).encode(), [gone])
    monkeypatch.setattr(os, "replace", real)
    if then == "opened":
        Record.open(home.record_root)
    else:
        record.commit(record.snapshot().changes().remove_evidence(gone))
    assert not [path for path, (data, _) in home.files().items() if REMOVED_WORDS.encode() in data]


def test_an_output_cached_after_a_crashed_removal_is_not_kept(home, monkeypatch):
    record = collected(home)
    gone = seeds.evidence_id(world.by_title("redis db per worker"))

    def crash(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(_txn, "apply", crash)
    with pytest.raises(KeyboardInterrupt):
        record.commit(record.snapshot().changes().remove_evidence(gone))
    monkeypatch.undo()
    record.cache_put("b" * 64, json.dumps({"intent": REMOVED_WORDS}).encode(), [gone])
    Record.open(home.record_root)
    assert not [path for path, (data, _) in home.files().items() if REMOVED_WORDS.encode() in data]


def schema_error(call):
    raise ValueError(f"structured output did not match schema: {REMOVED_WORDS!r} is not of type 'string'")


def unusable_card(call):
    return {"intent": {"words": REMOVED_WORDS}, "outcome": "x", "unfinished": None}


@pytest.mark.parametrize("answer", [unusable_card, schema_error], ids=["unusable", "refused"])
def test_a_failed_model_call_logs_no_words_of_the_conversation(home, caplog, answer):
    home.ctx.fake_llm = FakeLlm({"atlas_backfill": answer})
    atlas(home).backfill(NOW)
    assert "stopped writing cards" in caplog.text and REMOVED_WORDS not in caplog.text


def test_sessions_a_grouping_answer_left_out_are_filed_by_the_next_backfill(home, caplog):
    answers = world_model()._script["atlas_backfill"]

    def no_one_offs(call):
        out = answers(call)
        if call["schema"]["title"] == "group":
            out["one_offs"] = []
        return out

    home.ctx.fake_llm = FakeLlm({"atlas_backfill": no_one_offs})
    assert atlas(home).backfill(NOW).filed is None and "gave no grouping" in caplog.text
    home.ctx.fake_llm = world_model()
    filed = atlas(home).backfill(NOW).filed
    assert (filed.pursuits, filed.one_offs) == (11, 3)


def test_a_later_backfill_prints_the_pursuits_as_they_were_built(home):
    first = atlas(home).backfill(NOW).render()
    assert atlas(home).backfill(NOW + timedelta(days=40)).render() == first


def invented(key, started, last, cwd="~/work/invented", asked="start the invented export") -> SessionEvidence:
    id = evidence_id(HarnessId.CODEX, LOCAL, key)
    return SessionEvidence(id, HarnessId.CODEX, None, LOCAL, key, "invented export", started, last, "ubuntu-pc", cwd,
                           None, (RawPart(part_file("main"), "", "~/.codex/invented", "1@0",
                                          part_origin(HarnessId.CODEX, key, "main")),),
                           (Turn("owner", started, asked, "main"),
                            Turn("agent", last, "The invented export is done for now.", "main")))


def holding(tmp_path, zone, *sessions) -> Record:
    record = Record.open(tmp_path / "atlas")
    record.create(replace(seeds.SETTINGS, timezone=zone), seeds.SETUP_PROFILE)
    cs = record.snapshot().changes()
    for s in sessions:
        cs.put_session(s, {part_file("main"): b"invented\n"})
    record.commit(cs)
    return record


def one_session(tmp_path, zone, started, last, cwd="~/work/invented") -> tuple[Record, SessionEvidence]:
    s = invented("invented-thread", started, last, cwd)
    return holding(tmp_path, zone, s), s


def files_it_as_one_pursuit() -> HostLlm:
    def respond(call):
        ids = SESSION.findall(call["input"])
        return {"card": {"intent": "Build the invented export.", "outcome": "Stopped.", "unfinished": None},
                "group": {"pursuits": [{"name": "Invented export", "kind": "project", "area": "work", "sessions": ids,
                                        "left_off": [], "decided": [], "open_questions": [], "reopen": ids,
                                        "next_step": None}], "one_offs": []},
                "reconcile": {"merges": []}}[call["schema"]["title"]]
    return HostLlm(FakePluginContext(fake_llm=FakeLlm({"atlas_backfill": respond})))


@pytest.mark.parametrize("zone, started, last, now", [
    ("Europe/Berlin", "2026-10-06T09:00+02:00", "2026-11-14T20:00+01:00", "2026-11-15T20:10+01:00"),
    ("Asia/Tokyo", "2026-10-15T23:30+09:00", "2026-10-16T00:30+09:00", "2026-11-15T12:00+09:00"),
    ("America/Los_Angeles", "2026-10-15T23:30-07:00", "2026-10-16T00:30-07:00", "2026-11-15T12:00-08:00"),
], ids=["started-40-days-ago", "after-midnight-in-tokyo", "after-midnight-in-los-angeles"])
def test_a_pursuit_starts_active_when_its_last_turn_is_in_the_last_30_days(tmp_path, zone, started, last, now):
    record, _ = one_session(tmp_path, zone, datetime.fromisoformat(started), datetime.fromisoformat(last))
    Atlas(record, files_it_as_one_pursuit()).backfill(datetime.fromisoformat(now))
    (p,) = record.snapshot().pursuits()
    assert (p.status, p.last_worked) == (Status.ACTIVE, datetime.fromisoformat(last).date())


def test_a_folder_with_a_line_break_in_its_name_leaves_backfill_running(tmp_path):
    at = NOW - timedelta(days=2)
    record, s = one_session(tmp_path, "Europe/Berlin", at, at + timedelta(minutes=5), cwd="~/work/a b")
    for _ in range(2):
        Atlas(record, files_it_as_one_pursuit()).backfill(NOW)
    assert record.snapshot().card(s.id).where.text == "~/work/a b."
    assert record.snapshot().pursuits()[0].resume.where[0].text == "ubuntu-pc, ~/work/a b"


def test_a_card_atlas_cannot_read_is_written_again(tmp_path, caplog):
    at = NOW - timedelta(days=2)
    record, s = one_session(tmp_path, "Europe/Berlin", at, at + timedelta(minutes=5))
    card = next((record.root / "evidence").rglob("session.md")).with_name("card.md")
    record.commit(record.snapshot().changes()._write(str(card.relative_to(record.root)), b"not a card\n"))
    Atlas(record, files_it_as_one_pursuit()).backfill(NOW)
    assert record.snapshot().card(s.id).intent.text == "Build the invented export."
    assert len(record.snapshot().pursuits()) == 1 and "could not be read" in caplog.text


def renaming(name: str, to: str) -> FakeLlm:
    answers = world_model()._script["atlas_backfill"]

    def respond(call):
        out = answers(call)
        for row in out.get("pursuits", []):
            if row["name"] == name:
                row["name"] = to
        return out
    return FakeLlm({"atlas_backfill": respond})


@pytest.mark.parametrize("odd", ["\x80\x9f", "\ufffe\uffff", "\ud83d"], ids=["c1", "noncharacters", "lone-surrogate"])
def test_an_odd_character_in_a_name_the_model_wrote_leaves_backfill_and_tick_running(home, odd):
    home.ctx.fake_llm = renaming("Payments CI flakes", f"Payments CI{odd} flakes")
    first = atlas(home).backfill(NOW).render()
    assert atlas(home).backfill(NOW).render() == first
    atlas(home).tick(NOW)
    assert "Payments CI flakes" in {p.name for p in snap(home).pursuits()}


def test_backfill_builds_nothing_over_a_pursuit_file_it_cannot_read(home, caplog):
    atlas(home).backfill(NOW)
    pursuits = sorted((home.record_root / "pursuits").glob("*.md"))
    for path in pursuits:
        (home.record_root / ".atlas/last-written" / path.relative_to(home.record_root)).unlink()
        path.write_text(path.read_text().replace("name: ", "name: \x9f", 1))
    assert atlas(home).backfill(NOW).filed.pursuits == 0
    assert sorted((home.record_root / "pursuits").glob("*.md")) == pursuits
    assert "Pursuit file it could not read" in caplog.text


def answering(**answers) -> HostLlm:
    """A model that answers by the schema's title: an answer, or a function of what it reads."""
    answers = {"card": {"intent": "Build the invented export.", "outcome": "Stopped.", "unfinished": None},
               "reconcile": {"merges": []}, **answers}

    def respond(call):
        answer = answers[call["schema"]["title"]]
        return answer(call["input"]) if callable(answer) else answer
    return HostLlm(FakePluginContext(fake_llm=FakeLlm({"atlas_backfill": respond})))


def pursuit_row(name, ids) -> dict:
    return {"name": name, "kind": "project", "area": "work", "sessions": list(ids), "left_off": [], "decided": [],
            "open_questions": [], "reopen": list(ids)[-1:], "next_step": None}


@pytest.mark.parametrize("field", ["card", "name"])
def test_an_answer_with_a_lone_surrogate_is_written_and_cached_without_it(tmp_path, field):
    at = NOW - timedelta(days=2)
    record, s = one_session(tmp_path, "Europe/Berlin", at, at + timedelta(minutes=5))
    odd = "Invented \ud83dexport"
    card = {"intent": odd if field == "card" else "Invented intent.", "outcome": "Stopped.", "unfinished": None}
    llm = answering(card=card, group=lambda read: {"pursuits": [pursuit_row(odd, SESSION.findall(read))], "one_offs": []})
    for _ in range(2):
        Atlas(record, llm).backfill(NOW)
    (p,) = record.snapshot().pursuits()
    assert p.name == "Invented export"
    assert record.snapshot().card(s.id).intent.text == ("Invented export" if field == "card" else "Invented intent.")
    assert not [c for c in (record.root / ".atlas/cache").glob("*.json") if "\\ud83d" in c.read_text()]


@pytest.mark.parametrize("when", ["merging", "planned"])
def test_a_one_off_forgotten_during_the_build_leaves_no_word_of_it_in_the_merge_cache(tmp_path, monkeypatch, when):
    at = NOW - timedelta(days=2)
    x = invented("invented-x", at, at + timedelta(minutes=5), "~/work/a", asked=f"what is a {REMOVED_WORDS}?")
    y = invented("invented-y", at + timedelta(hours=1), at + timedelta(hours=2), "~/work/a")
    z = invented("invented-z", at + timedelta(hours=3), at + timedelta(hours=4), "~/work/b")
    record = holding(tmp_path, "Europe/Berlin", x, y, z)
    gone = []

    def forget_x():
        if not gone:
            gone.append(x.id)
            forget(record.root, x.id)

    def group(read):
        ids = SESSION.findall(read)
        if x.id in ids:
            return {"pursuits": [pursuit_row(f"Invented {REMOVED_WORDS} work", [y.id])],
                    "one_offs": [{"session": x.id, "area": "work"}]}
        return {"pursuits": [pursuit_row("Invented work", ids)], "one_offs": []}

    def reconcile(read):
        if when == "merging":
            forget_x()
        name = f"Merged {REMOVED_WORDS} work" if REMOVED_WORDS in read else "Merged work"
        return {"merges": [{"drafts": ["d1", "d2"], "name": name}]}

    real = rules.start_state

    def then_forget(*args):
        out = real(*args)
        if when == "planned":
            forget_x()
        return out

    monkeypatch.setattr(rules, "start_state", then_forget)
    Atlas(record, answering(group=group, reconcile=reconcile)).backfill(NOW)
    assert [p.name for p in record.snapshot().pursuits()] == ["Merged work"]
    assert not [p for p in record.root.rglob("*") if p.is_file() and REMOVED_WORDS.encode() in p.read_bytes()]


def test_a_later_backfill_prints_the_same_block_when_every_session_is_a_one_off(home):
    answers = world_model()._script["atlas_backfill"]

    def one_offs_only(call):
        if call["schema"]["title"] == "group":
            return {"pursuits": [], "one_offs": [{"session": id, "area": "work"} for id in SESSION.findall(call["input"])]}
        return answers(call)

    home.ctx.fake_llm = FakeLlm({"atlas_backfill": one_offs_only})
    first = atlas(home).backfill(NOW).render()
    assert "touched since 15 Oct" in first
    assert atlas(home).backfill(NOW + timedelta(days=40)).render() == first


def test_pursuits_built_after_a_build_of_one_offs_only_count_touched_since_from_their_own_day(tmp_path):
    at = NOW - timedelta(days=2)
    record = holding(tmp_path, "Europe/Berlin", invented("invented-first", at, at + timedelta(minutes=5)))
    one_offs = answering(group=lambda read: {"pursuits": [], "one_offs": [{"session": id, "area": "work"}
                                                                          for id in SESSION.findall(read)]})
    assert "touched since 15 Oct" in Atlas(record, one_offs).backfill(NOW).render()
    later = NOW + timedelta(days=40)
    cs = record.snapshot().changes()
    cs.put_session(invented("invented-later", later - timedelta(hours=2), later - timedelta(hours=1)),
                   {part_file("main"): b"invented\n"})
    record.commit(cs)
    report = Atlas(record, files_it_as_one_pursuit()).backfill(later).render()
    assert "  active         1   touched since 24 Nov" in report
    assert Atlas(record, files_it_as_one_pursuit()).backfill(later + timedelta(days=9)).render() == report


def test_a_pursuit_is_never_written_over_a_file_atlas_cannot_read_under_any_name(tmp_path, caplog):
    at = NOW - timedelta(days=2)
    record, _ = one_session(tmp_path, "Europe/Berlin", at, at + timedelta(minutes=5))
    Atlas(record, files_it_as_one_pursuit()).backfill(NOW)
    (p,) = record.snapshot().pursuits()
    (path,) = (record.root / "pursuits").glob("*.md")
    unreadable = [path, path.with_name(f"{path.stem}-{p.id}.md")]
    (record.root / ".atlas/last-written" / path.relative_to(record.root)).unlink()
    for file in unreadable:
        file.write_bytes(b"invented unreadable Pursuit\n")
    record.commit(record.snapshot().changes().put_pursuit(p))
    assert [file.read_bytes() for file in unreadable] == [b"invented unreadable Pursuit\n"] * 2
    assert [q.name for q in record.snapshot().pursuits()] == [p.name]
    assert caplog.text.count("Pursuit file it could not read") >= 2


def test_long_names_the_model_wrote_name_files_any_filesystem_takes(tmp_path):
    at = NOW - timedelta(days=2)
    a = invented("invented-a", at, at + timedelta(minutes=5), "~/work/a")
    b = invented("invented-b", at + timedelta(hours=1), at + timedelta(hours=2), "~/work/b")
    record = holding(tmp_path, "Europe/Berlin", a, b)
    longest = FIELD_MAX["name"]
    names = {a.id: ("Invented " * 9)[:longest - 1] + "a", b.id: ("Invented " * 9)[:longest - 1] + "b"}
    llm = answering(group=lambda read: {"pursuits": [pursuit_row(names[id], [id]) for id in SESSION.findall(read)],
                                        "one_offs": []})
    Atlas(record, llm).backfill(NOW)
    assert sorted(p.name for p in record.snapshot().pursuits()) == sorted(names.values())
    assert all(len(path.name) < 80 for path in (record.root / "pursuits").glob("*.md"))


def test_pursuits_backfill_cannot_write_are_logged_and_asked_for_again(tmp_path, monkeypatch, caplog):
    at = NOW - timedelta(days=2)
    record, _ = one_session(tmp_path, "Europe/Berlin", at, at + timedelta(minutes=5))
    llm = answering(group=lambda read: {"pursuits": [pursuit_row("Invented export", SESSION.findall(read))],
                                        "one_offs": []})
    real = Record.commit

    def refused(rec, cs):
        if any(op.path.startswith("pursuits/") for op in cs._ops):
            raise OSError(36, "File name too long", "invented export")
        return real(rec, cs)

    monkeypatch.setattr(Record, "commit", refused)
    assert Atlas(record, llm).backfill(NOW).filed is None
    assert "could not write them (OSError)" in caplog.text and "invented export" not in caplog.text.lower()
    monkeypatch.setattr(Record, "commit", real)
    groups = lambda: sum(call["schema"]["title"] == "group" for call in llm._ctx.fake_llm.calls)
    asked = groups()
    assert Atlas(record, llm).backfill(NOW).filed.pursuits == 1
    assert groups() == asked + 1
