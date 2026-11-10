"""Secret scrubbing (parked Q12, D Q4). One function, called in exactly two places: the
librarian before every model input, and record._codec when rendering ANY record text other
than raw/ parts (transcripts, Notes, Resume Notes, Journal, Profile, sent messages). Answers and
agent reads come from the record, so they are masked too. Raw parts stay verbatim (R2 Q4);
Forget is the remedy for a pasted secret. Detection is best effort.
Detectors: PEM private keys, whole or cut off; URLs with credentials; known key prefixes (sk-,
sk_live_, ghp_, glpat-, xox*, AKIA..., AIza, hf_, Telegram bot tokens); Authorization headers;
and the value after a name that says it is secret, either an environment variable such as
AWS_SECRET_ACCESS_KEY= or HF_TOKEN=, or words such as "password is" or "token:". A password
is masked when it has a digit or a symbol, an environment value when it has a digit, and any
other named value when it looks generated. A value ends at a space or where a mask begins, and
a mask is never read as a secret's name.
The detectors read a value whole, so they miss two names: one inside a value they leave bare
(`api key: pwd=<password>`), and one a mask takes whose value comes after the mask
(`<token>.pwd:<password>`). A second pass reads those. It only adds masks, so whatever the
detectors mask stays masked, and it reads every name as the detectors will read its own output,
so scrubbing twice changes nothing. A name inside a bare value of its own family is read to the
next , ; & ) ] or } of that value, and there a password needs a digit, as the other two rules
already ask: code and prose stay as written (`{auth:{pwd:form.v,ttl:0}}`). A mask can also start
a word, so a Telegram token right after one whose first digit is not ASCII (`]١23456789:...`) is
masked too, as the detectors would mask it on a second pass.
"""
from __future__ import annotations

import bisect
import functools
import math
import re
from collections import Counter
from typing import Callable, Iterator

_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----(?:.*?-----END \1PRIVATE KEY-----"
                  r"|(?:\r?\n[A-Za-z0-9+/=]{64})*(?:\r?\n[A-Za-z0-9+/=]{1,64})?)", re.S)
_URL_CREDENTIALS = re.compile(r"(?<![a-zA-Z0-9+.-])(?P<scheme>(?:\b[a-zA-Z]|(?>[a-zA-Z0-9+.-]*?[+.-][a-zA-Z]))"
                              r"[a-zA-Z0-9+.-]*+://)[^\s/:@\[\]]+:[^\s/@\[\]]+@")
