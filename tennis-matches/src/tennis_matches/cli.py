"""Local runner, no Apify needed.

    python -m src.tennis_matches.cli schedule --tour atp --max 5 --enrich
    python -m src.tennis_matches.cli results --date 2026-09-08 --tournament "US Open"
    python -m src.tennis_matches.cli schedule --player Alcaraz --days 3 --enrich --odds-history
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .fetch import Client
from .service import enrich_matches, list_matches


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tennis_matches")
    ap.add_argument("mode", choices=["schedule", "results", "live"])
    ap.add_argument("--date", help="YYYY-MM-DD, default today")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--tour", default="all", choices=["all", "atp", "wta"])
    ap.add_argument("--doubles", action="store_true")
    ap.add_argument("--tournament")
    ap.add_argument("--player")
    ap.add_argument("--status", choices=["scheduled", "in_progress", "finished"])
    ap.add_argument("--max", type=int, default=50, dest="max_matches")
    ap.add_argument("--enrich", action="store_true")
    ap.add_argument("--enrich-limit", type=int)
    ap.add_argument("--latest", type=int, default=10)
    ap.add_argument("--odds-history", action="store_true")
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    log = (lambda m: None) if a.quiet else (lambda m: print(m, file=sys.stderr))
    client = Client(delay_s=a.delay)
    matches = list_matches(
        client, kind=a.mode, start=date.fromisoformat(a.date) if a.date else None, days=a.days,
        tour=a.tour, doubles=a.doubles, tournament=a.tournament, player=a.player, status=a.status,
        max_matches=a.max_matches, progress=log,
    )
    if a.enrich:
        n = enrich_matches(client, matches, limit=a.enrich_limit, latest_limit=a.latest, odds_history=a.odds_history, progress=log)
        log(f"enriched {n} matches")
    log(f"{len(matches)} matches, {client.requests_made} requests")
    json.dump(matches, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
