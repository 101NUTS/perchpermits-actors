"""One call: spec in, plan + takeoff + summary out."""

from __future__ import annotations

import csv
import io

from .plan import render_svg
from .summary import summarize
from .takeoff import takeoff
from .units import to_metres
from .validate import validate

CSV_FIELDS = ["location", "item", "width_in", "height_in", "depth_in", "note"]


class SpecError(ValueError):
    def __init__(self, errors: list[str], warnings: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.warnings = warnings


class OutputIntegrityError(RuntimeError):
    """The deliverables do not hold together. Never charge for these."""


def verify_outputs(result: dict) -> list[str]:
    """Integrity guard run before charging. Parses the SVG as XML, re-reads
    the CSV, and cross-checks the summary against the takeoff rows.
    Returns a list of problems; empty means the deliverables are sound."""
    import xml.etree.ElementTree as ET

    problems: list[str] = []
    svg = result.get("svg") or ""
    try:
        root = ET.fromstring(svg)
        if not root.tag.endswith("svg"):
            problems.append(f"plan root element is <{root.tag}>, not <svg>")
        elif len(root) < 5:
            problems.append("plan SVG has almost no elements")
    except ET.ParseError as exc:
        problems.append(f"plan SVG is not well-formed XML: {exc}")

    rows = result.get("rows") or []
    if not rows:
        problems.append("takeoff produced no rows")
    parsed = list(csv.DictReader(io.StringIO(result.get("csv") or "")))
    if len(parsed) != len(rows):
        problems.append(f"takeoff CSV has {len(parsed)} rows but {len(rows)} were computed")
    elif parsed and list(parsed[0].keys()) != CSV_FIELDS:
        problems.append(f"takeoff CSV columns are {list(parsed[0].keys())}")
    for r in rows:
        if not isinstance(r.get("width_in"), int) or r["width_in"] <= 0:
            problems.append(f"takeoff row has a bad width: {r}")
            break

    s = result.get("summary") or {}
    if s.get("line_items") != len(rows):
        problems.append(f"summary.line_items={s.get('line_items')} but {len(rows)} rows")
    if sum(i["count"] for i in s.get("item_counts", [])) != len(rows):
        problems.append("summary.item_counts do not add up to the row count")
    for key in ("base_cabinet_linear_ft", "upper_cabinet_linear_ft", "countertop_sqft"):
        v = s.get(key)
        if not isinstance(v, (int, float)) or v < 0:
            problems.append(f"summary.{key} is {v!r}")
    return problems


def build(spec: dict, units: str = "m", *, title: str | None = None) -> dict:
    """Validate, convert, and produce every deliverable.

    Returns {"spec_m", "warnings", "svg", "rows", "csv", "summary"}.
    Raises SpecError when the spec cannot be drawn.
    """
    spec_m = to_metres(spec, units)
    errors, warnings = validate(spec_m)
    if errors:
        raise SpecError(errors, warnings)
    rows = takeoff(spec_m)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    result = {
        "spec_m": spec_m,
        "warnings": warnings,
        "svg": render_svg(spec_m, title),
        "rows": rows,
        "csv": buf.getvalue(),
        "summary": summarize(spec_m, rows),
    }
    problems = verify_outputs(result)
    if problems:
        raise OutputIntegrityError("; ".join(problems))
    return result
