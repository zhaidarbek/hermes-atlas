"""PRIVATE. The fold: native units in, one Session per owner conversation out. Pure.

A native unit's parent is its Harness's own link (a Codex thread_spawn, a Hermes subagent
session) or an Interface's lineage fact (a T3 Code subagent thread). Each family folds under
the Session at its top: every other member becomes parts of it, and the turns its "owner"
typed become agent turns, because the owner there was the parent agent. A family whose top
Session was not read (excluded, forgotten or gone) is left out. Units are told apart by
native key, and links name them by native id.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from ..model import RawPart, Turn, part_file, skip_key
from . import MAIN, CollectedSession, Key, Lineage, NativeSession


def resolve(natives: Sequence[NativeSession], lineage: Sequence[Lineage],
            alone: Mapping[Key, str] | None = None) -> list[CollectedSession]:
    """`alone` names native units (harness, native id) that start their own family whatever links
    them to a Session that started them, each with the skip key of the Session whose evidence
    holds it: their CollectedSession is `alone` and guarded by that key."""
    by_key = {(n.harness, n.evidence.native_key): n for n in natives}
    named: dict[Key, Key] = {}
    for key, n in by_key.items():
        named.setdefault((n.harness, n.native_id), key)
    unlinked = {named.get(key, key): home for key, home in (alone or {}).items()}
    parent_of = {key: named.get((n.harness, n.parent_native_id), (n.harness, n.parent_native_id))
                 for key, n in by_key.items() if n.parent_native_id and key not in unlinked}
    from_lineage = set()
    interface_of: dict[Key, str] = {}
    for fact in lineage:
        child = named.get(fact.child, fact.child)
        if fact.parent is None:
            interface_of.setdefault(child, fact.interface)
        elif child in unlinked:
            continue
        elif (parent := named.get(fact.parent, fact.parent)) != child and child not in parent_of:
            parent_of[child] = parent
            from_lineage.add(child)
    families: dict[Key, list[Key]] = {}
    for key in by_key:
        families.setdefault(_top(key, parent_of), []).append(key)
    folded = [_fold(by_key[top], [by_key[k] for k in members if k != top], from_lineage, interface_of,
                    unlinked.get(top)) for top, members in families.items() if top in by_key]
    return sorted(folded, key=lambda c: (c.evidence.started, c.evidence.id))


def _top(key: Key, parent_of: dict[Key, Key]) -> Key:
    """The family's starting Session. Links that loop back name the same member from anywhere on the loop."""
    path = [key]
    while key in parent_of:
        key = parent_of[key]
        if key in path:
            return min(path[path.index(key):])
        path.append(key)
    return key


def _fold(top: NativeSession, children: list[NativeSession], from_lineage: set[Key],
          interface_of: dict[Key, str], home: str | None) -> CollectedSession:
    parts: list[RawPart] = list(top.evidence.parts)
    raw = {part_file(k): data for k, data in top.raw.items()}
    turns: list[Turn] = list(top.evidence.turns)
    replaces = {}
    for child in sorted(children, key=lambda c: (c.evidence.started, c.native_id)):
        lineage = (child.harness, child.evidence.native_key) in from_lineage
        prefix = f"{'t3-child' if lineage else 'subagent'}:{child.native_id}"
        named = {p.name: p for p in child.evidence.parts}
        for k, data in child.raw.items():
            moved = prefix if k == MAIN else f"{prefix}/{k}"
            parts.append(replace(named[part_file(k)], name=part_file(moved)))
            raw[part_file(moved)] = data
        turns += [replace(t, role="agent", part=prefix if t.part == MAIN else f"{prefix}/{t.part}")
                  for t in child.evidence.turns]
        if lineage:
            replaces[child.evidence.id] = prefix
    start = top.evidence.started
    evidence = replace(top.evidence,
                       interface=top.interface or interface_of.get((top.harness, top.evidence.native_key)),
                       parts=tuple(parts), turns=tuple(sorted(turns, key=lambda t: (t.at is None, t.at or start))),
                       last_at=max([top.evidence.last_at, *(c.evidence.last_at for c in children)]))
    subagent_parts = sum(k.startswith("subagent:") for n in (top, *children) for k in n.raw)
    keys = {skip_key(n.harness, n.evidence.native_key) for n in (top, *children)} | ({home} if home else set())
    return CollectedSession(evidence, raw, replaces, subagent_parts + len(children), frozenset(keys), home is not None)
