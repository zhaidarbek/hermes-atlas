import subprocess

from atlas import _hermes
from atlas.app import Atlas


def test_setup_names_every_place_it_writes_outside_the_plugin(throwaway_home, monkeypatch):
    def hermes(*args, timeout=60):
        out = '{"platforms": {"telegram": {}}}' if args[0] == "send" else "  Name: atlas-tick\n"
        return subprocess.CompletedProcess(["hermes", *args], 0, out, "")

    monkeypatch.setattr(_hermes, "run", hermes)

    shown = Atlas.open().setup().render()

    told = shown.split("What Atlas does on this machine:\n", 1)[1]
    assert "~/atlas" in told
    assert "~/.hermes/scripts/atlas_tick.sh" in told
    assert "cron job atlas-tick" in told
    assert (throwaway_home / ".hermes/scripts/atlas_tick.sh").is_file()
