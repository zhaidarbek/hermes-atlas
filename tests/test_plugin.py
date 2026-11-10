import ast
import inspect
import re
import textwrap
import threading
import tomllib
from pathlib import Path

import yaml

import atlas
from atlas import agentapi
from harness.home import FakePluginContext

PACKAGE = Path(atlas.__file__).parent
MANIFEST = yaml.safe_load((PACKAGE / "plugin.yaml").read_text())


def documented_manifest() -> dict:
    block = atlas.__doc__.split("tested equal to the file:\n", 1)[1]
    lines = [re.sub(r"\s+#.*$", "", line[2:]) for line in block.splitlines() if line.startswith("  ") and line.strip()]
    return yaml.safe_load("\n".join(line for line in lines if line.strip()))


def test_the_manifest_matches_the_package_docstring():
    assert MANIFEST == documented_manifest()
    assert MANIFEST["manifest_version"] == 2


def test_provides_tools_and_hooks_match_what_register_registers():
    ctx = FakePluginContext()
    atlas.register(ctx)
    assert MANIFEST["provides_tools"] == [t.name for t in agentapi.TOOLS] == list(ctx.tools)
    assert sorted(MANIFEST["provides_hooks"]) == sorted(ctx.hooks)
    assert set(ctx.cli_commands) == {"atlas"} and set(ctx.commands) == {"atlas"}
    assert ctx.skills["atlas"].is_file()


def test_register_reads_no_lazy_attribute_and_starts_no_thread(monkeypatch):
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self))
    ctx = FakePluginContext()
    atlas.register(ctx)
    assert ctx.lazy_reads == []
    assert started == []


def test_one_version_everywhere():
    pyproject = tomllib.loads((PACKAGE.parent / "pyproject.toml").read_text())
    assert MANIFEST["version"] == atlas.__version__ == pyproject["project"]["version"]


def test_the_shells_clock_is_the_fake_clock_when_one_is_set(monkeypatch):
    monkeypatch.setenv("ATLAS_NOW", "2026-11-16T07:30:00+01:00")
    assert atlas._now().isoformat() == "2026-11-16T07:30:00+01:00"


README = (PACKAGE.parent / "README.md").read_text()


def registered() -> FakePluginContext:
    ctx = FakePluginContext()
    atlas.register(ctx)
    return ctx


def test_the_manifest_declares_no_middleware_or_env_because_register_registers_none():
    assert MANIFEST["provides_middleware"] == [] and MANIFEST["requires_env"] == []


def test_the_readme_names_every_surface_register_registers():
    ctx = registered()
    names = [*ctx.tools, *ctx.hooks, *ctx.skills, *ctx.auxiliary_tasks, *(f"/{c}" for c in ctx.commands),
             *(f"hermes {c}" for c in ctx.cli_commands)]
    missing = [n for n in names if f"`{n}" not in README]
    missing += [s for s in atlas.SUBCOMMANDS if f"`hermes atlas {s}`" not in README and f"`{s}`" not in README]
    assert missing == []


def test_the_readme_says_whether_atlas_has_a_dashboard_tab():
    assert (PACKAGE / "dashboard").exists() == ("Atlas has no dashboard tab" not in README)


def test_pyproject_and_the_manifest_bound_the_same_dependencies():
    pyproject = tomllib.loads((PACKAGE.parent / "pyproject.toml").read_text())
    assert pyproject["project"]["dependencies"] == MANIFEST["python_dependencies"]
    assert all("<" in dep and ">=" in dep for dep in MANIFEST["python_dependencies"])


def is_stub(fn) -> bool:
    body = [n for n in ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0].body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    return len(body) == 1 and isinstance(body[0], ast.Raise) and "NotImplementedError" in ast.dump(body[0])


def rows_naming(name: str) -> list[str]:
    return [line for line in README.splitlines() if line.startswith("|") and f"`{name}`" in line]


def test_the_readme_names_every_route_the_dashboard_mounts():
    from fastapi import FastAPI

    from atlas.dashboard import plugin_api
    app = FastAPI()
    app.include_router(plugin_api.router)
    routes = [f"`{method.upper()} {path}`" for path, methods in app.openapi()["paths"].items() for method in methods]
    assert len(routes) == 6
    assert [r for r in routes if r not in README] == []
    assert "/api/plugins/atlas" in README


def test_the_readme_says_not_built_of_a_surface_exactly_while_it_is_a_stub():
    ctx = registered()
    stubs = {"hermes atlas mcp": is_stub(agentapi.serve_mcp),
             **{f"hermes atlas {s}": run is atlas._not_built for s, (_, run) in atlas.SUBCOMMANDS.items() if s != "mcp"},
             **{tool: is_stub(agentapi.call) for tool in ctx.tools},
             **{name: False for name in (*ctx.hooks, *ctx.auxiliary_tasks, *(f"/{c}" for c in ctx.commands))}}
    wrong = {name: stub for name, stub in stubs.items()
             if not rows_naming(name) or any(("not built" in row.lower()) != stub for row in rows_naming(name))}
    assert wrong == {}
