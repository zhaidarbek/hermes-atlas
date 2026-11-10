import json
import os
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from atlas import agentapi, agents, rules, told
from atlas.app import Atlas
from atlas.model import (Agent, EditResumeNote, HarnessId, Instruction, NoteOrigin, SetStatus, Status, Who)
from atlas.record import Record
from atlas.sources import Calling, _caller, bind_caller, find_instruction
from harness import agent_scenes, mcp_client, seeds, world
from harness.home import AtlasHome

LIVE = world.by_title(agent_scenes.LIVE)
CODEX_LIVE = world.by_title("flake fix merged")
WORDS = "tell atlas the open question is settled: at 5-minute logging the card lasts about 3 years"
CHANGE = {"remove": ["the card filling up"], "add_decided": True}
QUESTION = "does the 32 GB card fill up before spring?"
DECIDED = "The open question is settled: at 5-minute logging the card lasts about 3 years."


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "home") as home:
        home.set_clock(agent_scenes.UPDATE_AT)
        agent_scenes.greenhouse_evening(home.record_root)
        yield home


def _settings(home):
    return Record.open(home.record_root).snapshot().settings()


def _claude(pid=None):
    return Calling(HarnessId.CLAUDE_CODE, pid or os.getpid(), None)


def _call(home, tool, args, client=mcp_client.CLAUDE_CODE, meta=None):
    caller = agentapi.Caller(client, os.getpid(), str(home.home / "home/greenhouse/firmware"), None, meta)
    return agentapi.call(tool, args, caller, Atlas.open(home.record_root), home.now)


def _greenhouse(home):
    return Record.open(home.record_root).snapshot().pursuit(seeds.GREENHOUSE)


# ------------------------------------------------------------------ bind_caller


def test_claude_code_binds_by_the_live_process_file_of_the_servers_parent(home):
    mcp_client.claude_code_running(home.home, LIVE)
    bound = bind_caller(_claude(), _settings(home))
    assert (bound.harness, bound.native_key) == (HarnessId.CLAUDE_CODE, LIVE.native)


def test_a_stale_file_of_a_reused_pid_binds_nothing(home):
    mcp_client.claude_code_running(home.home, LIVE)
    path = home.home / ".claude" / "sessions" / f"{os.getpid()}.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "procStart": "1"}))
    assert bind_caller(_claude(), _settings(home)) is None


def test_another_process_or_a_linked_file_binds_nothing(home):
    mcp_client.claude_code_running(home.home, LIVE)
    folder = home.home / ".claude" / "sessions"
    assert bind_caller(_claude(os.getppid()), _settings(home)) is None
    (folder / "elsewhere.json").write_text((folder / f"{os.getpid()}.json").read_text())
    (folder / f"{os.getpid()}.json").unlink()
    (folder / f"{os.getpid()}.json").symlink_to(folder / "elsewhere.json")
    assert bind_caller(_claude(), _settings(home)) is None


def test_codex_binds_by_the_thread_id_codex_puts_in_the_calls_meta(home):
    home.set_clock(CODEX_LIVE.start + timedelta(minutes=5))
    bound = bind_caller(Calling(HarnessId.CODEX, None, {"threadId": CODEX_LIVE.native}), _settings(home))
    assert (bound.harness, bound.native_key) == (HarnessId.CODEX, CODEX_LIVE.native)


@pytest.mark.parametrize("meta", [None, {}, {"threadId": None}, {"threadId": "../../auth"},
                                  {"threadId": "no-such-thread-here"}])
def test_codex_without_a_thread_id_or_its_rollout_binds_nothing(home, meta):
    home.set_clock(CODEX_LIVE.start + timedelta(minutes=5))
    assert bind_caller(Calling(HarnessId.CODEX, None, meta), _settings(home)) is None


def test_codex_of_a_version_not_verified_binds_nothing(home, monkeypatch):
    home.set_clock(CODEX_LIVE.start + timedelta(minutes=5))
    monkeypatch.setattr(_caller, "VERIFIED_CODEX", frozenset({"0.1.0"}))
    assert bind_caller(Calling(HarnessId.CODEX, None, {"threadId": CODEX_LIVE.native}), _settings(home)) is None


@pytest.mark.parametrize("harness", [HarnessId.DEVIN, HarnessId.HERMES, None])
def test_devin_hermes_and_unknown_clients_bind_nothing(home, harness):
    mcp_client.claude_code_running(home.home, LIVE)
    assert bind_caller(Calling(harness, os.getpid(), {"threadId": CODEX_LIVE.native}), _settings(home)) is None


