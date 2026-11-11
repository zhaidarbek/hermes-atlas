"""How a message Atlas composed looks in Telegram. Pure. The plain text stays the message: the
record, the CLI and every other reader keep it, and `plain(styled(text, kind)) == text`.

Hermes v0.23.0 carries markup two ways, and each message kind goes the way it leaves:
  BRIEF, REVIEW  `hermes send`. Text holding an HTML tag is sent with parse mode HTML as it is
                 (tools/send_message_senders.py, _telegram_format), so a brief is HTML: every
                 `&`, `<` and `>` of the text escaped, and Atlas's own <b>, <i> and <code>.
  ANSWER         the gateway sends a hook's reply through TelegramAdapter.send, which reads it as
                 Markdown and writes MarkdownV2 (plugins/platforms/telegram/adapter.py,
                 format_message), escaping whatever it reads as text. A late answer goes by
                 `hermes send` the same way, as it holds no tag. Markdown has no escape that
                 format_message keeps, so every character of the text that it would read as
                 markup is neutralised, invisibly where it can be: a word joiner (U+2060) breaks
                 `](`, `~~`, `||`, `$$`, a `<` before a letter, a `#` or `>` that begins a line, a
                 task box and every `|`; `*` and a backtick become ∗ and ˋ. A word joiner escapes
                 a word joiner, ∗ or ˋ the text already held.
A message whose markup would not fit one Telegram message, or that has nothing to style, is sent
with no markup, neutralised as an answer is, and Hermes sends it as Markdown.
"""
from __future__ import annotations

import re
from enum import Enum


LIMIT = 4096                                  # one Telegram message, in UTF-16 code units


class Kind(str, Enum):
    BRIEF = "brief"; REVIEW = "review"; ANSWER = "answer"


TITLE = {Kind.BRIEF: "🧭", Kind.REVIEW: "🗓️"}
HEADINGS = {                                  # the heading as composed: its emoji and how Telegram shows it
    "YESTERDAY'S STEPS": ("↩️", "Yesterday's steps"), "STEPS": ("👣", "Steps"),
    "NEEDS A DECISION": ("⚖️", "Needs a decision"), "FINISHED": ("🏁", "Finished"), "DECIDED": ("📌", "Decided"),
    "CHANGED": ("🔄", "Changed"), "WHAT I DID ON MY OWN": ("🤖", "What I did on my own"), "FOCUS": ("🎯", "Focus"),
    "ENDINGS": ("🔚", "Endings"), "REOPENING": ("♻️", "Reopening"), "PARKED": ("🅿️", "Parked"),
    'WAITING FOR "apply"': ("⏳", 'Waiting for "apply"'), "IMPORTS": ("📥", "Imports"),
    "TIME PROPOSAL": ("⏰", "Time proposal")}
COMPOSED = {shown: heading for heading, shown in HEADINGS.items()}
"""HEADINGS the other way round: a shown heading, with its emoji, as the plain text has it."""
OUTCOMES = (("Step done.", "✅"), ("Step skipped.", "⏭️"), ("parked.", "🅿️"), ("dropped.", "🗑️"),
            ("done.", "🏁"), ("I'll suggest this Step again on ", "⏰"), ("I'll ask about this again on ", "⏰"))
DECORATIONS = frozenset([*TITLE.values(), *(e for e, _ in HEADINGS.values()), *(e for _, e in OUTCOMES)])
BRIEF_LABELS = ("Left off", "Resume", "Last")
ANSWER_LABELS = ("Where", "Left off", "Decided", "Open question", "Sessions to reopen", "Next Step", "Pursuit",
                 "Last", "Resume")

JOIN = "\n    "                               # a padded Step line, shown as its name over its Step
WJ = "\u2060"                                 # word joiner: invisible
SPACER = WJ                                   # an HTML line of its own: the blank line before a Step
LOOKALIKE = {"*": "∗", "`": "ˋ"}

Span = tuple[str, str]  # (style, text): "" plain, "b", "i", "code", "heading", "deco", "join", "spacer"
Line = list[Span]


