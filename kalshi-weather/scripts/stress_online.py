"""Online stress suite against the live Kalshi and NWS APIs: every station,
every mode, a week of settlements checked against the official report,
throttling limits, concurrent buyers, and awkward actor inputs.

    python scripts/stress_online.py            # full suite, about 10 minutes
    python scripts/stress_online.py --quick    # trims the long scenarios
    python scripts/stress_online.py --only station_coverage settlement_accuracy_7_days

Writes storage/stress_report.json (gitignored). Read the report, not the exit code.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from src.kalshi_weather.fetch import FetchError, KalshiClient, NwsClient  # noqa: E402
from src.kalshi_weather.parse import KNOWN_STATION_BY_SERIES  # noqa: E402
from src.kalshi_weather.service import SourceDrift, StationCache, enrich_events, list_events  # noqa: E402

report: dict = {}


def scenario(name):
    def deco(fn):
        def run(*a, **k):
            t0 = time.time()
            try:
                out = fn(*a, **k) or {}
                out["ok"] = out.get("ok", True)
            except Exception as exc:  # noqa: BLE001
                out = {"ok": False, "exception": f"{type(exc).__name__}: {exc}"}
            out["seconds"] = round(time.time() - t0, 1)
            report[name] = out
            print(f"{name}: {json.dumps(out)[:500]}", file=sys.stderr)
            return out
        return run
    return deco


@scenario("station_coverage")
def station_coverage():
    """Every station in the known table resolves on NWS and has a CLI product list."""
    nws = NwsClient(delay_s=0.2)
    cache = StationCache(nws)
    stations = sorted({v for v in KNOWN_STATION_BY_SERIES.values() if v})
    out = {}
    bad = []
    for cli in stations:
        meta = cache.station("K" + cli)
        cli_n = None
        try:
            cli_n = len(nws.cli_products(cli))
        except FetchError as exc:
            cli_n = f"ERR {exc}"
        out[cli] = {"icao_ok": meta is not None, "name": (meta or {}).get("name"), "tz": (meta or {}).get("timezone"), "cli_products": cli_n}
        if meta is None or not isinstance(cli_n, int) or cli_n == 0:
            bad.append(cli)
    return {"stations": len(stations), "bad": bad, "detail": out, "requests": nws.requests_made, "ok": not bad}


@scenario("all_open_ladders_enriched")
def all_open(quick):
    kalshi, nws = KalshiClient(delay_s=0.25), NwsClient(delay_s=0.25)
    rows = list_events(kalshi, kind="both", status="open", max_events=20 if quick else 500)
    n = enrich_events(nws, rows, hourly_periods=False)
    statuses = Counter((r.get("enrichment") or {}).get("status") for r in rows)
    sources = Counter(((r.get("enrichment") or {}).get("analysis") or {}).get("forecast_source") for r in rows if r.get("enrichment"))
    by_station = Counter((r["station"] or {}).get("cli_id") for r in rows)
    unmatched = sorted({r["series_ticker"] for r in rows if not r["station"]})
    sizes = sorted(len(json.dumps(r).encode()) for r in rows)
    projected_hits = sum(1 for r in rows if (((r.get("enrichment") or {}).get("analysis") or {}).get("projected") or {}).get("ticker"))
    unpriced = [r["event_ticker"] for r in rows if r["market_summary"]["priced"] < 3]
    return {"rows": len(rows), "enriched": n, "statuses": dict(statuses), "forecast_sources": dict(sources), "stations": len(by_station),
            "ladders_per_station": dict(by_station), "series_without_station": unmatched, "projected_on_strike": projected_hits,
            "unpriced_ladders": unpriced, "max_row_bytes": sizes[-1] if sizes else 0, "median_row_bytes": sizes[len(sizes) // 2] if sizes else 0,
            "kalshi_requests": kalshi.requests_made, "nws_requests": nws.requests_made,
            "ok": n >= 0.9 * sum(1 for r in rows if r["station"])}


@scenario("settlement_accuracy_7_days")
def settlement_accuracy(quick):
    """For every settled ladder with a final climate report, the report's reading must
    fall in the strike Kalshi paid out. This is the correctness metric of the product."""
    kalshi, nws = KalshiClient(delay_s=0.25), NwsClient(delay_s=0.25)
    days = 2 if quick else 7
    rows = list_events(kalshi, kind="both", status="settled", date_from=date.today() - timedelta(days=days + 1), date_to=date.today() - timedelta(days=2), max_events=60 if quick else 1000)
    enrich_events(nws, rows)
    matched = mismatched = no_report = partial_report = 0
    misses = []
    for r in rows:
        e = r.get("enrichment") or {}
        a = e.get("analysis") or {}
        rep = e.get("climate_report") or {}
        yes = [m for m in r["markets"] if m["result"] == "yes"]
        if not rep or rep.get("max_f" if r["kind"] == "high" else "min_f") is None:
            no_report += 1
            continue
        if not rep.get("final"):
            partial_report += 1
        off = a.get("official") or {}
        if yes and off.get("ticker") == yes[0]["ticker"]:
            matched += 1
        else:
            mismatched += 1
            misses.append({"event": r["event_ticker"], "official": off.get("temp_f"), "official_bracket": off.get("label"), "kalshi_yes": yes[0]["label"] if yes else None,
                           "expiration_value": None, "final": rep.get("final")})
    return {"ladders": len(rows), "matched": matched, "mismatched": mismatched, "no_report": no_report, "partial_report": partial_report,
            "misses": misses[:20], "kalshi_requests": kalshi.requests_made, "nws_requests": nws.requests_made,
            "ok": mismatched == 0 and matched >= 0.7 * max(1, len(rows))}


@scenario("closed_status_and_hourly_periods")
def closed_and_hourly():
    kalshi, nws = KalshiClient(delay_s=0.25), NwsClient(delay_s=0.25)
    closed = list_events(kalshi, kind="both", status="closed", stations=["NYC", "MDW"], max_events=10)
    n = enrich_events(nws, closed, hourly_periods=True)
    sizes = [len(json.dumps(r).encode()) for r in closed]
    with_periods = sum(1 for r in closed if ((r.get("enrichment") or {}).get("hourly_forecast") or {}).get("periods"))
    return {"closed_rows": len(closed), "enriched": n, "with_hourly_periods": with_periods, "max_row_bytes": max(sizes, default=0),
            "statuses": dict(Counter(m["status"] for r in closed for m in r["markets"]))}


@scenario("aggressive_kalshi_delay_0.05s")
def aggressive_kalshi():
    """Find Kalshi's tolerance so the default 0.25 s delay is known to be safe. Not the default."""
    c = KalshiClient(delay_s=0.05, retries=0)
    try:
        for i in range(60):
            c.events("KXHIGHNY", status="open")
    except FetchError as exc:
        return {"ok": True, "throttled_after": c.requests_made, "error": str(exc)[:120]}
    return {"ok": True, "requests": c.requests_made, "throttled": False}


