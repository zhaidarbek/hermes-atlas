"""Audit finding: the paste-in prompt gives a Session's text to a tool-bearing agent as a bare
instruction line.

messages.resume_prompt writes "Next Step: <text>" with no mark, and the other lines with a bare
[F] or [I]. The Step's text is what the model drew from a Session, which may hold text the owner
did not write. Nothing in the prompt says which lines are the owner's words and which are quoted
from a Session, so the agent the owner pastes it into reads the Step as the owner's request."""
from atlas import messages
from audit_poisoned_tick import STEP, filed


def test_the_paste_in_prompt_marks_a_step_drawn_from_a_session_as_not_the_owner_s_words(tmp_path, throwaway_home, monkeypatch):
    rec, _, _ = filed(tmp_path, throwaway_home, monkeypatch)
    snap = rec.snapshot()
    (p,) = snap.pursuits()
    prompt = messages.resume_prompt(p, snap)
    step = next(line for line in prompt.split("\n") if line.startswith("Next Step: "))
    assert STEP[1:] in step                         # the Session's words are the Step, verbatim
    assert step.endswith("[I]") or "quoted from your Sessions" in prompt, prompt
