"""The Evidence ids the record's docs show are ids model.evidence_id can produce."""
import re
import string
from pathlib import Path

import pytest
import yaml

from atlas.model import HarnessId, evidence_id
from layout_examples import DOC, examples

SOURCES = [DOC, Path(__file__).resolve().parents[1] / "atlas" / "record" / "_codec.py"]
EVIDENCE_ID = re.compile(r"(cc|cx|dv|hm|gpt|cai)-([a-z2-7]{16})")
LOOKS_LIKE_AN_ID = re.compile(r"\b(?:cc|cx|dv|hm|gpt|cai)-[A-Za-z0-9]+\b")


def test_the_grammar_is_what_evidence_id_produces():
    made = [evidence_id(h, "local", f"key-{n}") for h in HarnessId for n in range(200)]
    assert {EVIDENCE_ID.fullmatch(i).group(1) for i in made} == {h.prefix for h in HarnessId}
    assert set("".join(EVIDENCE_ID.fullmatch(i).group(2) for i in made)) == set(string.ascii_lowercase + "234567")


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.name)
def test_every_example_id_is_one_evidence_id_can_produce(source):
    ids = LOOKS_LIKE_AN_ID.findall(source.read_text())
    assert ids
    assert [i for i in ids if not EVIDENCE_ID.fullmatch(i)] == []


def test_the_session_example_id_is_derived_from_its_native_key():
    text = examples()["evidence/codex/2026/10/cx-kxmhgr73ardh6q4s/session.md"]
    front = yaml.safe_load(text.split("---\n")[1])
    assert front["id"] == evidence_id(HarnessId(front["harness"]), front["namespace"], front["native_key"])
