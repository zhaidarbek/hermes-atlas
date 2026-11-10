"""Channels: where Atlas talks to the owner. v1 ships Telegram through Hermes; the Channel
protocol is the seam for others (Slack/Discord via the same `hermes send` targets).

Outbound: `hermes send --to <target> --json "<text>"` as a subprocess. It works from the cron
tick and any CLI process, needs no gateway for bot-token platforms, and returns the chat and
message_id, both recorded on the send attempt: a Telegram message id is unique only within its
chat. It cannot attach buttons. Hermes mirrors the text into the owner's chat session in state.db
as an unmarked assistant row; the Hermes adapter drops those rows by matching the sent
ledger (briefs/, reviews/, and the atlas_reply of Notes).

Inbound (gateway process only, in_process isolation; Listener holds what the hooks share):
  pre_gateway_dispatch   -> note_inbound(): learns the owner and chat of the home channel from the
                            live gateway config, and for each owner message records which brief it
                            replies to (SentRef) and the owner's message before it in the chat. No
                            LLM; it reads the record only for a reply. Always returns None (allow).
                            A message id heard before is a redelivery: what was heard the first
                            time stands.
  post_gateway_admission -> claim(): decides with ONE predicate whether the message is Atlas's:
        owner sender (source user id == home channel user id; its chat id when /sethome named no user)
        and in the Channel chat/thread (settings.channel.atlas_chat, else the home channel's chat)
        and (settings.channel.dedicated     # Atlas's own chat or topic, one setting (D Q16)
             or reply_to is a brief sent to this chat
             or text starts with the address word ("atlas", "atlas,")
             or the owner's previous message in this chat was claimed < exchange_window ago)
  A message whose platform_ref already has a Note is a redelivery: the owner's and the chat's
  checks still apply, then it gets the Note's reply whatever the window, even after a gateway
  restart, and changes nothing. The window is read when the message is claimed, so a claim
  that finished after the message was heard counts.
  A reply names a brief by the chat and message id it was sent as (either copy of a brief sent
  twice). A reply that arrives while a brief is being sent waits, at most SETTLE seconds, for the
  tick to record the brief's message id. Atlas's answers have no message id on record: the gateway
  sends them (ADR 0007), and Hermes v0.23.0 tells no hook the id. So a reply to an answer is
  Atlas's by the window or the address word, never by the text it quotes: Telegram quotes any part
  of any message.
  The Channel's chat is where `hermes send --to <platform>` lands: Hermes v0.23.0 sends to the home
  channel's chat and leaves out the topic /sethome was run in, so a brief to a home in a forum topic
  arrives in the forum's General topic. A Telegram forum's General topic is its chat: Hermes gives
  its messages thread "1" and sends to it with no thread.
  If claimed: Atlas.handle(msg) within a 25 s budget -> {"action":"handled","reply": text}, on a
  worker thread so the gateway's event loop keeps running. Words Atlas cannot read yet (the model
  tier of converse is not built), or no words at all, are not claimed after all: handle writes
  nothing and returns None. Once Atlas has read the words as its own, the message stays handled
  even if the commit fails: handle answers with what the record holds after recovery.
  Over budget (D Q8): reply "Looking, I'll send it here." and finish on a per-request worker
  thread in the gateway that sends the answer via `hermes send` to the same chat, or "I couldn't
  answer that: <reason>." on failure. The late answer is recorded as the Note's atlas_reply
  (sent ledger) so its mirror row is dropped like a brief's.
  Unaddressed messages are Hermes's (D Q1, D S2): Atlas's limits, such as never contacting other
  people, bind Atlas, not Hermes's agent. Not claimed: return None, and the Hermes agent gets the message with
  Atlas's tools and skill. If the chat is busy, Hermes skips this hook, so even a reply to a
  brief then goes to the agent, which calls atlas_update/atlas_read; it is handled, only slower.
  A record Atlas cannot read leaves the message to Hermes too (logged, never raised).

/atlas <words> -> slash(): the gateway runs pre_gateway_dispatch for it, then the command. From a
  messaging chat it is Atlas's only from the owner in the Channel's chat, the same checks as
  claim's; it carries its platform_ref, so a redelivery gets the same reply, changes nothing and
  leaves the window alone, and it opens the exchange window like a claimed message. With no
  messaging source (the terminal or the TUI on this machine, ADR 0003) it is the owner's.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Protocol

from . import _hermes, messages
from .model import ChannelSetting, OwnerMessage, SentRef, Settings

if TYPE_CHECKING:
    from .app import Atlas

log = logging.getLogger(__name__)

GENERAL = "1"                                 # the thread Hermes gives a Telegram forum's General topic
SETTLE = 10.0                                 # seconds a reply waits for a brief being sent to be recorded
BUDGET = 25.0                                 # seconds an answer may take before the holding reply (D Q8)
HOLDING = "Looking, I'll send it here."
LATE_FAILURE = "I couldn't answer that: something went wrong while I looked."


class ArrivalUnknown(Exception):
    """`hermes send` failed or timed out after the platform may have accepted the message (D Q13)."""


class SendRefused(Exception):
    """`hermes send` refused before sending: a usage error, or no `hermes` on PATH."""


@dataclass(frozen=True)
class Delivered:
    chat: str | None                         # "telegram:<chat>[:<thread>]", as hermes send resolved the target
    message_ids: tuple[str, ...]


class Channel(Protocol):
    def send(self, text: str) -> Delivered: ...
    def check(self) -> str | None: ...       # None ok, else what setup should tell the owner


@dataclass
class Telegram:
    settings: Settings

    def send(self, text: str) -> Delivered:
        """subprocess: hermes send --to <settings.channel.target> --json <text>.
        exit 0 -> the chat and message ids (SENT); exit 1 after the platform may have accepted it, or
        a timeout -> ArrivalUnknown (retried once the same day, D Q13); exit 2 (usage) -> SendRefused."""
        return send(self.settings.channel, text)

    def check(self) -> str | None:
        """`hermes send -l` lists the home channel for the platform; otherwise tell the owner to /sethome."""
        listed = _hermes.run("send", "-l", "--json")
        if listed.returncode != 0:
            return f"Hermes could not list its channels: {(listed.stderr or listed.stdout).strip()}"
        platform = self.settings.channel.platform
        if platform not in json.loads(listed.stdout).get("platforms", {}):
            return (f"Hermes has no {platform.title()} home channel yet. Send /sethome to your bot in "
                    f"{platform.title()}, then run hermes atlas setup again.")
        return None


def send(setting: ChannelSetting, text: str) -> Delivered:
    """Telegram.send for a Channel setting alone, as the gateway sends a late answer (D Q8)."""
    try:
        done = _hermes.run("send", "--to", setting.target, "--json", text)
    except subprocess.TimeoutExpired as late:
        raise ArrivalUnknown("hermes send timed out") from late
    except OSError as refused:
        raise SendRefused(f"hermes send could not start ({type(refused).__name__})") from refused
    if done.returncode == 1:
        raise ArrivalUnknown(_last_line(done.stderr) or _result(done.stdout).get("error") or "hermes send failed")
    if done.returncode != 0:
        raise SendRefused(_last_line(done.stderr) or f"hermes send exited {done.returncode}")
    result = _result(done.stdout)
    platform, _, rest = setting.target.partition(":")
    thread = rest.split(":")[1] if rest.count(":") else None
    chat = chat_key(platform, result["chat_id"], thread) if result.get("chat_id") else None
    return Delivered(chat, (str(result["message_id"]),) if result.get("message_id") is not None else ())


def _result(stdout: str) -> dict:
    """The JSON object `hermes send --json` prints last. Under the Docker install, container
    start-up lines come first on the same stdout, and the object itself is indented."""
    lines = stdout.splitlines()
    start = next((n for n in range(len(lines) - 1, -1, -1) if lines[n].startswith("{")), None)
    if start is None:
        return {}
    try:
        return json.loads("\n".join(lines[start:]))
    except json.JSONDecodeError:
        return {}


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


# ---- gateway hooks (registered in atlas.__init__.register) ------------------------------------

@dataclass(frozen=True)
class Home:
    """The platform's Hermes home channel: the chat `hermes send --to <platform>` lands in, and the
    owner who ran /sethome there. A home channel set from the environment names no user; its chat
    id is then the owner's, as in a Telegram direct chat, and in a group nobody matches, so Atlas
    claims nothing there."""
    chat: str                                 # "telegram:<chat>", with no topic
    owner: str


@dataclass(frozen=True)
class Heard:
    """What note_inbound learned about one owner message, for claim."""
    reply_to: SentRef | None
    previous: str | None                      # the id of the owner's message before it in the same chat
    reply_id: str | None = None               # the id of the message it replies to


def chat_key(platform: str, chat_id: object, thread_id: object = None) -> str:
    """A chat as ChannelSetting.atlas_chat names one: "telegram:<chat>[:<thread>]". A Telegram
    forum's General topic (thread "1") is the chat itself."""
    if platform == "telegram" and str(thread_id) == GENERAL:
        thread_id = None
    return f"{platform}:{chat_id}" + (f":{thread_id}" if thread_id else "")