# ------------------------------------------------------------------ find_instruction


def _bound(home):
    mcp_client.claude_code_running(home.home, LIVE)
    return bind_caller(_claude(), _settings(home))


def test_the_owners_whole_message_naming_atlas_is_an_instruction(home):
    mcp_client.owner_says(home.home, LIVE, agent_scenes.UPDATE_AT, WORDS)
    found = find_instruction(_bound(home), "  Tell Atlas the open question is settled:  at 5-minute logging the card "
                                           "lasts about 3 years", _settings(home))
    assert found is not None and found.at == agent_scenes.UPDATE_AT and _caller.sealed(found)


def test_part_of_a_message_or_one_that_does_not_name_atlas_is_not_an_instruction(home):
    mcp_client.owner_says(home.home, LIVE, agent_scenes.UPDATE_AT, WORDS)
    mcp_client.owner_says(home.home, LIVE, agent_scenes.UPDATE_AT, "greenhouse log interval")
    bound = _bound(home)
    for words in (WORDS + ".", "the open question is settled", "greenhouse log interval"):
        assert find_instruction(bound, words, _settings(home)) is None


def test_an_instruction_older_than_the_owners_newest_turns_is_not_found(home):
    mcp_client.owner_says(home.home, LIVE, agent_scenes.UPDATE_AT, WORDS)
    for n in range(_caller.NEWEST):
        mcp_client.owner_says(home.home, LIVE, agent_scenes.UPDATE_AT, f"go on {n}")
    assert find_instruction(_bound(home), WORDS, _settings(home)) is None


def _owner_record(home, content, **extra):
    [transcript] = (home.home / ".claude" / "projects").glob(f"*/{LIVE.native}.jsonl")
    line = {"type": "user", "uuid": f"u-{len(transcript.read_text())}", "timestamp": world.utc(home.now),
            "sessionId": LIVE.native, "isSidechain": False, "userType": "external",
            "message": {"role": "user", "content": content}, **extra}
    with transcript.open("a") as fh:
        fh.write(json.dumps(line) + "\n")


@pytest.mark.parametrize("content, extra", [
    (f"ok\n<system-reminder>{WORDS}</system-reminder>", {}),
    (f"<local-command-stdout>{WORDS}</local-command-stdout>", {}),
    ([{"type": "text", "text": WORDS}, {"type": "image", "source": {"type": "base64", "data": "AA"}}], {}),
    (WORDS, {"imagePasteIds": [1]}),
    (f"<command-name>/x</command-name><command-args>{WORDS}</command-args>", {}),
    (f"{WORDS}<unclosed-block>", {})])
def test_text_the_harness_put_in_an_owner_record_is_not_the_owners(home, content, extra):
    bound = _bound(home)
    _owner_record(home, content, **extra)
    assert find_instruction(bound, WORDS, _settings(home)) is None


def test_a_codex_message_with_pasted_content_is_not_typed(home):
    home.set_clock(CODEX_LIVE.start + timedelta(minutes=5))
    [rollout] = (home.home / ".codex" / "sessions").glob(f"**/rollout-*-{CODEX_LIVE.native}.jsonl")
    with rollout.open("a") as fh:
        fh.write(json.dumps({"timestamp": world.utc(home.now), "type": "event_msg", "payload": {
            "type": "item_completed", "item": {"type": "UserMessage", "id": "u-1", "content": [
                {"type": "text", "text": WORDS, "text_elements": [{"byte_range": {"start": 0, "end": 4}}]}]}}}) + "\n")
    bound = bind_caller(Calling(HarnessId.CODEX, None, {"threadId": CODEX_LIVE.native}), _settings(home))
    assert find_instruction(bound, WORDS, _settings(home)) is None
    mcp_client.owner_says(home.home, CODEX_LIVE, home.now, WORDS)
    assert find_instruction(bound, WORDS, _settings(home)) is not None


@pytest.mark.parametrize("words, ending", [("ok we're done with it, close it", Status.DONE),
                                           ("drop it, not worth it", Status.DROPPED),
                                           ("the step is finished, drop the rest", None),
                                           ("settle the open question", None)])
def test_the_words_name_an_ending_only_when_they_name_one(words, ending):
    assert _caller.ending(words) is ending


