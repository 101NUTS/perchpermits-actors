"""Run the join from the command line, no Apify account needed.

    python -m src.polymarket_weather.cli --city nyc                       # nearest open highest-temperature ladder
    python -m src.polymarket_weather.cli --city london --kind low --date 2026-09-17
    python -m src.polymarket_weather.cli --slug highest-temperature-in-tokyo-on-september-18-2026
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .fetch import GammaClient, MetarClient, NwsClient
from .service import NotServable, find_event, join


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Polymarket temperature ladder joined to its settling station")
    ap.add_argument("--slug", help="event slug (highest-temperature-in-<city>-on-<month>-<day>-<year>)")
    ap.add_argument("--city", help="Polymarket city slug: nyc, london, tokyo, los-angeles, ...")
    ap.add_argument("--kind", choices=["high", "low"], default="high")
    ap.add_argument("--date", help="target date YYYY-MM-DD (default: nearest open ladder)")
    a = ap.parse_args(argv)
    if not a.slug and not a.city:
        ap.error("give --slug or --city")
    gamma, metar, nws = GammaClient(), MetarClient(), NwsClient()
    try:
        ev = gamma.event(a.slug) if a.slug else find_event(gamma, a.city, date.fromisoformat(a.date) if a.date else None, a.kind)
        print(json.dumps(join(ev, metar, nws), indent=1, ensure_ascii=False))
    except NotServable as exc:
        print(f"not servable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
