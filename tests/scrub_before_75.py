"""privacy.scrub as main had it at 9e00865, before #75 read the names its detectors skip. The
differential fuzz in test_scrub checks that privacy.scrub masks everything this one does."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable

_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----(?:.*?-----END \1PRIVATE KEY-----"
                  r"|(?:\r?\n[A-Za-z0-9+/=]{64})*(?:\r?\n[A-Za-z0-9+/=]{1,64})?)", re.S)
_URL_CREDENTIALS = re.compile(r"(?P<scheme>\b[a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/:@\[\]]+:[^\s/@\[\]]+@")
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
MASK = re.compile(r"\[secret: [A-Za-z ]+\]")
_VALUE = r"""["']?(?P<value>[^\s"'`\[](?:(?!""" + MASK.pattern + r""")[^\s"'`])*)"""
_AUTHORIZATION = re.compile(r"(?i)(?P<name>\bauthorization\s*:\s*(?:bearer|basic|token)\s+|\bbearer\s+)"
                            r"(?P<value>[A-Za-z0-9._~+/=-]{12,})")
_ENVIRONMENT = re.compile(r"\b(?P<name>(?:[A-Z][A-Z0-9]*_)*(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|PASS"
                          r"|PASSPHRASE|CREDENTIALS?|AUTH)(?:_[A-Z0-9]+)*\s*=\s*)" + _VALUE)
_WORDS = re.compile(r"(?i)(?!(?<=\[)secret: [A-Za-z ]+\])\b(?P<name>(?P<word>api[_ -]?key|access[_ -]?key"
                    r"|secret(?:[_ -]?key)?|token|password|passwd|passphrase|pwd|auth)\b[\"']?"
                    r"(?:\s*[:=]\s*|\s+(?:is|was)(?:\s*:\s*|\s+)))" + _VALUE)
_TRAILING = ".,;:)]}"


def scrub(text: str) -> str:
    """Replace each detected secret with "[secret: <kind>]". Idempotent."""
    text = _PEM.sub("[secret: private key]", text)
    text = _URL_CREDENTIALS.sub(lambda m: f"{m['scheme']}[secret: password in URL]@", text)
    for kind, pattern in _PREFIXED:
        text = pattern.sub(f"[secret: {kind}]", text)
    text = _AUTHORIZATION.sub(lambda m: m["name"] + "[secret: token]", text)
    text = _ENVIRONMENT.sub(lambda m: _masked(m, m["name"], lambda v: len(v) >= 8 and any(map(str.isdigit, v))), text)
    return _WORDS.sub(lambda m: _masked(m, m["word"], lambda v: len(v) >= 20 and _random(v)), text)


def _masked(m: re.Match, name: str, secret: Callable[[str], bool]) -> str:
    """The value after a secret's name, masked when it looks like one. A full stop or a closing
    bracket after it stays."""
    value = m["value"].rstrip(_TRAILING)
    if any(word in name.lower() for word in ("pass", "pwd")):
        kind = "password" if len(value) >= 6 and not value.isalpha() and any(c.isalnum() for c in value) else None
    else:
        kind = "token" if secret(value) else None
    if kind is None:
        return m[0]
    return m[0][: m.start("value") - m.start()] + f"[secret: {kind}]" + m["value"][len(value):]


def _random(value: str) -> bool:
    """Looks like a generated key: mixed letters and digits, and about 3.5 bits per character or more."""
    counts = Counter(value)
    entropy = -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())
    return entropy >= 3.5 and any(c.isdigit() for c in value) and any(c.isalpha() for c in value)
