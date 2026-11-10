"""PRIVATE. A Hermes user handoff row read as Hermes v0.23.0 reads it. Pure.

When Hermes compacts a conversation it can fold its summary into the owner's next ask: one user
row holds the summary and, after its end marker or before its delimiter, the ask. Hermes
v0.23.0's `_display_dedupe_key` reads a user row through `split_user_originated_turn`, and when
the row is such a handoff with a live ask, keys it by the ask's content, so the handoff and the
ask's own row are shown once. `live_ask` gives that content for the row's `content` and
`display_kind`. The row's `display_metadata` decides nothing here: the split keeps only its
`reactions`, and the key reads only the ask's content.

Hermes's summary prefixes and the texts it writes as synthetic user turns are held as SHA-256
digests of their UTF-8 text, a prefix with its length in characters:
`agent.context_compressor.SUMMARY_PREFIX`, `LEGACY_SUMMARY_PREFIX` and
`_HISTORICAL_SUMMARY_PREFIXES`; the texts `_is_synthetic_compression_user_turn` matches whole.
"""
from __future__ import annotations

import hashlib
import json
import re

_JSON = "\x00json:"
_SURROGATE = re.compile("[\ud800-\udfff]")
_END = "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
_DELIMITER = "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]"
_PRIOR = "[PRIOR CONTEXT — for reference only; not a new message]"
_HIDDEN = "hidden"
_PREFIXES = (
    (1954, "7e9c6ce1b413ec6c11da5f2f7b5a3313bfeaf120c23b262d83ae5531e23da9eb"),
    (18, "fc5e4bcfdac72fa9ab2be873cb38a9a9264b6651fa4586fa21cdc5c08854a03c"),
    (1527, "ff3014fed07c965aa893a69b555a91f0f63cacb013bd276d47efeefb35399391"),
    (1632, "1c004c9c0ec1a25cbc96ae326724269d8ee67d363a8aeca17e722801e0f4b672"),
    (1428, "f953b27ebb6288ec9aea994184be18cbc56783d2237241df2b1f24b55f3b0230"),
    (1378, "2999da51e1781fab13ee35aa6b0f6d2815091130426d1c840a3b0fc9b7df7141"),
    (586, "5cd6c1b9dc95f8ff066df65c33abb66cad2718fe6f12bf736491ae9ea3b1c013"),
)
_SYNTHETIC = frozenset({
    "813558469c3ee538148ddf78e284638f83a6e83fe6e252a03d24a6426f5fa507",
    "8e9df8ea1780496e6ce95b2bad9b21f7f0bce68f2dda54fb17a6d0362bd969f9",
    "9fc064d62da9070f198ecd14ea0395781d20a5448a8fa79793ca5f8bb9f9cd41",
    "279ab90ad0f397baf55b99f6d4241047cef31a1d66e9af94e4ec5cc2383f8cec",
    "ca0efef9eb4164c2834c0c6e113ff24acc81ab8c893a4736bd980c2ca41b165d",
    "413b4e46bc42be01119522bb17db5cf97a3cd20e9f8bc77b10b431a9ac8c03e8",
    "b9f63dab3e4018166a1353dc7836ccc99b103cb5a024d2a96450edddb98cc7ed",
    "ebb4495cd05fcc28d52577d51ad44402e349e60a239ef4c0c849fadf802dbf3a",
    "e4403cfa28638e7bd7b96162d6ee75a886bbf99089c9e0f086863d30443c3495",
    "768ba8a3adfb123d05595b25cb1558a55e48f17d6acd4ba671b2dd28df5d913d",
    "c528a82294a08dd3b703454b6dc5f5cf8cfefa40565e7cadeded33490fe0cabf",
})
_SYNTHETIC_STARTS = ("[IMPORTANT: Background process ",
                     "[Your active task list was preserved across context compression]\n",
                     "[System: Your previous tool call ")


def live_ask(content: object, display_kind: object) -> object | None:
    """The live ask of a user handoff row, as Hermes stores content, or None for any other row."""
    content = _decoded(content)
    if not _is_summary(content) or (display_kind and display_kind != _HIDDEN):
        return None
    ask = _unwrapped(content)
    if ask is None or _is_summary(ask) or _blank(ask) or _synthetic(ask):
        return None
    return _encoded(ask)


def _decoded(content: object) -> object:
    if isinstance(content, str) and content.startswith(_JSON):
        try:
            return json.loads(content[len(_JSON):])
        except ValueError:
            return content
    return content


def _encoded(content: object) -> object:
    if isinstance(content, str):
        content = _SURROGATE.sub("�", content)
        if not content.startswith(_JSON):
            return content
    return _JSON + json.dumps(content)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _text(part: object) -> object:
    return part if isinstance(part, str) else part.get("text") if isinstance(part, dict) else None


def _with_text(part: object, text: str) -> object:
    return {**part, "text": text} if isinstance(part, dict) else text


def _joined(content: object) -> str:
    if isinstance(content, list):
        return "\n".join(t for t in map(_text, content) if isinstance(t, str) and t)
    return "" if content is None else content if isinstance(content, str) else str(content)


def _is_summary(content: object) -> bool:
    text = _joined(content).lstrip()
    if _DELIMITER in text:
        text = text.split(_DELIMITER, 1)[1].lstrip()
    return any(len(text) >= n and _digest(text[:n]) == digest for n, digest in _PREFIXES)


def _unwrapped(content: object) -> object | None:
    """What the handoff carries besides its summary: the prior tail before a delimiter, else what
    follows the end marker."""
    if isinstance(content, str):
        if _DELIMITER in content:
            prior = content.split(_DELIMITER, 1)[0].strip()
            if prior.startswith(_PRIOR):
                prior = prior[len(_PRIOR):].lstrip()
        elif _END in content:
            prior = content.split(_END, 1)[1].lstrip()
        else:
            prior = ""
        return prior or None
    if not isinstance(content, list):
        return None
    blocks = []
    for part in content:
        text = _text(part)
        if isinstance(text, str) and _DELIMITER in text:
            before = text.split(_DELIMITER, 1)[0]
            if before.strip():
                blocks.append(_with_text(part, before))
            break
        blocks.append(part)
    else:
        for at, part in enumerate(content):
            text = _text(part)
            if isinstance(text, str) and _END in text:
                rest = text.split(_END, 1)[1].lstrip()
                return [*([_with_text(part, rest)] if rest else []), *content[at + 1:]] or None
        return None
    for at, part in enumerate(blocks):
        text = _text(part)
        if isinstance(text, str) and text.lstrip().startswith(_PRIOR):
            leading = text.lstrip()[len(_PRIOR):].lstrip()
            if leading:
                blocks[at] = _with_text(part, leading)
            else:
                blocks.pop(at)
            break
    return blocks or None


def _blank(content: object) -> bool:
    if content is None or isinstance(content, str):
        return not (content or "").strip()
    if not isinstance(content, list):
        return False
    return all(not part.strip() if isinstance(part, str) else isinstance(part, dict)
               and part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str)
               and not part["text"].strip() for part in content)


def _synthetic(content: object) -> bool:
    text = _joined(content).strip()
    return _digest(text) in _SYNTHETIC or text.startswith(_SYNTHETIC_STARTS)
