"""Apify actor entry point: Polymarket daily temperature markets joined to the airport
station each market's rules settle on.

Pay-per-event events (configured in Apify Console, charged here):

    ladder         one city-day ladder from Polymarket (highest or lowest temperature):
                   every bracket with bid, ask, implied probability and volume
    joined-ladder  the same row joined to the settling station: its reports so far that
                   local day recomputed the way the rules read them, brackets already ruled
                   out, the US forecast, and where running value, forecast and market land

The join is capped up front to what the caller's max_total_charge_usd allows; rows past
the cap, and rows that cannot be joined (Hong Kong settles on the Observatory's daily
extract), are returned and charged as plain ladders.
"""

from __future__ import annotations

from datetime import date, timedelta

from apify import Actor

from .polymarket_weather.fetch import FetchError, GammaClient, MetarClient, NwsClient
from .polymarket_weather.parse import ParseDrift, event_row, parse_slug
from .polymarket_weather.service import NotServable, current_open, join, slug_for

EVENT_PLAIN = "ladder"
EVENT_JOINED = "joined-ladder"


def _join_cap(n_records: int) -> int | None:
    """How many of `n_records` can be joined so every record still fits the budget."""
    cm = Actor.get_charging_manager()
    info = cm.get_pricing_info()
    if not info.is_pay_per_event:
        return None
    remaining = cm.get_max_total_charge_usd() - cm.calculate_total_charged_amount()
    if not remaining.is_finite():
        return None
    prices = info.per_event_prices
    pj, pp = prices.get(EVENT_JOINED), prices.get(EVENT_PLAIN)
    if pj is None or pp is None or pj <= pp:
        return cm.calculate_max_event_charge_count_within_limit(EVENT_JOINED)
    return max(0, min(n_records, int((remaining - n_records * pp) / (pj - pp))))


def _want(slug: str, kind: str, cities: set[str]) -> bool:
    meta = parse_slug(slug)
    if not meta:
        return False
    if kind != "both" and meta["kind"] != kind:
        return False
    return not cities or meta["city"] in cities


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        kind = inp.get("kind", "both") if inp.get("kind") in ("both", "high", "low") else "both"
        cities = {str(c).strip().lower().replace(" ", "-") for c in (inp.get("cities") or []) if str(c).strip()}
        max_events = int(inp.get("maxEvents", 100))
        do_join = bool(inp.get("join", True))
        delay = float(inp.get("requestDelaySeconds", 0.25))
        gamma, metar, nws = GammaClient(delay_s=delay), MetarClient(delay_s=delay), NwsClient(delay_s=delay)

        events: list[dict] = []
        if inp.get("dateFrom"):
            if not cities:
                await Actor.fail(status_message="dateFrom needs a list of cities (the date lookup is by market slug)")
                return
            d0 = date.fromisoformat(inp["dateFrom"])
            d1 = date.fromisoformat(inp.get("dateTo") or inp["dateFrom"])
            await Actor.set_status_message(f"Fetching {len(cities)} cities from {d0} to {d1}")
            day = d0
            while day <= d1 and len(events) < max_events:
                for c in sorted(cities):
                    for k in (("high", "low") if kind == "both" else (kind,)):
                        try:
                            events.append(gamma.event(slug_for(c, day, k)))
                        except FetchError:
                            Actor.log.info(f"no event for {c} {k} {day}")
                day += timedelta(days=1)
        else:
            await Actor.set_status_message("Listing open Polymarket temperature markets")
            try:
                events = [e for e in current_open(gamma.open_temperature_events()) if _want(e.get("slug") or "", kind, cities)]
            except FetchError as exc:
                await Actor.fail(status_message=f"Polymarket listing unavailable: {exc}")
                return
        events = events[:max_events]
        Actor.log.info(f"{len(events)} events to process")

        limit = 0
        if do_join and events:
            info = Actor.get_charging_manager().get_pricing_info()
            if info.is_pay_per_event and EVENT_JOINED not in info.per_event_prices:
                Actor.log.error(f"Event '{EVENT_JOINED}' has no price configured; returning plain ladders only")
            else:
                cap = _join_cap(len(events))
                limit = len(events) if cap is None else min(len(events), cap)
                if limit < len(events):
                    Actor.log.warning(f"Budget allows {limit} joins; {len(events) - limit} ladders will be returned plain.")

        joined, plain, drift = [], [], []
        for i, ev in enumerate(events):
            try:
                if i < limit:
                    await Actor.set_status_message(f"Joining {i + 1} of {limit} to its station")
                    row = join(ev, metar, nws)
                    # charged as joined only when the join added something: reports or a forecast
                    has_data = (row.get("observations") or {}).get("count") or row.get("forecast")
                    (joined if has_data else plain).append(row)
                else:
                    row = event_row(ev)
                    row.pop("rules", None)
                    plain.append(row)
            except NotServable as exc:
                Actor.log.warning(str(exc))
                row = event_row(ev)
                row.pop("rules", None)
                plain.append({**row, "join_error": str(exc)})
            except ParseDrift as exc:
                drift.append(f"{ev.get('slug')}: {exc}")
        if drift and not (joined or plain):
            await Actor.set_value("OUTPUT", {"ok": False, "errors": drift[:10]})
            await Actor.fail(status_message=f"Source drift detected, run aborted: {drift[0]}")
            return
        for d in drift:
            Actor.log.warning(f"skipped, payload not understood: {d}")

        charged = 0
        if joined:
            charged += (await Actor.push_data(joined, charged_event_name=EVENT_JOINED)).charged_count
        if plain:
            charged += (await Actor.push_data(plain, charged_event_name=EVENT_PLAIN)).charged_count
        await Actor.set_status_message(
            f"Done: {len(joined)} joined + {len(plain)} plain ladders; {gamma.requests_made} Polymarket, "
            f"{metar.requests_made} METAR, {nws.requests_made} NWS requests")
        Actor.log.info(f"pushed {len(joined) + len(plain)} rows, charged {charged} events")
