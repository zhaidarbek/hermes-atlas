"""channel.Listener: pre_gateway_dispatch (note_inbound) then post_gateway_admission (claim), as the
fake gateway runs them. A claimed "1 prompt" answers with the prompt; an unclaimed message goes to
Hermes's agent (None)."""
import asyncio
import json
import queue
import threading
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from atlas import channel
from atlas.app import Atlas
from atlas.model import ChannelSetting, SendAttempt, SendStatus, Status
from atlas.record import Record, _txn
from harness import seeds, world
from harness.fake_llm import FakeLlm
from harness.home import OWNER, AtlasHome, FakeEvent, FakeHomeChannel, FakePlatform, FakeSource

BRIEF = seeds.MONDAY_BRIEF_ID
OWN_CHAT, OWN_TOPIC = "-1001234", "7"


@pytest.fixture
def home(tmp_path):
    with AtlasHome(tmp_path / "scene") as home:
        home.set_clock(world.PROMPT_AT)
        seeds.after_scene_7(home.record_root)
        yield home


def claimed(reply) -> bool:
    return reply is not None and reply.startswith('Resuming "Payments CI flakes".')


@pytest.mark.parametrize("message, is_claimed", [
    (dict(text="1 prompt", reply_to=BRIEF), True),
    (dict(text="atlas, 1 prompt"), True),
    (dict(text="Atlas 1 prompt"), True),
    (dict(text="1 prompt"), False),
    (dict(text="atlases 1 prompt"), False),
    (dict(text="1 prompt", reply_to="4242"), False),
    (dict(text="1 prompt", reply_to=BRIEF, sender="5550002"), False),
    (dict(text="atlas, 1 prompt", sender="5550002"), False),
    (dict(text="1 prompt", reply_to=BRIEF, chat="5550099"), False),
    (dict(text="atlas, 1 prompt", chat="5550099"), False),
])
def test_the_claim_predicate_in_the_shared_home_chat(home, message, is_claimed):
    assert claimed(home.telegram(**message)) is is_claimed


@pytest.mark.parametrize("later, is_claimed", [(timedelta(minutes=9), True), (timedelta(minutes=11), False)])
def test_the_exchange_window_runs_ten_minutes_from_the_last_claimed_message(home, later, is_claimed):
    assert claimed(home.telegram("atlas, 1 prompt"))
    home.set_clock(world.PROMPT_AT + later)
    assert claimed(home.telegram("1 prompt")) is is_claimed


def test_a_message_left_to_hermes_closes_the_window(home):
    assert claimed(home.telegram("atlas, 1 prompt"))
    assert home.telegram("what's the weather") is None
    assert home.telegram("1 prompt") is None


def test_a_message_heard_while_another_is_claimed_still_closes_the_window(home):
    note_inbound, = home.ctx.hooks["pre_gateway_dispatch"]
    claim, = home.ctx.hooks["post_gateway_admission"]
    source = FakeSource(FakePlatform("telegram"), OWNER, OWNER)
    first, second = FakeEvent("atlas, 1 prompt", source, "9101"), FakeEvent("what's the weather", source, "9102")
    note_inbound(event=first, gateway=home.gateway)
    note_inbound(event=second, gateway=home.gateway)
    assert asyncio.run(claim(session_key="", platform="telegram", source=source.to_dict(), message_id="9101",
                             text=first.text))
    assert home.telegram("1 prompt") is None


def test_every_owner_message_in_atlas_own_chat_is_atlas(home):
    record = Record.open(home.record_root)
    snap = record.snapshot()
    own = replace(snap.settings().channel, atlas_chat=f"telegram:{OWN_CHAT}:{OWN_TOPIC}")
    record.commit(snap.changes().put_settings(replace(snap.settings(), channel=own)))
    assert claimed(home.telegram("1 prompt", chat=OWN_CHAT, thread=OWN_TOPIC))
    assert home.telegram("1 prompt", chat=OWN_CHAT) is None
    assert home.telegram("atlas, 1 prompt") is None
    assert home.telegram("1 prompt", chat=OWN_CHAT, thread=OWN_TOPIC, sender="5550002") is None


def test_words_atlas_cannot_read_yet_go_to_hermes_and_write_nothing(home):
    before = home.files()
    assert home.telegram("atlas, merge the two greenhouse ones") is None
    assert home.files() == before


