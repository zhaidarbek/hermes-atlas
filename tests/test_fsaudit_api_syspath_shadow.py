"""fsaudit: dashboard/plugin_api.py puts the whole plugins folder FIRST on sys.path, so any other
folder beside atlas/ there (a plugin the owner installed but never enabled, say) is importable as a
top-level module by the dashboard process, ahead of the standard library and site-packages.

Hermes v0.23.0 imports a plugin's Python only once it is enabled (web_server_dashboard.py,
_mount_plugin_api_routes, GHSA-mcfc-hp25-cjv7), and does not itself put the plugins folder on
sys.path. After Atlas's router is mounted, the first `import <name>` of any module not imported
yet resolves to plugins/<name>/ instead."""
import importlib.util
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_a_sibling_plugin_folder_does_not_shadow_a_module_the_dashboard_imports_later(tmp_path, monkeypatch):
    plugins = tmp_path / "hermes" / "plugins"
    api = plugins / "atlas" / "dashboard" / "plugin_api.py"
    api.parent.mkdir(parents=True)
    shutil.copy(REPO / "atlas" / "dashboard" / "plugin_api.py", api)
    # A standard-library module nothing has imported yet stands for "any module imported later".
    name = next(n for n in ("sndhdr", "chunk", "colorsys", "wave", "mailcap", "nntplib", "xdrlib", "tabnanny", "pyclbr")
                if n not in sys.modules and importlib.util.find_spec(n) is not None)
    stdlib = importlib.util.find_spec(name).origin
    other = plugins / name                       # another plugin's folder, installed and NOT enabled
    other.mkdir()
    (other / "__init__.py").write_text("RAN_IN_THE_DASHBOARD = True\n")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "hermes_dashboard_plugin_atlas", raising=False)

    spec = importlib.util.spec_from_file_location("hermes_dashboard_plugin_atlas", api)   # as Hermes mounts it
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.router.routes, "the router mounted"

    importlib.invalidate_caches()
    found = importlib.util.find_spec(name).origin
    assert found == stdlib, (f"after Atlas's router is mounted, `import {name}` in the dashboard process loads "
                             f"{found}, a file in the plugins folder, instead of {stdlib}")
