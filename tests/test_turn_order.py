"""Turn order in session.md across ticks that read random parts of one Session again: random
roles, same-minute times, edits, twins that show alike once masked, turns taken back, a late
fold and the folded Session read alone."""
import random
from collections import Counter
from dataclasses import replace
from datetime import timedelta

import pytest

from atlas.model import Turn, part_file
from atlas.record import Record, _codec
from test_commit import BERLIN, PROFILE, SETTINGS
from test_evidence import T0, session

SECRETS = ("sk-proj-" + "Ab3dE5gH7jK9mN1pQ3sT5vX7zB9dF1hJ", "sk-proj-" + "Zy9xW7vU5tS3rQ1pO9nM7lK5jI3hG1fE")
PARTS = ("main", "subagent:invented-a", "subagent:invented-b")
CHILD = "t3-child:invented-child"


def key(t):
    return t.part, t.role, t.at, t.text


def shown(turns, parts=PARTS):
    return [key(t) for t in _codec.shown(session(parts=parts), turns, BERLIN)]


def within(small, big):
    rest = iter(big)
    return all(x in rest for x in small)


def when(t):
    return t.at is None, t.at or T0


def by_time(turns):
    return sorted(turns, key=when)


def interleaved(truth, parts):
    """Each part's turns in their order, the parts merged by the time of each one's next turn."""
    heads, out = dict.fromkeys(parts, 0), []
    while live := [p for p in parts if heads[p] < len(truth[p])]:
        p = min(live, key=lambda p: when(truth[p][heads[p]]))
        out.append(truth[p][heads[p]])
        heads[p] += 1
    return out


class Store:
    def __init__(self, root, rng):
        self.root, self.rng, self.n = root, rng, 0
        self.truth = {p: [] for p in PARTS}
        self.raw = {part_file(p): b"" for p in PARTS}
        Record.open(root).create(SETTINGS, PROFILE)

    def text(self):
        self.n += 1
        return f"invented key {self.rng.choice(SECRETS)}" if self.rng.random() < 0.1 else f"invented {self.n}"

    def at(self):
        return None if self.rng.random() < 0.04 else T0 + timedelta(seconds=self.rng.randint(0, 700))

    def turn(self, part):
        return Turn(self.rng.choice(("owner", "agent")), self.at(), self.text(), part)

    def change(self, part) -> bool:
        """Changes `part` as its Harness might; True when a turn is taken back."""
        ts, rng, op = self.truth[part], self.rng, self.rng.random()
        if not ts or op < 0.45:
            ts.append(self.turn(part))
        elif op < 0.65:
            n = rng.randrange(len(ts))
            ts.insert(rng.randint(n + 1, len(ts)), replace(ts[n], text=self.text()))
        elif op < 0.8:
            n = rng.randrange(len(ts))
            t = ts[n]
            text = t.text if rng.random() < 0.5 else t.text.replace(*SECRETS) if SECRETS[0] in t.text else (
                t.text.replace(*reversed(SECRETS)))
            ts.insert(rng.randint(n, len(ts)), Turn(t.role, t.at and t.at.replace(second=rng.randint(0, 59)), text, part))
        elif op < 0.9:
            ts.insert(rng.randint(0, len(ts)), self.turn(part))
        else:
            ts.pop(rng.randrange(len(ts)))
            return True
        return False

    def put(self, s, raw, folds=None, alone=False):
        rec = Record.open(self.root)
        rec.commit(rec.snapshot().changes().put_session(s, raw, folds, alone=alone))
        return rec.snapshot()

    def files(self):
        return sorted(p.read_bytes() for p in self.root.glob("evidence/codex/*/*/*/session.md"))


@pytest.mark.parametrize("seed", range(30))
def test_turns_keep_each_parts_order_and_the_held_order_over_ticks_that_read_random_parts(tmp_path, seed):
    rng = random.Random(seed)
    store, top, held, child, moved, folded = Store(tmp_path, rng), session(parts=PARTS), [], [], [], False
    ticks = rng.randint(3, 9)
    fold_at = rng.randint(1, ticks) if rng.random() < 0.5 else None
    for tick in range(ticks + 1):
        read = PARTS if tick == ticks or rng.random() < 0.3 else tuple(
            p for p in PARTS if p in rng.sample(PARTS, rng.randint(1, len(PARTS))))
        taken_back = False
        for p in read:
            for _ in range(rng.randint(0, 3)):
                taken_back |= store.change(p)
            store.raw[part_file(p)] += f"invented line {tick}\n".encode()
        s = session(*interleaved(store.truth, read), parts=read)
        raw = {part_file(p): store.raw[part_file(p)] for p in read}
        folds = None
        if fold_at is not None and tick == fold_at - 1:
            child = [store.turn("main") for _ in range(rng.randint(1, 4))]
            alone = session(*by_time(child), native="invented-child")
            child_held = store.put(alone, {"main.jsonl.zst": b"invented child 0\n"}).session(alone.id).turns
        if fold_at is not None and tick == fold_at:
            folds = {session(native="invented-child").id: CHILD}
            moved = [key(replace(t, role="agent", part=CHILD)) for t in child_held]
            folded = True
        actual = [key(t) for t in store.put(s, raw, folds).session(top.id).turns]
        where = f"seed {seed} tick {tick} read {read} fold {bool(folds)}"
        assert Counter(actual) == Counter([k for k in held if k[0] not in read] + shown(s.turns) +
                                          (moved if folds else [])), where
        for p in {k[0] for k in actual} | set(PARTS):
            want = (shown(store.truth[p]) if p in read else
                    [k for k in held if k[0] == p] or (moved if folds and p == CHILD else []))
            assert [k for k in actual if k[0] == p] == want, (where, p)
        if not taken_back:
            assert within(held, actual), where
        before = store.files()
        for _ in range(2):
            store.put(s, raw)
            assert store.files() == before, where
        held = actual
        if folded and rng.random() < 0.4:
            child.append(store.turn("main"))
            alone = session(*by_time(child), native="invented-child")
            child_raw = b"".join(f"invented child {n}\n".encode() for n in range(len(child)))
            after = [key(t) for t in store.put(alone, {"main.jsonl.zst": child_raw}, alone=True).session(top.id).turns]
            assert [k for k in after if k[0] == CHILD] == [(CHILD, "agent", *k[2:]) for k in
                                                           shown(alone.turns, ("main",))], where
            assert [k for k in after if k[0] != CHILD] == [k for k in held if k[0] != CHILD], where
            assert within(held, after), where
            before = store.files()
            store.put(alone, {"main.jsonl.zst": child_raw}, alone=True)
            assert store.files() == before, where
            held = after
    for p in PARTS:
        assert [k for k in held if k[0] == p] == shown(store.truth[p])
