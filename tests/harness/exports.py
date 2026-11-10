"""Web-chat export files for the fixture world, in the shapes open-source parsers read (#15).

ChatGPT: conversations.json, a list of conversations whose `mapping` holds parent-pointer nodes
under a root with no message, a hidden system message first, times in epoch seconds, and one
message with no time; beside it group_chats.json (other people's messages), chat.html and
user.json. claude.ai: conversations.json with `chat_messages` in order, ISO times, beside
users.json. Invented data only.
"""
from __future__ import annotations

import json
import os
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from . import world

GROUP_CHAT = "the cobalt otter picnic plan"   # only the group chats hold these words


def chatgpt_conversation(s: world.WorldSession, undated: bool = False) -> dict:
    root = str(uuid.uuid5(uuid.NAMESPACE_URL, s.native + ":root"))
    mapping = {root: {"id": root, "message": None, "parent": None, "children": []}}
    system = _node(s, "system", 0, "You are a helpful assistant.", None)
    system["message"]["metadata"] = {"is_visually_hidden_from_conversation": True}
    chain = [system]
    for n, t in enumerate(s.turns()):
        at = None if undated and n == 2 else t.at.timestamp()
        chain.append(_node(s, "user" if t.owner else "assistant", n + 1, t.text, at))
    parent = root
    for node in chain:
        node["parent"] = parent
        mapping[parent]["children"].append(node["id"])
        mapping[node["id"]] = node
        parent = node["id"]
    return {"id": s.native, "conversation_id": s.native, "title": s.title, "create_time": s.start.timestamp(),
            "update_time": s.end.timestamp(), "mapping": mapping, "current_node": parent}


def _node(s: world.WorldSession, role: str, n: int, text: str, at: float | None) -> dict:
    id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{s.native}:{n}"))
    return {"id": id, "children": [], "message": {
        "id": id, "author": {"role": role, "name": None, "metadata": {}}, "create_time": at, "update_time": None,
        "content": {"content_type": "text", "parts": [text]}, "status": "finished_successfully", "metadata": {}}}


def claude_conversation(s: world.WorldSession) -> dict:
    return {"uuid": s.native, "name": s.title, "created_at": world.utc(s.start), "updated_at": world.utc(s.end),
            "chat_messages": [{"uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{s.native}:{n}")), "text": t.text,
                               "content": [{"type": "text", "text": t.text}], "sender": "human" if t.owner else "assistant",
                               "created_at": world.utc(t.at), "updated_at": world.utc(t.at), "attachments": [],
                               "files": []} for n, t in enumerate(s.turns())]}


def chatgpt_zip(path: Path, sessions: tuple[world.WorldSession, ...], at: datetime) -> Path:
    conversations = [chatgpt_conversation(s, undated=n == 0) for n, s in enumerate(sessions)]
    group = [{"id": "group-1", "title": "weekend", "messages": [{"author": "a friend", "text": GROUP_CHAT}]}]
    return _zip(path, {"conversations.json": conversations, "group_chats.json": group, "user.json": {"id": "user-1"},
                       "chat.html": "<html></html>"}, at)


def claude_zip(path: Path, sessions: tuple[world.WorldSession, ...], at: datetime) -> Path:
    return _zip(path, {"conversations.json": [claude_conversation(s) for s in sessions],
                       "users.json": [{"uuid": "user-1"}]}, at)


def _zip(path: Path, members: dict[str, object], at: datetime) -> Path:
    """The archive, dated `at`, as the owner's mail client saved it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, value in members.items():
            z.writestr(name, value if isinstance(value, str) else json.dumps(value))
    os.utime(path, (at.timestamp(), at.timestamp()))
    return path
