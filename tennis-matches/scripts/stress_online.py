"""Online stress suite: load, concurrency, edge inputs, row sizes, against the live source.

    python scripts/stress_online.py            # full suite, about 8 minutes
    python scripts/stress_online.py --quick    # skips the long runs

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
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from src.tennis_matches.fetch import Client, FetchError  # noqa: E402
from src.tennis_matches.service import SourceDrift, enrich_matches, list_matches  # noqa: E402

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
            print(f"{name}: {json.dumps(out)[:400]}", file=sys.stderr)
            return out
        return run
    return deco


@scenario("volume_14_days_plain_all_tours")
def volume():
    c = Client(delay_s=0.4)
    rows = list_matches(c, kind="schedule", start=date.today() - timedelta(days=7), days=14, tour="all", max_matches=100000)
    ids = [r["match_id"] for r in rows]
    return {"rows": len(rows), "unique": len(set(ids)), "requests": c.requests_made, "bytes_json": len(json.dumps(rows))}


@scenario("enrich_200_with_odds_history")
def enrich_big(quick):
    c = Client(delay_s=0.4)
    rows = list_matches(c, kind="schedule", start=date.today(), tour="all", max_matches=40 if quick else 200)
    n = enrich_matches(c, rows, odds_history=True, latest_limit=30)
    sizes = sorted(len(json.dumps(r).encode()) for r in rows)
    failed = [r["enrichment"] for r in rows if r.get("enrichment", {}).get("status") not in ("ok", "partial")]
    return {"rows": len(rows), "enriched": n, "failed": len(failed), "max_row_bytes": sizes[-1], "median_row_bytes": sizes[len(sizes) // 2],
            "requests": c.requests_made, "first_error": failed[0] if failed else None}


@scenario("aggressive_delay_0.1s_50_requests")
def aggressive():
    """Find the site's tolerance so the default delay is known to be safe. Not the default."""
    c = Client(delay_s=0.1, retries=0)
    rows = list_matches(c, kind="schedule", start=date.today(), tour="all", max_matches=50)
    errors = 0
    for r in rows:
        try:
            c.detail_page(r["match_id"])
        except FetchError as exc:
            errors += 1
            if "429" in str(exc) or "403" in str(exc):
                return {"ok": True, "throttled_after": c.requests_made, "error": str(exc)}
    return {"ok": True, "requests": c.requests_made, "errors": errors, "throttled": False}


@scenario("three_concurrent_buyers")
def concurrent_buyers():
    def buyer(i):
        c = Client(delay_s=0.4)
        rows = list_matches(c, kind=["schedule", "results", "live"][i], start=date.today() - timedelta(days=i), tour="all", max_matches=10)
        n = enrich_matches(c, rows)
        return {"kind": ["schedule", "results", "live"][i], "rows": len(rows), "enriched": n, "requests": c.requests_made}
    with concurrent.futures.ThreadPoolExecutor(3) as ex:
        results = list(ex.map(buyer, range(3)))
    return {"buyers": results, "ok": all(r["enriched"] == r["rows"] for r in results)}


@scenario("edge_dates")
def edge_dates():
    c = Client(delay_s=0.4)
    out = {}
    for label, d in (("year_2000", date(2000, 1, 1)), ("far_future", date.today() + timedelta(days=400)), ("leap_day", date(2024, 2, 29)), ("dec_31", date(2025, 12, 31))):
        try:
            rows = list_matches(c, kind="results", start=d, max_matches=5)
            out[label] = len(rows)
        except SourceDrift as exc:
            out[label] = f"drift: {exc}"
    return out


@scenario("actor_runner_edge_inputs")
def runner_edges(quick):
    """Drive the real actor entrypoint with awkward inputs through run_local.sh."""
    cases = {
        "empty_input": ("{}", "0.10"),
        "budget_too_small_for_one_enriched": ('{"tour":"atp","maxMatches":3}', "0.005"),
        "budget_zero": ('{"tour":"atp","maxMatches":3}', "0"),
        "bad_mode_and_tour": ('{"mode":"xyz","tour":"xyz","maxMatches":3,"enrich":false}', ""),
        "max_matches_zero": ('{"maxMatches":0}', ""),
        "unicode_filters": ('{"player":"Švec","tournament":"(open)","maxMatches":5,"enrich":false}', ""),
        "far_future_empty": ('{"date":"2027-12-25","maxMatches":5}', "0.10"),
        "no_enriched_price_configured": ('{"tour":"atp","maxMatches":2}', "0.10"),
    }
    if quick:
        cases = {k: v for k, v in list(cases.items())[:4]}
    out = {}
    for name, (inp, budget) in cases.items():
        env = dict(os.environ)
        # MSYS bash launched from Python rewrites arguments that look like paths or globs;
        # switch that off or the JSON input and the budget arrive mangled.
        env["MSYS"] = "noglob"
        env["MSYS_NO_PATHCONV"] = "1"
        env["MSYS2_ARG_CONV_EXCL"] = "*"
        env["INPUT_FILE"] = "storage/stress_input.json"
        bash = "C:/Program Files/Git/bin/bash.exe" if os.path.exists("C:/Program Files/Git/bin/bash.exe") else (shutil.which("bash") or "bash")
        (ROOT / "storage").mkdir(exist_ok=True)
        inp_file = ROOT / "storage" / "stress_input.json"
        inp_file.write_text(inp, encoding="utf-8")
        cmd = [bash, "scripts/run_local.sh", "{}"] + ([budget] if budget else [])
        if name == "no_enriched_price_configured":
            env["STRIP_ENRICHED_PRICE"] = "1"
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env, timeout=300)
        log = p.stdout + p.stderr
        ds = ROOT / "storage" / "datasets" / "default"
        n_items = len([f for f in ds.glob("*.json") if not f.name.startswith("__")]) if ds.exists() else 0
        charged = [ln for ln in log.splitlines() if "charged" in ln][-1:] or [""]
        status = [ln for ln in log.splitlines() if "Status message" in ln][-1:] or [""]
        out[name] = {"exit": p.returncode, "items": n_items, "charged_line": charged[0].strip()[-80:], "last_status": status[0].strip()[-120:],
                     "failed": "exit_code\": 1" in log, "input_seen": (ROOT / "storage/key_value_stores/default/INPUT.json").read_text(encoding="utf-8")[:60]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", nargs="*", help="scenario names to run")
    ap.add_argument("--log", action="store_true", help="append a summary line to docs/reliability/runs.jsonl")
    a = ap.parse_args(argv)
    todo = {"edge_dates": edge_dates, "actor_runner_edge_inputs": lambda: runner_edges(a.quick),
            "three_concurrent_buyers": concurrent_buyers, "aggressive_delay_0.1s_50_requests": aggressive,
            "enrich_200_with_odds_history": lambda: enrich_big(a.quick), "volume_14_days_plain_all_tours": volume}
    for name, fn in todo.items():
        if a.only and name not in a.only:
            continue
        if name == "volume_14_days_plain_all_tours" and a.quick and not a.only:
            continue
        fn()
    (ROOT / "storage").mkdir(exist_ok=True)
    (ROOT / "storage" / "stress_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    if a.log:
        log_path = ROOT.parents[1] / "docs" / "reliability" / "runs.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        verdict = "healthy" if all(v.get("ok") for v in report.values()) else "broken"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "actor": "tennis-matches",
                                "kind": "stress_online", "verdict": verdict, "scenarios": report}) + "\n")


if __name__ == "__main__":
    main()
