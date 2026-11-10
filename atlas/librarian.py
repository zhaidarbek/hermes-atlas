"""The librarian: every model call Atlas makes for upkeep. Reads a Snapshot, returns values
(cards, drafts, Filings, lines). It never commits. atlas.app feeds its output to rules.* and commits.

Model use (parked Q12/Q15): ctx.llm.complete_structured through the `Llm` port, with one
auxiliary task per job registered at plugin load (atlas_file, atlas_backfill, atlas_journal,
atlas_converse, atlas_answer), so the owner can point each at a model in Hermes config.
Every input passes privacy.scrub first; detected secrets never reach a model or a derived line.

Grounding: the model drafts statements with a quote; `_ground` checks the quote against the
scrubbed transcript. A Fact whose quote is not found becomes an Inference with the same basis.
Every model output is cached under .atlas/cache keyed by sha256(task, prompt version, inputs)
and tagged with the input Evidence ids (Forget purges). Backfill and re-plans after a
Conflict are therefore cheap and resumable.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Callable, Mapping, Protocol, Sequence, TypeVar
from zoneinfo import ZoneInfo

from . import privacy
from .model import (Answer, Area, Cite, Command, CorrectProfile, Coverage, EvidenceId, Fact, Inference,
                    Kind, Membership, Note, ProfileEntry, Pursuit, PursuitId, ResumeNote, SessionCard, SessionEvidence,
                    Statement, Status, StepId, Turn, harness_of)
from .record import Hit, Snapshot

PROMPT_VERSION = 1
BACKFILL = "atlas_backfill"
JOURNAL = "atlas_journal"
ANSWER = "atlas_answer"
READ_HITS = 3                                # transcripts the model reads for a FindQ
FILE_TASK = "atlas_file"
MIN_QUOTE = 15                               # characters, as the prototype's validator required
OWNER_CAP, AGENT_CAP = 1700, 1800            # one turn, as in the prototype; the last turn is never cut further
SESSION_BUDGET = 11_000                      # characters of one Session's transcript in a card call
GROUP_BUDGET = 60_000                        # characters of Session summaries in one group call
T = TypeVar("T")


class Llm(Protocol):
    """Port over ctx.llm (CLI, gateway, MCP processes all have it). Tests use a fake."""
    def structured(self, task: str, instructions: str, input: str, schema: Mapping) -> dict: ...


class Cache(Protocol):
    def cache_get(self, key: str) -> bytes | None: ...
    def cache_put(self, key: str, value: bytes, evidence: Sequence[EvidenceId]) -> None: ...


class ModelError(Exception):
    """The model gave no answer Atlas can use."""


class Unusable(ModelError):
    """The model answered, and the answer is not one Atlas can use: a call for another input may
    still be answered well."""


FIELD_MAX = {"name": 80, "quote": 200, "reason": 300}
"""The longest text, in characters, a model answer may give in a field of that name; every other
field holds at most TEXT_MAX. The tutorial's longest are about half of each. An answer with a
longer text is unusable, never cut into the record."""
TEXT_MAX = 400


class HostLlm:
    """The Llm port over Hermes's ctx.llm. It reads ctx.llm only when a call is made, never in
    register(ctx), and routes each call through the plugin's own auxiliary task."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    def structured(self, task: str, instructions: str, input: str, schema: Mapping) -> dict:
        result = self._ctx.llm.complete_structured(instructions=instructions, input=[{"type": "text", "text": input}],
                                                   json_schema=dict(schema), task=task, purpose=schema.get("title"))
        if not isinstance(result.parsed, dict):
            raise Unusable(f"{task} answered with {result.content_type}, not a JSON object")
        return result.parsed


@dataclass(frozen=True)
class StepSeen:
    """The open Step of the Pursuit a Session is filed in, seen done in that Session: `basis`
    is grounded on the turn that shows it."""
    step: StepId
    basis: Fact


@dataclass(frozen=True)
class Filing:
    """Where one Session goes, and what it changes: a delta, so it re-applies on a fresh Snapshot
    after a Conflict. Exactly one of `pursuit`, `new_pursuit` and `one_off_area` is set."""
    evidence: EvidenceId
    last_at: datetime                        # the Session's last turn as the model read it
    card: SessionCard
    pursuit: PursuitId | None
    new_pursuit: "PursuitDraft | None"
    one_off_area: Area | None
    advances: bool
    left_off: tuple[Statement, ...]          # replaces the Resume Note's Left off when it advances
    next_step: Inference | None              # the next Step, used only when no Step is open
    step_outcome: StepSeen | None
    kind_change: tuple[Kind, Inference] | None
    rule: Fact | None                        # the owner's Filing rule the model filed it by
    ending: tuple[Status, Inference] | None = None   # the ending the Session shows, with its reason


@dataclass(frozen=True)
class PursuitDraft:
    name: str
    kind: Kind
    area: Area
    sessions: tuple[Membership, ...]         # oldest first
    resume: ResumeNote
    next_step: Inference | None


@dataclass(frozen=True)
class JournalLines:
    """What the model drafted for a Journal day, each line grounded on the one Session it read."""
    worked_on: tuple[Statement, ...] = ()
    decided: tuple[Statement, ...] = ()
    finished: tuple[Statement, ...] = ()

    def __add__(self, other: "JournalLines") -> "JournalLines":
        return JournalLines(self.worked_on + other.worked_on, self.decided + other.decided,
                            self.finished + other.finished)


