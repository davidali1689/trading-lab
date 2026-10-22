"""Upload one premarket finding JSON file to the journal bucket.

Usage:
    uv run python scripts/upload_finding.py findings/AAPL.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from trading_lab.journal.findings import upload_premarket_finding


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: upload_finding.py <finding.json>", file=sys.stderr)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = upload_premarket_finding(payload)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
