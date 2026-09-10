"""Local runner.

    python -m src.kitchen_plan.cli tests/fixtures/kitchen_example.json --out out/example
    python -m src.kitchen_plan.cli spec.json --units ft --validate-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .service import SpecError, build


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kitchen_plan")
    ap.add_argument("spec", help="path to the spec JSON")
    ap.add_argument("--units", default="m", choices=["m", "ft", "in"])
    ap.add_argument("--out", help="directory for plan.svg, takeoff.csv, summary.json")
    ap.add_argument("--validate-only", action="store_true")
    a = ap.parse_args(argv)

    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    try:
        result = build(spec, a.units)
    except SpecError as e:
        json.dump({"ok": False, "errors": e.errors, "warnings": e.warnings}, sys.stdout, indent=2)
        print()
        return 1
    if a.validate_only:
        json.dump({"ok": True, "errors": [], "warnings": result["warnings"]}, sys.stdout, indent=2)
        print()
        return 0
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "plan.svg").write_text(result["svg"], encoding="utf-8")
        (out / "takeoff.csv").write_text(result["csv"], encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(result["summary"], indent=2), encoding="utf-8")
        print(f"wrote {out / 'plan.svg'}, {out / 'takeoff.csv'}, {out / 'summary.json'}", file=sys.stderr)
    json.dump({"ok": True, "warnings": result["warnings"], "summary": result["summary"]}, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
