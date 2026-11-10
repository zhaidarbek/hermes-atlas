"""The change an agent's `atlas_update` makes, read from the owner's own message, never from the
agent's text. Pure: a Pursuit, the Pursuits listing the calling Session, the owner's message and
what the agent asked for in, an EditResumeNote or the reason it is not the owner's out.

The agent only asks: remove the Open question, add the owner's words as a decision, or both.
- The Decided line is the owner's message itself, its address taken off its start (ADDRESSES),
  first letter capitalised, ending in a full stop, on one line, at most DECIDED_MAX characters.
- One Open question is removed only when the owner's message tells Atlas outright that a question
  is closed (closes_question) and the Pursuit has exactly one.
- The Pursuit must list the calling Session; when more than one does, the message must name it.
A refused update keeps, for "apply", a change under the same limits (offered), which the offer
shows whole.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .librarian import TEXT_MAX
from .model import EditResumeNote, Pursuit, PursuitId
from .rules import named, words

DECIDED_MAX = TEXT_MAX
ADDRESSES = ("please tell atlas that", "please tell atlas", "tell atlas that", "tell atlas", "hey atlas")
"""The ways of addressing Atlas taken off the start of the owner's message, with any , : ; - and
spaces after them; a bare "atlas" too, but only before one of those marks ("atlas, ...")."""
CLOSED = frozenset({"settled", "answered", "closed", "resolved", "done"})
NEGATED = frozenset({"not", "no", "never", "nothing", "yet", "isn", "hasn", "wasn", "aren", "haven", "don",
                     "doesn", "didn", "unsettled", "unanswered", "unresolved"})
_ADDRESS = re.compile(r"^\s*(?:(?:" + "|".join(a.replace(" ", r"\s+") for a in ADDRESSES) + r")(?![^\W_])"
                      r"|atlas(?=\s*[,:;-]))[\s,:;-]*", re.I)


@dataclass(frozen=True)
class Asked:
    """What an agent asked atlas_update for, which selects nothing by itself."""
    pursuit: PursuitId
    remove: str | None                      # the agent's words for the Open question, for its answer only
    decide: bool                            # add the owner's message as a decision


def decision(message: str) -> str | None:
    """The owner's message as a Decided line, or None when nothing is left once the address is off,
    or it is longer than DECIDED_MAX or not one line."""
    rest = _ADDRESS.sub("", message, count=1).strip()
    if not rest:
        return None
    rest = rest[:1].upper() + rest[1:] + ("" if rest[-1] in ".!?" else ".")
    return rest if len(rest) <= DECIDED_MAX and len(rest.splitlines()) == 1 else None


_CLOSES = re.compile(r"(?:(?:the|that|this|my)\s+)?(?P<whose>[^\W_][\w\s-]{0,80}?\s+)?questions?\s+"
                     r"(?:is|are|was|were|has\s+been|have\s+been)\s+(?:now\s+|all\s+)?(?:%s)(?![^\W_])\s*(?:[.!:;,-].*)?"
                     % "|".join(sorted(CLOSED)), re.I | re.S)


def closes_question(message: str, name: str = "") -> bool:
    """Whether the owner's message tells Atlas outright that a question is closed: addressed to
    Atlas, then "the open question is settled", alone or before the owner's own words after a
    mark. Between "the" and "question" stand only "open" and words of the Pursuit's `name`. A
    question to Atlas, a condition ("once the question is settled"), a question that will be
    settled, a quoted line and another Pursuit's question are not that."""
    address = _ADDRESS.match(message)
    if address is None or not address.group().strip() or "?" in message or NEGATED & set(words(message)):
        return False
    m = _CLOSES.fullmatch(message[address.end():].strip())
    return m is not None and set(words(m["whose"] or "")) <= {"open"} | set(words(name))


def names(message: str, name: str) -> bool:
    """Whether the message holds every word of the Pursuit's name, case ignored."""
    return set(words(name)) <= set(words(message))


def owners(p: Pursuit, listing: Sequence[Pursuit], message: str, asked: Asked) -> EditResumeNote | str:
    """The change the owner's own `message` makes to `p`, or why it makes none: NOT_LISTED (the
    Pursuit does not list the calling Session), UNNAMED (more than one Pursuit lists it and the
    message does not name this one alone), NOT_CLOSED (a removal the message does not say, or not
    exactly one Open question), NO_DECISION (no words left for a decision, or the message asks a question)."""
    if p.id not in {q.id for q in listing}:
        return "NOT_LISTED"
    if len(listing) > 1 and [q.id for q in listing if names(message, q.name)] != [p.id]:
        return "UNNAMED"
    remove: tuple[str, ...] = ()
    if asked.remove is not None:
        if not closes_question(message, p.name) or len(p.resume.open_questions) != 1:
            return "NOT_CLOSED"
        remove = (p.resume.open_questions[0].text,)
    decided = decision(message) if asked.decide and "?" not in message else None
    if asked.decide and decided is None:
        return "NO_DECISION"
    return EditResumeNote(p.id, remove, (decided,) if decided else ())


def offered(p: Pursuit, message: str, asked: Asked) -> EditResumeNote | None:
    """The change a refused update keeps for "apply", shown whole in the offer: at most one Decided
    line, `message` as decision() makes it, and at most one Open question, the Pursuit's only one
    or the one the agent's words name. None when there is no such change."""
    remove: tuple[str, ...] = ()
    if asked.remove is not None:
        questions = p.resume.open_questions
        one = list(questions) if len(questions) == 1 else named(asked.remove, questions)
        if len(one) != 1:
            return None
        remove = (one[0].text,)
    decided = decision(message) if asked.decide else None
    if asked.decide and decided is None:
        return None
    return EditResumeNote(p.id, remove, (decided,) if decided else ())


def answer(name: str, asked: Asked, change: EditResumeNote) -> str:
    """What the agent is told, as scene 12 shows it: fixed words around the agent's own phrase for
    the Open question, "Updated the Resume Note of Greenhouse logger. Removed the open question
    about the card filling up and added that decision."."""
    removed = None
    if change.remove:
        removed = (f'the open question "{asked.remove}"' if asked.remove == change.remove[0]
                   else f"the open question about {asked.remove}")
    said = (f"Removed {removed}" + (" and added that decision" if change.add_decided else "") if removed
            else "Added that decision")
    return f"Updated the Resume Note of {name}. {said}."
