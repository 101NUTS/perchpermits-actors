"""Apify actor entry point: Apify Store demand, ratings, and failing incumbents.

Pay-per-event events (configured in Apify Console, charged here):

    actor   one flat row per store actor: users (7/30/90 days, lifetime),
            runs and fail rate over 30 days, rating and review count, price,
            maintenance notice, days idle, target bucket, gap signals, and the
            change since the previous snapshot
    target  one aggregate row per target bucket (and per search query):
            demand, actor count, leader and its share, rating, fail rate,
            weighted and best rating, broken actors, and the A to G flags

The actor charges through push_data(charged_event_name=...), so the platform
stops both the charge and the row when the caller's budget is reached. The
split is planned up front from the remaining budget: target rows first, then
as many actor rows as fit; rows past the cap are dropped, never charged.
"""

from __future__ import annotations

from decimal import Decimal

from apify import Actor

from .store_trends.fetch import Client
from .store_trends.service import ROW_ACTOR, SourceDrift, apply_deltas, charge_plan, latest_snapshot, run

EVENT_ACTOR = "actor"
EVENT_TARGET = "target"


def _row_caps(n_actors: int, n_targets: int) -> tuple[int, int]:
    """How many (actor rows, target rows) fit in the caller's max_total_charge_usd.
    Unlimited when the run is not pay-per-event or has no cap."""
    cm = Actor.get_charging_manager()
    info = cm.get_pricing_info()
    if not info.is_pay_per_event:
        return n_actors, n_targets
    remaining = cm.get_max_total_charge_usd() - cm.calculate_total_charged_amount()
    if not Decimal(remaining).is_finite():
        return n_actors, n_targets
    prices = info.per_event_prices
    return charge_plan(remaining, n_actors, n_targets, prices.get(EVENT_ACTOR), prices.get(EVENT_TARGET))


async def _read_prior_snapshot(name: str) -> list[dict]:
    """Actor rows of the newest snapshot already in the named dataset, newest first."""
    ds = await Actor.open_dataset(name=name)
    prior: list[dict] = []
    stamp = None
    async for item in ds.iterate_items(desc=True):
        if item.get("row_type", ROW_ACTOR) != ROW_ACTOR:
            continue
        s = item.get("snapshot_at")
        if stamp is None:
            stamp = s
        if s != stamp:
            break
        prior.append(dict(item))
    return latest_snapshot(prior)


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        mode = inp.get("mode", "search")
        if mode not in ("search", "full"):
            mode = "search"
        query = (inp.get("query") or "").strip()
        if mode == "search" and not query:
            await Actor.set_value("OUTPUT", {"ok": False, "errors": ["`query` is required when mode is search"]})
            await Actor.fail(status_message="Input problem: query is required in search mode")
            return
        min_users = int(inp.get("minUsers30d", 5))
        max_actors = max(1, min(int(inp.get("maxActors", 200)), 5000))
        include_targets = bool(inp.get("includeTargets", True))
        snapshot_name = (inp.get("snapshotDatasetName") or "").strip()

        def progress(msg: str) -> None:
            Actor.log.info(msg)

        client = Client(delay_s=float(inp.get("requestDelaySeconds", 0.2)))
        await Actor.set_status_message(f"Sweeping the whole store catalog (about ten minutes)" if mode == "full" else f"Searching the store for '{query}'")
        try:
            rows, targets = run(
                client, mode=mode, query=query or None, min_users_30d=min_users, max_actors=max_actors,
                include_targets=include_targets, progress=progress,
            )
        except SourceDrift as exc:
            await Actor.set_value("OUTPUT", {"ok": False, "errors": [str(exc)]})
            await Actor.fail(status_message=f"Source drift detected, run aborted: {exc}")
            return
        Actor.log.info(f"{len(rows)} actor rows, {len(targets)} target rows after filters")

        prior = None
        if snapshot_name:
            prior = await _read_prior_snapshot(snapshot_name)
            Actor.log.info(f"prior snapshot in '{snapshot_name}': {len(prior)} actor rows" if prior else f"no prior snapshot in '{snapshot_name}'")
        apply_deltas(rows, targets, prior)

        info = Actor.get_charging_manager().get_pricing_info()
        if info.is_pay_per_event:
            for ev in (EVENT_ACTOR, EVENT_TARGET):
                if ev not in info.per_event_prices:
                    # Operator error (event not priced in the Console): say so where the run log is visible.
                    Actor.log.error(f"Event '{ev}' has no price configured")
        n_actors, n_targets = _row_caps(len(rows), len(targets))
        if n_actors < len(rows) or n_targets < len(targets):
            Actor.log.warning(f"Budget allows {n_targets} target and {n_actors} actor rows; {len(targets) - n_targets + len(rows) - n_actors} rows dropped, not charged.")
        charged = 0
        if targets[:n_targets]:
            charged += (await Actor.push_data(targets[:n_targets], charged_event_name=EVENT_TARGET)).charged_count
        if rows[:n_actors]:
            charged += (await Actor.push_data(rows[:n_actors], charged_event_name=EVENT_ACTOR)).charged_count
        if snapshot_name and rows:
            # Named datasets persist beyond run-data retention; the next run reads the newest
            # snapshot back to fill `delta`. Not charged: the default dataset already was.
            ds = await Actor.open_dataset(name=snapshot_name)
            await ds.push_data(rows[:n_actors])
            Actor.log.info(f"appended {len(rows[:n_actors])} actor rows to snapshot dataset '{snapshot_name}'")
        await Actor.set_status_message(f"Done: {n_targets} target + {n_actors} actor rows, {client.requests_made} requests")
        Actor.log.info(f"pushed {n_targets + n_actors} rows, charged {charged} events")
