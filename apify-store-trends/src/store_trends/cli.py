"""Local runner, no Apify needed. Prints one JSON object per line.

    python -m src.store_trends.cli search "google trends" --min-users 20 --max 50
    python -m src.store_trends.cli search bizbuysell --no-targets
    python -m src.store_trends.cli full --out catalog.jsonl --max 5000
"""

from __future__ import annotations

import argparse
import json
import sys

from .fetch import Client
from .service import run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="store_trends")
    ap.add_argument("mode", choices=["search", "full"])
    ap.add_argument("query", nargs="?", help="search text (search mode only)")
    ap.add_argument("--min-users", type=int, default=5, dest="min_users", help="drop actors under this many users in 30 days")
    ap.add_argument("--max", type=int, default=200, dest="max_actors")
    ap.add_argument("--no-targets", action="store_true", help="skip the per-target aggregate rows")
    ap.add_argument("--out", help="write JSON lines here instead of stdout")
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.mode == "search" and not a.query:
        ap.error("search mode needs a query")

    log = (lambda m: None) if a.quiet else (lambda m: print(m, file=sys.stderr))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # store titles carry emoji; Windows consoles default to cp1252
    client = Client(delay_s=a.delay)
    rows, targets = run(
        client, mode=a.mode, query=a.query, min_users_30d=a.min_users, max_actors=a.max_actors,
        include_targets=not a.no_targets, progress=log,
    )
    log(f"{len(rows)} actor rows, {len(targets)} target rows, {client.requests_made} requests")
    out = open(a.out, "w", encoding="utf-8") if a.out else sys.stdout
    try:
        for r in targets + rows:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    finally:
        if a.out:
            out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
