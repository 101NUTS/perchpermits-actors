"""Apify actor entry point: Nashville building permits, enriched.

Pay-per-event events (configured in Apify Console, charged here):

    permit           one normalized permit record from the open dataset
    enriched-permit  one permit record joined to ePermits contractor, owner,
                     sub-trade permits, and inspection stage
    contractor       one ranked contractor row

The actor charges through push_data(charged_event_name=...), so the platform
stops both the charge and the row when the caller's budget is reached. The
slow part, enrichment, is capped up front to what the budget allows so we
never do minutes of polite fetching that cannot be billed.
"""

from __future__ import annotations

from apify import Actor

from .nashville_permits.epermits import Cache
from .nashville_permits.service import (
    EnrichmentDrift,
    enrich_permits,
    preflight,
    rank_contractors,
    search_permits,
)

EVENT_PERMIT = "permit"
EVENT_ENRICHED = "enriched-permit"
EVENT_CONTRACTOR = "contractor"
CACHE_STORE = "nashville-epermits-cache"


class KVCache(Cache):
    """Persist ePermits payloads in a named key-value store shared across
    runs, so repeat lookups cost Metro's server nothing."""

    def __init__(self, store):
        self.store = store
        self._pending: list[tuple[str, dict]] = []

    def get(self, key: str) -> dict | None:
        return self._sync(self.store.get_value(key))

    def set(self, key: str, value: dict) -> None:
        self._sync(self.store.set_value(key, value))

    @staticmethod
    def _sync(coro):
        import asyncio

        loop = asyncio.get_event_loop()
        if loop.is_running():
            # enrich_permits is synchronous; run the store call to completion
            # on a helper loop so the actor's main loop is not blocked twice.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                return ex.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)


def _enrich_cap(n_records: int, plain_event: str, rich_event: str) -> int | None:
    """How many of `n_records` can be enriched so that every record still
    fits in the caller's max_total_charge_usd: the enriched ones at the
    rich price and the rest at the plain price. None means unlimited."""
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


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        mode = inp.get("mode", "search")
        enrich = bool(inp.get("enrich", False))
        max_records = int(inp.get("maxRecords", 200))
        # In contractors mode maxRecords caps the ranked rows, so the permit
        # pool that feeds the ranking has its own, larger cap.
        permit_pool = int(inp.get("maxPermitsScanned", 2000)) if mode == "contractors" else max_records

        def progress(msg: str) -> None:
            Actor.log.info(msg)

        datasets = inp.get("datasets", "both")
        await Actor.set_status_message("Checking Metro Nashville data sources")
        problems, warnings = preflight(datasets)
        for w in warnings:
            Actor.log.warning(w)
        if problems:
            # Loud failure, nothing charged: better than silently selling nulls.
            await Actor.set_value("OUTPUT", {"ok": False, "errors": problems, "warnings": warnings})
            await Actor.fail(status_message="Source drift detected, run aborted: " + " | ".join(problems))
            return

        await Actor.set_status_message("Querying Metro Nashville open data")
        records = search_permits(
            datasets=datasets,
            date_from=inp.get("dateFrom") or None,
            date_to=inp.get("dateTo") or None,
            scope=inp.get("scope", "any"),
            keyword=inp.get("keyword") or None,
            address=inp.get("address") or None,
            parcel=inp.get("parcel") or None,
            zips=inp.get("zips") or None,
            applicant=inp.get("applicant") or None,
            permit_number=inp.get("permitNumber") or None,
            permit_type=inp.get("permitType") or None,
            min_valuation=inp.get("minValuation"),
            max_valuation=inp.get("maxValuation"),
            residential_only=bool(inp.get("residentialOnly", False)),
            max_records=permit_pool,
            progress=progress,
        )
        Actor.log.info(f"{len(records)} permits matched")

        if enrich and records:
            if mode == "search":
                cap = _enrich_cap(len(records), EVENT_PERMIT, EVENT_ENRICHED)
            else:
                # Contractor rows are billed per row, not per enriched permit,
                # so enrichment is limited only by the rows' own budget.
                cap = _enrich_cap(len(records), EVENT_CONTRACTOR, EVENT_CONTRACTOR)
            limit = len(records) if cap is None else min(len(records), cap)
            if limit < len(records):
                Actor.log.warning(
                    f"Budget allows {limit} enriched records; {len(records) - limit} will be returned un-enriched."
                )
            await Actor.set_status_message(f"Enriching {limit} permits from ePermits (about {limit} s)")
            store = await Actor.open_key_value_store(name=CACHE_STORE)
            try:
                enrich_permits(
                    records,
                    KVCache(store),
                    limit=limit,
                    max_age_days=int(inp.get("enrichMaxAgeDays", 7)),
                    progress=progress,
                )
            except EnrichmentDrift as exc:
                await Actor.set_value("OUTPUT", {"ok": False, "errors": [str(exc)], "warnings": warnings})
                await Actor.fail(status_message=f"Source drift detected, run aborted: {exc}")
                return

        if mode == "contractors":
            rows = rank_contractors(records, min_permits=int(inp.get("minPermits", 1)), limit=max_records)
            await Actor.set_status_message(f"Ranked {len(rows)} contractors")
            result = await Actor.push_data(rows, charged_event_name=EVENT_CONTRACTOR)
            Actor.log.info(f"pushed {len(rows)} contractors, charged {result.charged_count}")
            return

        enriched = [r for r in records if (r.get("enrichment") or {}).get("status") in ("ok", "partial")]
        plain = [r for r in records if r not in enriched]
        charged = 0
        if enriched:
            charged += (await Actor.push_data(enriched, charged_event_name=EVENT_ENRICHED)).charged_count
        if plain:
            charged += (await Actor.push_data(plain, charged_event_name=EVENT_PERMIT)).charged_count
        await Actor.set_status_message(f"Done: {len(enriched)} enriched + {len(plain)} plain permits")
        Actor.log.info(f"pushed {len(records)} permits, charged {charged} events")