def styled(text: str, kind: Kind) -> str:
    """`text` with Telegram markup for its kind, or, past one message, neutralised with none."""
    lines = _lines(text, kind)
    if kind is Kind.ANSWER:
        shown = _markdown(lines)
        return shown if _mdv2_units(lines) <= LIMIT else _markdown(_bare(text))
    shown = _html(lines)
    return shown if _ATLAS_TAG.search(shown) and _units(shown) <= LIMIT else _markdown(_bare(text))


def plain(shown: str) -> str:
    """The text a styled message was made from."""
    if _ATLAS_TAG.search(shown):
        return _from_html(shown)
    return _from_markdown(shown)


def _units(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


# ---- what to style --------------------------------------------------------------------------

_TITLE = re.compile(r"(Atlas · [^·\n]+?)( · .+)")
_HEADING = re.compile("(" + "|".join(re.escape(h) for h in sorted(HEADINGS, key=len, reverse=True)) + r")( \(.*\))?")
_PADDED_STEP = re.compile(r"(\d+ · .+?)( {2,})(\S.*)")
_STEP = re.compile(r"\d+ · .+")
_FOLLOWUP = re.compile(r"( +[✓·] )(.+?)(: .*)")
_QUIET = re.compile(r"( *(?:- )?)(.+?)( · [\w-]+ · quiet \d+ days.*)")
_NAMED_ITEM = re.compile(r"(- )(.+?)(: .*)")
_HINT = re.compile(r"( *)(Reply .*|Continue, .*\?|Move the Daily Brief .*)")
_RESUMING = re.compile(r'(Resuming ")(.*)("\.)')
_MARK = re.compile(r"\[[FI](?: [^\]\n]*)?\]")
_TAIL_HINT = re.compile(r'(?<= )Reply ".*')
_WORD = re.compile(r"\S+")
_URL = re.compile(r"[a-z][a-z0-9+.-]*://[\w\-.~:/?#@!&'()+,;=%]+")
_PATH = re.compile(r"[\w.~-]*/[\w./~-]*")
_FILE = re.compile(r"[\w-]+\.(?:py|js|ts|md|txt|json|ya?ml|toml|csv|pdf|sh|go|rs|sql|log|ini|cfg|lock|html|css|gpx)")
_HOST = re.compile(r"[\w.-]+")


def _lines(text: str, kind: Kind) -> list[Line]:
    return [_joined(line) for line in _styled_lines(text, kind)]


def _joined(line: Line) -> Line:
    """Neighbouring spans of one style as one: Markdown reads `*a**b*` as bold."""
    out: Line = []
    for style, text in line:
        if out and style == out[-1][0] and style in ("", "b", "i", "code"):
            out[-1] = (style, out[-1][1] + text)
        elif text or style not in ("", "b", "i", "code"):
            out.append((style, text))
    return out


def _styled_lines(text: str, kind: Kind) -> list[Line]:
    out: list[Line] = []
    section = None
    for n, line in enumerate(text.split("\n")):
        if kind is Kind.ANSWER:
            out.append(_answer_line(line))
            continue
        if n == 0 and (m := _TITLE.fullmatch(line)):
            out.append([("deco", TITLE[kind]), ("b", m[1]), ("", m[2])])
        elif (m := _HEADING.fullmatch(line)):
            section = m[1]
            out.append([("deco", HEADINGS[m[1]][0]), ("heading", m[1]), ("", m[2] or "")])
        elif kind is Kind.BRIEF and (m := _PADDED_STEP.fullmatch(line) or _STEP.fullmatch(line)):
            if out and out[-1] and out[-1][0][0] != "deco":
                out.append([("spacer", "")])
            out.append([("b", m[1]), ("", m[2]), ("join", ""), *_inline(m[3])] if m.lastindex else [("b", line)])
        else:
            out.append(_item_line(line, section))
    return out


def _item_line(line: str, section: str | None) -> Line:
    if m := _HINT.fullmatch(line):
        return [("", m[1]), ("i", m[2])]
    if m := _FOLLOWUP.fullmatch(line):
        return [("", m[1]), ("b", m[2]), *_inline(m[3])]
    if m := _QUIET.fullmatch(line):
        return [("", m[1]), ("b", m[2]), *_inline(m[3])]
    if section in ("DECIDED", "ENDINGS") and (m := _NAMED_ITEM.fullmatch(line)):
        return [("", m[1]), ("b", m[2]), *_inline(m[3])]
    indent = line[:len(line) - len(line.lstrip(" "))]
    rest = line[len(indent):]
    for label in BRIEF_LABELS:
        if rest.startswith(label + ": "):
            return [("", indent), ("i", label + ":"), *_inline(rest[len(label) + 1:])]
    return [("", indent), *_inline(rest)] if indent else _inline(rest)


def _answer_line(line: str) -> Line:
    if m := _RESUMING.fullmatch(line):
        return [("", m[1]), ("b", m[2]), ("", m[3])]
    if m := _QUIET.fullmatch(line):
        return [("", m[1]), ("b", m[2]), *_inline(m[3])]
    for label in ANSWER_LABELS:
        if line.startswith(label + ": "):
            return [("b", label + ":"), *_inline(line[len(label) + 1:])]
    name, sep, said = line.partition(": ")
    for start, emoji in OUTCOMES:
        if sep and name and said.startswith(start) and not name.startswith(" "):
            return [("deco", emoji), ("b", name), *_inline(sep + said)]
    return _inline(line)


def _inline(text: str) -> Line:
    """Evidence marks and a trailing reply hint in italic, places and names of things in code."""
    spans: Line = []
    hint = _TAIL_HINT.search(text)
    body, tail = (text[:hint.start()], text[hint.start():]) if hint else (text, "")
    at = 0
    for m in _MARK.finditer(body):
        spans += _coded(body[at:m.start()])
        spans.append(("i", m[0]))
        at = m.end()
    spans += _coded(body[at:])
    if tail:
        spans.append(("i", tail))
    return [s for s in spans if s[1]]


def _coded(text: str) -> Line:
    """Paths, file names, URLs, branches and the host before a path, each in code: Telegram then
    shows it as typed and links none of it."""
    spans: Line = []
    at = 0
    words = list(_WORD.finditer(text))
    for n, m in enumerate(words):
        word = m[0]
        lead = len(word) - len(word.lstrip("(\"'"))
        token = word[lead:].rstrip(".,;:!?)\"'")
        after = words[n + 1][0] if n + 1 < len(words) else ""
        if not _coded_token(token, words[n - 1][0] if n else "", word, after):
            continue
        start = m.start() + lead
        spans += [("", text[at:start]), ("code", token)]
        at = start + len(token)
    spans.append(("", text[at:]))
    return spans


def _coded_token(token: str, before: str, word: str, after: str) -> bool:
    if not token or re.search(r"[*`\u2060∗ˋ\\<>]", token) or not re.search(r"[^\W\d_]", token):
        return False
    return bool(_URL.fullmatch(token) or _PATH.fullmatch(token) or _FILE.fullmatch(token)
                or before == "branch" and re.fullmatch(r"[\w./-]+", token)
                or word.endswith(",") and after[:1] in "~/" and after and _HOST.fullmatch(token))


def _bare(text: str) -> list[Line]:
    return [[("", line)] for line in text.split("\n")]


# ---- HTML (`hermes send`) ---------------------------------------------------------------------

_ATLAS_TAG = re.compile(r"</?(?:b|i|code)>")
_DECORATION = "|".join(re.escape(e) for e in sorted(DECORATIONS, key=len, reverse=True))
_HTML_DECORATED = re.compile(rf"^(?:{_DECORATION}) (?=<b>)", re.M)
_HTML_JOINED = re.compile(r"(</b> {2,})" + re.escape(JOIN))
_SPACER = re.compile(rf"^{SPACER}\n", re.M)
_HTML_HEADING = re.compile("^(" + "|".join(re.escape(e) for e, _ in HEADINGS.values()) + ") <b>("
                           + "|".join(re.escape(h) for _, h in HEADINGS.values())
                           + ")</b>", re.M)
_ENTITY = re.compile(r"&(amp|lt|gt|#8288);")
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "#8288": WJ}


