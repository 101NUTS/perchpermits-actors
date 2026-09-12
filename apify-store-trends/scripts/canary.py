"""Canary: hit the live store API with a fixed small workload and fail loudly if
coverage drops. Meant to run once or twice a day from a scheduler.

    python scripts/canary.py            # exit 0 = healthy, 1 = degraded, 2 = broken
    python scripts/canary.py --json     # machine-readable report on stdout
    python scripts/canary.py --log      # also append one line to docs/reliability/runs.jsonl

Set NTFY_TOPIC (and optionally NTFY_SERVER, default https://ntfy.sh) to push the
verdict to the ntfy app on a phone: healthy runs are sent at low priority (silent),
degraded at default, broken at high. Set NTFY_QUIET=1 to send only degraded and broken.

Workload: search "google trends". Healthy means at least five actor rows, a
leader with more than 100 users in the last 30 days, every documented field
present, and a target row for the query.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.store_trends.fetch import Client  # noqa: E402
from src.store_trends.service import SourceDrift, run  # noqa: E402

REQUIRED_ACTOR = ["actor", "title", "target", "query_match", "users_30d", "users_total", "runs_30d", "fail_rate_30d", "rating", "reviews",
                  "price", "pricing_model", "notice", "days_since_last_run", "url", "gap_signals", "snapshot_at", "delta", "row_type"]
REQUIRED_TARGET = ["target", "target_kind", "hostility", "actors", "active_actors", "users_30d", "leader", "leader_share",
                   "leader_rating", "leader_fail_rate", "weighted_rating", "best_rating", "broken_actors", "signals", "snapshot_at", "delta", "row_type"]


def check(label: str, ok: bool, detail: str, problems: list, severity: str = "broken") -> None:
    if not ok:
        problems.append({"check": label, "severity": severity, "detail": detail})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--query", default="google trends")
    ap.add_argument("--log", action="store_true", help="append to docs/reliability/runs.jsonl")
    a = ap.parse_args(argv)
    problems: list[dict] = []
    report: dict = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "query": a.query}
    client = Client(delay_s=0.2)
    t0 = time.time()

    rows: list[dict] = []
    targets: list[dict] = []
    try:
        rows, targets = run(client, mode="search", query=a.query, min_users_30d=5, max_actors=200)
    except SourceDrift as exc:
        problems.append({"check": "search", "severity": "broken", "detail": str(exc)})
    except Exception as exc:  # noqa: BLE001  network after retries
        problems.append({"check": "search", "severity": "broken", "detail": f"{exc.__class__.__name__}: {exc}"})
    matched_rows = [r for r in rows if r.get("query_match")]
    report["actor_rows"] = len(rows)
    report["query_matched_rows"] = len(matched_rows)
    report["target_rows"] = len(targets)
    check("actor_rows", len(matched_rows) >= 5, f"only {len(matched_rows)} actor rows match the query ({len(rows)} returned)", problems)
    if matched_rows:
        leader = matched_rows[0]
        report["leader"] = leader["actor"]
        report["leader_users_30d"] = leader["users_30d"]
        check("leader_demand", leader["users_30d"] > 100, f"leader {leader['actor']} has {leader['users_30d']} users in 30d", problems)
    if rows:
        for r in rows:
            missing = [k for k in REQUIRED_ACTOR if k not in r]
            if missing:
                problems.append({"check": "actor_fields", "severity": "broken", "detail": f"{r.get('actor')} missing {missing}"})
                break
        with_rating = sum(1 for r in rows if r["rating"] is not None)
        with_fail = sum(1 for r in rows if r["fail_rate_30d"] is not None)
        matched = sum(1 for r in rows if r["target"] == "google trends")
        report.update({"with_rating": with_rating, "with_fail_rate": with_fail, "matched_target": matched})
        check("fail_rate_coverage", with_fail >= 0.5 * len(rows), f"{with_fail}/{len(rows)} rows have fail_rate_30d", problems, "degraded")
        check("target_regex", matched >= 3, f"only {matched} rows bucketed as google trends", problems, "degraded")
    check("target_rows", bool(targets) and targets[0]["target_kind"] == "query", "no query target row", problems)
    for t in targets:
        missing = [k for k in REQUIRED_TARGET if k not in t]
        if missing:
            problems.append({"check": "target_fields", "severity": "broken", "detail": f"target {t.get('target')} missing {missing}"})
            break

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
        line = {"ran_at": report["ran_at"], "actor": "apify-store-trends", "kind": "canary", "verdict": worst,
                **{k: report[k] for k in ("actor_rows", "query_matched_rows", "target_rows", "leader_users_30d", "requests", "seconds") if k in report},
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
    body = f"{report.get('actor_rows', 0)} actors, {report.get('target_rows', 0)} targets, {report.get('requests', 0)} requests, {report.get('seconds', 0)} s. {problems}"
    req = urllib.request.Request(f"{server}/{topic}", data=body.encode("utf-8"), method="POST",
                                 headers={"Title": f"store trends actor canary: {verdict}", "Priority": priority,
                                          "Tags": {"healthy": "white_check_mark", "degraded": "warning", "broken": "rotating_light"}[verdict]})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as exc:  # noqa: BLE001
        print(f"ntfy push failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