@pytest.mark.parametrize("text", ["same here, thanks", "one thing, can you check the weather?",
                                  "put that in the same folder", "can you join me for lunch?",
                                  "is the bay area warm in May?", "separate question: what time is it?",
                                  "let's split the bill"])
def test_ordinary_words_in_the_exchange_window_reach_no_model_and_write_nothing(tmp_path, text):
    answers = {"Merge": {"command": "Merge", "a": "Payments CI flakes", "b": "Agent-tooling newsletter",
                         "into_name": "Payments", "rule": ""},
               "Split": {"command": "Split", "pursuit": "Payments CI flakes", "new_name": "Payments CI: lunch",
                         "criterion": "lunch"},
               "AddArea": {"command": "AddArea", "area": "bay"}}
    model = FakeLlm({"atlas_converse": lambda call: {"commands": list(answers.values())}})
    with AtlasHome(tmp_path / "scene", model) as home:
        home.set_clock(world.PROMPT_AT)
        seeds.after_scene_7(home.record_root)
        assert claimed(home.telegram("atlas, 1 prompt"))
        before = home.files()
        assert home.telegram(text) is None
        assert home.files() == before
        assert model.calls == []


def test_a_reply_to_either_copy_of_a_brief_sent_twice_is_a_reply_to_that_brief(home):
    record = Record.open(home.record_root)
    snap = record.snapshot()
    monday = snap.brief(world.FIRST_BRIEF_AT.date())
    twice = (SendAttempt(world.FIRST_BRIEF_AT, SendStatus.ARRIVAL_UNKNOWN, (BRIEF,), chat=seeds.HOME_CHAT),
             SendAttempt(world.FIRST_BRIEF_AT + timedelta(minutes=10), SendStatus.SENT, ("8813",), chat=seeds.HOME_CHAT))
    record.commit(snap.changes().put_brief(replace(monday, attempts=twice)))
    assert claimed(home.telegram("1 prompt", reply_to=BRIEF))
    assert claimed(home.telegram("1 prompt", reply_to="8813"))


def test_a_redelivered_message_id_changes_nothing(home):
    first = home.telegram("1 done, 10/10 pass with one worker. park the newsletter", reply_to=BRIEF)
    before = home.files()
    again = home.telegram("1 done, 10/10 pass with one worker. park the newsletter", reply_to=BRIEF,
                          message_id=home.last_message_id)
    assert again == first == "Payments CI flakes: Step done.\nAgent-tooling newsletter: parked."
    assert home.files() == before


def test_a_home_channel_set_without_a_user_takes_its_chat_id_as_the_owner(home):
    home.gateway.homes["telegram"] = FakeHomeChannel(OWNER, "Home")
    assert claimed(home.telegram("1 prompt", reply_to=BRIEF))
    home.gateway.homes["telegram"] = FakeHomeChannel("-100777", "Group home")
    assert home.telegram("atlas, 1 prompt", chat="-100777") is None


def test_nothing_is_claimed_before_setup(tmp_path):
    with AtlasHome(tmp_path / "fresh") as fresh:
        fresh.set_clock(world.PROMPT_AT)
        assert fresh.telegram("atlas, 1 prompt") is None


def test_the_slash_command_reaches_the_same_handler(home):
    assert claimed(home.ctx.commands["atlas"]("1 prompt"))
    assert home.ctx.commands["atlas"]("merge the two greenhouse ones") is None


def test_the_notes_file_reads_as_the_conversation(home):
    home.telegram("1 done, 10/10 pass with one worker. park the newsletter", reply_to=BRIEF)
    home.telegram("atlas, 1 prompt")
    text = (home.record_root / "notes" / "2026" / "11" / "2026-11-16-telegram.md").read_text()
    said = [line for line in text.splitlines() if line.startswith(("You: ", "Atlas: "))]
    assert said == ["You: 1 done, 10/10 pass with one worker. park the newsletter",
                    "Atlas: Payments CI flakes: Step done.",
                    "You: 1 prompt",
                    'Atlas: Resuming "Payments CI flakes".']


STRANGER, GROUP, TOPIC = "5550002", "-100777", "7"
GENERAL = "1"                         # the thread Hermes v0.23.0 gives a Telegram forum's General topic
PARKED_IT = "Agent-tooling newsletter: parked."


