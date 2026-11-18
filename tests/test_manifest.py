import json
import re
import tomllib
from pathlib import Path

import yaml

import atlas

PACKAGE = Path(atlas.__file__).parent
MANIFEST = yaml.safe_load((PACKAGE / "plugin.yaml").read_text())
PROJECT = tomllib.loads((PACKAGE.parent / "pyproject.toml").read_text())["project"]


def documented_manifest() -> dict:
    block = atlas.__doc__.split("tested equal to the file:\n", 1)[1]
    lines = [re.sub(r"\s+#.*$", "", line[2:]) for line in block.splitlines() if line.startswith("  ") and line.strip()]
    return yaml.safe_load("\n".join(line for line in lines if line.strip()))


def test_the_manifest_matches_the_package_docstring():
    assert MANIFEST == documented_manifest()
    assert MANIFEST["manifest_version"] == 2


def test_the_manifest_and_pyproject_declare_the_same_dependencies():
    assert MANIFEST["python_dependencies"] == PROJECT["dependencies"]


def test_one_version_everywhere():
    dashboard = json.loads((PACKAGE / "dashboard" / "manifest.json").read_text())
    assert MANIFEST["version"] == atlas.__version__ == PROJECT["version"] == dashboard["version"]