@dataclass(frozen=True)
class Grouping:
    """What the first build makes of the Sessions it read: Pursuit drafts, and the one-offs with
    their Area. Every Session read is in one of them."""
    read: tuple[EvidenceId, ...]
    drafts: tuple[PursuitDraft, ...]
    one_offs: Mapping[EvidenceId, Area]


CARD = """You read one Session the owner had with an AI tool. Answer in short plain sentences:
intent: what the owner set out to do;
outcome: where the Session ended;
unfinished: what was left undone, or null when nothing was.
Never copy a secret; "[secret: ...]" marks one that was masked."""

GROUP = """You read Sessions the owner had with AI tools in one folder, oldest first. Each shows its
card and how it ended. Decide which Pursuits they hold. A Pursuit is anything the owner started
and might return to: a project, idea, experiment, problem or personal matter. It is a place the
owner would resume from, not a repository, a ticket or a specification. A rewrite continues the
same Pursuit. A deployment with its own leftover work is a separate Pursuit from the feature it
deploys. A Session that is a one-off question, with no unfinished intent, is not a Pursuit: list
it under one_offs with its Area. Every Session shown goes in one Pursuit or under one_offs.

Give every Pursuit a short name, its kind (idea, experiment, project or problem), its Area, its
Sessions, and its Resume Note: left_off (where the owner stopped), decided, open_questions,
reopen (one to three Session ids to reopen), and next_step (the smallest unblocked action, or
null when none is known). Each statement names the Sessions it rests on. When a statement is
read straight from one Session, give its quote: 15 to 200 characters copied exactly from that
Session's text; otherwise give a null quote. Use only the Session ids shown. Never copy a
secret; "[secret: ...]" marks one that was masked."""

JOURNAL_LINES = """You read what the owner did in one Session with an AI tool on one day. Write that day's
Journal lines for it, each one short plain sentence about one of the Pursuits named:
worked_on: what the owner worked on;
decided: what the owner decided;
finished: what the owner finished.
Leave a list empty when the Session shows nothing for it. When a line is read straight from the
Session, give its quote: 15 to 200 characters copied exactly from the Session's text; otherwise
give a null quote. Never copy a secret; "[secret: ...]" marks one that was masked."""

RECONCILE = """These are Pursuit drafts made folder by folder from the owner's Sessions. Work that moved to
another folder stays one Pursuit. List the drafts that are one Pursuit and should be merged, with
the merged name. Leave every other draft as it is. A rewrite continues the same Pursuit; a
deployment with its own leftover work stays separate from the feature it deploys."""

PARTITION = """The owner asked to split some Sessions out of one of their Pursuits into a new Pursuit.
You read that Pursuit's Sessions, oldest first, and the owner's words for what goes. List the
Sessions those words name; every other Session stays. Give the new Pursuit's kind (idea,
experiment, project or problem) and its Resume Note, read from the Sessions that go: left_off
(where the owner stopped), decided, open_questions, reopen (one to three Session ids to reopen),
and next_step (the smallest unblocked action, or null when none is known). Each statement names
the Sessions it rests on. When a statement is read straight from one Session, give its quote: 15
to 200 characters copied exactly from that Session's text; otherwise give a null quote. Use only
the Session ids shown. Never copy a secret; "[secret: ...]" marks one that was masked."""

FIND = """The owner asks which of their Sessions with AI tools something happened in. You read the
Sessions that match their words best. Pick the one Session that answers the question, and say in
one short plain sentence, to the owner as "you", what was done there. Give its quote: 15 to 200
characters copied exactly from that Session's text. When no Session shown answers it, give a null
session. Never copy a secret; "[secret: ...]" marks one that was masked."""
FILE = """You read one Session the owner had with an AI tool, and the owner's Pursuits. Say where the
Session belongs: in one Pursuit (give its id), in a new Pursuit (name, kind and Area) when it starts
work the owner might return to, or as a one-off with its Area when it is a single question with no
unfinished intent. Follow the owner's Filing rules: when one decides where the Session goes, give its
number. Say whether the Session advanced the work (false when it only read or asked about it). For a
Pursuit, give its new left_off when the Session moved it on, step_done when the Session shows the
Pursuit's open Step done, next_step (the smallest unblocked action, or null when none is known), and
kind when the Session shows the Pursuit is now another kind, with the reason, and ending when the
Session shows the Pursuit's purpose is met (done) or given up (dropped), with the reason. Also give the
Session's intent and outcome in short plain sentences. When a statement is read straight from the
Session, give its quote: 15 to 200 characters copied exactly from the Session's text; otherwise give
a null quote. Never copy a secret; "[secret: ...]" marks one that was masked."""

_STATEMENT = {"type": "object", "required": ["text", "quote", "sessions"], "properties": {
    "text": {"type": "string"}, "quote": {"type": ["string", "null"]},
    "sessions": {"type": "array", "items": {"type": "string"}}}}
FIND_SCHEMA = {"title": "answer", "type": "object", "required": ["session", "text", "quote"], "properties": {
    "session": {"type": ["string", "null"]}, "text": {"type": "string"}, "quote": {"type": ["string", "null"]}}}
CARD_SCHEMA = {"title": "card", "type": "object", "required": ["intent", "outcome", "unfinished"], "properties": {
    "intent": {"type": "string"}, "outcome": {"type": "string"}, "unfinished": {"type": ["string", "null"]}}}
