"""Local runner, no Apify needed.

    python -m src.nashville_permits.cli search --scope kitchen --days 30 --max 20
    python -m src.nashville_permits.cli search --zip 37206 37216 --enrich --max 5
    python -m src.nashville_permits.cli contractors --scope pool --days 365
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

from .epermits import DirCache
from .scopes import SCOPES
from .service import enrich_permits, rank_contractors, search_permits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nashville_permits")
    ap.add_argument("mode", choices=["search", "contractors"])
    ap.add_argument("--datasets", default="both", choices=["issued", "applications", "both"])
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--days", type=int, help="shortcut for --from (today minus N days)")
    ap.add_argument("--scope", default="any", choices=list(SCOPES))
    ap.add_argument("--keyword")
    ap.add_argument("--address")
    ap.add_argument("--parcel")
    ap.add_argument("--zip", nargs="*", dest="zips")
    ap.add_argument("--applicant")
    ap.add_argument("--permit")
    ap.add_argument("--type", dest="permit_type")
    ap.add_argument("--min", type=float, dest="min_valuation")
    ap.add_argument("--max-val", type=float, dest="max_valuation")
    ap.add_argument("--residential", action="store_true")
    ap.add_argument("--max", type=int, default=200, dest="max_records")
    ap.add_argument("--enrich", action="store_true")
    ap.add_argument("--enrich-limit", type=int)
    ap.add_argument("--cache-dir", default=".cache/epermits")
    ap.add_argument("--min-permits", type=int, default=1)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    log = (lambda m: None) if a.quiet else (lambda m: print(m, file=sys.stderr))
    if a.days and not a.date_from:
        a.date_from = (date.today() - timedelta(days=a.days)).isoformat()

    records = search_permits(
        datasets=a.datasets, date_from=a.date_from, date_to=a.date_to, scope=a.scope,
        keyword=a.keyword, address=a.address, parcel=a.parcel, zips=a.zips,
        applicant=a.applicant, permit_number=a.permit, permit_type=a.permit_type,
        min_valuation=a.min_valuation, max_valuation=a.max_valuation,
        residential_only=a.residential, max_records=a.max_records, progress=log,
    )
    if a.enrich:
        n = enrich_permits(records, DirCache(a.cache_dir), limit=a.enrich_limit, progress=log)
        log(f"enriched {n} permits")

    result = records if a.mode == "search" else rank_contractors(records, min_permits=a.min_permits)
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
