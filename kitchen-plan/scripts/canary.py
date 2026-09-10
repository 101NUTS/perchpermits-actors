"""Canary for the kitchen takeoff actor. It has no external source, so the check
is: build every fixture spec, verify the output integrity rules, and time it.

    python scripts/canary.py [--json] [--log]
    exit 0 = healthy, 1 = degraded, 2 = broken
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.kitchen_plan.service import SpecError, build, verify_outputs  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time()
    problems: list[dict] = []
    built = 0
    for path in sorted((ROOT / "tests" / "fixtures").glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        units = spec.pop("_units", "m") if isinstance(spec, dict) else "m"
        try:
            result = build(spec, units)
        except SpecError as exc:
            if path.name.startswith("test_"):
                continue  # some test fixtures are meant to be invalid
            problems.append({"check": "build", "severity": "broken", "detail": f"{path.name}: {exc}"})
            continue
        except Exception as exc:  # noqa: BLE001
            problems.append({"check": "build", "severity": "broken", "detail": f"{path.name}: {type(exc).__name__}: {exc}"})
            continue
        issues = verify_outputs(result)
        if issues:
            problems.append({"check": "integrity", "severity": "broken", "detail": f"{path.name}: {issues[:3]}"})
        built += 1
    dt = time.time() - t0
    if built == 0:
        problems.append({"check": "fixtures", "severity": "broken", "detail": "no fixture built"})
    if dt > 20:
        problems.append({"check": "speed", "severity": "degraded", "detail": f"{built} builds took {dt:.1f} s"})
    worst = max((p["severity"] for p in problems), default="healthy", key=lambda s: ["healthy", "degraded", "broken"].index(s))
    report = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "built": built, "seconds": round(dt, 1), "problems": problems, "verdict": worst}
    print(json.dumps(report, indent=1) if a.json else f"{worst}: {report}")
    if a.log:
        log_path = ROOT.parents[1] / "docs" / "reliability" / "runs.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"actor": "kitchen-plan", "kind": "canary", **report}) + "\n")
    return {"healthy": 0, "degraded": 1, "broken": 2}[worst]


if __name__ == "__main__":
    sys.exit(main())
