"""python -m harness (from tests/): run every claimed scene and week mode, print the report.
Exits non-zero only when a claimed scene fails."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.scenes import claimed, report, run_scenes, run_week  # noqa: E402


def main() -> int:
    results = run_scenes(claimed())
    print(report(results, run_week()))
    return 1 if any(r.status == "fail" for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
