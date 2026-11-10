"""Text an agent in a Harness wrote, as Atlas lets it into the record and in front of the owner.

Every piece of agent-supplied text passes `one_line` where it enters Atlas (agents), so a Note body,
the owner's words a refused update quotes, a Decided line, and the "apply" offer built from them are
one masked line that cannot end the quotation Atlas puts it in, start a line of its own, or read as
Atlas's reply or one of its marks.
"""
from __future__ import annotations

import re
import unicodedata

from . import privacy

_REORDERS = frozenset(map(chr, (0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))))
_QUOTE_NAMES = ("QUOTATION", "PRIME", "GERESH", "GERSHAYIM", "DITTO", "APOSTROPHE", "ACUTE", "GRAVE",
                "TURNED COMMA", "REVERSED COMMA")
_SPEAKER = re.compile(r"(?<![^\W_])(atlas|you)\s*:", re.I)
_WORD = re.compile(r"[^\W_]+")


class Refused(ValueError):
    """Text Atlas does not take from an agent; the message says why."""


def one_line(value: object, most: int, what: str) -> str:
    """`value` as Atlas shows an agent's text: NFKC-normalised; without format characters (zero
    width, U+FEFF, the soft hyphen); on one line; every quotation mark, however written, as an
    apostrophe; every opening and closing bracket as "(" and ")" (masks excepted); masked; and
    "Atlas:" or "You:" as "Atlas -". Refused when it is not text, is empty, is longer than `most`
    characters, holds a control character other than a tab or a line break or a character that
    reorders text, or has a word that mixes Latin letters with letters of another script."""
    if not isinstance(value, str) or not value.strip():
        raise Refused(f"{what} must be text.")
    if any(c in _REORDERS or (unicodedata.category(c) == "Cc" and c not in "\t\n\r") for c in value):
        raise Refused(f"{what} holds a control character.")
    if len(value) > most:
        raise Refused(f"{what} is longer than {most} characters.")
    text = "".join(map(_shown, unicodedata.normalize("NFKC", value)))
    if any(_mixed(w) for w in _WORD.findall(text)):
        raise Refused(f"{what} mixes Latin letters with letters of another script in one word.")
    text = " ".join(privacy.scrub(text).split())
    if not text:
        raise Refused(f"{what} must be text.")
    masks = privacy.MASK.findall(text)
    parts = privacy.MASK.split(text)
    shown = [_SPEAKER.sub(lambda m: m[1] + " -", p) for p in parts]
    return "".join(s + (masks[i] if i < len(masks) else "") for i, s in enumerate(shown))


def _shown(c: str) -> str:
    kind = unicodedata.category(c)
    if c in "\t\n\r":
        return " "
    if kind in ("Cf", "Cc"):
        return ""
    if c in "\"`" or kind in ("Pi", "Pf") or ((kind == "Lm" or kind[0] not in "LN") and any(
            n in unicodedata.name(c, "") for n in _QUOTE_NAMES)):
        return "'"
    if (kind[0] not in "LN" and "COLON" in unicodedata.name(c, "").split()) or c == "\u2236":
        return ":"
    if kind == "Ps":
        return "("
    if kind == "Pe":
        return ")"
    return c


def _mixed(word: str) -> bool:
    scripts = {unicodedata.name(c, "?").split()[0] for c in word if unicodedata.category(c)[0] == "L"}
    return "LATIN" in scripts and len(scripts) > 1