_PREFIXED = (
    ("api key", re.compile(r"\bsk-(?:proj-|ant-[a-z0-9]+-)?[A-Za-z0-9_-]{20,}")),
    ("api key", re.compile(r"\b[rsp]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("gitlab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("aws key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("hugging face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}")),
)
_TELEGRAM_AFTER_MASK = re.compile(r"(?<=\])(?:\d{8,10}:(?>[A-Za-z0-9_-]{30,}))+")
MASK = re.compile(r"\[secret: [A-Za-z ]+\]")
_VALUE = r"""["']?(?P<value>[^\s"'`\[](?:(?!""" + MASK.pattern + r""")[^\s"'`])*)"""
_VALUE_AHEAD = r"""(?=["']?[^\s"'`\[])"""
_VALUE_END = re.compile(r"""[\s"'`]|""" + MASK.pattern)
_AUTHORIZATION = re.compile(r"(?i)(?P<name>\bauthorization\s*:\s*(?:bearer|basic|token)\s+|\bbearer\s+)"
                            r"(?P<value>[A-Za-z0-9._~+/=-]{12,})")
_ENVIRONMENT_NAME = (r"\b(?P<name>(?P<word>(?:[A-Z][A-Z0-9]*_)*(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD"
                     r"|PASS|PASSPHRASE|CREDENTIALS?|AUTH)(?:_[A-Z0-9]+)*)\s*=\s*)")
_ENVIRONMENT_WORDS = frozenset(("APIKEY", "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PWD", "PASS", "PASSPHRASE",
                                "CREDENTIAL", "CREDENTIALS", "AUTH"))
_ENVIRONMENT_PARTS = re.compile(r"[A-Z0-9]+(?:_[A-Z0-9]+)*")
_ENVIRONMENT_EQUALS = re.compile(r"\s*=\s*" + _VALUE_AHEAD)
_WORDS_NAME = (r"(?i)(?!(?<=\[)secret: [A-Za-z ]+\])\b(?P<name>(?P<word>api[_ -]?key|access[_ -]?key"
               r"|secret(?:[_ -]?key)?|token|password|passwd|passphrase|pwd|auth)\b[\"']?"
               r"(?:\s*[:=]\s*|\s+(?:is|was)(?:\s*:\s*|\s+)))")
_ENVIRONMENT = re.compile(_ENVIRONMENT_NAME + _VALUE)
_WORDS = re.compile(_WORDS_NAME + _VALUE)
_ENVIRONMENT_START = re.compile(r"\b(?P<word>[A-Z][A-Z0-9_]*)\s*=\s*" + _VALUE_AHEAD)
_WORDS_ONLY = re.compile(_WORDS_NAME + _VALUE_AHEAD)
_TRAILING = ".,;:)]}"
_FIELD_END = re.compile(r"[,;&)\]}]")
_WORD_RUN = re.compile(r"\w*")
_SCALE = 2 ** 40

Rules = tuple[str, ...]
Glued = tuple[int, Rules]
Reader = tuple[int, bool, Rules]
Mask = tuple[int, int, str]


def scrub(text: str) -> str:
    """Replace each detected secret with "[secret: <kind>]". Idempotent."""
    text = _PEM.sub("[secret: private key]", text)
    text = _URL_CREDENTIALS.sub(lambda m: f"{m['scheme']}[secret: password in URL]@", text)
    glued: list[Glued] = []
    for kind, pattern in _PREFIXED:
        text, glued = _sub(pattern, text, glued, lambda m, mask=f"[secret: {kind}]": (m.start(), m.end(), mask))
    text, glued = _sub(_AUTHORIZATION, text, glued, lambda m: (*m.span("value"), "[secret: token]"))
    text, glued = _sub(_ENVIRONMENT, text, glued, lambda m: _masked(m, _rules(m["name"], "environment")))
    text, glued = _sub(_WORDS, text, glued, lambda m: _masked(m, _rules(m["word"], "words")))
    text, glued = _sub(_TELEGRAM_AFTER_MASK, text, glued, lambda m: (*m.span(), "[secret: telegram bot token]"))
    return _second_pass(text, glued)


def _sub(pattern: re.Pattern, text: str, glued: list[Glued],
         mask: Callable[[re.Match], tuple[int, int, str] | None]) -> tuple[str, list[Glued]]:
    """`pattern.sub`, writing the mask that `mask` gives a match over its start..end. Returns the
    glued values carried over to the new text, with those whose name a new mask took."""
    if (first := pattern.search(text)) is None:
        return text, glued
    out, done, edits = [], 0, []
    for m in pattern.finditer(text, first.start()):
        if (edit := mask(m)) is None:
            continue
        start, end, replacement = edit
        out += (text[done:start], replacement)
        edits.append((start, end, len(replacement) - (end - start)))
        if (value := _glued(text, start, m.end())) is not None:
            glued.append(value)
        done = end
    if not edits:
        return text, glued
    out.append(text[done:])
    moved, shift, i = [], 0, 0
    for at, rules in sorted(glued):
        while i < len(edits) and edits[i][1] <= at:
            shift += edits[i][2]
            i += 1
        if i == len(edits) or at < edits[i][0]:
            moved.append((at + shift, rules))
    return "".join(out), moved


def _masked(m: re.Match, rules: Rules) -> tuple[int, int, str] | None:
    """The mask for the value after a secret's name, when it looks like one. A full stop or a
    closing bracket after it stays."""
    value = m["value"].rstrip(_TRAILING)
    if (kind := _kind_of(value, rules)) is None:
        return None
    return m.start("value"), m.start("value") + len(value), f"[secret: {kind}]"


@functools.lru_cache(maxsize=1024)
def _rules(name: str, family: str) -> Rules:
    return ("password",) if any(word in name.lower() for word in ("pass", "pwd")) else (family,)


def _kind(rules: Rules, length: int, value: _Text | _Window, nested: bool = False) -> str | None:
    """The kind of secret `value`, of `length` characters, is by one of `rules`, if any. A `nested`
    value, one inside a value left bare, needs a digit to be a password."""
    mark = str.isdigit if nested else _not_alpha
    if "password" in rules and length >= 6 and value.has(mark) and value.has(str.isalnum):
        return "password"
    if "environment" in rules and length >= 8 and value.has(str.isdigit):
        return "token"
    if "words" in rules and length >= 20 and value.has(str.isdigit) and value.has(str.isalpha) and value.random():
        return "token"
    return None


def _kind_of(value: str, rules: Rules) -> str | None:
    return _kind(rules, len(value), _Text(value))


class _Text:
    def __init__(self, value: str):
        self.value = value

    def has(self, test: Callable[[str], bool]) -> bool:
        return any(map(test, self.value))

    def random(self) -> bool:
        return _entropy(self.value) >= 3.5


def _not_alpha(c: str) -> bool:
    return not c.isalpha()


def _entropy(value: str) -> float:
    """Bits per character. About 3.5 or more looks like a generated key."""
    counts = Counter(value)
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def _glued(text: str, start: int, end: int) -> Glued | None:
    """The value of a name that starts inside a mask over start..end and whose value begins after
    `end`. Such a name starts in the span's last few characters, or is an environment variable
    that ends at `end` or just before an "=" that ends the span."""
    if not (text[end - 1] in ":=" or end < len(text) and (text[end] in "\"':=" or text[end].isspace())):
        return None
    found: dict[int, Rules] = {}
    for stop in (end, end - 1) if end - 1 > start and text[end - 1] == "=" else (end,):
        if (m := _ENVIRONMENT_EQUALS.match(text, stop)) and (value := _value_start(text, m.end())) >= end:
            run = stop - _WORD_RUN.match(text[start:stop][::-1]).end()
            word = text[run:stop]
            if (run > start or start == 0 or not _is_word(text[start - 1])) and _environment_word(word):
                found[value] = _rules(word, "environment")
                break
    for name_start in range(max(start, end - 11), end):
        if (m := _WORDS_ONLY.match(text, name_start)) and (value := _value_start(text, m.end())) >= end:
            found[value] = tuple(sorted({*found.get(value, ()), *_rules(m["word"], "words")}))
            break
    return min(found.items()) if found else None


def _is_word(c: str) -> bool:
    return c.isalnum() or c == "_"


def _environment_word(word: str) -> bool:
    """`word` is a name `_ENVIRONMENT_NAME` reads."""
    if not _ENVIRONMENT_PARTS.fullmatch(word):
        return False
    for part in word.split("_"):
        if part in _ENVIRONMENT_WORDS:
            return True
        if not "A" <= part[0] <= "Z":
            return False
    return False


def _value_start(text: str, at: int) -> int:
    return at + (text[at] in "\"'")


def _stripped(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1] in _TRAILING:
        end -= 1
    return end


def _second_pass(text: str, glued: list[Glued]) -> str:
    """Mask the values of the names the detectors skip. Names are read in the order of their values,
    one run of text at a time (a value runs to a space, a quote or a mask), as the detectors will
    read the output. A name that starts inside a bare value of its own family is nested; every other
    name keeps the detectors' rule, as on its own: a name a family's detector reaches, and a name a
    mask took."""
    names = sorted([*_names(text), *((value, -1, 2, rules) for value, rules in glued)])
    reached = [0, 0]
    masks: list[Mask] = []
    i = 0
    while i < len(names):
        end = m.start() if (m := _VALUE_END.search(text, names[i][0] + 1)) else len(text)
        readers: list[Reader] = []
        while i < len(names) and names[i][0] < end:
            value, name, family, rules = names[i]
            i += 1
            if family == 2 or masks and masks[-1][1] > name:
                readers.append((value, True, rules))
            elif name < reached[family]:
                readers.append((value, False, rules))
            else:
                readers.append((value, True, rules))
                reached[family] = end
        masks += _run_masks(text, readers, end)
    out, done = [], 0
    for start, stop, kind in masks:
        out += (text[done:start], f"[secret: {kind}]")
        done = stop
    return "".join(out) + text[done:]


def _names(text: str) -> Iterator[tuple[int, int, int, Rules]]:
    """Every environment and words name with a value after it, as (value, name, family, rules)."""
    for m in _ENVIRONMENT_START.finditer(text):
        if _environment_word(m["word"]):
            yield _value_start(text, m.end()), m.start(), 0, _rules(m["word"], "environment")
    for m in _WORDS_ONLY.finditer(text):
        yield _value_start(text, m.end()), m.start(), 1, _rules(m["word"], "words")


def _run_masks(text: str, readers: list[Reader], end: int) -> list[Mask]:
    """The masks for the values that start in one run of text, which ends at `end`. The first value
    that looks secret is masked to the end of the run, as the detectors mask a value. Each value
    before it now ends at that mask, so they are read again, from the last back, and each one masked
    cuts the ones before it again."""
    readers.sort(reverse=True)
    low = readers[-1][0]
    stop = _stripped(text, low, end)
    fields: list[int] = []
    field = -1
    window = _Window(text, -1)
    first = None
    for value, main, rules in readers:
        if main:
            kind = _kind_of(text[value:stop], rules)
        else:
            fields = fields or [m.start() for m in _FIELD_END.finditer(text, low, end)] or [end]
            if field != (field := _field_end(fields, value, end)):
                window = _Window(text, _stripped(text, low, field))
            kind = window.kind(value, rules)
        if kind:
            first = value, kind
    if first is None:
        return []
    start, kind = first
    masks = [(start, stop, kind)]
    cut = _stripped(text, low, start)
    window = _Window(text, cut)
    for value, main, rules in readers:
        if value >= cut or not main and _field_end(fields, value, end) < cut:
            continue
        if kind := _kind_of(text[value:cut], rules) if main else window.kind(value, rules):
            masks.append((value, cut, kind))
            cut = _stripped(text, low, value)
            window = _Window(text, cut)
    return masks[::-1]


def _field_end(fields: list[int], value: int, end: int) -> int:
    i = bisect.bisect_left(fields, value)
    return fields[i] if i < len(fields) else end


class _Window:
    """The values text[start:stop] nested in a bare value, for one stop and falling starts, each
    read in time that adds up to the characters between one start and the next."""

    def __init__(self, text: str, stop: int):
        self.text, self.stop = text, stop
        self.start = self.seen = self.counted = stop
        self.found: dict[Callable[[str], bool], tuple[int, bool]] = {}
        self.distinct: set[str] = set()
        self.counts: dict[str, int] = {}
        self.total = 0

    def kind(self, start: int, rules: Rules) -> str | None:
        self.start = start
        return _kind(rules, self.stop - start, self, nested=True)

    def has(self, test: Callable[[str], bool]) -> bool:
        low, found = self.found.get(test, (self.stop, False))
        if not found and low > self.start:
            found = any(map(test, self.text[self.start:low]))
            self.found[test] = self.start, found
        return found

    def random(self) -> bool:
        """About 3.5 bits per character or more, from counts moved to the value's start. The sum of
        n log n is a whole number, so the answer depends on the value alone. A value of fewer than
        12 distinct characters has at most log2(11) bits, under 3.5, so it is not counted."""
        self.distinct.update(self.text[self.start:self.seen])
        self.seen = self.start
        if len(self.distinct) < 12:
            return False
        chunk, counts, total = self.text[self.start:self.counted], self.counts, self.total
        for c, k in Counter(chunk).items() if len(chunk) > 64 else zip(chunk, _ONES):
            n = counts.get(c, 0)
            counts[c] = n + k
            total += _n_log_n(n + k) - _n_log_n(n)
        self.counted, self.total = self.start, total
        length = self.stop - self.start
        return math.log2(length) - total / _SCALE / length >= 3.5 - 1e-9


_ONES = (1,) * 64


@functools.lru_cache(maxsize=4096)
def _n_log_n(n: int) -> int:
    return int(n * math.log2(n) * _SCALE) if n > 1 else 0