RECONCILE_SCHEMA = {"title": "reconcile", "type": "object", "required": ["merges"], "properties": {
    "merges": {"type": "array", "items": {"type": "object", "required": ["drafts", "name"], "properties": {
        "drafts": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}}}}}}


JOURNAL_SECTIONS = ("worked_on", "decided", "finished")


def journal_schema(pursuits: Sequence[str]) -> dict:
    line = {"type": "object", "required": ["pursuit", "text", "quote"], "properties": {
        "pursuit": {"enum": list(pursuits)}, "text": {"type": "string"}, "quote": {"type": ["string", "null"]}}}
    return {"title": "journal", "type": "object", "required": list(JOURNAL_SECTIONS),
            "properties": {attr: {"type": "array", "items": line} for attr in JOURNAL_SECTIONS}}


_STATEMENTS = {"type": "array", "items": _STATEMENT}
PARTITION_SCHEMA = {"title": "partition", "type": "object", "required": [
    "sessions", "kind", "left_off", "decided", "open_questions", "reopen", "next_step"], "properties": {
    "sessions": {"type": "array", "items": {"type": "string"}}, "kind": {"enum": [k.value for k in Kind]},
    "left_off": _STATEMENTS, "decided": _STATEMENTS, "open_questions": _STATEMENTS,
    "reopen": {"type": "array", "items": {"type": "string"}}, "next_step": {"anyOf": [{"type": "null"}, _STATEMENT]}}}


def ask(llm: Llm, task: str, instructions: str, input: str, schema: Mapping) -> dict:
    """One model call that is not kept: its input masked, its answer cut to the schema and made safe."""
    out = _answer(llm.structured(task, instructions, privacy.scrub(input), schema), schema)
    try:
        _bounded(out)
    except ValueError as error:
        raise Unusable(f"an unusable {schema.get('title')} answer ({type(error).__name__})") from error
    return out


def file_schema(pursuits: Sequence[str], areas: Sequence[str]) -> dict:
    kinds = [k.value for k in Kind]
    statement = {"anyOf": [{"type": "null"}, _STATEMENT]}
    return {"title": "filing", "type": "object", "required": [
        "intent", "outcome", "pursuit", "new_pursuit", "one_off_area", "rule", "advances", "left_off", "step_done",
        "next_step", "kind", "ending"], "properties": {
        "intent": {"type": "string"}, "outcome": {"type": "string"},
        "pursuit": {"anyOf": [{"type": "null"}, {"enum": list(pursuits)}]},
        "new_pursuit": {"anyOf": [{"type": "null"}, {"type": "object", "required": ["name", "kind", "area"],
                                                      "properties": {"name": {"type": "string"}, "kind": {"enum": kinds},
                                                                     "area": {"enum": list(areas)}}}]},
        "one_off_area": {"anyOf": [{"type": "null"}, {"enum": list(areas)}]},
        "rule": {"type": ["integer", "null"]}, "advances": {"type": "boolean"},
        "left_off": {"type": "array", "items": _STATEMENT}, "step_done": statement, "next_step": statement,
        "kind": {"anyOf": [{"type": "null"}, {"type": "object", "required": ["to", "reason"], "properties": {
            "to": {"enum": kinds}, "reason": {"type": "string"}}}]},
        "ending": {"anyOf": [{"type": "null"}, {"type": "object", "required": ["to", "reason"], "properties": {
            "to": {"enum": [Status.DONE.value, Status.DROPPED.value]}, "reason": {"type": "string"}}}]}}}


def group_schema(areas: Sequence[str]) -> dict:
    statements = {"type": "array", "items": _STATEMENT}
    return {"title": "group", "type": "object", "required": ["pursuits", "one_offs"], "properties": {
        "pursuits": {"type": "array", "items": {"type": "object", "required": [
            "name", "kind", "area", "sessions", "left_off", "decided", "open_questions", "reopen", "next_step"],
            "properties": {
                "name": {"type": "string"}, "kind": {"enum": [k.value for k in Kind]}, "area": {"enum": list(areas)},
                "sessions": {"type": "array", "items": {"type": "string"}}, "left_off": statements,
                "decided": statements, "open_questions": statements,
                "reopen": {"type": "array", "items": {"type": "string"}},
                "next_step": {"anyOf": [{"type": "null"}, _STATEMENT]}}}},
        "one_offs": {"type": "array", "items": {"type": "object", "required": ["session", "area"], "properties": {
            "session": {"type": "string"}, "area": {"enum": list(areas)}}}}}}


CONVERSE = "atlas_converse"
PROFILE_FILE = "profile.md"
_PROFILE_STYLE = """The owner's Profile lists, by number, what Atlas has learned about how the owner works,
each entry one short sentence that starts with "You". Write each new entry in that style, saying
only what the owner said: no reason they did not give, and nothing about other entries. Never copy
a secret; "[secret: ...]" marks one that was masked."""
PROFILE = """The owner corrects one entry of their Profile in their own words. Give the number of the
entry their words correct and the entry as it should read now.

""" + _PROFILE_STYLE
PROFILE_EDIT = """The owner edited their Profile file by hand. The diff shows the lines they removed (-) and
the lines they wrote (+). For each entry the edit changes, give its number and the entry as it
should read now.

""" + _PROFILE_STYLE
_CORRECTION = {"type": "object", "required": ["entry", "text"],
               "properties": {"entry": {"type": "integer"}, "text": {"type": "string"}}}
