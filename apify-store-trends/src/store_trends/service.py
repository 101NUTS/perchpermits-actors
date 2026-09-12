"""What the actor and the CLI both call: fetch store items, flatten, aggregate
targets, compute deltas against a prior snapshot, plan charges.

`search_items` runs the store's own search for a query (relevance-ranked, a
few requests). `sweep_items` unions the whole catalog across the global
popularity sort, every category, and every pricing model, because the global
sort is unstable past about 12,000 rows; that is the monthly-snapshot path and
takes about ten minutes. Both return raw store items; `build_rows` turns them
into the flat rows the README documents, `aggregate_targets` folds those into
one row per target bucket, and `apply_deltas` fills `delta` from the previous
snapshot.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from .fetch import PAGE_LIMIT, Client, FetchError
from .parse import CATS, PRICING, UNMATCHED, ParseDrift, flatten, gap_signals, hostility, query_match

Progress = Callable[[str], None]

MAX_OFFSET = 30000
ROW_ACTOR = "actor"
ROW_TARGET = "target"


class SourceDrift(Exception):
    """The store API changed shape; fail loudly rather than sell empty rows."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- fetch

def _items(body: dict[str, Any], what: str) -> list[dict[str, Any]]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or "items" not in data or not isinstance(data["items"], list):
        raise SourceDrift(f"store payload for {what} has no `items` list")
    return data["items"]


def _key(it: dict[str, Any]) -> tuple[str, str]:
    return (str(it.get("username")), str(it.get("name")))


