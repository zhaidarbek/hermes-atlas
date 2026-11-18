"""Audit finding: concrete secret forms privacy.scrub leaves bare, and the sinks they reach.

Every value is invented. Each form came back from scrub() unchanged. Each is then followed from a Claude Code Session into the text sent
to the model and into the record's session.md, which the transcript route and the index read."""
import pytest

from atlas import privacy
from atlas.app import Atlas
from audit_fixtures import FIRST, NOW, Scripted, claude, one_pursuit, record

BOT_TOKEN = "123456789:AAHk3Lm9Qw2Zx8Vb7Nc6Jd5Fg4Hs1Pt0Ry_e"
FORMS = {
    "telegram bot api url": (f"curl https://api.telegram.org/bot{BOT_TOKEN}/getMe", BOT_TOKEN),
    "url with an empty user": ("redis://:hunter2pass@cache:6379/0", "hunter2pass"),
    "password flag with a space": ("psql --password hunter2! -h db", "hunter2!"),
    "access_token in a query": ("https://h/cb?access_token=Zq8Lm3Xv9Kp2Rt7Wn4Yb6Hc1", "Zq8Lm3Xv9Kp2Rt7Wn4Yb6Hc1"),
    "PGPASSWORD": ("PGPASSWORD=hunter3! psql", "hunter3!"),
    "lower-case aws ini": ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                           "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    "value after a mask look-alike": ("token: [secret: a]Zq8Lm3Xv9Kp2Rt7Wn4Yb6Hc1", "Zq8Lm3Xv9Kp2Rt7Wn4Yb6Hc1"),
}


@pytest.mark.parametrize("text, secret", FORMS.values(), ids=list(FORMS))
def test_scrub_masks_the_form(text, secret):
    assert secret not in privacy.scrub(text), privacy.scrub(text)


@pytest.mark.parametrize("text, secret", FORMS.values(), ids=list(FORMS))
def test_the_form_reaches_neither_the_model_nor_session_md(text, secret, tmp_path, throwaway_home):
    claude(throwaway_home, FIRST, [("user", "why does this fail"), ("assistant", f"I ran: {text}")])
    rec = record(tmp_path)
    llm = Scripted(group=one_pursuit())
    Atlas(rec, llm).backfill(NOW)
    (session,) = (tmp_path / "atlas" / "evidence").glob("*/*/*/*/session.md")
    assert not [title for title, input in llm.inputs if secret in input], "the secret was sent to the model"
    assert secret not in session.read_text()