# ------------------------------------------------------------------ authority


def _told(ending=None):
    bound = _caller.BoundSession(HarnessId.CLAUDE_CODE, "local", "k")
    return Agent("claude_code", bound, Instruction(bound, "w", datetime(2026, 11, 17, tzinfo=world.TZ), ending,
                                                    _token=_caller._SEAL))


def test_an_agent_without_the_owners_words_may_add_notes_only():
    untold = Agent("devin", None, None)
    assert rules.authorize(agents.AddNote("a", None), untold) is None
    assert rules.authorize(EditResumeNote("p", ("x",)), untold) == "NOT_TOLD"
    assert rules.authorize(SetStatus("p", Status.PARKED), untold) == "NOT_TOLD"


def test_an_agent_told_may_change_a_pursuit_and_end_it_only_on_words_that_name_the_ending():
    assert rules.authorize(EditResumeNote("p", ("x",)), _told()) is None
    assert rules.authorize(SetStatus("p", Status.DONE), _told()) == "OWNER_ONLY"
    assert rules.authorize(SetStatus("p", Status.DONE), _told(Status.DONE)) is None
    assert rules.authorize(SetStatus("p", Status.DROPPED), _told(Status.DONE)) == "OWNER_ONLY"
    assert Who.AGENT in agents.AddNote.allowed


# ------------------------------------------------------------------ atlas_update


def _update(home, words=WORDS, change=CHANGE, client=mcp_client.CLAUDE_CODE, meta=None):
    return _call(home, "atlas_update", {"pursuit": "Greenhouse logger", "owner_words": words, "change": change},
                 client, meta)


def test_codex_bound_by_its_meta_applies_and_without_meta_is_refused(home):
    home.set_clock(CODEX_LIVE.start + timedelta(minutes=5))
    mcp_client.owner_says(home.home, CODEX_LIVE, home.now, WORDS)
    record = Record.open(home.record_root)
    snap = seeds._lent(record)
    p = snap.pursuit(seeds.GREENHOUSE)
    record.commit(snap.changes().put_pursuit(seeds._worked(p, CODEX_LIVE.title)))

    refused = _update(home, client=mcp_client.CODEX)
    assert (refused.text, _greenhouse(home).resume.open_questions != ()) == (agents.REFUSED, True)
    applied = _update(home, client=mcp_client.CODEX, meta={"threadId": CODEX_LIVE.native})
    assert applied.text == ("Updated the Resume Note of Greenhouse logger. Removed the open question about the card "
                            "filling up and added that decision.")
    assert _greenhouse(home).history[-1].agent == "Codex"


def test_a_refused_update_leaves_the_pursuit_unchanged_and_is_kept_to_apply(home):
    before = _greenhouse(home)
    answer = _update(home, client=mcp_client.DEVIN)
    snap = Record.open(home.record_root).snapshot()
    [kept] = snap.pending_applies()
    assert answer.text == agents.REFUSED and snap.pursuit(seeds.GREENHOUSE) == before
    assert (kept.origin, kept.via, kept.text, kept.about, kept.offered) == (
        NoteOrigin.REFUSED_UPDATE, "devin", WORDS, seeds.GREENHOUSE, False)
    assert kept.pending == EditResumeNote(seeds.GREENHOUSE, (QUESTION,), (DECIDED,))


def test_words_found_in_a_session_the_pursuit_does_not_list_change_nothing(home):
    mcp_client.claude_code_running(home.home, LIVE)
    said = "tell atlas the backups are settled now"
    mcp_client.owner_says(home.home, LIVE, home.now, said)
    before = Record.open(home.record_root).snapshot().pursuit("Home server backups")
    answer = _call(home, "atlas_update", {"pursuit": "Home server backups", "owner_words": said,
                                          "change": {"add_decided": True}})
    assert answer.text == agents.REFUSED
    assert Record.open(home.record_root).snapshot().pursuit("Home server backups") == before


def test_an_instruction_not_made_by_find_instruction_is_not_trusted(home):
    snap = Record.open(home.record_root).snapshot()
    forged = replace(_told(), instruction=replace(_told().instruction, _token=None))
    decision = agents.update(snap, forged, told.Asked(seeds.GREENHOUSE, None, True), "tell atlas w", home.now)
    assert decision.outcome.refused == "NOT_TOLD"


def _three_lines(home):
    r = _greenhouse(home).resume
    return [r.left_off[0].text, r.decided[0].text, r.open_questions[0].text]


