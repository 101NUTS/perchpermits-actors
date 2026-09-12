"""Apify actor entry point: Kalshi temperature markets joined to their NWS
settlement station.

Pay-per-event events (configured in Apify Console, charged here):

    event           one city-day ladder from Kalshi (high or low temperature)
                    with every strike, its prices, and the implied distribution
    enriched-event  the same row joined to api.weather.gov for the station that
                    settles it: observations so far that day, daily and hourly
                    forecast, the official climate report once issued, and which
                    bracket each of those lands in
    ladder-analysis a written read of an enriched row by Claude
                    (`analysis_text` field), charged on top of enriched-event,
                    only for rows that actually got one

The actor charges through push_data(charged_event_name=...), so the platform
stops both the charge and the row when the caller's budget is reached. The
slow part, the NWS join, is capped up front to what the budget allows; the
analyses are capped the same way after the join. Rows whose settlement
station is not an NWS site (Kalshi's international cities) are never charged
as enriched.
"""

from __future__ import annotations

from datetime import date

from apify import Actor

from .kalshi_weather import preview
from .kalshi_weather.fetch import KalshiClient, NwsClient
from .kalshi_weather.service import SourceDrift, enrich_events, list_events

EVENT_PLAIN = "event"
EVENT_ENRICHED = "enriched-event"
EVENT_ANALYSIS = "ladder-analysis"


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


def _extra_cap(n_rich: int, n_plain: int, plain_event: str, rich_event: str, extra_event: str) -> int | None:
    """How many of the `n_rich` enriched rows can also carry the extra event so that
    the whole run still fits max_total_charge_usd. None means unlimited."""
    cm = Actor.get_charging_manager()
    info = cm.get_pricing_info()
    if not info.is_pay_per_event:
        return None
    remaining = cm.get_max_total_charge_usd() - cm.calculate_total_charged_amount()
    if not remaining.is_finite():
        return None
    prices = info.per_event_prices
    px = prices.get(extra_event)
    if px is None or px <= 0:
        return 0
    base = n_rich * (prices.get(rich_event) or 0) + n_plain * (prices.get(plain_event) or 0)
    return max(0, min(n_rich, int((remaining - base) / px)))


async def _add_analyses(enriched: list[dict], n_plain: int) -> int:
    """Attach `analysis_text` to as many enriched rows as the budget and the key allow. Returns the count to charge."""
    info = Actor.get_charging_manager().get_pricing_info()
    if info.is_pay_per_event and EVENT_ANALYSIS not in info.per_event_prices:
        Actor.log.error(f"Event '{EVENT_ANALYSIS}' has no price configured; analyses skipped")
        return 0
    if not preview.available():
        Actor.log.error("ANTHROPIC_API_KEY is not set on this actor; analyses skipped")
        return 0
    cap = _extra_cap(len(enriched), n_plain, EVENT_PLAIN, EVENT_ENRICHED, EVENT_ANALYSIS)
    limit = len(enriched) if cap is None else min(len(enriched), cap)
    if limit < len(enriched):
        Actor.log.warning(f"Budget allows {limit} analyses; {len(enriched) - limit} enriched rows go without.")
    if not limit:
        return 0
    await Actor.set_status_message(f"Writing {limit} ladder analyses")
    return preview.attach_analyses(enriched, limit=limit, progress=Actor.log.info)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s)


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        kind = inp.get("kind", "both")
        if kind not in ("both", "high", "low"):
            kind = "both"
        status = inp.get("status", "open")
        if status not in ("open", "closed", "settled"):
            status = "open"
        enrich = bool(inp.get("enrich", True))
        max_events = int(inp.get("maxEvents", 100))
        delay = float(inp.get("requestDelaySeconds", 0.25))

        def progress(msg: str) -> None:
            Actor.log.info(msg)

        kalshi = KalshiClient(delay_s=delay)
        nws = NwsClient(delay_s=delay)
        await Actor.set_status_message(f"Listing {status} Kalshi temperature markets")
        try:
            events = list_events(
                kalshi,
                kind=kind,
                status=status,
                stations=inp.get("stations") or None,
                date_from=_parse_date(inp.get("dateFrom")),
                date_to=_parse_date(inp.get("dateTo")),
                max_events=max_events,
                progress=progress,
            )
        except SourceDrift as exc:
            await Actor.set_value("OUTPUT", {"ok": False, "errors": [str(exc)]})
            await Actor.fail(status_message=f"Source drift detected, run aborted: {exc}")
            return
        Actor.log.info(f"{len(events)} events listed")

        if enrich and events:
            info = Actor.get_charging_manager().get_pricing_info()
            if info.is_pay_per_event and EVENT_ENRICHED not in info.per_event_prices:
                Actor.log.error(f"Event '{EVENT_ENRICHED}' has no price configured; returning plain rows only")
                await Actor.set_status_message("Configuration problem: enriched-event is not priced, plain rows only")
            cap = _enrich_cap(len(events), EVENT_PLAIN, EVENT_ENRICHED)
            limit = len(events) if cap is None else min(len(events), cap)
            if limit < len(events):
                Actor.log.warning(f"Budget allows {limit} enriched events; {len(events) - limit} will be returned plain.")
            await Actor.set_status_message(f"Joining {limit} events to NWS stations")
            try:
                enrich_events(nws, events, limit=limit, hourly_periods=bool(inp.get("includeHourlyPeriods", False)), progress=progress)
            except SourceDrift as exc:
                await Actor.set_value("OUTPUT", {"ok": False, "errors": [str(exc)]})
                await Actor.fail(status_message=f"Source drift detected, run aborted: {exc}")
                return

        enriched = [e for e in events if (e.get("enrichment") or {}).get("status") in ("ok", "partial")]
        plain = [e for e in events if e not in enriched]
        attempted = [e for e in events if (e.get("enrichment") or {}).get("status") not in (None, "no_nws_station")]
        failed = [e for e in attempted if e not in enriched]
        if len(attempted) >= 5 and not enriched:
            await Actor.set_value("OUTPUT", {"ok": False, "errors": [f"0 of {len(attempted)} NWS joins succeeded: {failed[0]['enrichment'].get('error')}"]})
            await Actor.fail(status_message=f"NWS join failed for all {len(attempted)} events, nothing charged")
            return
        if failed:
            Actor.log.warning(f"{len(failed)} of {len(attempted)} NWS joins failed; those rows are returned plain")
        analyses = 0
        if bool(inp.get("analysis", False)) and enriched:
            analyses = await _add_analyses(enriched, len(plain))
        charged = 0
        if enriched:
            charged += (await Actor.push_data(enriched, charged_event_name=EVENT_ENRICHED)).charged_count
            if analyses:
                charged += (await Actor.charge(EVENT_ANALYSIS, count=analyses)).charged_count
        if plain:
            charged += (await Actor.push_data(plain, charged_event_name=EVENT_PLAIN)).charged_count
        archive = (inp.get("archiveDatasetName") or "").strip()
        if archive and events:
            ds = await Actor.open_dataset(name=archive)
            await ds.push_data(events)
            Actor.log.info(f"archived {len(events)} events to dataset '{archive}'")
        await Actor.set_status_message(
            f"Done: {len(enriched)} enriched + {len(plain)} plain events, {kalshi.requests_made} Kalshi + {nws.requests_made} NWS requests")
        Actor.log.info(f"pushed {len(events)} events, charged {charged} events")
