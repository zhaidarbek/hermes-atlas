"""fsaudit A3. `_regular`: a FIFO in a store file's place "is refused instead of waited on", and
test_collect has the same for a FIFO named as a store database. SQLite is given the pin's name
(/proc/self/fd/<n>), reads that link and opens what it says by name, with no O_NONBLOCK. Once the
pinned database is unlinked the link reads "<path> (deleted)": a FIFO of that name in the store
folder is opened and waited on for good, with the tick lease held."""
import os
import threading

from atlas.model import Sent
from atlas.sources import _adapters
from test_collect import hermes


def test_a_fifo_named_like_a_deleted_database_is_not_waited_on(throwaway_home, monkeypatch):
    root = throwaway_home / ".hermes"
    db = hermes(root, [("chat", "cli", None, None, "invented")], [("chat", "user", "an invented turn")])
    fifo = root / "state.db (deleted)"
    real, calls = _adapters._sidecars, []

    def unlinked_after_the_pin(folder, base):        # between _pinned and sqlite3.connect
        if not calls:
            db.unlink()                              # as a Harness replacing its database does
            os.mkfifo(fifo)
        calls.append(base)
        return real(folder, base)

    monkeypatch.setattr(_adapters, "_sidecars", unlinked_after_the_pin)
    done = []

    def read():
        try:
            list(_adapters.Hermes()._units(root, Sent()))
        except Exception as error:
            done.append(type(error).__name__)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(3)
    waited = reader.is_alive()
    while reader.is_alive():                         # let SQLite's open return, so the test leaves nothing waiting
        try:
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            pass
        reader.join(0.1)
    assert not waited, "SQLite waited on a FIFO named '<database> (deleted)' until a writer opened it"
