"""Canary: hit the live source with a fixed small workload and fail loudly if
coverage drops. Meant to run two or three times a day from a scheduler.

    python scripts/canary.py            # exit 0 = healthy, 1 = degraded, 2 = broken
    python scripts/canary.py --json     # machine-readable report on stdout
    python scripts/canary.py --log      # also append one line to docs/reliability/runs.jsonl

Set NTFY_TOPIC (and optionally NTFY_SERVER, default https://ntfy.sh) to push the
verdict to the ntfy app on a phone: healthy runs are sent at low priority (silent),
degraded at default, broken at high. Set NTFY_QUIET=1 to send only degraded and broken.

Thresholds are deliberately loose so lower-tier events (no odds, no ranking)
do not page anyone; a real outage or layout change trips them at once.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tennis_matches.fetch import Client  # noqa: E402
from src.tennis_matches.service import SourceDrift, enrich_matches, list_matches  # noqa: E402

REQUIRED_TOP = ["match_id", "match_url", "date", "status", "tournament", "home", "away", "odds", "result", "start_utc", "fetched_at_utc"]
REQUIRED_ENRICH = ["status", "home", "away", "h2h", "bookmakers", "bookmaker_count", "best_odds", "market"]

# Tournaments in the Americas publish the day's order of play in their morning, so until
# mid-afternoon UTC a new tournament day (Mondays especially) lists matches without times.
EARLY_UTC_HOUR = 16
UTC_FLOOR_EARLY = 5


def check(label: str, ok: bool, detail: str, problems: list, severity: str = "broken") -> None:
    if not ok:
        problems.append({"check": label, "severity": severity, "detail": detail})


def utc_required(n_rows: int, utc_hour: int) -> tuple[int, str]:
    """How many listed matches must carry start_utc at this hour, and the rule applied."""
    if utc_hour < EARLY_UTC_HOUR:
        return min(UTC_FLOOR_EARLY, n_rows), f"at least {UTC_FLOOR_EARLY} before {EARLY_UTC_HOUR:02d}:00 UTC"
    return math.ceil(0.7 * n_rows), "70% from 16:00 UTC"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--matches", type=int, default=25)
    ap.add_argument("--log", action="store_true", help="append to docs/reliability/runs.jsonl")
    a = ap.parse_args(argv)
    problems: list[dict] = []
    now = time.gmtime()
    report: dict = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", now)}
    client = Client(delay_s=0.4)
    t0 = time.time()

    # 1. schedule for today, all tours, enriched
    try:
        rows = list_matches(client, kind="schedule", start=date.today(), tour="all", max_matches=a.matches)
    except SourceDrift as exc:
        problems.append({"check": "schedule_list", "severity": "broken", "detail": str(exc)})
        rows = []
    report["schedule_rows"] = len(rows)
    check("schedule_rows", len(rows) >= 5, f"only {len(rows)} rows listed", problems)
    for r in rows:
        missing = [k for k in REQUIRED_TOP if k not in r]
        if missing:
            problems.append({"check": "schedule_fields", "severity": "broken", "detail": f"match {r.get('match_id')} missing {missing}"})
            break
    if rows:
        try:
            n_ok = enrich_matches(client, rows)
        except SourceDrift as exc:
            problems.append({"check": "enrich", "severity": "broken", "detail": str(exc)})
            n_ok = 0
        report["enriched_ok"] = n_ok
        check("enrich_rate", n_ok >= 0.8 * len(rows), f"{n_ok}/{len(rows)} enriched", problems)
        enriched = [r for r in rows if (r.get("enrichment") or {}).get("status") in ("ok", "partial")]
        for r in enriched:
            missing = [k for k in REQUIRED_ENRICH if k not in r["enrichment"]]
            if missing:
                problems.append({"check": "enrich_fields", "severity": "broken", "detail": f"match {r['match_id']} enrichment missing {missing}"})
                break
        with_books = sum(1 for r in enriched if r["enrichment"]["bookmaker_count"] > 0)
        with_latest = sum(1 for r in enriched if r["enrichment"]["home"]["latest_matches"])
        with_utc = sum(1 for r in rows if r.get("start_utc"))
        report.update({"with_bookmakers": with_books, "with_latest": with_latest, "with_start_utc": with_utc})
        if enriched:
            # Early in the site's day the list is ITF/UTR matches that no book prices, so
            # require a few priced matches rather than a share.
            check("bookmaker_coverage", with_books >= min(3, len(enriched)), f"{with_books}/{len(enriched)} enriched rows have bookmakers", problems, "degraded")
            check("latest_coverage", with_latest >= 0.7 * len(enriched), f"{with_latest}/{len(enriched)} have latest matches", problems, "degraded")
        need, rule = utc_required(len(rows), now.tm_hour)
        check("utc_coverage", with_utc >= need, f"{with_utc}/{len(rows)} have start_utc (need {need}: {rule})", problems, "degraded")

    # 2. yesterday's results, plain
    try:
        res = list_matches(client, kind="results", start=date.today() - timedelta(days=1), tour="all", max_matches=200)
        report["results_rows"] = len(res)
        check("results_rows", len(res) >= 5, f"only {len(res)} results", problems)
        check("results_scored", all(r["status"] == "finished" and r["result"]["winner"] for r in res), "unscored rows on results page", problems)
    except SourceDrift as exc:
        problems.append({"check": "results_list", "severity": "broken", "detail": str(exc)})

    # 3. live snapshot must not error (may legitimately be empty at night)
    try:
        live = list_matches(client, kind="live", max_matches=20)
        report["live_rows"] = len(live)
    except SourceDrift as exc:
        problems.append({"check": "live_list", "severity": "broken", "detail": str(exc)})

    report["seconds"] = round(time.time() - t0, 1)
    report["requests"] = client.requests_made
    report["problems"] = problems
    worst = max((p["severity"] for p in problems), default="healthy", key=lambda s: ["healthy", "degraded", "broken"].index(s))
    report["verdict"] = worst
    if a.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"{worst}: {report}")
    if a.log:
        log_path = Path(__file__).resolve().parents[3] / "docs" / "reliability" / "runs.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        line = {"ran_at": report["ran_at"], "actor": "tennis-matches", "kind": "canary", "verdict": worst,
                **{k: report[k] for k in ("schedule_rows", "enriched_ok", "results_rows", "live_rows", "requests", "seconds") if k in report},
                "problems": problems}
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    notify(worst, report)
    return {"healthy": 0, "degraded": 1, "broken": 2}[worst]


def notify(verdict: str, report: dict) -> None:
    """Push the verdict to ntfy if NTFY_TOPIC is set. Never raises."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    if os.environ.get("NTFY_QUIET") and verdict == "healthy":
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    priority = {"healthy": "2", "degraded": "3", "broken": "5"}[verdict]
    problems = "; ".join(p["detail"] for p in report.get("problems", [])) or "all checks passed"
    body = f"{report.get('schedule_rows', 0)} listed, {report.get('enriched_ok', 0)} enriched, {report.get('requests', 0)} requests, {report.get('seconds', 0)} s. {problems}"
    req = urllib.request.Request(f"{server}/{topic}", data=body.encode("utf-8"), method="POST",
                                 headers={"Title": f"tennis actor canary: {verdict}", "Priority": priority,
                                          "Tags": {"healthy": "white_check_mark", "degraded": "warning", "broken": "rotating_light"}[verdict]})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as exc:  # noqa: BLE001
        print(f"ntfy push failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
