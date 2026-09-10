"""Apify actor: kitchen or bathroom spec in, floor plan + takeoff + summary out.

Pay-per-event: one `plan` event per successful build, charged only after
the plan SVG, takeoff CSV, and summary are all stored. Validation failures
end the run with a clear message and no charge.
"""

from __future__ import annotations

import json

from apify import Actor

from .kitchen_plan.service import OutputIntegrityError, SpecError, build

EVENT_PLAN = "plan"


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        spec = inp.get("spec")
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError as e:
                await Actor.fail(status_message=f"spec is not valid JSON: {e}")
                return
        if not isinstance(spec, dict):
            await Actor.fail(status_message="Input field `spec` is required and must be a JSON object.")
            return
        units = inp.get("units", "m")
        validate_only = bool(inp.get("validateOnly", False))
        title = inp.get("title") or None

        try:
            result = build(spec, units, title=title)
        except SpecError as e:
            await Actor.set_value("OUTPUT", {"ok": False, "errors": e.errors, "warnings": e.warnings})
            await Actor.fail(status_message="Spec invalid: " + " | ".join(e.errors[:3]))
            return
        except OutputIntegrityError as e:
            # Code or SDK drift, not a bad spec. Loud failure, nothing charged.
            await Actor.set_value("OUTPUT", {"ok": False, "errors": [f"internal: {e}"], "warnings": []})
            await Actor.fail(status_message=f"Output integrity check failed, run aborted: {e}")
            return
        except ValueError as e:
            await Actor.fail(status_message=str(e))
            return

        for w in result["warnings"]:
            Actor.log.warning(w)

        if validate_only:
            await Actor.set_value("OUTPUT", {"ok": True, "errors": [], "warnings": result["warnings"], "summary": result["summary"]})
            await Actor.set_status_message("Spec is valid (validate-only run, not charged)")
            return

        await Actor.set_value("plan.svg", result["svg"], content_type="image/svg+xml")
        await Actor.set_value("takeoff.csv", result["csv"], content_type="text/csv")
        await Actor.set_value("summary.json", result["summary"])
        await Actor.push_data(result["rows"])

        store = await Actor.open_key_value_store()
        output = {
            "ok": True,
            "warnings": result["warnings"],
            "summary": result["summary"],
            "plan_svg_url": await store.get_public_url("plan.svg"),
            "takeoff_csv_url": await store.get_public_url("takeoff.csv"),
            "takeoff_rows": len(result["rows"]),
        }
        await Actor.set_value("OUTPUT", output)

        charge = await Actor.charge(EVENT_PLAN)
        await Actor.set_status_message(
            f"Plan built: {result['summary']['cabinet_count']} cabinets, "
            f"{result['summary']['countertop_sqft']} sq ft counter, {len(result['rows'])} takeoff lines"
        )
        Actor.log.info(f"charged {charge.charged_count} x {EVENT_PLAN}")