def newsletter(home):
    return Record.open(home.record_root).snapshot().pursuit("p-7d1c")


def record_files(home):
    """The record's files only: moving the clock rewrites the Harness stores too."""
    return {path: data for path, data in home.files().items() if path.startswith("atlas/")}


@pytest.mark.parametrize("home_chat, sender, chat, thread", [
    (None, STRANGER, STRANGER, None),
    (None, STRANGER, OWNER, None),
    (None, OWNER, "5550099", None),
    ((GROUP, TOPIC), STRANGER, GROUP, TOPIC),
    ((GROUP, TOPIC), OWNER, "-100778", None),
    ((GROUP, TOPIC), OWNER, GROUP, "8"),
])
def test_slash_atlas_is_atlas_only_from_the_owner_in_the_channel_chat(home, home_chat, sender, chat, thread):
    if home_chat:
        home.gateway.homes["telegram"] = FakeHomeChannel(home_chat[0], "Invented group home", home_chat[1], OWNER)
    before = home.files()
    assert home.slash("drop the newsletter", sender=sender, chat=chat, thread=thread) is None
    assert newsletter(home).status is Status.ACTIVE
    assert home.files() == before


def test_slash_atlas_from_the_owner_in_the_channel_chat_is_handled(home):
    assert home.slash("park the newsletter") == PARKED_IT
    assert newsletter(home).status is Status.PARKED


def test_a_redelivered_slash_atlas_is_one_note_and_changes_nothing(home):
    first = home.slash("park the newsletter", message_id="9201")
    before = record_files(home)
    home.set_clock(world.PROMPT_AT + timedelta(minutes=1))
    assert home.slash("park the newsletter", message_id="9201") == first == PARKED_IT
    assert record_files(home) == before
    refs = [n.platform_ref for n in Record.open(home.record_root).snapshot().notes() if n.via == "slash"]
    assert refs == [f"telegram:{OWNER}:9201"]


def test_slash_atlas_opens_the_exchange_window(home):
    assert claimed(home.slash("1 prompt"))
    assert home.telegram("1 done") == "Payments CI flakes: Step done."


@pytest.mark.parametrize("text", ["", ".", "atlas", "Atlas!"])
def test_no_words_are_not_atlas_even_inside_the_window(home, text):
    assert home.telegram("1 done", reply_to=BRIEF)
    before = home.files()
    assert home.telegram(text) is None
    assert home.telegram(text, reply_to=BRIEF) is None
    assert home.slash(text.removeprefix("atlas").removeprefix("Atlas")) is None
    assert home.files() == before


def test_a_redelivery_gets_the_same_reply_after_the_window_and_after_a_gateway_restart(home):
    assert claimed(home.telegram("atlas, 1 prompt"))
    first = home.telegram("1 done", message_id="9301")
    assert first == "Payments CI flakes: Step done."
    before = record_files(home)
    home.set_clock(world.PROMPT_AT + timedelta(minutes=11))
    assert home.telegram("1 done", message_id="9301") == first
    home.restart_gateway()
    assert home.telegram("1 done", message_id="9301") == first
    assert record_files(home) == before


def test_a_redelivery_does_not_reopen_a_window_a_later_message_closed(home):
    original = home.telegram("atlas, 1 prompt", message_id="9101")
    home.set_clock(world.PROMPT_AT + timedelta(minutes=1))
    assert home.telegram("what is the weather", message_id="9102") is None
    home.set_clock(world.PROMPT_AT + timedelta(minutes=2))
    assert home.telegram("atlas, 1 prompt", message_id="9101") == original
    assert home.telegram("park the newsletter", message_id="9103") is None


def test_a_message_heard_while_the_one_before_is_being_claimed_is_in_its_window(home):
    note_inbound, = home.ctx.hooks["pre_gateway_dispatch"]
    claim, = home.ctx.hooks["post_gateway_admission"]
    source = FakeSource(FakePlatform("telegram"), OWNER, OWNER)
    first, second = FakeEvent("1 done", source, "9101", BRIEF), FakeEvent("2 done", source, "9102")
    note_inbound(event=first, gateway=home.gateway)
    note_inbound(event=second, gateway=home.gateway)
    assert asyncio.run(claim(session_key="", platform="telegram", source=source.to_dict(), message_id="9101",
                             text=first.text))
    handled = asyncio.run(claim(session_key="", platform="telegram", source=source.to_dict(), message_id="9102",
                                text=second.text))
    assert handled == {"action": "handled", "reply": "Billing export service: Step done."}


