"""Hermes v0.23.0's dashboard imports this file by path, as a module of its own, and mounts its
`router` at /api/plugins/atlas/. The atlas package is this file's grandparent folder. It is taken
from the modules this process already holds when one of them was loaded from that folder (Hermes
loads the plugin as hermes_plugins.atlas), and is loaded from that folder under a name of its own
otherwise. sys.path stays as it is: with the plugins folder on it, every other folder there, also
a plugin the owner never enabled, could be imported in place of a module the dashboard imports
later. The Board's and the Settings tab's routes share the one router."""
import importlib
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter

_INIT = Path(__file__).resolve().parents[1] / "__init__.py"
_OWN = "hermes_dashboard_plugin_atlas_package"


def _package() -> str:
    """The name the atlas package beside this file is imported under in this process. With no
    package beside it (this file copied out alone), the name atlas is installed under."""
    if not _INIT.is_file():
        return "atlas"
    for name in ("hermes_plugins.atlas", "atlas", _OWN):
        loaded = getattr(sys.modules.get(name), "__file__", None)
        if loaded and Path(loaded).resolve() == _INIT:
            return name
    spec = importlib.util.spec_from_file_location(_OWN, _INIT, submodule_search_locations=[str(_INIT.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[_OWN] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[_OWN]
        raise
    return _OWN


_ATLAS = _package()
board_api = importlib.import_module(".board_api", _ATLAS)
settings_api = importlib.import_module(".settings_api", _ATLAS)

router = APIRouter()
router.include_router(board_api.router)
router.include_router(settings_api.router)
