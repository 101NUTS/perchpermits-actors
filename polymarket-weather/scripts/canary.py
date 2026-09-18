"""Canary: hit the live sources with a small fixed workload and fail loudly if coverage or
the settlement match drops. Runs with the other canaries (scripts/canary_all.py).

    python scripts/canary.py            # exit 0 = healthy, 1 = degraded, 2 = broken
    python scripts/canary.py --json     # machine-readable report on stdout
    python scripts/canary.py --log      # also append one line to docs/reliability/runs.jsonl

Three checks:
1. the open listing: enough current ladders, and every one parses;
2. live joins for four cities on four continents: station found, reports for the day;
3. settled ladders from two days ago: the recomputed value lands in the bracket Polymarket
   paid. This is the product's promise, so a mismatch is `broken`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.polymarket_weather.fetch import FetchError, GammaClient, MetarClient, NwsClient  # noqa: E402
from src.polymarket_weather.parse import ParseDrift, event_row  # noqa: E402
from src.polymarket_weather.service import NotServable, current_open, find_event, join, slug_for  # noqa: E402

LIVE = [("nyc", "high"), ("chicago", "low"), ("london", "high"), ("tokyo", "high")]
SETTLED = [("nyc", "high"), ("london", "high"), ("denver", "low")]
ORDER = ["healthy", "degraded", "broken"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", action="store_true", help="append to docs/reliability/runs.jsonl")
    a = ap.parse_args(argv)
    t0 = time.time()
    gamma, metar, nws = GammaClient(delay_s=0.2), MetarClient(delay_s=0.2), NwsClient(delay_s=0.2)
    problems: list[dict] = []
    report: dict = {"ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}

    def check(label: str, ok: bool, detail: str, severity: str = "broken") -> None:
        if not ok:
            problems.append({"check": label, "severity": severity, "detail": detail})

    # 1. open listing
    try:
        events = current_open(gamma.open_temperature_events())
        report["open_rows"] = len(events)
        drift = []
        for ev in events:
            try:
                event_row(ev)
            except ParseDrift as exc:
                drift.append(f"{ev.get('slug')}: {exc}")
        check("open_rows", len(events) >= 40, f"only {len(events)} current ladders listed (expect ~100 across ~50 cities)",
              "broken" if not events else "degraded")
        check("parse", len(drift) <= max(1, len(events) // 20), f"{len(drift)} of {len(events)} did not parse: {drift[:2]}")
    except FetchError as exc:
        problems.append({"check": "open_list", "severity": "broken", "detail": str(exc)})

    # 2. live joins
    joined = 0
    for city, kind in LIVE:
        try:
            row = join(find_event(gamma, city, None, kind), metar, nws)
            ok = bool(row["station"].get("icao")) and bool(row.get("brackets"))
            joined += ok
            check(f"join_{city}", ok, f"{city} {kind}: no station or no brackets")
            if not (row.get("observations") or {}).get("count"):
                # early in the station's day there may be none yet; only degraded
                check(f"obs_{city}", False, f"{city} {kind} ({row['station'].get('icao')}): no reports yet for {row['target_date']}", "degraded")
        except (NotServable, FetchError, ParseDrift) as exc:
            problems.append({"check": f"join_{city}", "severity": "broken", "detail": str(exc)[:200]})
    report["joined_ok"] = joined

    # 3. settled match, two days back
    day = date.today() - timedelta(days=2)
    matched = settled = 0
    for city, kind in SETTLED:
        try:
            row = join(gamma.event(slug_for(city, day, kind)), metar, nws, with_forecast=False)
        except (NotServable, FetchError, ParseDrift) as exc:
            problems.append({"check": f"settled_{city}", "severity": "degraded", "detail": str(exc)[:200]})
            continue
        v = row.get("verdict") or {}
        if not v.get("paid_bracket"):
            check(f"settled_{city}", False, f"{city} {kind} {day}: not settled yet", "degraded")
            continue
        settled += 1
        ok = v.get("final_if_complete") == v.get("paid_bracket")
        matched += ok
        check(f"match_{city}", ok, f"{city} {kind} {day}: recomputed {v.get('running_value')} -> {v.get('final_if_complete')}, paid {v.get('paid_bracket')}")
    report.update(settled_rows=settled, settled_matched=matched)

    report["seconds"] = round(time.time() - t0, 1)
    report["requests"] = gamma.requests_made + metar.requests_made + nws.requests_made
    report["problems"] = problems
    worst = max((p["severity"] for p in problems), default="healthy", key=ORDER.index)
    report["verdict"] = worst
    print(json.dumps(report, indent=1) if a.json else f"{worst}: {report}")
    if a.log:
        log_path = Path(__file__).resolve().parents[3] / "docs" / "reliability" / "runs.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = {"ran_at": report["ran_at"], "actor": "polymarket-weather", "kind": "canary", "verdict": worst,
                **{k: report[k] for k in ("open_rows", "joined_ok", "settled_rows", "settled_matched", "requests", "seconds") if k in report},
                "problems": problems}
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    return ORDER.index(worst)


if __name__ == "__main__":
    sys.exit(main())