@pytest.mark.parametrize("thread", [None, TOPIC])
def test_a_message_id_names_a_brief_only_in_the_chat_it_was_sent_to(home, thread):
    home.gateway.homes["telegram"] = FakeHomeChannel(GROUP, "Invented group home", thread, OWNER)
    assert home.telegram("1 done", reply_to=BRIEF, chat=GROUP, thread=thread) is None
    assert Record.open(home.record_root).snapshot().pursuit("p-2x7b").next_step is not None


@pytest.mark.parametrize("later, is_claimed", [(timedelta(minutes=9), True), (timedelta(minutes=11), False)])
def test_a_reply_to_an_atlas_answer_is_atlas_only_inside_the_window(home, later, is_claimed):
    assert claimed(home.telegram("atlas, 1 prompt"))
    home.set_clock(world.PROMPT_AT + later)
    reply = home.telegram("park the newsletter", reply_to=home.last_reply_id)
    assert (reply == PARKED_IT) is is_claimed
    assert newsletter(home).status is (Status.PARKED if is_claimed else Status.ACTIVE)


@pytest.mark.parametrize("home_chat, said, quoting", [
    (OWNER, "atlas, 1 done", "Step done."),
    (GROUP, "atlas, continue the newsletter", "stays active"),
    (GROUP, "atlas, 1 prompt", "Payments CI flakes"),
])
def test_a_reply_to_someone_else_is_not_atlas_whatever_it_quotes(home, home_chat, said, quoting):
    home.gateway.homes["telegram"] = FakeHomeChannel(home_chat, "Invented home", None, OWNER)
    assert quoting in home.telegram(said, chat=home_chat)
    home.set_clock(world.PROMPT_AT + timedelta(minutes=11))
    before = record_files(home)
    assert home.telegram("drop the newsletter", reply_to="9150", quoting=quoting, chat=home_chat) is None
    assert newsletter(home).status is Status.ACTIVE
    assert record_files(home) == before


def test_numbers_in_a_reply_to_an_atlas_answer_name_the_latest_brief_inside_the_window(home):
    assert claimed(home.telegram("atlas, 1 prompt"))
    assert home.telegram("1 done", reply_to=home.last_reply_id) == "Payments CI flakes: Step done."


def test_a_redelivered_slash_atlas_does_not_move_the_window(home):
    first = home.slash("park the newsletter", message_id="9601")
    home.set_clock(world.PROMPT_AT + timedelta(minutes=9))
    assert home.slash("park the newsletter", message_id="9601") == first == PARKED_IT
    home.set_clock(world.PROMPT_AT + timedelta(minutes=15))
    assert home.telegram("1 done") is None


def test_a_redelivered_slash_atlas_after_a_gateway_restart_opens_no_window(home):
    first = home.slash("park the newsletter", message_id="9601")
    home.restart_gateway()
    home.set_clock(world.PROMPT_AT + timedelta(minutes=60))
    assert home.slash("park the newsletter", message_id="9601") == first
    assert home.telegram("1 done") is None


def test_a_reply_to_an_atlas_answer_in_another_chat_is_not_atlas(home):
    assert claimed(home.telegram("atlas, 1 prompt"))
    answer = home.last_reply_id
    home.set_clock(world.PROMPT_AT + timedelta(minutes=11))
    home.gateway.homes["telegram"] = FakeHomeChannel(GROUP, "Invented group home", None, OWNER)
    assert home.telegram("park the newsletter", reply_to=answer, chat=GROUP) is None


def fail_once(when):
    """The move of a staged file into the record, failing once where `when(target)` says."""
    real, failed = _txn._replace_below, []

    def replace_or_fail(root, staged, target):
        if not failed and when(Path(target)):
            failed.append(target)
            raise OSError(5, "invented I/O failure")
        return real(root, staged, target)
    return replace_or_fail


