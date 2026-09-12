"""Run the join from a shell without Apify, for development and spot checks.

    python -m src.kalshi_weather.cli --stations NYC MDW --kind high
    python -m src.kalshi_weather.cli --status settled --date-from 2026-09-08 --date-to 2026-09-09 --no-enrich
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .fetch import KalshiClient, NwsClient
from .service import enrich_events, list_events


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["both", "high", "low"], default="both")
    ap.add_argument("--status", choices=["open", "closed", "settled"], default="open")
    ap.add_argument("--stations", nargs="*", default=None, help="CLI station ids, e.g. NYC MDW MIA")
    ap.add_argument("--date-from", type=date.fromisoformat, default=None)
    ap.add_argument("--date-to", type=date.fromisoformat, default=None)
    ap.add_argument("--max", type=int, default=20)
    ap.add_argument("--no-enrich", action="store_true")
    ap.add_argument("--hourly", action="store_true", help="keep the hourly forecast periods")
    ap.add_argument("--delay", type=float, default=0.25)
    a = ap.parse_args(argv)

    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    kalshi, nws = KalshiClient(delay_s=a.delay), NwsClient(delay_s=a.delay)
    events = list_events(kalshi, kind=a.kind, status=a.status, stations=a.stations, date_from=a.date_from, date_to=a.date_to, max_events=a.max, progress=log)
    if not a.no_enrich:
        n = enrich_events(nws, events, hourly_periods=a.hourly, progress=log)
        log(f"{n}/{len(events)} enriched")
    log(f"{kalshi.requests_made} Kalshi + {nws.requests_made} NWS requests")
    json.dump(events, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
