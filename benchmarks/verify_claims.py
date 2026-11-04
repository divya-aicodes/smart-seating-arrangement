"""Report whether stored measurements qualify 100K or 500K PostgreSQL import claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.common import qualifying_import_claims


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    args = parser.parse_args()
    measurements = [json.loads(path.read_text(encoding="utf-8")) for path in args.results]
    claims = qualifying_import_claims(measurements)
    print(json.dumps(claims, indent=2, sort_keys=True))
    return 0 if all(claims.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
