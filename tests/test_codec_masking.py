"""Every text a codec writes is masked (D Q4): an invented secret planted in any text of any
record file's value is never written, and the masked file reads back as itself."""
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime
from types import UnionType
from typing import Literal, Mapping, NewType, Union, get_args, get_origin, get_type_hints
from zoneinfo import ZoneInfo

import pytest

from atlas.model import (Actor, Area, ChangeSettings, Cite, EditResumeNote, EndingProposal, Fact, FocusProposal,
                         Forgotten, HistoryLine, Inference, Note, NoteOrigin, Pattern, Pause, Profile, ProfileEntry,
                         RawPart, ReopenProposal, SendAttempt, SessionEvidence, Settings, Status, Turn, Who)
from atlas.record import Record, _codec, _layout
from harness import seeds
from layout_examples import examples

EXAMPLES = examples()
TZ = ZoneInfo(_codec.parse("settings", EXAMPLES["settings.md"].encode(), None).timezone)
SECRET = "ghp_" + "Inv3ntedT0kenAbCdEfGhIjKlMnOpQrStUvWx"
MASK = "[secret: github token]"
CODEC_PATHS = ["settings.md", "profile.md", "forgotten.md", "pursuits/billing-export-staging-deploy.md",
               "journal/2026-11-16.md", "notes/2026/11/2026-11-15-telegram.md", "notes/2026/11/2026-11-17-claude-code.md",
               "notes/2026/11/2026-11-20-hand-edits.md", "briefs/2026/11/2026-11-20.md", "reviews/2026/2026-W47.md",
               "evidence/codex/2026/10/cx-kxmhgr73ardh6q4s/session.md",
               "evidence/codex/2026/10/cx-kxmhgr73ardh6q4s/card.md"]


def example(path: str) -> tuple[str, object]:
    kind = _layout.kind_of(path)
    return kind, _codec.parse(kind, EXAMPLES[path].encode(), TZ)


def full_pursuit() -> tuple[str, object]:
    """The example Pursuit with a proposal of each kind and an agent's History line."""
    kind, pf = example("pursuits/billing-export-staging-deploy.md")
    p = pf.pursuit
    seen = next(s for s in p.resume.where if isinstance(s, Fact))
    agent = HistoryLine("h-0099", seen.source.at, Actor.AGENT, Inference("Invented agent line.", (seen.source.evidence,)),
                        agent="Codex")
    return kind, replace(pf, pursuit=replace(p, history=(*p.history, agent), proposals=(
        EndingProposal(Status.DONE, Inference("Invented ending.", (seen.source.evidence,)), date(2026, 11, 18), False),
        FocusProposal(True, Inference("Invented focus.", (seen.source.evidence,)), date(2026, 11, 18)),
        ReopenProposal(seen, date(2026, 11, 18)))))


def pending_note() -> tuple[str, object]:
    """A Note with every field the Notes file keeps, a pending change among them."""
    at = datetime(2026, 11, 15, 20, 39, tzinfo=TZ)
    note = Note("nt-1", at, NoteOrigin.OWNER, "telegram", "Invented text.", atlas_reply="Invented reply.",
                platform_ref="4410", offered=True, derived_from=("cx-1",),
                pending=EditResumeNote("Invented pursuit", ("Invented line.",)))
    return "notes", _codec.NotesFile(date(2026, 11, 15), "telegram", (_codec.NoteEntry(note, "Invented pursuit"),))


def a_pause() -> tuple[str, object]:
    return "pauses", (Pause(datetime(2026, 11, 20, 21, 5, tzinfo=TZ), datetime(2026, 11, 22, 8, 0, tzinfo=TZ),
                            "Invented words."),)


def pending_settings() -> tuple[str, object]:
    """A Note waiting for "yes" on a settings change, its patch holding a list."""
    note = Note("nt-2", datetime(2026, 11, 15, 20, 41, tzinfo=TZ), NoteOrigin.OWNER, "telegram", "Invented text.",
                pending=ChangeSettings({"exclusions": ["~/invented"]}))
    return "notes", _codec.NotesFile(date(2026, 11, 15), "telegram", (_codec.NoteEntry(note),))


SAMPLES = [example(p) for p in CODEC_PATHS] + [full_pursuit(), pending_note(), pending_settings(), a_pause()]


def plain_text(hint) -> bool:
    """A field declared as text: str or an Area, alone or in a union, never an id (another NewType) or a fixed word
    (a Literal)."""
    if hint in (str, Area):
        return True
    return get_origin(hint) in (Union, UnionType) and not {str, Area}.isdisjoint(get_args(hint))


def element(hint, value) -> object:
    for arg in get_args(hint) if get_origin(hint) in (Union, UnionType) else (hint,):
        origin = get_origin(arg) or arg
        if isinstance(origin, type) and isinstance(value, origin):
            return arg
    return None