PROFILE_SCHEMA = {"title": "profile_correction", **_CORRECTION}
PROFILE_EDIT_SCHEMA = {"title": "profile_edit", "type": "object", "required": ["changes"],
                       "properties": {"changes": {"type": "array", "items": _CORRECTION}}}


@dataclass(frozen=True)
class _Seen:
    """What a group call reads of one Session: its metadata, its card and its last turn."""
    session: SessionEvidence                 # without its turns
    card: SessionCard | None
    last: Turn | None

    @property
    def id(self) -> EvidenceId:
        return self.session.id


class Librarian:
    def __init__(self, llm: Llm, cache: Cache) -> None:
        self._llm = llm
        self._cache = cache

    def card(self, s: SessionEvidence) -> SessionCard:
        def read(out: dict) -> SessionCard:
            intent, outcome, unfinished = _text(out["intent"]), _text(out["outcome"]), out["unfinished"]
            if not intent or not outcome:
                raise ModelError("a card with no intent or outcome")
            unfinished = _text(unfinished) if unfinished is not None else ""
            return SessionCard(s.id, Inference(intent, (s.id,)), Inference(outcome, (s.id,)),
                               Inference(unfinished, (s.id,)) if unfinished else None, _where(s))
        return self._ask(BACKFILL, CARD, _transcript(s), CARD_SCHEMA, (s.id,), read)

    def group_backfill(self, snap: Snapshot, ids: Sequence[EvidenceId]) -> Grouping:
        """Approach B (owner chose its division): group by the folder each Session ran in, from
        its metadata, one call per group reading the group's cards and how each Session ended
        (a busy folder takes several calls), then one cross-group reconciliation call over all
        drafts (catches work that moved folders). Every card must be written first. Raises
        ModelError, or whatever the model call raised, when a call gives nothing usable."""
        seen = {id: _seen(snap, id) for id in ids}
        if any(s.card is None for s in seen.values()):
            raise ModelError("a Session has no card yet")
        areas = {_safe(a): Area(a) for a in snap.profile().areas}
        drafts: list[PursuitDraft] = []
        one_offs: dict[EvidenceId, Area] = {}
        for chunk in _chunks(seen.values()):
            found, singles = self._group(chunk, snap, areas)
            drafts += found
            one_offs.update(singles)
        return Grouping(tuple(seen), tuple(self._reconcile(drafts, tuple(seen))), one_offs)

    def _group(self, chunk: Sequence[_Seen], snap: Snapshot, areas: Mapping[str, Area]
               ) -> tuple[list[PursuitDraft], dict[EvidenceId, Area]]:
        """`areas` maps each Area, masked as the model reads it, to the owner's Area. An answer
        that leaves a Session it was shown in no Pursuit and not a one-off is not used."""
        by_id = {s.id: s for s in chunk}

        def read(out: dict) -> tuple[list[PursuitDraft], dict[EvidenceId, Area]]:
            claimed: set[EvidenceId] = set()
            drafts, singles = [], {}
            for row in out["pursuits"]:
                members = sorted((by_id[i] for i in dict.fromkeys(row["sessions"]) if i in by_id and i not in claimed),
                                 key=lambda m: (_instant(m.session.last_at), m.id))
                name, kind, area = _text(row["name"]), Kind(row["kind"]), _area(row["area"], areas)
                if members and name:
                    claimed |= {m.id for m in members}
                    drafts.append(_draft(row, name, kind, area, members, snap))
            for row in out["one_offs"]:
                id, area = row["session"], _area(row["area"], areas)
                if id in by_id and id not in claimed:
                    claimed.add(id)
                    singles[EvidenceId(id)] = area
            if len(claimed) < len(by_id):
                raise ModelError(f"the group answer filed {len(claimed)} of {len(by_id)} Sessions")
            return drafts, singles
        return self._ask(BACKFILL, GROUP, "\n\n".join(_summary(s) for s in chunk), group_schema(list(areas)),
                         tuple(by_id), read)

    def _reconcile(self, drafts: Sequence[PursuitDraft], shown: Sequence[EvidenceId]) -> list[PursuitDraft]:
        """`shown` is every Session the group calls read, one-offs included: a draft may hold
        words the model took from any of them, so the cached merge is tagged with them all."""
        if len(drafts) < 2:
            return list(drafts)
        keys = {f"d{n}": d for n, d in enumerate(drafts, 1)}

        def read(out: dict) -> list[PursuitDraft]:
            used: set[str] = set()
            merged = []
            for row in out["merges"]:
                group = [k for k in dict.fromkeys(row["drafts"]) if k in keys and k not in used]
                if len(group) > 1:
                    used |= set(group)
                    merged.append(_merge([keys[k] for k in group], _text(row["name"])))
            return [d for k, d in keys.items() if k not in used] + merged
        return self._ask(BACKFILL, RECONCILE, "\n\n".join(_outline(k, d) for k, d in keys.items()), RECONCILE_SCHEMA,
                         shown, read)

    def _ask(self, task: str, instructions: str, input: str, schema: Mapping, evidence: Sequence[EvidenceId],
             read: Callable[[dict], T]) -> T:
        """One model call, or its cached output. Before the answer is read or kept, it is cut to
        the properties the schema names and every text in it is made safe (`_safe`). Only an
        answer `read` accepts is cached."""
        input = privacy.scrub(input)
        key = hashlib.sha256(json.dumps([task, PROMPT_VERSION, instructions, schema, input], sort_keys=True).encode()
                             ).hexdigest()
        cached = _json(self._cache.cache_get(key))
        out = _answer(cached if cached is not None else self._llm.structured(task, instructions, input, schema), schema)
        try:
            value = read(_bounded(out))
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise Unusable(f"an unusable {schema['title']} answer ({type(error).__name__})") from error
        if cached is None:
            self._cache.cache_put(key, json.dumps(out, sort_keys=True).encode(), evidence)
        return value

    def file(self, snap: Snapshot, s: SessionEvidence) -> Filing:
        """Approach C (ledger) for daily upkeep: one call per new or grown Session, seeing the
        Pursuit list (names, Filing rules, Left off, open Steps) and that Session. Turns of an
        exchange with Atlas never advance a Pursuit. Raises Unusable for an answer it cannot use,
        or whatever the model call raised."""
        pursuits = [p for p in snap.pursuits()]
        by_key = {_safe(p.id): p for p in pursuits}
        areas = {_safe(a): Area(a) for a in snap.profile().areas}
        rules = [(p.id, r) for p in pursuits for r in p.filing_rules]
        own = {s.id: s}

        def read(out: dict) -> Filing:
            intent, outcome = _text(out["intent"]), _text(out["outcome"])
            if not intent or not outcome:
                raise ValueError("a filing with no intent or outcome")
            card = SessionCard(s.id, Inference(intent, (s.id,)), Inference(outcome, (s.id,)), None, _where(s))
            p = by_key.get(out["pursuit"]) if out["pursuit"] is not None else None
            draft = area = None
            left_off = tuple(st for r in out["left_off"] if (st := ground({**r, "sessions": [s.id]}, own)) is not None)
            nxt = ground({**out["next_step"], "quote": None, "sessions": [s.id]}, own) if out["next_step"] else None
            if p is None and out["new_pursuit"]:
                new = out["new_pursuit"]
                where = _place(s)
                draft = PursuitDraft(_text(new["name"]), Kind(new["kind"]), _area(new["area"], areas),
                                     (Membership(s.id, s.last_at, True),),
                                     ResumeNote((where,) if where else (), left_off, (), (), (s.id,)), nxt)
                if not draft.name:
                    raise ValueError("a new Pursuit with no name")
                if draft.name.casefold() in {q.name.casefold() for q in pursuits}:
                    raise ValueError("a new Pursuit with the name of one there is")
            elif p is None:
                area = _area(out["one_off_area"], areas)
            advances = bool(out["advances"]) and any(t.role == "owner" and t.own and not t.atlas_tool_turn
                                                     for t in s.turns)
            seen = None
            if advances and p is not None and out["step_done"] and p.next_step is not None and p.next_step.ended is None:
                row = out["step_done"]
                read_to = next((m.at for m in p.sessions if m.evidence == s.id), None)
                unread = [t for t in s.turns if read_to is None or (t.at and _instant(t.at) > _instant(read_to))]
                turn = _found(row["quote"], unread) if isinstance(row["quote"], str) else None
                seen = StepSeen(p.next_step.id, Fact(_text(row["text"]), Cite(s.id, _label(s), turn.at))) if turn else None
            kind = None
            if advances and p is not None and out["kind"] and Kind(out["kind"]["to"]) is not p.kind \
                    and _text(out["kind"]["reason"]):
                kind = (Kind(out["kind"]["to"]), Inference(out["kind"]["reason"], (s.id,)))
            ending = None
            said = out.get("ending")
            if advances and p is not None and said and Status(said["to"]).is_ending and _text(said["reason"]):
                ending = (Status(said["to"]), Inference(_text(said["reason"]), (s.id,)))
            rule = out["rule"]
            cited = rules[rule - 1] if isinstance(rule, int) and 1 <= rule <= len(rules) else None
            return Filing(s.id, s.last_at, card, p.id if p else None, draft, area, advances, left_off,
                          nxt if isinstance(nxt, Inference) else None, seen, kind,
                          cited[1] if cited and p is not None and cited[0] == p.id else None, ending)
        listing = "\n\n".join(_listed(p, rules) for p in pursuits) or "(none)"
        input = f"Areas: {', '.join(areas)}\n\nPursuits:\n{listing}\n\n{_transcript(s)}"
        return self._ask(FILE_TASK, FILE, input, file_schema(list(by_key), list(areas)), (s.id,), read)

    def regroup(self, snap: Snapshot) -> list[Command]:
        """Weekly pass (with the Review) proposing Merge/Split where the ledger lumped. Applied
        as Librarian changes, so they show on the Board and in the Review. Never undoes an owner
        Filing rule."""
        raise NotImplementedError

    def partition(self, snap: Snapshot, pursuit: Pursuit, criterion: str, name: str) -> PursuitDraft:
        """Split pre-work: the new Pursuit `name`, with the Sessions of `pursuit` that match the
        owner's criterion and a Resume Note and Next Step read from them. ModelError when it would
        take none of them or all of them."""
        seen = [_seen(snap, m.evidence) for m in pursuit.sessions]
        if any(s.card is None for s in seen):
            raise ModelError("a Session has no card yet")
        by_id = {s.id: s for s in seen}

        def read(out: dict) -> PursuitDraft:
            members = [by_id[i] for i in dict.fromkeys(out["sessions"]) if i in by_id]
            if not members or len(members) == len(by_id):
                raise ValueError("a split that leaves one side empty")
            members.sort(key=lambda m: (_instant(m.session.last_at), m.id))
            return _draft(out, name, Kind(out["kind"]), pursuit.area, members, snap)
        input = f"Pursuit: {pursuit.name}\nWhat goes: {criterion}\n\n" + "\n\n".join(_summary(s) for s in seen)
        return self._ask(CONVERSE, PARTITION, input, PARTITION_SCHEMA, tuple(by_id), read)

    def journal_lines(self, snap: Snapshot, day: date, new: Sequence[EvidenceId]) -> JournalLines:
        """Worked on, Decided and Finished lines for `day` from the Sessions in `new`: the
        caller passes only Sessions the day's file does not cite yet. One call per Session that
        a Pursuit lists, reading its turns of that day, so each line rests on the one Session
        whose words it holds. A one-off Session gets no lines. Raises ModelError, or whatever
        the model call raised, when a call gives nothing usable."""
        zone = ZoneInfo(snap.settings().timezone)
        names: dict[EvidenceId, list[str]] = {}
        for p in snap.pursuits():
            for m in p.sessions:
                if m.advances and m.evidence in new:
                    names.setdefault(m.evidence, []).append(p.name)
        lines = JournalLines()
        for id in (i for i in new if i in names):
            s = snap.session(id)
            turns = tuple(t for t in s.turns if t.at and t.at.astimezone(zone).date() == day)
            if any(t.own for t in turns):
                lines += self._day_lines(replace(s, turns=turns), day, names[id])
        return lines

    def _day_lines(self, s: SessionEvidence, day: date, pursuits: Sequence[str]) -> JournalLines:
        """The lines one Session gives `day` about the Pursuits that list it, `pursuits`; the model
        reads their names masked, and a line about another Pursuit is left out."""
        masked = {_safe(name): name for name in pursuits}

        def line(row: Mapping) -> Statement | None:
            if row["pursuit"] not in masked or not _text(row["text"]):
                return None
            return ground({"text": f"{masked[row['pursuit']]}: {row['text']}", "quote": row["quote"],
                           "sessions": [s.id]}, {s.id: s})

        def read(out: dict) -> JournalLines:
            return JournalLines(**{attr: tuple(st for row in out[attr] if (st := line(row)) is not None)
                                   for attr in JOURNAL_SECTIONS})
        input = f"Day: {day:%A} {day.day} {day:%B} {day.year}\nPursuits: {', '.join(masked)}\n\n{_transcript(s)}"
        return self._ask(JOURNAL, JOURNAL_LINES, input, journal_schema(list(masked)), (s.id,), read)

    def answer(self, snap: Snapshot, question: str, hits: Sequence[Hit]) -> Answer:
        """FindQ: one call reading the transcripts of the best READ_HITS hits. The Answer's data
        is the Session the model picked, and its one statement is grounded on that Session: a
        Fact when its quote is found there. No Session when the model picked none of them.
        Raises ModelError, or whatever the model call raised, when the call gives nothing usable."""
        sessions = {h.evidence: snap.session(h.evidence) for h in hits[:READ_HITS]}
        if not sessions:
            return Answer(None, (), Coverage())

        def read(out: dict) -> Answer:
            picked = out["session"]
            if picked not in sessions:
                return Answer(None, (), Coverage())
            line = ground({"text": out["text"], "quote": out["quote"], "sessions": [picked]}, sessions)
            return Answer(None, (line,) if line else (), Coverage(), data=EvidenceId(picked))
        input = f"Question: {question}\n\n" + "\n\n---\n\n".join(_transcript(s) for s in sessions.values())
        return self._ask(ANSWER, FIND, input, FIND_SCHEMA, tuple(sessions), read)

    def correct_profile(self, snap: Snapshot, words: str) -> CorrectProfile:
        """The Profile entry the owner's words correct, and its new text."""
        entries = snap.profile().entries

        def read(out: dict) -> CorrectProfile:
            return _correction(out, entries)
        return self._ask(CONVERSE, PROFILE, _profile(entries) + f"\n\nThe owner's words:\n{words}",
                         PROFILE_SCHEMA, _cited(entries), read)

    def edit_to_commands(self, snap: Snapshot, hand_edit: Note) -> list[Command]:
        """'apply' a hand edit: translate the diff into Commands (applied with Owner authority).
        Built so far: a hand edit of the Profile."""
        if hand_edit.via != PROFILE_FILE:
            raise ModelError(f"applying a hand edit of {hand_edit.via} is not built yet")
        entries = snap.profile().entries

        def read(out: dict) -> list[Command]:
            changes = [_correction(row, entries) for row in out["changes"]]
            if not changes:
                raise ValueError("an edit that changes no entry")
            return changes
        return self._ask(CONVERSE, PROFILE_EDIT, _profile(entries) + f"\n\nThe owner's edit:\n{hand_edit.diff}",
                         PROFILE_EDIT_SCHEMA, _cited(entries), read)

    def profile_pass(self, snap: Snapshot, day: date | None) -> list[Command]:
        """Profile entries limited to what improves the Daily Brief (R3 Q6). day=None: the whole
        history, once, at the end of backfill. Otherwise daily, for the Journal day just closed
        (G3). Commands run under Librarian authority; each entry records added + by."""
        raise NotImplementedError


