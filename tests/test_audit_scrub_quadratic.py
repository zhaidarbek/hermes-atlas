"""Audit finding: privacy.scrub takes quadratic time on one unbroken run of upper-case names.

25 KB of "KEY_" took about 2.4 s here and 100 KB about 38 s, with no timeout. Every transcript turn,
model input and quote check passes scrub, so one such line in a Session (a pasted page, a tool's
output an agent repeats) stalls the unattended tick while it holds the tick lease."""
import time

from atlas import privacy


def test_scrub_stays_fast_on_a_long_run_of_key_names():
    start = time.perf_counter()
    privacy.scrub("KEY_" * 6250)                               # 25 KB
    assert time.perf_counter() - start < 1.0