def test_an_accepted_command_stays_handled_when_its_commit_fails_partway(home, monkeypatch):
    said = "1 done. park the newsletter"
    monkeypatch.setattr(_txn, "_replace_below", fail_once(lambda dst: dst.parent.name == "pursuits"))
    reply = home.telegram(said, reply_to=BRIEF)
    assert reply == "Payments CI flakes: Step done.\nAgent-tooling newsletter: parked."
    assert home.hook_failures == []
    snap = Record.open(home.record_root).snapshot()
    assert [n.atlas_reply for n in snap.notes() if n.via == "telegram" and n.id != seeds.SPLIT_SAID.id] == [reply]
    assert snap.pursuit("p-2x7b").next_step is None and newsletter(home).status is Status.PARKED
    before = home.files()
    assert home.telegram(said, reply_to=BRIEF, message_id=home.last_message_id) == reply
    assert home.files() == before


def test_a_terminal_slash_atlas_whose_commit_was_rolled_forward_gets_its_reply(home, monkeypatch):
    monkeypatch.setattr(_txn, "_replace_below", fail_once(lambda dst: dst.parent.name == "pursuits"))
    assert home.ctx.commands["atlas"]("park the newsletter") == PARKED_IT
    assert newsletter(home).status is Status.PARKED


def test_a_redelivery_stays_handled_while_its_transaction_cannot_be_applied(home, monkeypatch):
    real = _txn._replace_below

    def pursuits_fail(root, staged, target):
        if Path(target).parent.name == "pursuits":
            raise OSError(5, "invented I/O failure")
        return real(root, staged, target)
    monkeypatch.setattr(_txn, "_replace_below", pursuits_fail)
    first = home.telegram("1 done", reply_to=BRIEF)
    home.restart_gateway()
    home.set_clock(world.PROMPT_AT + timedelta(minutes=11))
    again = home.telegram("1 done", reply_to=BRIEF, message_id=home.last_message_id)
    assert first == again == "Payments CI flakes: Step done."
    assert home.hook_failures == []
    monkeypatch.setattr(_txn, "_replace_below", real)
    snap = Record.open(home.record_root).snapshot()
    assert snap.pursuit("p-2x7b").next_step is None
    assert [n.atlas_reply for n in snap.notes() if n.via == "telegram" and n.id != seeds.SPLIT_SAID.id] == [first]
    assert not list((home.record_root / ".atlas/txn").glob("*.ready"))


def test_an_accepted_command_whose_commit_wrote_nothing_is_answered_with_the_failure(home, monkeypatch):
    real = _txn.stage
    monkeypatch.setattr(_txn, "stage", lambda root, files: (_ for _ in ()).throw(OSError(28, "invented full disk")))
    before = home.files()
    failed = "I couldn't answer that: my record could not be written."
    assert home.telegram("park the newsletter", reply_to=BRIEF) == failed
    assert home.hook_failures == [] and home.files() == before
    monkeypatch.setattr(_txn, "stage", real)
    assert home.telegram("park the newsletter", reply_to=BRIEF) == PARKED_IT


def test_numbers_never_name_a_brief_the_owner_was_not_sent(tmp_path):
    with AtlasHome(tmp_path / "unsent") as home:
        home.set_clock(world.FIRST_BRIEF_AT)
        seeds.after_scene_6(home.record_root)
        home.script_hermes("send", exit=2, stderr="invented usage refusal\n")
        assert home.cli("atlas", "tick").code == 0
        assert Record.open(home.record_root).snapshot().briefs()[-1].status is SendStatus.SENDING
        assert home.telegram("atlas, 1 done") is None
        assert Record.open(home.record_root).snapshot().pursuit("p-2x7b").next_step is not None


def test_a_pronoun_names_no_pursuit(home):
    record = Record.open(home.record_root)
    snap = record.snapshot()
    trip = replace(snap.pursuit("p-3n0w"), id="p-9t1p", name="Japan trip itinerary", focus=False)
    record.commit(snap.changes().put_pursuit(trip))
    assert claimed(home.telegram("1 prompt", reply_to=BRIEF))
    assert home.telegram("drop it") is None
    assert Record.open(home.record_root).snapshot().pursuit("p-9t1p").status is Status.ACTIVE