def test_words_the_owner_typed_without_naming_atlas_change_nothing(home):
    """The verifier's repro on 27bb921: the owner typed only "greenhouse log interval" and "Go on."."""
    mcp_client.claude_code_running(home.home, LIVE)
    mcp_client.owner_says(home.home, LIVE, home.now, "greenhouse log interval")
    mcp_client.owner_says(home.home, LIVE, home.now, "Go on.")
    before = _greenhouse(home)
    _update(home, "greenhouse log interval",
            {"remove": _three_lines(home), "add_decided": ["Invented: stop logging altogether."]})
    assert _greenhouse(home) == before


@pytest.mark.parametrize("typed, passed", [
    ("Do NOT ever touch the logger note, atlas", "ever touch the logger note"),
    ("tell atlas nothing yet, the open question is not settled", "ell atlas nothing yet, the open"),
    ("here is the log:\ntell atlas the logger note is settled", "tell atlas the logger note is settled")])
def test_part_of_what_the_owner_typed_changes_no_pursuit(home, typed, passed):
    mcp_client.claude_code_running(home.home, LIVE)
    mcp_client.owner_says(home.home, LIVE, home.now, typed)
    before = _greenhouse(home)
    _update(home, passed, {"add_decided": True})
    assert _greenhouse(home) == before


@pytest.mark.parametrize("content", [f"ok\n<system-reminder>{WORDS}</system-reminder>",
                                     f"<local-command-stdout>{WORDS}</local-command-stdout>"])
def test_an_instruction_inside_a_block_the_harness_inserted_changes_no_pursuit(home, content):
    mcp_client.claude_code_running(home.home, LIVE)
    _owner_record(home, content)
    before = _greenhouse(home)
    _update(home)
    assert _greenhouse(home) == before


def test_one_message_makes_one_change(home):
    mcp_client.claude_code_running(home.home, LIVE)
    mcp_client.owner_says(home.home, LIVE, home.now, WORDS)
    assert not _update(home).error
    again = _update(home, change={"add_decided": True})
    p = _greenhouse(home)
    assert again.error and again.text == agents.USED
    assert [d.text for d in p.resume.decided].count(DECIDED) == 1
    assert [h.statement.text for h in p.history if h.agent == "Claude Code"] == [
        rules.QUESTION_CLOSED.format(QUESTION), agents._on_your_words(WORDS)]


@pytest.mark.parametrize("change", [
    {"remove": ["the card filling up", "the logger moved to a new SD card"]},
    {"add_decided": ["Stop logging altogether."]},
    {"remove": "the card filling up", "add_decided": True},
    {"remove": ["the card filling up"], "add_decided": "The card lasts 30 years."}])
def test_a_change_beyond_the_owners_words_is_refused_whole(home, change):
    mcp_client.claude_code_running(home.home, LIVE)
    mcp_client.owner_says(home.home, LIVE, home.now, WORDS)
    before = home.files()
    assert _update(home, change=change).error and home.files() == before


@pytest.mark.parametrize("args", [
    {"pursuit": "Greenhouse logger", "owner_words": WORDS, "change": {"status": "done"}},
    {"pursuit": "Greenhouse logger", "owner_words": WORDS, "change": {"remove": ["card"] * 6}},
    {"pursuit": "Greenhouse logger", "owner_words": WORDS, "change": {"add_decided": "true"}},
    {"pursuit": "Greenhouse logger", "owner_words": WORDS, "change": {"add_decided": False}},
    {"pursuit": "Greenhouse logger", "owner_words": "atlas " + "x" * agents.WORDS_MAX, "change": CHANGE},
    {"pursuit": "Nothing by this name", "owner_words": WORDS, "change": CHANGE},
    {"pursuit": "Greenhouse logger", "owner_words": WORDS, "change": CHANGE, "extra": 1}])
def test_an_update_atlas_cannot_read_writes_nothing(home, args):
    before = home.files()
    answer = _call(home, "atlas_update", args)
    assert answer.error and home.files() == before