# ------------------------------------------------------------------ inputs

def _label(s: SessionEvidence) -> str:
    return s.harness.label + (f" via {s.interface}" if s.interface else "")


def _when(t: Turn) -> str:
    return f"{t.at:%Y-%m-%d %H:%M}" if t.at else "undated"


def _head(s: SessionEvidence) -> str:
    where = "".join([f" · {s.cwd}" if s.cwd else "", f" · branch {s.git_branch}" if s.git_branch else ""])
    return (f"Session {s.id} · {_label(s)} · {s.started:%Y-%m-%d %H:%M} to {s.last_at:%Y-%m-%d %H:%M}{where}\n"
            f"Title: {s.title}")


def _cap(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + " [...]"


def _turn(s: SessionEvidence, t: Turn) -> str:
    who, cap = ("You", OWNER_CAP) if t.role == "owner" else (s.harness.label, AGENT_CAP)
    return f"{who} · {_when(t)}\n{_cap(t.text, cap)}"


def _transcript(s: SessionEvidence) -> str:
    """The Session for a card call: the first turn, the last, and as many turns before the last
    as fit in SESSION_BUDGET."""
    blocks = [_turn(s, t) for t in s.turns if t.own]
    if sum(map(len, blocks)) > SESSION_BUDGET and len(blocks) > 2:
        first, middle, last = blocks[0], blocks[1:-1], blocks[-1]
        room, tail = SESSION_BUDGET - len(first) - len(last), []
        for block in reversed(middle):
            if len(block) > room:
                break
            tail.insert(0, block)
            room -= len(block)
        left = len(middle) - len(tail)
        blocks = [first, *([f"[{left} earlier turns left out]"] if left else []), *tail, last]
    return _head(s) + "\n\n" + "\n\n".join(blocks)


def _seen(snap: Snapshot, id: EvidenceId) -> _Seen:
    s = snap.session(id)
    own = [t for t in s.turns if t.own]
    return _Seen(replace(s, turns=()), snap.card(id), own[-1] if own else None)


def _summary(seen: _Seen) -> str:
    c = seen.card
    lines = [_head(seen.session), f"Intent: {c.intent.text}", f"Outcome: {c.outcome.text}"]
    if c.unfinished:
        lines.append(f"Unfinished: {c.unfinished.text}")
    if seen.last:
        who = "You" if seen.last.role == "owner" else seen.session.harness.label
        lines.append(f"Ended with {who} · {_when(seen.last)}:\n{_cap(seen.last.text, AGENT_CAP)}")
    return "\n".join(lines)


def _chunks(seen: Sequence[_Seen]) -> list[list[_Seen]]:
    """One group per folder, in folder order, oldest Session first; a group whose summaries
    pass GROUP_BUDGET is split into consecutive chunks."""
    folders: dict[str, list[_Seen]] = {}
    for s in seen:
        folders.setdefault(s.session.cwd or "", []).append(s)
    chunks = []
    for folder in sorted(folders):
        chunk, size = [], 0
        for s in sorted(folders[folder], key=lambda s: (_instant(s.session.started), s.id)):
            n = len(_summary(s))
            if chunk and size + n > GROUP_BUDGET:
                chunks.append(chunk)
                chunk, size = [], 0
            chunk.append(s)
            size += n
        chunks.append(chunk)
    return chunks


def _outline(key: str, d: PursuitDraft) -> str:
    first, last = d.sessions[0].at.date(), d.sessions[-1].at.date()
    lines = [f"{key} · {d.name} · {d.kind.value} · {d.area}",
             f"{len(d.sessions)} Sessions, {first.isoformat()} to {last.isoformat()}"]
    lines += [f"Where: {s.text}" for s in d.resume.where] + [f"Left off: {s.text}" for s in d.resume.left_off]
    return "\n".join(lines)


def _listed(p: Pursuit, rules: Sequence[tuple[PursuitId, Fact]]) -> str:
    """One Pursuit as a filing call reads it, its Filing rules numbered across every Pursuit."""
    lines = [f"{p.id} · {p.name} · {p.kind.value} · {p.status.value} · {p.area}"]
    lines += [f"Filing rule {n}: {r.text}" for n, (owner, r) in enumerate(rules, 1) if owner == p.id]
    lines += [f"Left off: {st.text}" for st in p.resume.left_off]
    if p.next_step is not None and p.next_step.ended is None:
        lines.append(f"Open Step: {p.next_step.text.text}")
    return "\n".join(lines)


# ------------------------------------------------------------------ outputs

def _json(data: bytes | None) -> dict | None:
    try:
        out = json.loads(data) if data is not None else None
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


_NONCHARACTERS = "".join(chr(plane + n) for plane in range(0, 0x110000, 0x10000) for n in (0xFFFE, 0xFFFF))
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f]")
_UNWRITABLE = re.compile(f"[\ud800-\udfff\ufdd0-\ufdef{_NONCHARACTERS}]")


