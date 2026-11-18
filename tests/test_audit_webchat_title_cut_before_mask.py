"""Audit finding: a web chat's title is cut to 80 characters before it is masked.

sources._webchat._title returns first[:80] bare; the record masks the title afterwards. A secret
the cut divides no longer matches its detector, so its first part is written to session.md, the
search index and the model's "Title:" line. sources._adapters._title masks first, then cuts."""
from atlas import privacy
from atlas.sources import _webchat

PASSWORD = "hunter2!SuperSecretPw9"
SAID = f"please connect to the staging database for me with postgres://app:{PASSWORD}@db.internal/x"


def test_a_title_holds_no_part_of_a_secret_the_cut_divides():
    assert PASSWORD not in privacy.scrub(SAID)                 # whole, the URL's password is masked
    title = privacy.scrub(_webchat._title(SAID))               # the record masks the title it is given
    assert PASSWORD[:8] not in title, title
