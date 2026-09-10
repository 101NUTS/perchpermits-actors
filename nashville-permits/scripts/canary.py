"""Canary for the permits actor: preflight both Metro feeds, run a small search,
enrich two records from ePermits, and fail loudly if any layer is off.

    python scripts/canary.py [--json] [--log]
    exit 0 = healthy, 1 = degraded, 2 = broken
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nashville_permits.epermits import NullCache  # noqa: E402
from src.nashville_permits.service import EnrichmentDrift, enrich_permits, preflight, search_permits  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time()
    problems: list[dict] = []
    report: dict = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    problems_pf, warnings = preflight("both")
    report["preflight_warnings"] = warnings
    for p in problems_pf:
        problems.append({"check": "preflight", "severity": "broken", "detail": p})
    for w in warnings:
        problems.append({"check": "freshness", "severity": "degraded", "detail": w})

    records = []
    if not problems_pf:
        try:
            records = search_permits(datasets="both", scope="kitchen", residential_only=True, max_records=5)
        except Exception as exc:  # noqa: BLE001
            problems.append({"check": "search", "severity": "broken", "detail": f"{type(exc).__name__}: {exc}"})
        report["search_rows"] = len(records)
        if not problems_pf and len(records) < 1:
            problems.append({"check": "search_rows", "severity": "broken", "detail": "kitchen search returned nothing"})
        if records:
            try:
                n = enrich_permits(records, NullCache(), limit=2, max_age_days=7)
                ok = [r for r in records[:2] if (r.get("enrichment") or {}).get("status") in ("ok", "partial")]
                report["enriched_ok"] = len(ok)
                if not ok:
                    problems.append({"check": "enrich", "severity": "broken", "detail": "0 of 2 enrichments succeeded"})
                elif ok and not any((r["enrichment"].get("contractor") or {}).get("company") for r in ok):
                    problems.append({"check": "enrich_fields", "severity": "degraded", "detail": "enriched rows carry no contractor"})
            except EnrichmentDrift as exc:
                problems.append({"check": "enrich", "severity": "broken", "detail": f"drift: {exc}"})

    report["seconds"] = round(time.time() - t0, 1)
    report["problems"] = problems
    worst = max((p["severity"] for p in problems), default="healthy", key=lambda s: ["healthy", "degraded", "broken"].index(s))
    report["verdict"] = worst
    print(json.dumps(report, indent=1) if a.json else f"{worst}: {report}")
    if a.log:
        log_path = Path(__file__).resolve().parents[3] / "docs" / "reliability" / "runs.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"actor": "nashville-permits", "kind": "canary", **report}) + "\n")
    return {"healthy": 0, "degraded": 1, "broken": 2}[worst]


if __name__ == "__main__":
    sys.exit(main())