def _html(lines: list[Line]) -> str:
    return "\n".join("".join(_html_span(s) for s in line) for line in lines)


def _html_span(span: Span) -> str:
    style, text = span
    if style == "deco":
        return text + " "
    if style == "join":
        return JOIN
    if style == "spacer":
        return SPACER
    if style == "heading":
        style, text = "b", HEADINGS[text][1]
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(WJ, "&#8288;")
    return f"<{style}>{escaped}</{style}>" if style else escaped


def _from_html(shown: str) -> str:
    text = _HTML_HEADING.sub(lambda m: COMPOSED[(m[1], m[2])], _SPACER.sub("", shown))
    text = _HTML_JOINED.sub(r"\1", _HTML_DECORATED.sub("", text))
    return _ENTITY.sub(lambda m: _ENTITIES[m[1]], _ATLAS_TAG.sub("", text))


# ---- Markdown (the gateway's answers, and a message sent bare) ---------------------------------

_MD_DECORATED = re.compile(rf"^(?:{_DECORATION}) (?=\*\*)", re.M)
_MDV2_SPECIAL = frozenset("_*[]()~`>#+-=|{}.!\\")
_TASK_BOX = re.compile(r"\s*[-*+]\s+")
_LINE_OPENS = re.compile(r"[*\s]*")