def test_a_command_after_a_comma_is_a_command_not_the_step_detail(home):
    reply = home.telegram("1 done, 2 skip", reply_to=BRIEF)
    assert reply == "Payments CI flakes: Step done.\nBilling export service: Step skipped."
    snap = Record.open(home.record_root).snapshot()
    assert snap.pursuit("p-2x7b").past_steps[-1].ended.basis.text == ""
    assert snap.pursuit("p-4k7q").past_steps[-1].ended.how == "word"


def test_a_brief_sent_before_atlas_kept_its_chat_is_named_by_no_reply(home):
    record = Record.open(home.record_root)
    snap = record.snapshot()
    monday = snap.brief(world.FIRST_BRIEF_AT.date())
    record.commit(snap.changes().put_brief(replace(monday, attempts=tuple(replace(a, chat=None)
                                                                          for a in monday.attempts))))
    home.gateway.homes["telegram"] = FakeHomeChannel(GROUP, "Invented new group home", None, OWNER)
    assert home.telegram("1 done", reply_to=BRIEF, chat=GROUP) is None
    assert Record.open(home.record_root).snapshot().pursuit("p-2x7b").next_step is not None


@pytest.mark.parametrize("home_thread", [GENERAL, "42"])
def test_a_brief_to_a_forum_home_lands_in_its_general_topic_and_a_reply_there_is_atlas(tmp_path, home_thread):
    with AtlasHome(tmp_path / "forum") as home:
        home.gateway.homes["telegram"] = FakeHomeChannel(GROUP, "Invented forum home", home_thread, OWNER)
        home.set_clock(world.FIRST_BRIEF_AT)
        seeds.after_scene_6(home.record_root)
        home.script_hermes("send", stdout=json.dumps({"success": True, "platform": "telegram", "chat_id": GROUP,
                                                      "message_id": BRIEF}) + "\n")
        home.cli("atlas", "tick")
        assert Record.open(home.record_root).snapshot().briefs()[-1].attempts[-1].chat == f"telegram:{GROUP}"
        home.set_clock(world.PROMPT_AT)
        assert claimed(home.telegram("1 prompt", reply_to=BRIEF, chat=GROUP, thread=GENERAL))
        if home_thread != GENERAL:
            assert home.telegram("atlas, 1 prompt", chat=GROUP, thread=home_thread) is None


def test_atlas_own_chat_named_by_a_forum_general_topic_is_that_topic(home):
    record = Record.open(home.record_root)
    snap = record.snapshot()
    own = replace(snap.settings().channel, atlas_chat=f"telegram:{GROUP}:{GENERAL}")
    record.commit(snap.changes().put_settings(replace(snap.settings(), channel=own)))
    assert claimed(home.telegram("1 prompt", chat=GROUP, thread=GENERAL))
    assert home.telegram("1 prompt", chat=GROUP, thread="42") is None


def test_a_reply_that_arrives_before_the_tick_records_the_brief_as_sent_is_a_reply_to_it(tmp_path, monkeypatch):
    """Telegram delivers the brief and the owner replies while the tick, its own process in Hermes,
    has not yet recorded the brief's message id."""
    with AtlasHome(tmp_path / "in-flight") as home:
        home.set_clock(world.FIRST_BRIEF_AT)
        seeds.after_scene_6(home.record_root)
        send, delivered, heard = channel.Telegram.send, queue.Queue(), threading.Event()

        def delivered_then_slow_to_return(transport, text):
            result = send(transport, text)
            delivered.put(result.message_ids[0])
            heard.wait(timeout=5)
            return result
        monkeypatch.setattr(channel.Telegram, "send", delivered_then_slow_to_return)
        tick = threading.Thread(target=Atlas.open(home.record_root).tick, args=(world.FIRST_BRIEF_AT,))
        tick.start()
        note_inbound, = home.ctx.hooks["pre_gateway_dispatch"]
        claim, = home.ctx.hooks["post_gateway_admission"]
        source = FakeSource(FakePlatform("telegram"), OWNER, OWNER)
        note_inbound(event=FakeEvent("1 done", source, "9101", delivered.get(timeout=5)), gateway=home.gateway)
        heard.set()
        handled = asyncio.run(claim(session_key="", platform="telegram", source=source.to_dict(), message_id="9101",
                                    text="1 done"))
        tick.join(timeout=10)
        assert handled == {"action": "handled", "reply": "Payments CI flakes: Step done."}
