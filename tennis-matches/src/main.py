"""Apify actor entry point: tennis matches with odds, form, and head-to-head.

Pay-per-event events (configured in Apify Console, charged here):

    match           one match row from the schedule or results list
    enriched-match  one match row joined to its detail page: round, surface,
                    rankings, surface W/L, head-to-head, bookmaker odds with
                    openings, and both players' latest matches

The actor charges through push_data(charged_event_name=...), so the platform
stops both the charge and the row when the caller's budget is reached. The
slow part, enrichment, is capped up front to what the budget allows.
"""

from __future__ import annotations

from datetime import date

from apify import Actor

from .tennis_matches.fetch import Client
from .tennis_matches.service import SourceDrift, enrich_matches, list_matches

EVENT_MATCH = "match"
EVENT_ENRICHED = "enriched-match"


def _enrich_cap(n_records: int, plain_event: str, rich_event: str) -> int | None:
    """How many of `n_records` can be enriched so that every record still
    fits in the caller's max_total_charge_usd. None means unlimited."""
    cm = Actor.get_charging_manager()
    info = cm.get_pricing_info()
    if not info.is_pay_per_event:
        return None
    remaining = cm.get_max_total_charge_usd() - cm.calculate_total_charged_amount()
    if not remaining.is_finite():
        return None
    prices = info.per_event_prices
    pe, pp = prices.get(rich_event), prices.get(plain_event)
    if pe is None or pp is None or pe <= pp:
        return cm.calculate_max_event_charge_count_within_limit(rich_event)
    k = int((remaining - n_records * pp) / (pe - pp))
    return max(0, min(n_records, k))


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s)


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        kind = inp.get("mode", "schedule")
        if kind not in ("schedule", "results", "live"):
            kind = "schedule"
        enrich = bool(inp.get("enrich", True))
        max_matches = int(inp.get("maxMatches", 50))

        def progress(msg: str) -> None:
            Actor.log.info(msg)

        client = Client(delay_s=float(inp.get("requestDelaySeconds", 0.4)))
        await Actor.set_status_message(f"Listing tennis {kind} from TennisExplorer")
        try:
            matches = list_matches(
                client,
                kind=kind,
                start=_parse_date(inp.get("date")),
                days=int(inp.get("days", 1)),
                tour=inp.get("tour", "all"),
                doubles=bool(inp.get("doubles", False)),
                tournament=inp.get("tournament") or None,
                player=inp.get("player") or None,
                status=inp.get("status") or None,
                max_matches=max_matches,
                progress=progress,
            )
        except SourceDrift as exc:
            await Actor.set_value("OUTPUT", {"ok": False, "errors": [str(exc)]})
            await Actor.fail(status_message=f"Source drift detected, run aborted: {exc}")
            return
        Actor.log.info(f"{len(matches)} matches listed")

        if enrich and matches:
            info = Actor.get_charging_manager().get_pricing_info()
            if info.is_pay_per_event and EVENT_ENRICHED not in info.per_event_prices:
                # Operator error (event not priced in the Console) would otherwise degrade
                # silently to plain rows. Say so where the run log and status are visible.
                Actor.log.error(f"Event '{EVENT_ENRICHED}' has no price configured; returning plain rows only")
                await Actor.set_status_message("Configuration problem: enriched-match is not priced, plain rows only")
            cap = _enrich_cap(len(matches), EVENT_MATCH, EVENT_ENRICHED)
            limit = len(matches) if cap is None else min(len(matches), cap)
            if limit < len(matches):
                Actor.log.warning(f"Budget allows {limit} enriched matches; {len(matches) - limit} will be returned plain.")
            await Actor.set_status_message(f"Enriching {limit} matches (about {limit} s)")
            try:
                enrich_matches(
                    client,
                    matches,
                    limit=limit,
                    latest_limit=int(inp.get("latestMatches", 10)),
                    odds_history=bool(inp.get("oddsHistory", False)),
                    progress=progress,
                )
            except SourceDrift as exc:
                await Actor.set_value("OUTPUT", {"ok": False, "errors": [str(exc)]})
                await Actor.fail(status_message=f"Source drift detected, run aborted: {exc}")
                return

        enriched = [m for m in matches if (m.get("enrichment") or {}).get("status") in ("ok", "partial")]
        plain = [m for m in matches if m not in enriched]
        attempted = [m for m in matches if "enrichment" in m]
        failed = [m for m in attempted if m not in enriched]
        if len(attempted) >= 5 and not enriched:
            # Every detail fetch failed: the site is blocking or changed. Do not sell plain
            # rows to a caller who asked for enrichment; fail so monitoring alerts.
            await Actor.set_value("OUTPUT", {"ok": False, "errors": [f"0 of {len(attempted)} enrichments succeeded: {failed[0]['enrichment'].get('error')}"]})
            await Actor.fail(status_message=f"Enrichment failed for all {len(attempted)} matches, nothing charged")
            return
        if failed:
            Actor.log.warning(f"{len(failed)} of {len(attempted)} enrichments failed; those rows are returned plain")
        charged = 0
        if enriched:
            charged += (await Actor.push_data(enriched, charged_event_name=EVENT_ENRICHED)).charged_count
        if plain:
            charged += (await Actor.push_data(plain, charged_event_name=EVENT_MATCH)).charged_count
        archive = (inp.get("archiveDatasetName") or "").strip()
        if archive and matches:
            # Named datasets persist beyond the plan's run-data retention; used by the
            # owner's own archive schedules. Not charged: the default dataset already was.
            ds = await Actor.open_dataset(name=archive)
            await ds.push_data(matches)
            Actor.log.info(f"archived {len(matches)} matches to dataset '{archive}'")
        await Actor.set_status_message(f"Done: {len(enriched)} enriched + {len(plain)} plain matches, {client.requests_made} requests")
        Actor.log.info(f"pushed {len(matches)} matches, charged {charged} events")
