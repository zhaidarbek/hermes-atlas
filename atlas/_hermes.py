"""The public `hermes` CLI as a subprocess: Atlas's only way to send and to schedule, because
Hermes gives a plugin no call for either outside a live session."""
from __future__ import annotations

import subprocess


def run(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["hermes", *args], capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return subprocess.CompletedProcess(["hermes", *args], 127, "", "hermes is not on PATH")