def _safe(text: str) -> str:
    """A text the model wrote, as Atlas may write it: on one line with single spaces, lone
    surrogates and noncharacters left out, then detected secrets masked, so a secret the spacing
    hid is masked too."""
    return privacy.scrub(" ".join(_UNWRITABLE.sub("", _CONTROL.sub(" ", text)).split()))


def _answer(value: object, schema: Mapping) -> object:
    """An answer with only the properties its schema names, every text and every key of a map
    the schema leaves open made safe."""
    if isinstance(value, str):
        return _safe(value)
    if isinstance(value, list):
        return [_answer(v, schema.get("items", {})) for v in value]
    if isinstance(value, dict):
        named = next((s["properties"] for s in (schema, *schema.get("anyOf", ())) if "properties" in s), None)
        if named is None:
            return {_safe(str(k)): _answer(v, {}) for k, v in value.items()}
        return {k: _answer(v, named[k]) for k, v in value.items() if k in named}
    return value


def _bounded(value: object, field: str = "") -> object:
    """`value`, when each text in it is within its field's maximum (FIELD_MAX); else ValueError."""
    if isinstance(value, str) and len(value) > FIELD_MAX.get(field, TEXT_MAX):
        raise ValueError(f"a {field or 'text'} field over its maximum")
    if isinstance(value, list):
        for v in value:
            _bounded(v, field)
    if isinstance(value, dict):
        for k, v in value.items():
            _bounded(v, k)
    return value