@scenario("aggressive_nws_delay_0.05s")
def aggressive_nws():
    n = NwsClient(delay_s=0.05, retries=0)
    try:
        for i in range(40):
            n.latest_observation("KNYC")
    except FetchError as exc:
        return {"ok": True, "throttled_after": n.requests_made, "error": str(exc)[:120]}
    return {"ok": True, "requests": n.requests_made, "throttled": False}


@scenario("three_concurrent_buyers")
def concurrent_buyers():
    def buyer(i):
        kalshi, nws = KalshiClient(delay_s=0.25), NwsClient(delay_s=0.25)
        spec = [dict(kind="high", status="open", stations=["NYC", "MIA"]),
                dict(kind="low", status="open", stations=["MDW", "DEN"]),
                dict(kind="both", status="settled", stations=["PHL"], date_from=date.today() - timedelta(days=3))][i]
        rows = list_events(kalshi, max_events=8, **spec)
        n = enrich_events(nws, rows)
        return {"spec": {k: str(v) for k, v in spec.items()}, "rows": len(rows), "enriched": n, "requests": kalshi.requests_made + nws.requests_made}
    with concurrent.futures.ThreadPoolExecutor(3) as ex:
        results = list(ex.map(buyer, range(3)))
    return {"buyers": results, "ok": all(r["enriched"] == r["rows"] for r in results)}


@scenario("edge_dates")
def edge_dates():
    kalshi = KalshiClient(delay_s=0.25)
    out = {}
    cases = {"far_future": dict(status="open", date_from=date.today() + timedelta(days=400)),
             "inverted_range": dict(status="settled", date_from=date.today(), date_to=date.today() - timedelta(days=5)),
             "ancient_settled": dict(status="settled", date_from=date(2024, 1, 1), date_to=date(2024, 1, 31)),
             "today_only_open": dict(status="open", date_from=date.today(), date_to=date.today())}
    for label, spec in cases.items():
        try:
            out[label] = len(list_events(kalshi, stations=["NYC"], max_events=50, **spec))
        except SourceDrift as exc:
            out[label] = f"drift: {exc}"
    out["requests"] = kalshi.requests_made
    out["ok"] = out["far_future"] == 0 and out["inverted_range"] == 0 and out["ancient_settled"] == 0
    return out


