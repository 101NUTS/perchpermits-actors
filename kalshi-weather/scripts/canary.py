"""Canary: hit both live sources with a fixed small workload and fail loudly
if coverage drops. Meant to run two or three times a day from a scheduler.

    python scripts/canary.py            # exit 0 = healthy, 1 = degraded, 2 = broken
    python scripts/canary.py --json     # machine-readable report on stdout
    python scripts/canary.py --log      # also append one line to docs/reliability/runs.jsonl

Set NTFY_TOPIC (and optionally NTFY_SERVER) to push the verdict to the ntfy
app; NTFY_QUIET=1 sends only degraded and broken.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kalshi_weather.fetch import KalshiClient, NwsClient  # noqa: E402
from src.kalshi_weather.service import SourceDrift, enrich_events, list_events  # noqa: E402

STATIONS = ["NYC", "MDW", "MIA", "DEN"]
REQUIRED_TOP = ["event_ticker", "series_ticker", "kind", "target_date", "station", "markets", "market_summary", "status", "fetched_at_utc", "event_url"]
REQUIRED_ENRICH = ["status", "station", "observations", "forecast", "hourly_forecast", "climate_report", "analysis"]


def check(label: str, ok: bool, detail: str, problems: list, severity: str = "broken") -> None:
    if not ok:
        problems.append({"check": label, "severity": severity, "detail": detail})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", action="store_true", help="append to docs/reliability/runs.jsonl")
    a = ap.parse_args(argv)
    problems: list[dict] = []
    report: dict = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    kalshi, nws = KalshiClient(delay_s=0.25), NwsClient(delay_s=0.25)
    t0 = time.time()

    # 1. open ladders for four stations, enriched
    try:
        rows = list_events(kalshi, kind="both", status="open", stations=STATIONS, max_events=20)
    except SourceDrift as exc:
        problems.append({"check": "open_list", "severity": "broken", "detail": str(exc)})
        rows = []
    report["open_rows"] = len(rows)
    check("open_rows", len(rows) >= 4, f"only {len(rows)} open ladders for {STATIONS}", problems)
    for r in rows:
        missing = [k for k in REQUIRED_TOP if k not in r]
        if missing:
            problems.append({"check": "row_fields", "severity": "broken", "detail": f"{r.get('event_ticker')} missing {missing}"})
            break
        if len(r["markets"]) < 3:
            problems.append({"check": "ladder_size", "severity": "degraded", "detail": f"{r['event_ticker']} has {len(r['markets'])} strikes"})
            break
    priced = sum(1 for r in rows if r["market_summary"]["priced"] >= 3)
    report["priced_rows"] = priced
    check("priced_rows", priced >= 0.7 * len(rows) if rows else True, f"{priced}/{len(rows)} ladders have 3+ priced strikes", problems, "degraded")
    if rows:
        try:
            n_ok = enrich_events(nws, rows)
        except SourceDrift as exc:
            problems.append({"check": "enrich", "severity": "broken", "detail": str(exc)})
            n_ok = 0
        report["enriched_ok"] = n_ok
        check("enrich_rate", n_ok >= 0.8 * len(rows), f"{n_ok}/{len(rows)} enriched", problems)
        enriched = [r for r in rows if (r.get("enrichment") or {}).get("status") in ("ok", "partial")]
        for r in enriched:
            missing = [k for k in REQUIRED_ENRICH if k not in r["enrichment"]]
            if missing:
                problems.append({"check": "enrich_fields", "severity": "broken", "detail": f"{r['event_ticker']} enrichment missing {missing}"})
                break
        # A ladder open after its local day has begun to close out (west-coast ladders seen
        # from a Central-time midnight) has no daily-forecast period left; the actor then
        # uses the hourly forecast. Count a forecast on a strike from either source.
        with_fc = sum(1 for r in enriched if (r["enrichment"]["analysis"]["forecast"] or {}).get("ticker"))
        with_obs = sum(1 for r in enriched if (r["enrichment"]["observations"] or {}).get("count"))
        with_hit = sum(1 for r in enriched if (r["enrichment"]["analysis"]["projected"] or {}).get("ticker"))
        report.update({"with_forecast": with_fc, "with_observations": with_obs, "with_projected_bracket": with_hit})
        if enriched:
            check("forecast_coverage", with_fc >= 0.8 * len(enriched), f"{with_fc}/{len(enriched)} have a forecast on a strike (daily or hourly)", problems, "degraded")
            check("projected_bracket", with_hit >= 0.9 * len(enriched), f"{with_hit}/{len(enriched)} projected extremes land on a strike", problems, "degraded")
            check("observation_coverage", with_obs >= 0.5 * len(enriched), f"{with_obs}/{len(enriched)} have observations today (low early in the day)", problems, "degraded")

    # 2. settled ladders from two days ago must carry results and an official temperature
    try:
        day = date.today() - timedelta(days=2)
        settled = list_events(kalshi, kind="high", status="settled", stations=["NYC"], date_from=day, date_to=day, max_events=2)
        report["settled_rows"] = len(settled)
        check("settled_rows", len(settled) >= 1, f"no settled NYC high ladder for {day}", problems, "degraded")
        if settled:
            r = settled[0]
            check("settled_results", any(m.get("result") in ("yes", "no") for m in r["markets"]), "settled ladder has no yes/no results", problems)
            enrich_events(nws, settled)
            rep = (settled[0].get("enrichment") or {}).get("climate_report") or {}
            report["settled_official_max_f"] = rep.get("max_f")
            check("settled_official", rep.get("max_f") is not None and rep.get("final"), f"no final climate report for {day}: {rep}", problems, "degraded")
    except SourceDrift as exc:
        problems.append({"check": "settled_list", "severity": "broken", "detail": str(exc)})

    report["seconds"] = round(time.time() - t0, 1)
    report["requests"] = kalshi.requests_made + nws.requests_made
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
        line = {"ran_at": report["ran_at"], "actor": "kalshi-weather", "kind": "canary", "verdict": worst,
                **{k: report[k] for k in ("open_rows", "enriched_ok", "settled_rows", "requests", "seconds") if k in report},
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
    body = f"{report.get('open_rows', 0)} ladders, {report.get('enriched_ok', 0)} enriched, {report.get('requests', 0)} requests, {report.get('seconds', 0)} s. {problems}"
    req = urllib.request.Request(f"{server}/{topic}", data=body.encode("utf-8"), method="POST",
                                 headers={"Title": f"kalshi weather canary: {verdict}", "Priority": priority,
                                          "Tags": {"healthy": "white_check_mark", "degraded": "warning", "broken": "rotating_light"}[verdict]})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as exc:  # noqa: BLE001
        print(f"ntfy push failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