def chat_named(target: str) -> str:
    """The chat a "<platform>:<chat>[:<thread>]" setting names, written as chat_key writes it."""
    platform, chat_id, thread = (target.split(":", 2) + [None, None])[:3]
    return chat_key(platform, chat_id, thread)


def addressed(text: str, word: str) -> str | None:
    """The words after the address word ("atlas", "atlas,") when the text starts with it."""
    m = re.match(rf"\s*{re.escape(word)}\b[\s,:;.!-]*(?P<rest>.*)\Z", text, re.I | re.S)
    return m["rest"] if m else None


def from_owner(setting: ChannelSetting, home: Home, sender: str | None, chat: str) -> bool:
    """The owner wrote it, in the Channel's chat."""
    return sender == home.owner and chat == (chat_named(setting.atlas_chat) if setting.atlas_chat else home.chat)


def claims(setting: ChannelSetting, home: Home, sender: str | None, chat: str, text: str, reply_to: SentRef | None,
           in_window: bool) -> bool:
    """The one claim predicate of the module docstring."""
    return from_owner(setting, home, sender, chat) and (
        setting.dedicated or reply_to is not None or addressed(text, setting.address_word) is not None or in_window)


class Listener:
    """The gateway process's half of the Channel. note_inbound and claim are the two hooks, slash
    the /atlas command, and this object is what they tell each other. It lives as long as the
    gateway: a restart forgets the exchange window, and a reply to a brief is still claimed."""

    KEEP = 256                                # owner messages remembered, per kind

    def __init__(self, atlas: Callable[[], "Atlas"], clock: Callable[[], datetime], budget: float = BUDGET) -> None:
        self._atlas = atlas
        self._clock = clock
        self._budget = budget
        self._homes: dict[str, Home] = {}
        self._heard: OrderedDict[tuple[str, str], Heard] = OrderedDict()
        self._latest: dict[str, str] = {}
        self._claimed: OrderedDict[tuple[str, str], datetime] = OrderedDict()

    def note_inbound(self, event, gateway, **_) -> None:
        """pre_gateway_dispatch: never changes the message (returns None, allow)."""
        source = event.source
        platform = source.platform.value
        home = gateway.config.get_home_channel(source.platform)
        if home is None:
            return None
        self._homes[platform] = Home(chat_key(platform, home.chat_id), str(home.user_id or home.chat_id))
        if str(source.user_id) != self._homes[platform].owner:
            return None
        chat = chat_key(platform, source.chat_id, source.thread_id)
        heard = (chat, str(event.message_id))
        if heard in self._heard:
            return None
        reply_id = str(event.reply_to_message_id) if event.reply_to_message_id else None
        try:
            reply_to = self._atlas().sent(chat, reply_id) if reply_id else None
        except Exception as failed:
            log.warning("Atlas could not read which brief a message replies to (%s)", type(failed).__name__)
            reply_to = None
        self._heard[heard] = Heard(reply_to, self._latest.get(chat), reply_id)
        self._latest[chat] = heard[1]
        _keep(self._heard, self.KEEP)
        return None

    async def claim(self, session_key: str, platform: str, source: dict, message_id: str, text: str,
                    **_) -> dict | None:
        """post_gateway_admission: {"action": "handled", "reply": str} or None. Builds OwnerMessage
        (domain type) here; nothing past this function sees Hermes's event shapes."""
        try:
            return await self._claim(chat_key(platform, source.get("chat_id"), source.get("thread_id")),
                                     str(source.get("user_id")), str(message_id), text)
        except Exception as failed:
            log.warning("Atlas could not read its record to claim a message (%s); it is left to Hermes",
                        type(failed).__name__)
            return None

    async def _claim(self, chat: str, sender: str, message_id: str, text: str) -> dict | None:
        heard = self._heard.get((chat, message_id), Heard(None, None))
        owned = self._owned(sender, chat)
        if owned is None:
            return None
        atlas, setting, home = owned
        ref = f"{chat}:{message_id}"
        earlier = await asyncio.to_thread(atlas.answered, ref)
        if earlier is not None:
            return {"action": "handled", "reply": earlier}
        reply_to = heard.reply_to
        if reply_to is None and heard.reply_id:
            reply_to = await self._once_sent(atlas, chat, heard.reply_id)
        now = self._clock()
        if not claims(setting, home, sender, chat, text, reply_to, self._in_window(chat, heard, setting, now)):
            return None
        words = addressed(text, setting.address_word)
        msg = OwnerMessage(text if words is None else words, now, "telegram", chat, reply_to, ref, words is not None)
        reply = await self._within_budget(atlas, msg)
        if reply is None:
            return None
        self._mark_claimed(chat, message_id, now)
        return {"action": "handled", "reply": reply}

    async def _within_budget(self, atlas: "Atlas", msg: OwnerMessage) -> str | None:
        """Atlas's reply, or HOLDING once the budget is spent (D Q8). The handling thread then
        runs on and sends the reply with `hermes send` as its own message, or LATE_FAILURE when
        Atlas gave none. The reply sent is the one the Note keeps, so its mirror row is dropped."""
        handled: Future = Future()
        threading.Thread(target=lambda: handled.set_result(_handle(atlas, msg)), daemon=True).start()
        try:
            return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(handled)), self._budget)
        except asyncio.TimeoutError:
            handled.add_done_callback(lambda done: _send_late(atlas, done.result()))
            return HOLDING

    def slash(self, raw_args: str, source=None, **_) -> str | None:
        """/atlas <words>: the same handler as a claimed message. None when the words are not
        Atlas's to read, from this sender or chat or not yet; Hermes then sends nothing."""
        try:
            return self._slash(raw_args, source)
        except Exception as failed:
            log.warning("Atlas could not read its record for /atlas (%s)", type(failed).__name__)
            return None

    def _slash(self, raw_args: str, source) -> str | None:
        now = self._clock()
        if source is None or source.platform.value == "local":
            return _handle(self._atlas(), OwnerMessage(raw_args, now, "slash", None, None, addressed=True))
        chat = chat_key(source.platform.value, source.chat_id, source.thread_id)
        owned = self._owned(str(source.user_id), chat)
        if owned is None:
            return None
        atlas = owned[0]
        message = str(source.message_id) if source.message_id else None
        ref = f"{chat}:{message}" if message else None
        earlier = atlas.answered(ref) if ref else None
        if earlier is not None:
            return earlier
        heard = self._heard.get((chat, message), Heard(None, None))
        reply = _handle(atlas, OwnerMessage(raw_args, now, "slash", chat, heard.reply_to, ref, True))
        if reply is not None and message:
            self._mark_claimed(chat, message, now)
        return reply

    def _owned(self, sender: str, chat: str) -> tuple["Atlas", ChannelSetting, Home] | None:
        """Atlas, its Channel setting and the home channel, when the owner wrote in the Channel's
        chat; None otherwise, and before setup."""
        atlas = self._atlas()
        try:
            setting = atlas.channel()
        except FileNotFoundError:
            return None
        home = self._homes.get(setting.platform)
        if home is None or not from_owner(setting, home, sender, chat):
            return None
        return atlas, setting, home

    async def _once_sent(self, atlas: "Atlas", chat: str, reply_id: str) -> SentRef | None:
        """The brief a reply names, looked up again once no brief is being sent: Telegram can
        deliver the owner's reply before the tick has recorded the brief's message id."""
        deadline = time.monotonic() + SETTLE
        while time.monotonic() < deadline and await asyncio.to_thread(atlas.sending, self._clock()):
            await asyncio.sleep(0.2)
        return await asyncio.to_thread(atlas.sent, chat, reply_id)

    def _in_window(self, chat: str, heard: Heard, setting: ChannelSetting, now: datetime) -> bool:
        at = self._claimed.get((chat, heard.previous)) if heard.previous else None
        return at is not None and now - at < setting.exchange_window

    def _mark_claimed(self, chat: str, message: str, at: datetime) -> None:
        self._claimed[(chat, message)] = at
        _keep(self._claimed, self.KEEP)


def _handle(atlas: "Atlas", msg: OwnerMessage) -> str | None:
    """Atlas's reply, or None when the message stays Hermes's: words Atlas does not read, or a
    failure before it read them (convention 13: logged, never raised into the gateway)."""
    try:
        reply = atlas.handle(msg)
    except Exception as failed:
        log.warning("Atlas failed before reading a message (%s); it is left to Hermes", type(failed).__name__)
        return None
    return reply.text if reply else None


def _send_late(atlas: "Atlas", reply: str | None) -> None:
    try:
        send(atlas.channel(), messages.fit(reply or LATE_FAILURE))
    except Exception as failed:
        log.warning("Atlas could not send a late answer (%s)", type(failed).__name__)


def _keep(remembered: OrderedDict, n: int) -> None:
    while len(remembered) > n:
        remembered.popitem(last=False)