def plantings(value, hint, path=()):
    """(where, value with the secret planted in one text) for every text the value holds or may hold.
    `where` is the (dataclass, field) pairs leading to it."""
    if is_dataclass(value):
        hints = get_type_hints(type(value))
        for f in fields(value):
            for where, planted in plantings(getattr(value, f.name), hints[f.name], (*path, (type(value), f.name))):
                try:
                    yield where, replace(value, **{f.name: planted})
                except ValueError:
                    continue
        return
    if plain_text(hint) and (value is None or isinstance(value, str)):
        yield path, f"{SECRET} {value or ''}"
        return
    if isinstance(hint, NewType) or get_origin(hint) is Literal:
        return
    if isinstance(value, str) and hint is object:
        yield path, f"{SECRET} {value}"
        return
    hint = element(hint, value) or hint
    args = get_args(hint)
    if isinstance(value, tuple):
        items = args[:1] * len(value) if args[1:] == (...,) else args or (None,) * len(value)
        for i, (x, h) in enumerate(zip(value, items)):
            for where, planted in plantings(x, h, path):
                yield where, (*value[:i], planted, *value[i + 1:])
        if not value and args[:1] == (str,):
            yield path, (SECRET,)
    elif isinstance(value, list):
        for i, x in enumerate(value):
            for where, planted in plantings(x, args[0] if args else object, path):
                yield where, [*value[:i], planted, *value[i + 1:]]
    elif isinstance(value, frozenset) and args == (str,):
        yield path, value | {SECRET}
    elif isinstance(value, Mapping):
        args = args or (str, object)
        for k, v in value.items():
            for where, planted in plantings(v, args[1], path):
                yield where, {**value, k: planted}
            if plain_text(args[0]):
                yield path, {**{x: y for x, y in value.items() if x != k}, f"{SECRET} {k}": v}


VERBATIM = {(RawPart, "name"), (RawPart, "sha256"), (RawPart, "fingerprint"), (RawPart, "origin"),
            (SessionEvidence, "namespace"), (SessionEvidence, "native_key"), (Turn, "part"), (SendAttempt, "message_ids"),
            (Note, "platform_ref"), (Forgotten, "skip_keys")}


LEFT_OUT = {(SendAttempt, "text_sha256")}


def kept(where) -> bool:
    """Names something outside the file must match exactly, and the owner's settings."""
    return any((cls, name) in VERBATIM or cls is Settings for cls, name in where)


def label(where) -> str:
    return ".".join(f"{cls.__name__}.{name}" for cls, name in where)


@pytest.mark.parametrize("kind,value", SAMPLES, ids=[f"{k}-{i}" for i, (k, _) in enumerate(SAMPLES)])
def test_a_secret_in_any_text_of_a_record_file_is_written_masked(kind, value):
    unplanted = _codec.render(kind, value, TZ)
    written = 0
    for where, planted in plantings(value, type(value)):
        try:
            data = _codec.render(kind, planted, TZ)
        except ValueError:
            continue
        written += 1
        if kept(where):
            assert SECRET.encode() in data, label(where)
            continue
        if any(field in LEFT_OUT for field in where):
            assert SECRET.encode() not in data and MASK.encode() not in data, label(where)
            continue
        assert SECRET.encode() not in data, label(where)
        if data != unplanted:
            assert MASK.encode() in data or (Cite, "label") in where, label(where)
        assert _codec.render(kind, _codec.parse(kind, data, TZ), TZ) == data, label(where)
    assert written


def test_every_codec_but_the_readme_has_a_sample_to_plant_secrets_in():
    assert {kind for kind, _ in SAMPLES} == set(_codec.CODECS) - {"readme"}


def test_two_patterns_masking_makes_alike_keep_their_own_evidence():
    day = date(2026, 11, 16)
    other = "ghp_" + "An0therInv3ntedT0kenAbCdEfGhIjKlMnOpQr"
    entries = tuple(ProfileEntry(Inference(f"Invented line {n}.", Pattern(f"Invented work with {token}", day, day,
                                                                          (evidence,))), day, Who.LIBRARIAN)
                    for n, token, evidence in ((1, SECRET, "cx-a"), (2, other, "cx-b"), (3, SECRET, "cx-a")))
    data = _codec.render("profile", Profile(("work",), entries), TZ)
    back = _codec.parse("profile", data, TZ)
    assert [(e.statement.basis.description, e.statement.basis.drawn_from) for e in back.entries] == [
        (f"Invented work with {MASK}", ("cx-a",)), (f"Invented work with {MASK} (2)", ("cx-b",)),
        (f"Invented work with {MASK}", ("cx-a",))]
    assert _codec.render("profile", back, TZ) == data


def test_a_note_about_a_pursuit_whose_name_was_written_before_masking_still_names_it(tmp_path, monkeypatch):
    seeds.after_scene_6(tmp_path / "record")
    rec = Record.open(tmp_path / "record")
    pursuit = replace(rec.snapshot().pursuits()[0], name=f"Invented pursuit {SECRET}")
    with monkeypatch.context() as before_masking:
        before_masking.setattr(_codec, "_scrubbed", lambda value, hint: value)
        rec.commit(rec.snapshot().changes().put_pursuit(pursuit))
    note = Note("nt-3", datetime(2026, 11, 16, 9, 0, tzinfo=TZ), NoteOrigin.OWNER, "telegram", "Invented text.",
                about=pursuit.id)
    rec.commit(rec.snapshot().changes().append_note(note))
    assert next(n for n in rec.snapshot().notes() if n.id == note.id).about == pursuit.id