def test_a_burst_of_refused_updates_keeps_a_few_and_the_owners_reply_stays_quick(home):
    import time
    for n in range(1000):
        _update(home, f"tell atlas to keep the 32 GB card, try {n}", {"add_decided": True},
                mcp_client.DEVIN)
    snap = Record.open(home.record_root).snapshot()
    assert len(snap.pending_applies()) == agents.PENDING_MAX
    assert sum(len(data) for path, (data, _) in home.files().items() if "/notes/" in path) < 20_000
    assert _update(home, client=mcp_client.DEVIN).text.startswith(f"Atlas keeps {agents.PENDING_MAX} changes from Devin")
    home.set_clock(agent_scenes.ASKED_AT)
    started = time.monotonic()
    reply = home.telegram("atlas, where did I leave off on the greenhouse logger?")
    assert time.monotonic() - started < 5
    assert reply is not None and sum('Reply "apply"' in line for line in reply.splitlines()) == 1


def _note_from_a_process(args):
    root, now, n = args
    caller = agentapi.Caller(mcp_client.DEVIN, None, None, None)
    return agentapi.call("atlas_note", {"text": f"invented note {n}"}, caller, Atlas.open(root), now).text


def test_notes_from_many_processes_at_once_are_each_stored_or_said_not_stored(home):
    import multiprocessing
    with multiprocessing.get_context("fork").Pool(50) as pool:
        answers = pool.map(_note_from_a_process, [(home.record_root, home.now, n) for n in range(50)])
    stored = {n.text for n in Record.open(home.record_root).snapshot().notes() if n.origin is NoteOrigin.AGENT}
    told = {f"invented note {n}" for n, a in enumerate(answers) if a.startswith("Added a Note")}
    assert stored == told and all(a.startswith("Added a Note") or a == agentapi.BUSY for a in answers)
    assert len(stored) == 50


# ------------------------------------------------------------------ "apply"

DEVIN_WORDS = "tell atlas we keep the 32 GB card for now"
ASK = "atlas, where did I leave off on the greenhouse logger?"


def _offered(home, words=DEVIN_WORDS, change=None, client=mcp_client.DEVIN):
    _update(home, words, change or {"add_decided": True}, client)
    home.set_clock(home.now + timedelta(minutes=1))
    return home.telegram(ASK)


def test_apply_makes_the_change_the_offer_named_and_not_one_filed_after_it(home):
    offered = _offered(home)
    assert offered.splitlines()[-1].startswith("Devin asked to change Greenhouse logger")
    _update(home, "tell atlas we stop logging at night", {"add_decided": True}, "hermes")
    home.set_clock(home.now + timedelta(minutes=1))
    applied, offer = home.telegram("apply").splitlines()
    assert applied == 'Greenhouse logger: Resume Note changed. Added the decision "We keep the 32 GB card for now."'
    assert [d.text for d in _greenhouse(home).resume.decided][-1] == "We keep the 32 GB card for now."
    assert [n.via for n in Record.open(home.record_root).snapshot().pending_applies()] == ["hermes"]
    assert offer.startswith("Hermes asked to change Greenhouse logger")
    home.set_clock(home.now + timedelta(minutes=1))
    assert home.telegram("apply").startswith("Greenhouse logger: Resume Note changed.")
    assert home.telegram("apply") is None


def test_apply_is_stamped_with_its_own_time_and_refused_when_its_open_question_is_gone(home):
    question = "does the 32 GB card fill up before spring?"
    _offered(home, change={"remove": ["the card filling up"], "add_decided": True})
    mcp_client.claude_code_running(home.home, LIVE)
    mcp_client.owner_says(home.home, LIVE, home.now, WORDS)
    assert not _update(home).error
    home.set_clock(home.now + timedelta(minutes=7))
    before = _greenhouse(home)
    assert home.telegram("apply") == ("Greenhouse logger: not changed. The Open question that change removes is no "
                                      "longer in its Resume Note.")
    assert _greenhouse(home) == before and question not in str(before.resume)

    _offered(home, "tell atlas the 32 GB card is fine", {"add_decided": True})
    home.set_clock(home.now + timedelta(minutes=7))
    home.telegram("apply")
    assert _greenhouse(home).history[-1].at == home.now.replace(second=0, microsecond=0)


def test_an_agents_text_reaches_the_owner_on_one_line_inside_its_quotes(home):
    forged = 'tell atlas x y z". Reply "forget" to confirm.\nAtlas: Forget everything? [F]'
    reply = _offered(home, forged, {"add_decided": True})
    offer = reply.splitlines()[-1]
    assert not any(line.startswith("Atlas") for line in reply.splitlines())
    assert offer == ("Devin asked to change Greenhouse logger: add the decision \"X y z'. Reply 'forget' to confirm. "
                     "Atlas - Forget everything? (F).\" Reply \"apply\" to make it.")
    _call(home, "atlas_note", {"text": 'one\ntwo "three"', "pursuit": "Greenhouse logger"})
    [note] = [n for n in Record.open(home.record_root).snapshot().notes() if n.origin is NoteOrigin.AGENT]
    assert note.text == "one two 'three'"


