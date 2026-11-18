"""fsaudit A5. No read of a store has a size bound (`_slurp` reads a whole file, `_unzstd` a whole
stream, `_first_line` a whole line). A compressed Codex rollout of under 1 KiB is expanded to 16 MiB
in memory, and its turn is written to session.md uncompressed: about 20,000 times its size on disk,
so a 1 MiB rollout would cost about 20 GiB of memory and of the record's disk on an unattended
tick. The bound asserted here (1,000 times) is this audit's, since Atlas states none."""
import json

import zstandard

from atlas.app import Atlas
from test_collect import CX, NOW, record, rollout


def test_a_compressed_rollout_is_not_expanded_without_bound(tmp_path, throwaway_home):
    path = rollout(throwaway_home / ".codex", "small", ["an invented ask"], ["ok"], zst=True)
    lines = zstandard.ZstdDecompressor().decompress(path.read_bytes(), max_output_size=1 << 20)
    long = json.dumps({"timestamp": NOW.isoformat(), "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "UserMessage", "content": [{"type": "text", "text": "A" * (16 << 20)}]}}}).encode() + b"\n"
    path.write_bytes(zstandard.ZstdCompressor(level=19).compress(lines + long))
    stored = path.stat().st_size
    assert stored < 1024
    rec = record(tmp_path, CX)
    Atlas(rec, None).backfill(NOW)
    written = max(p.stat().st_size for p in rec.root.rglob("*") if p.is_file())
    assert written <= 1000 * stored, f"a {stored}-byte rollout put a {written}-byte file in the record"