def _instant(at: datetime) -> datetime:
    """`at` in UTC, so two times an hour apart across a DST fallback compare as instants."""
    return at.astimezone(timezone.utc)


def _profile(entries: Sequence[ProfileEntry]) -> str:
    return "Profile:\n" + "\n".join(f"{n}. {e.statement.text}" for n, e in enumerate(entries, 1))


def _correction(out: dict, entries: Sequence[ProfileEntry]) -> CorrectProfile:
    n, text = out["entry"], _text(out["text"])
    if not isinstance(n, int) or not 1 <= n <= len(entries) or not text:
        raise ValueError("a correction of no entry")
    return CorrectProfile(entries[n - 1].statement.text, text)


def _cited(entries: Sequence[ProfileEntry]) -> tuple[EvidenceId, ...]:
    """The Sessions the entries rest on, so Forget purges an answer that read them."""
    ids: list[EvidenceId] = []
    for e in entries:
        s = e.statement
        basis = (s.source.evidence,) if isinstance(s, Fact) and isinstance(s.source, Cite) else getattr(s, "basis", ())
        ids += getattr(basis, "drawn_from", basis)
    return tuple(i for i in dict.fromkeys(ids) if harness_of(i))


def _text(value: object) -> str:
    """A text of a safe answer."""
    if not isinstance(value, str):
        raise TypeError("not a text")
    return value