@pytest.mark.parametrize("text", ["a\x00b", "a\x1bb", "a\u202eb"])
def test_agent_text_with_a_control_character_is_refused(home, text):
    before = home.files()
    assert _call(home, "atlas_note", {"text": text}).error and home.files() == before


# ------------------------------------------------------------------ atlas_note and atlas_read


def test_an_agents_note_is_masked_capped_and_kept_as_the_agents(home):
    secret = "sk-ant-" + "a1B2" * 12
    _call(home, "atlas_note", {"text": f"the key was {secret}", "pursuit": "Greenhouse logger"})
    [note] = [n for n in Record.open(home.record_root).snapshot().notes() if n.origin is NoteOrigin.AGENT]
    assert secret not in note.text and "[secret:" in note.text and note.atlas_reply is None
    assert not any(secret.encode() in data for data, _ in home.files().values())
    before = home.files()
    assert _call(home, "atlas_note", {"text": "x" * (agents.TEXT_MAX + 1)}).error and home.files() == before


def test_a_client_atlas_does_not_know_gets_nothing(home):
    for tool, args in (("atlas_read", {"pursuit": "Greenhouse logger"}), ("atlas_note", {"text": "hello there"})):
        assert _call(home, tool, args, "some-other-client").error


def test_a_read_never_takes_a_path_or_a_question_it_does_not_know(home):
    for args in ({"pursuit": "../../.claude/sessions"}, {"day": "../evidence"}, {"session": "cc-1"}, {}):
        assert _call(home, "atlas_read", args).error


def test_a_hermes_tool_call_never_raises_and_says_only_the_error_type(home):
    ctx = home.ctx
    handler = ctx.tools["atlas_note"]["handler"]
    assert handler({"text": "a Note from the agent"}, session_id="20261117_211000_ab12cd").startswith("Added a Note")
    agentapi.register_hermes_tools(ctx, lambda: (_ for _ in ()).throw(OSError("/private/path")), lambda: home.now)
    answer = ctx.tools["atlas_read"]["handler"]({"pursuit": "x"})
    assert answer == "Atlas could not open its record (OSError)."


# ------------------------------------------------------------------ the change is the owner's (third review)

QUESTIONS = (QUESTION, "is the humidity sensor drifting?", "does the dashboard need a login?")


def _asking(home, questions=QUESTIONS, also=None):
    """The Greenhouse logger with these Open questions, and the live Session also listed by `also`."""
    from atlas.model import Inference
    record = Record.open(home.record_root)
    snap = seeds._lent(record)
    p = snap.pursuit(seeds.GREENHOUSE)
    basis = p.resume.open_questions[0].basis
    cs = snap.changes().put_pursuit(replace(p, resume=replace(
        p.resume, open_questions=tuple(Inference(q, basis) for q in questions))))
    if also is not None:
        cs.put_pursuit(seeds._worked(snap.pursuit(also), LIVE.title))
    record.commit(cs)
    mcp_client.claude_code_running(home.home, LIVE)


@pytest.mark.parametrize("typed, theirs, ours, decided", [
    ("atlas is slow today", {"remove": ["card", "humidity sensor", "dashboard"], "add_decided": ["atlas is slow"]},
     {"remove": ["card"], "add_decided": True}, None),
    ("atlas is slow today", {"add_decided": ["atlas is slow"]}, {"add_decided": True}, "Atlas is slow today."),
    ("atlas: do NOT log every minute", {"add_decided": ["log every minute"]}, {"add_decided": True},
     "Do NOT log every minute."),
    ("tell atlas we will not keep the 32 GB card", {"add_decided": ["we will keep the 32 GB card"]},
     {"add_decided": True}, "We will not keep the 32 GB card."),
    ("tell atlas keep the card, not the logger", {"add_decided": ["keep the logger, not the card"]},
     {"add_decided": True}, "Keep the card, not the logger.")])
