"""Hermes v0.23.0's dashboard imports this file by path, as a module of its own, and mounts its
`router` at /api/plugins/atlas/. The atlas package is this file's grandparent folder, so the
plugins folder above it goes on sys.path and the package is imported by name. The Board's and
the Settings tab's routes share the one router."""
import sys
from pathlib import Path

_PLUGINS = str(Path(__file__).resolve().parents[2])
if _PLUGINS not in sys.path:
    sys.path.insert(0, _PLUGINS)

from fastapi import APIRouter  # noqa: E402

from atlas import board_api, settings_api  # noqa: E402

router = APIRouter()
router.include_router(board_api.router)
router.include_router(settings_api.router)