def _area(value: object, areas: Mapping[str, Area]) -> Area:
    if value not in areas:
        raise ValueError("not one of the Areas")
    return areas[value]


def _where(s: SessionEvidence) -> Fact | None:
    """A card's Where line, read from the Session's metadata."""
    if not s.cwd:
        return None
    return Fact(s.cwd + (f", branch {s.git_branch}" if s.git_branch else "") + ".", Cite(s.id, _label(s), s.started))


def _place(s: SessionEvidence) -> Fact | None:
    """A Resume Note's Where line: the machine, folder and branch of the Pursuit's latest Session."""
    if not s.cwd:
        return None
    text = ", ".join(x for x in (s.machine, s.cwd, f"branch {s.git_branch}" if s.git_branch else "") if x)
    return Fact(text, Cite(s.id, _label(s), s.started))


def _found(quote: str, turns: Sequence[Turn]) -> Turn | None:
    """The last dated turn whose safe text holds the quote, whitespace aside. A quote shorter
    than MIN_QUOTE once its masked secrets are left out grounds nothing."""
    wanted = _safe(quote)
    if len(" ".join(privacy.MASK.sub(" ", wanted).split())) < MIN_QUOTE:
        return None
    return next((t for t in reversed(turns) if t.at and wanted in _safe(t.text)), None)


def ground(row: Mapping, sessions: Mapping[EvidenceId, SessionEvidence]) -> Statement | None:
    """A statement drafted in a safe answer, as a Fact when it names one Session and its quote
    is found in that Session's masked transcript, or else as an Inference on the Sessions it
    names. None when it has no text or names none of `sessions`."""
    text = _text(row["text"])
    ids = tuple(EvidenceId(i) for i in dict.fromkeys(row["sessions"]) if i in sessions)
    if not text or not ids:
        return None
    quote = row.get("quote")
    if isinstance(quote, str) and len(ids) == 1:
        turn = _found(quote, sessions[ids[0]].turns)
        if turn is not None:
            return Fact(text, Cite(ids[0], _label(sessions[ids[0]]), turn.at))
    return Inference(text, ids)


def _draft(row: Mapping, name: str, kind: Kind, area: Area, members: Sequence[_Seen], snap: Snapshot) -> PursuitDraft:
    sessions = {m.id: snap.session(m.id) for m in members}

    def statements(key: str) -> tuple[Statement, ...]:
        return tuple(st for r in row[key] if (st := ground(r, sessions)) is not None)

    nxt = ground({**row["next_step"], "quote": None}, sessions) if row["next_step"] else None
    reopen = tuple(EvidenceId(i) for i in dict.fromkeys(row["reopen"]) if i in sessions)[:3] or (members[-1].id,)
    where = _place(members[-1].session)
    return PursuitDraft(
        name, kind, area, tuple(Membership(m.id, m.session.last_at, True) for m in members),
        ResumeNote((where,) if where else (), statements("left_off"), statements("decided"),
                   statements("open_questions"), reopen), nxt)


def _merge(drafts: Sequence[PursuitDraft], name: str) -> PursuitDraft:
    """One Pursuit from drafts the reconciliation found to be one: every Session, and the Resume
    Note, Next Step, Kind and Area of the draft worked on last."""
    latest = max(drafts, key=lambda d: (_instant(d.sessions[-1].at), d.sessions[-1].evidence))
    sessions = sorted({m.evidence: m for d in drafts for m in d.sessions}.values(),
                      key=lambda m: (_instant(m.at), m.evidence))
    return replace(latest, name=name or latest.name, sessions=tuple(sessions))