def search_items(client: Client, query: str, *, max_items: int = 5000, progress: Progress = lambda m: None) -> list[dict[str, Any]]:
    """Every store item the search returns for `query`, in the store's relevance order."""
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required in search mode")
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    offset = 0
    limit = min(PAGE_LIMIT, max(50, max_items // 2))
    while offset < MAX_OFFSET and len(out) < max_items:
        progress(f"Searching the store for '{query}' (offset {offset})")
        items = _items(client.store_page(search=query, limit=limit, offset=offset), f"search '{query}'")
        for it in items:
            k = _key(it)
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
        if len(items) < limit:
            break
        offset += limit
    return out


def _sweep(client: Client, seen: dict[tuple[str, str], dict[str, Any]], param: str, value: str, progress: Progress) -> None:
    offset, empties = 0, 0
    kw = {"sort_by": "popularity"}
    if param == "category":
        kw["category"] = value
    elif param == "pricingModel":
        kw["pricing_model"] = value
    while offset < MAX_OFFSET:
        items = _items(client.store_page(limit=PAGE_LIMIT, offset=offset, **kw), f"{param}={value} offset={offset}")
        if not items:
            empties += 1
            if empties >= 2:
                return
            continue
        empties = 0
        top = 0
        for it in items:
            seen.setdefault(_key(it), it)
            s = it.get("stats") if isinstance(it, dict) else None
            if not isinstance(s, dict) or "totalUsers" not in s:
                raise SourceDrift(f"store item {it.get('username') if isinstance(it, dict) else it!r} has no stats.totalUsers")
            top = max(top, s["totalUsers"] or 0)
        progress(f"{param}={value} offset={offset} got={len(items)} unique={len(seen)}")
        offset += PAGE_LIMIT
        if top < 3:
            return


def sweep_items(client: Client, *, progress: Progress = lambda m: None) -> list[dict[str, Any]]:
    """The whole catalog, unioned across the popularity sort, every category, and
    every pricing model. About ten minutes and a hundred requests."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    _sweep(client, seen, "sortBy", "popularity", progress)
    for param, values in (("category", CATS), ("pricingModel", PRICING)):
        for v in values:
            try:
                _sweep(client, seen, param, v, progress)
            except FetchError as exc:
                # The store's enum changes without notice (FINANCE vanished in 2026-09). One
                # rejected slice must not fail a ten-minute run; the union of the others
                # still covers the catalog.
                progress(f"skipping {param}={v}: {exc}")
    return list(seen.values())


# ---------------------------------------------------------------- rows

def build_rows(items: list[dict[str, Any]], *, now: datetime, min_users_30d: int = 5, max_actors: int = 200, query: str | None = None) -> list[dict[str, Any]]:
    """Flatten, drop actors under `min_users_30d`, sort by demand (actors whose title or
    name match `query` first), cut at `max_actors`, and attach `query_match`,
    `gap_signals`, `snapshot_at`, `row_type`. Raises SourceDrift when an item is not
    shaped like a store actor."""
    snapshot_at = iso(now)
    rows: list[dict[str, Any]] = []
    for it in items:
        try:
            r = flatten(it, now)
        except ParseDrift as exc:
            raise SourceDrift(str(exc)) from exc
        if r["users_30d"] < min_users_30d:
            continue
        r["query_match"] = query_match(query, r["title"], it.get("name"))
        r["gap_signals"] = gap_signals(r)
        r["snapshot_at"] = snapshot_at
        r["delta"] = empty_actor_delta()
        r["row_type"] = ROW_ACTOR
        rows.append(r)
    rows.sort(key=lambda r: (0 if r["query_match"] else 1, -r["users_30d"], -r["users_total"], r["actor"]))
    if max_actors is not None and max_actors >= 0:
        rows = rows[:max_actors]
    return rows


def empty_actor_delta() -> dict[str, Any]:
    return {"users_30d_prev": None, "users_30d_change": None, "rating_prev": None, "fail_rate_prev": None, "is_new": None}


def empty_target_delta() -> dict[str, Any]:
    return {"users_30d_prev": None, "users_30d_change": None, "actors_prev": None, "leader_prev": None, "is_new": None}


# ---------------------------------------------------------------- targets

def _rated(r: dict[str, Any]) -> bool:
    return r.get("rating") is not None and (r.get("reviews") or 0) >= 3


def _aggregate(name: str, kind: str, rs: list[dict[str, Any]], snapshot_at: str) -> dict[str, Any]:
    active = [r for r in rs if r["users_30d"] > 0]
    u30 = sum(r["users_30d"] for r in rs)
    rated5 = [r for r in rs if (r.get("reviews") or 0) >= 5 and r.get("rating")]
    leader = max(rs, key=lambda r: (r["users_30d"], r["users_total"]))
    best_rated = max(rated5, key=lambda r: r["rating"] or 0) if rated5 else None
    share = leader["users_30d"] / u30 if u30 else 0.0
    broken = [r for r in rs if r["users_30d"] >= 10 and (r["fail_rate_30d"] or 0) >= 0.4]
    weighted = sum((r["rating"] or 0) * r["users_30d"] for r in rs if _rated(r))
    wdenom = sum(r["users_30d"] for r in rs if _rated(r))
    host = hostility(name) if kind == "site" else "med"
    t = {
        "row_type": ROW_TARGET,
        "target": name,
        "target_kind": kind,
        "hostility": host,
        "actors": len(rs),
        "active_actors": len(active),
        "users_30d": u30,
        "users_total": sum(r["users_total"] for r in rs),
        "leader": leader["actor"],
        "leader_url": leader["url"],
        "leader_share": round(share, 2),
        "leader_rating": leader["rating"],
        "leader_reviews": leader["reviews"],
        "leader_fail_rate": leader["fail_rate_30d"],
        "leader_price": leader["price"],
        "leader_signals": list(leader.get("gap_signals") or []),
        "weighted_rating": round(weighted / wdenom, 2) if wdenom else None,
        "best_rated": best_rated["actor"] if best_rated else "",
        "best_rating": best_rated["rating"] if best_rated else None,
        "broken_actors": len(broken),
        "signals": [],
        "snapshot_at": snapshot_at,
        "delta": empty_target_delta(),
    }
    t["signals"] = target_signals(t, rs)
    return t


def target_signals(t: dict[str, Any], rs: list[dict[str, Any]]) -> list[str]:
    """The A to G shortlist rules from the developer's store scan, as flags."""
    out: list[str] = []
    # A. Demand is proven, quality is weak: the whole target is poorly served
    if t["users_30d"] >= 150 and (
        (t["weighted_rating"] is not None and t["weighted_rating"] < 4.0)
        or (t["leader_fail_rate"] is not None and t["leader_fail_rate"] >= 0.3)
        or (t["best_rating"] is not None and t["best_rating"] < 4.2)
    ):
        out.append("A_poorly_served")
    # B. Individual big actors with bad ratings
    if any(r["users_total"] >= 500 and _rated(r) and r["rating"] < 3.6 for r in rs):
        out.append("B_big_actor_low_rated")
    # C. Actors that users keep trying but that mostly fail now
    if any(r["users_30d"] >= 25 and r["runs_30d"] >= 100 and (r["fail_rate_30d"] or 0) >= 0.4 for r in rs):
        out.append("C_actor_failing_in_use")
    # D. Under maintenance or idle while still in demand
    if any(r["users_30d"] >= 20 and (r["notice"] == "UNDER_MAINTENANCE" or (r["days_since_last_run"] or 0) >= 14) for r in rs):
        out.append("D_incumbent_idle")
    # E. Thin markets: demand with few active actors
    if t["users_30d"] >= 40 and t["active_actors"] <= 6:
        out.append("E_thin_market")
    # F. Low-hostility targets with real demand (on-call light)
    if t["hostility"] == "low" and t["users_30d"] >= 60:
        out.append("F_low_hostility_demand")
    # G. Monopolies: one actor owns 80%+ of a target with 300+ monthly users
    if t["users_30d"] >= 300 and t["leader_share"] >= 0.8:
        out.append("G_monopoly")
    return out


def aggregate_targets(rows: list[dict[str, Any]], *, query: str | None = None, min_target_users_30d: int = 0) -> list[dict[str, Any]]:
    """One row per target bucket present in `rows` (unmatched actors are skipped),
    plus, when `query` is given, one row over the actors whose title or name match it."""
    if not rows:
        return []
    snapshot_at = rows[0]["snapshot_at"]
    out: list[dict[str, Any]] = []
    if query:
        matched = [r for r in rows if r.get("query_match")]
        if matched:
            out.append(_aggregate(query.strip(), "query", matched, snapshot_at))
    by_t: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_t[r["target"]].append(r)
    sites = []
    for name, rs in by_t.items():
        if name == UNMATCHED:
            continue
        t = _aggregate(name, "site", rs, snapshot_at)
        if t["users_30d"] < min_target_users_30d:
            continue
        sites.append(t)
    sites.sort(key=lambda t: (-t["users_30d"], t["target"]))
    return out + sites


# ---------------------------------------------------------------- deltas

def latest_snapshot(prior_rows_desc: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Given rows read newest-first from a snapshot dataset, keep the ones that share
    the newest `snapshot_at`. Stops at the first older row, so callers can stream."""
    out: list[dict[str, Any]] = []
    stamp = None
    for r in prior_rows_desc:
        if r.get("row_type", ROW_ACTOR) != ROW_ACTOR:
            continue
        s = r.get("snapshot_at")
        if stamp is None:
            stamp = s
        if s != stamp:
            break
        out.append(r)
    return out


def apply_deltas(rows: list[dict[str, Any]], targets: list[dict[str, Any]], prior: list[dict[str, Any]] | None) -> None:
    """Fill `delta` on actor and target rows in place from the previous snapshot's
    actor rows. With no prior snapshot (None or empty) every delta stays null."""
    if not prior:
        return
    by_actor = {p["actor"]: p for p in prior if p.get("actor")}
    for r in rows:
        p = by_actor.get(r["actor"])
        if p is None:
            r["delta"] = {"users_30d_prev": None, "users_30d_change": None, "rating_prev": None, "fail_rate_prev": None, "is_new": True}
            continue
        prev_u = p.get("users_30d")
        r["delta"] = {
            "users_30d_prev": prev_u,
            "users_30d_change": (r["users_30d"] - prev_u) if isinstance(prev_u, (int, float)) else None,
            "rating_prev": p.get("rating"),
            "fail_rate_prev": p.get("fail_rate_30d"),
            "is_new": False,
        }
    prior_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in prior:
        prior_by_target[p.get("target") or UNMATCHED].append(p)
    for t in targets:
        if t["target_kind"] == "query":
            ps = [p for p in prior if p.get("query_match")]
        else:
            ps = prior_by_target.get(t["target"], [])
        if not ps:
            t["delta"] = {"users_30d_prev": None, "users_30d_change": None, "actors_prev": None, "leader_prev": None, "is_new": True}
            continue
        prev_u = sum(p.get("users_30d") or 0 for p in ps)
        leader_prev = max(ps, key=lambda p: (p.get("users_30d") or 0, p.get("users_total") or 0)).get("actor")
        t["delta"] = {"users_30d_prev": prev_u, "users_30d_change": t["users_30d"] - prev_u, "actors_prev": len(ps), "leader_prev": leader_prev, "is_new": False}


# ---------------------------------------------------------------- charging

def charge_plan(remaining_usd: Decimal | float | None, n_actors: int, n_targets: int, price_actor: Decimal | float | None, price_target: Decimal | float | None) -> tuple[int, int]:
    """How many (actor rows, target rows) fit in `remaining_usd`. Target rows are
    kept first (few, and the summary the caller asked for), actor rows fill the
    rest; rows past the cap are dropped, never charged. None remaining or None
    prices mean unlimited."""
    if remaining_usd is None or price_actor is None or price_target is None:
        return n_actors, n_targets
    rem = Decimal(str(remaining_usd))
    if not rem.is_finite():
        return n_actors, n_targets
    pa, pt = Decimal(str(price_actor)), Decimal(str(price_target))
    if rem <= 0:
        return 0, 0
    k_t = n_targets if pt <= 0 else min(n_targets, int(rem / pt))
    rem -= k_t * pt
    k_a = n_actors if pa <= 0 else min(n_actors, int(rem / pa))
    return max(0, k_a), max(0, k_t)


# ---------------------------------------------------------------- one call

def run(
    client: Client,
    *,
    mode: str = "search",
    query: str | None = None,
    min_users_30d: int = 5,
    max_actors: int = 200,
    include_targets: bool = True,
    now: datetime | None = None,
    progress: Progress = lambda m: None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch, flatten, and aggregate. Returns (actor_rows, target_rows) with null deltas."""
    now = now or now_utc()
    if mode == "full":
        items = sweep_items(client, progress=progress)
        min_target = 20
    else:
        items = search_items(client, query or "", max_items=max(max_actors, 50) * 5, progress=progress)
        min_target = 0
    progress(f"{len(items)} store items fetched")
    rows = build_rows(items, now=now, min_users_30d=min_users_30d, max_actors=max_actors, query=query if mode != "full" else None)
    targets = aggregate_targets(rows, query=query if mode != "full" else None, min_target_users_30d=min_target) if include_targets else []
    return rows, targets