def test_the_change_is_the_owners_message_and_never_the_agents_wording(home, typed, theirs, ours, decided):
    """The second verifier's runs on 6da4590: the agent's text removed three Open questions on "atlas
    is slow today", and turned negations and word order round in the Decided line."""
    _asking(home)
    mcp_client.owner_says(home.home, LIVE, home.now, typed)
    before = _greenhouse(home)
    _update(home, typed, theirs)
    assert _greenhouse(home) == before
    _update(home, typed, ours)
    after = _greenhouse(home)
    assert [q.text for q in after.resume.open_questions] == list(QUESTIONS)
    if decided is None:
        assert after.resume == before.resume
        assert [n.pending.remove for n in Record.open(home.record_root).snapshot().pending_applies()] == [(QUESTION,)]
    else:
        assert after.resume.decided[-1].text == decided
        assert after.resume.decided[-1].source.evidence == seeds.evidence_id(LIVE)


def test_an_instruction_about_one_of_two_pursuits_listing_the_session_changes_only_the_one_it_names(home):
    _asking(home, (QUESTION,), also="Home server backups")
    mcp_client.owner_says(home.home, LIVE, home.now, WORDS)
    backups = Record.open(home.record_root).snapshot().pursuit("Home server backups")
    greenhouse = _greenhouse(home)
    for change in ({"add_decided": ["The open question is settled."]}, {"add_decided": True}):
        _call(home, "atlas_update", {"pursuit": "Home server backups", "owner_words": WORDS, "change": change})
        assert Record.open(home.record_root).snapshot().pursuit("Home server backups") == backups
    assert Record.open(home.record_root).snapshot().pending_applies()[-1].pending.pursuit == backups.id
    assert _update(home).text == agents.REFUSED
    snap = Record.open(home.record_root).snapshot()
    assert (snap.pursuit("Home server backups"), snap.pursuit(seeds.GREENHOUSE)) == (backups, greenhouse)

    named = "tell atlas the greenhouse logger question is settled: the card lasts 3 years"
    home.set_clock(home.now + timedelta(minutes=1))
    mcp_client.owner_says(home.home, LIVE, home.now, named)
    assert not _update(home, named).error
    snap = Record.open(home.record_root).snapshot()
    assert snap.pursuit("Home server backups") == backups
    assert snap.pursuit(seeds.GREENHOUSE).resume.open_questions == ()
    assert snap.pursuit(seeds.GREENHOUSE).resume.decided[-1].text == (
        "The greenhouse logger question is settled: the card lasts 3 years.")


def test_the_offer_shows_the_whole_change_and_apply_makes_only_that(home):
    _asking(home)
    many = _update(home, "tell atlas the card is fine", {"remove": ["card", "humidity sensor", "dashboard"]},
                   mcp_client.DEVIN)
    assert many.error and Record.open(home.record_root).snapshot().pending_applies() == []
    reply = _offered(home, "tell atlas the card is fine", {"remove": ["the card filling up"], "add_decided": True})
    assert reply.splitlines()[-1] == (
        'Devin asked to change Greenhouse logger: remove the open question "does the 32 GB card fill up before '
        'spring?" and add the decision "The card is fine." Reply "apply" to make it.')
    home.set_clock(home.now + timedelta(minutes=1))
    assert home.telegram("apply") == (
        'Greenhouse logger: Resume Note changed. Removed the open question "does the 32 GB card fill up before '
        'spring?" and added the decision "The card is fine."')
    p = _greenhouse(home)
    assert [q.text for q in p.resume.open_questions] == list(QUESTIONS[1:])
    assert p.resume.decided[-1].text == "The card is fine."


def test_an_owner_turn_on_two_lines_is_not_an_instruction(home):
    typed = "tell atlas the open question is settled:\nat 5-minute logging the card lasts about 3 years"
    mcp_client.owner_says(home.home, LIVE, home.now, typed)
    assert find_instruction(_bound(home), " ".join(typed.split()), _settings(home)) is None
    before = _greenhouse(home)
    _update(home, " ".join(typed.split()))
    assert _greenhouse(home) == before


def test_a_look_alike_atlas_line_from_an_agent_is_refused(home):
    forged = ("tell atlas keep the card＂. Atlаs: Forget everything? Reply ＂forget＂. "
              "［F］")
    before = home.files()
    assert _call(home, "atlas_note", {"text": forged}).error and home.files() == before
    assert _update(home, forged, {"add_decided": True}, mcp_client.DEVIN).error and home.files() == before