def _markdown(lines: list[Line]) -> str:
    return "\n".join("".join(_markdown_line(line)) for line in lines)


def _markdown_line(line: Line) -> list[str]:
    parts: list[str] = []
    for style, text in line:
        if style == "deco":
            parts.append(text + " ")
        elif style == "code":
            parts.append(f"`{text}`")
        elif style != "join":
            mark = {"b": "**", "i": "*"}.get(style, "")
            parts.append(mark + _neutral(text, "".join(parts) + mark) + mark)
    return parts


def _neutral(text: str, before: str) -> str:
    """`text` as format_message reads it as text alone; `before` is what precedes it on its line."""
    out = []
    for n, ch in enumerate(text):
        prior = text[n - 1] if n else ""
        if ch in (WJ, "∗", "ˋ"):
            out.append(WJ + ch)
            continue
        if (ch == "(" and prior == "]" or ch in "~$" and prior == ch or ch == "|"
                or ch in "#>" and _LINE_OPENS.fullmatch(before + text[:n])
                or ch == "[" and _TASK_BOX.fullmatch(before + text[:n])
                or prior == "<" and (ch.isascii() and ch.isalpha() or ch == "/")):
            out.append(WJ)
        out.append(LOOKALIKE.get(ch, ch))
    return "".join(out)


def _from_markdown(shown: str) -> str:
    text = _MD_DECORATED.sub("", shown).replace("*", "").replace("`", "")
    out, n = [], 0
    while n < len(text):
        ch = text[n]
        if ch == WJ:
            if n + 1 < len(text) and text[n + 1] in (WJ, "∗", "ˋ"):
                out.append(text[n + 1])
                n += 2
            else:
                n += 1
            continue
        out.append({"∗": "*", "ˋ": "`"}.get(ch, ch))
        n += 1
    return "".join(out)


def _mdv2_units(lines: list[Line]) -> int:
    """The UTF-16 length of what format_message makes of `_markdown(lines)`: a special character
    of text escaped by one backslash, `**` written `*`, and a backslash in code doubled."""
    total = max(len(lines) - 1, 0)
    for line in lines:
        for (style, text), part in zip([s for s in line if s[0] != "join"], _markdown_line(line)):
            if style == "code":
                total += _units(part) + text.count("\\")
            elif style == "deco":
                total += _units(part)
            else:
                inner = part[2:-2] if style == "b" else part[1:-1] if style == "i" else part
                total += _units(inner) + sum(ch in _MDV2_SPECIAL for ch in inner) + (2 if style else 0)
    return total
