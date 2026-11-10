"""The conftest's guard, run on tests of its own in a separate pytest. The HOME their
`monkeypatch.undo()` restores is this test's throwaway home, standing in for a real one."""
from pathlib import Path

INNER = '''
from atlas import sources
from atlas.app import Atlas
from test_commit import SETTINGS


def test_undoes_the_home_patch_then_lists_the_stores(monkeypatch):
    monkeypatch.undo()
    sources.probe(SETTINGS)


def test_undoes_the_home_patch_then_opens_the_record(monkeypatch):
    monkeypatch.undo()
    Atlas.open()


def test_undoes_the_home_patch(monkeypatch):
    monkeypatch.undo()


def test_keeps_the_home_patch():
    sources.probe(SETTINGS)
    Atlas.open()
'''


def test_a_test_that_undoes_the_home_patch_fails_before_atlas_resolves_a_path(pytester, throwaway_home, monkeypatch):
    tests = Path(__file__).resolve().parent
    monkeypatch.setenv("HOME", str(throwaway_home))
    monkeypatch.setenv("PYTHONPATH", f"{tests}:{tests.parent}")
    pytester.makepyfile(test_inner=INNER)
    result = pytester.runpytest_subprocess("-p", "conftest", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, failed=2, errors=3)
    result.stdout.fnmatch_lines([
        "*ERROR at teardown of test_undoes_the_home_patch_then_lists_the_stores*",
        "the test left HOME outside its temporary folder*",
        "*ERROR at teardown of test_undoes_the_home_patch_then_opens_the_record*",
        "the test left HOME outside its temporary folder*",
        "*ERROR at teardown of test_undoes_the_home_patch *",
        "the test left HOME outside its temporary folder*",
        "*test_undoes_the_home_patch_then_lists_the_stores*",
        "E * conftest.LeftHome: Atlas resolved a T3Code store outside the test's temporary folder",
        "*test_undoes_the_home_patch_then_opens_the_record*",
        "E * conftest.LeftHome: Atlas resolved its record outside the test's temporary folder"])
