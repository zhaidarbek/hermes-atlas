"""Audit finding: text a model answer carries is not held to the rules agent text is.

untrusted.one_line turns brackets to ( ), "Atlas:" to "Atlas -" and refuses reordering characters,
"so it cannot pass for Atlas's own words". librarian._safe, the only cleaning a model answer gets,
keeps all three. A filing answer can therefore put a second evidence mark, the "Atlas:" speaker
label and a right-to-left override into a Left off line, and they reach the brief, atlas_read and
the paste-in prompt. telegram._inline then styles the forged mark exactly as a real one."""
from atlas import agents, messages, untrusted
from audit_poisoned_tick import FORGED_MARK, LEFT_OFF, filed

RLO = "‮"


def test_one_line_would_have_refused_or_defused_the_same_text():
    try:
        assert FORGED_MARK not in untrusted.one_line(LEFT_OFF, 400, "text")
    except untrusted.Refused:
        pass


def test_a_filing_answer_cannot_add_a_second_mark_a_speaker_label_or_reordered_text(tmp_path, throwaway_home, monkeypatch):
    rec, sent, later = filed(tmp_path, throwaway_home, monkeypatch)
    snap = rec.snapshot()
    (p,) = snap.pursuits()
    read, _ = agents.rendered(*agents.resume(snap, p, later))
    (brief,) = sent
    prompt = messages.resume_prompt(p, snap)
    for where, text in (("atlas_read", read), ("the paste-in prompt", prompt), ("the brief", brief)):
        assert FORGED_MARK not in text and f"<i>{FORGED_MARK}</i>" not in text, f"a forged mark in {where}:\n{text}"
        assert RLO not in text, f"a right-to-left override in {where}"
        assert "Atlas:" not in text.replace("<i>", "").replace("</i>", ""), f"Atlas's speaker label in {where}"