@scenario("actor_runner_edge_inputs")
def runner_edges(quick):
    """Drive the real actor entrypoint with awkward inputs through run_local.sh."""
    cases = {
        "empty_input": ("{}", "0.10"),
        "budget_too_small_for_one_enriched": ('{"stations":["NYC"],"maxEvents":3}', "0.005"),
        "budget_zero": ('{"stations":["NYC"],"maxEvents":3}', "0"),
        "bad_kind_and_status": ('{"kind":"xyz","status":"xyz","maxEvents":3,"enrich":false}', ""),
        "max_events_zero": ('{"maxEvents":0}', ""),
        "unknown_and_lowercase_stations": ('{"stations":["zzz","nyc"],"maxEvents":4}', "0.10"),
        "far_future_empty": ('{"dateFrom":"2027-12-25","maxEvents":5}', "0.10"),
        "no_enriched_price_configured": ('{"stations":["NYC"],"maxEvents":2}', "0.10"),
        "hourly_periods_and_archive": ('{"stations":["MDW"],"maxEvents":2,"includeHourlyPeriods":true,"archiveDatasetName":"stress-archive"}', "0.10"),
        "settled_no_enrich": ('{"status":"settled","stations":["NYC"],"dateFrom":"2026-09-01","enrich":false,"maxEvents":50}', ""),
    }
    if quick:
        cases = {k: v for k, v in list(cases.items())[:5]}
    out = {}
    for name, (inp, budget) in cases.items():
        env = dict(os.environ)
        env["MSYS"] = "noglob"
        env["MSYS_NO_PATHCONV"] = "1"
        env["MSYS2_ARG_CONV_EXCL"] = "*"
        env["INPUT_FILE"] = "storage/stress_input.json"
        bash = "C:/Program Files/Git/bin/bash.exe" if os.path.exists("C:/Program Files/Git/bin/bash.exe") else (shutil.which("bash") or "bash")
        (ROOT / "storage").mkdir(exist_ok=True)
        (ROOT / "storage" / "stress_input.json").write_text(inp, encoding="utf-8")
        cmd = [bash, "scripts/run_local.sh", "{}"] + ([budget] if budget else [])
        if name == "no_enriched_price_configured":
            env["STRIP_ENRICHED_PRICE"] = "1"
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env, timeout=600)
        log = p.stdout + p.stderr
        ds = ROOT / "storage" / "datasets" / "default"
        n_items = len([f for f in ds.glob("*.json") if not f.name.startswith("__")]) if ds.exists() else 0
        archive = ROOT / "storage" / "datasets" / "stress-archive"
        charged = [ln for ln in log.splitlines() if "charged" in ln][-1:] or [""]
        status = [ln for ln in log.splitlines() if "Status message" in ln][-1:] or [""]
        out[name] = {"exit": p.returncode, "items": n_items, "archived_items": len(list(archive.glob("0*.json"))) if archive.exists() else 0,
                     "charged_line": charged[0].strip()[-80:], "last_status": status[0].strip()[-140:],
                     "failed": "exit_code\": 1" in log, "errors": [ln.strip()[-160:] for ln in log.splitlines() if "ERROR" in ln][:3]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", nargs="*", help="scenario names to run")
    ap.add_argument("--log", action="store_true", help="append a summary line to docs/reliability/runs.jsonl")
    a = ap.parse_args(argv)
    todo = {"station_coverage": station_coverage, "edge_dates": edge_dates, "actor_runner_edge_inputs": lambda: runner_edges(a.quick),
            "three_concurrent_buyers": concurrent_buyers, "closed_status_and_hourly_periods": closed_and_hourly,
            "aggressive_kalshi_delay_0.05s": aggressive_kalshi, "aggressive_nws_delay_0.05s": aggressive_nws,
            "settlement_accuracy_7_days": lambda: settlement_accuracy(a.quick), "all_open_ladders_enriched": lambda: all_open(a.quick)}
    for name, fn in todo.items():
        if a.only and name not in a.only:
            continue
        fn()
    (ROOT / "storage").mkdir(exist_ok=True)
    (ROOT / "storage" / "stress_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    if a.log:
        log_path = ROOT.parents[1] / "docs" / "reliability" / "runs.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        verdict = "healthy" if all(v.get("ok") for v in report.values()) else "broken"
        slim = {k: {kk: vv for kk, vv in v.items() if kk not in ("detail", "ladders_per_station", "misses")} for k, v in report.items()}
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "actor": "kalshi-weather",
                                "kind": "stress_online", "verdict": verdict, "scenarios": slim}) + "\n")


if __name__ == "__main__":
    main()
