"""The two operations the actor and the CLI expose.

Both are plain functions with no Apify dependency so they can be tested and
reused from an MCP server later.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from . import arcgis
from .epermits import Cache, enrich_one
from .normalize import normalize
from .rank import rank
from .scopes import SCOPES

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def default_dates(days: int = 90) -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


STALE_WARN_DAYS = 7
STALE_FAIL_DAYS = 30


def preflight(datasets: str = "both") -> tuple[list[str], list[str]]:
    """Drift and freshness guard, run before any query.

    Returns (problems, warnings). Any problem means the run must stop: the
    fields this package reads are gone, or the feed has not updated in a
    month. Warnings cover a feed that is a week or more behind.
    """
    layers = ["issued", "applications"] if datasets == "both" else [datasets]
    problems = arcgis.check_schema(layers)
    warnings: list[str] = []
    if problems:
        return problems, warnings
    for layer in layers:
        try:
            newest = arcgis.newest_date(layer)
        except arcgis.ArcGISError as exc:
            problems.append(f"{layer}: freshness query failed ({exc})")
            continue
        if not newest:
            problems.append(f"{layer}: layer returned no records")
            continue
        age = (date.today() - date.fromisoformat(newest)).days
        if age > STALE_FAIL_DAYS:
            problems.append(f"{layer}: newest record is {newest}, {age} days old; Metro's feed looks dead")
        elif age > STALE_WARN_DAYS:
            warnings.append(f"{layer}: newest record is {newest}, {age} days old")
    return problems, warnings


class EnrichmentDrift(RuntimeError):
    pass


def search_permits(
    *,
    datasets: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    scope: str = "any",
    keyword: str | None = None,
    address: str | None = None,
    parcel: str | None = None,
    zips: list[str] | None = None,
    applicant: str | None = None,
    permit_number: str | None = None,
    permit_type: str | None = None,
    min_valuation: float | None = None,
    max_valuation: float | None = None,
    residential_only: bool = False,
    max_records: int = 200,
    max_scan: int = 20000,
    progress: Progress = _noop,
) -> list[dict]:
    """Return normalized permits, newest first, up to `max_records`.

    Server-side filters narrow the ArcGIS query; the scope preset's regexes
    then refine the candidates on the client so boilerplate mentions do not
    count. `max_scan` bounds how many raw rows are read per layer.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; choose from {', '.join(SCOPES)}")
    preset = SCOPES[scope]
    if not (date_from or date_to or permit_number or parcel or address):
        date_from, date_to = default_dates()
    layers = ["issued", "applications"] if datasets == "both" else [datasets]
    if any(l not in arcgis.LAYERS for l in layers):
        raise ValueError(f"datasets must be issued, applications, or both (got {datasets!r})")

    seen: set[str] = set()
    out: list[dict] = []
    for layer in layers:
        where = arcgis.build_where(
            layer,
            date_from=date_from,
            date_to=date_to,
            keyword=keyword,
            server_like=list(preset.server) or None,
            address=address,
            parcel=parcel,
            zips=[z.strip() for z in zips if z.strip()] if zips else None,
            applicant=applicant,
            permit_number=permit_number,
            min_valuation=min_valuation,
            max_valuation=max_valuation,
            permit_type=permit_type,
        )
        progress(f"{layer}: querying {where}")
        scanned = 0
        for attrs in arcgis.iter_records(layer, where, max_scan=max_scan):
            scanned += 1
            rec = normalize(attrs, layer)
            key = rec["permit_number"] or f"{layer}:{rec['address']}:{rec['date_entered']}"
            if key in seen:
                continue
            text = " ".join(filter(None, [rec["permit_type"], rec["permit_subtype"], rec["description"]]))
            if scope != "any" and not preset.matches(text):
                continue
            if residential_only and rec["is_residential"] is False:
                continue
            seen.add(key)
            out.append(rec)
            if len(out) >= max_records:
                break
        progress(f"{layer}: scanned {scanned}, kept {len(out)} so far")
        if len(out) >= max_records:
            break
    out.sort(key=lambda r: (r["date_issued"] or r["date_entered"] or ""), reverse=True)
    return out[:max_records]


def enrich_permits(
    records: list[dict],
    cache: Cache | None = None,
    *,
    limit: int | None = None,
    max_age_days: int = 7,
    rate_limit: float = 1.0,
    progress: Progress = _noop,
) -> int:
    """Attach the ePermits block to each record in place. Returns how many
    records were enriched (records without an IVR key are skipped).

    Raises EnrichmentDrift when the responses show Metro changed the API
    (fields missing on live responses) or when most fetches fail outright.
    Either way the caller should stop before charging anyone.
    """
    n = 0
    live = 0
    transport_failed = 0
    drift: list[str] = []
    for rec in records:
        if limit is not None and n >= limit:
            break
        ivr = rec.get("ivr_track")
        if not ivr:
            rec["enrichment"] = {"status": "no_ivr_key"}
            continue
        block = enrich_one(ivr, cache, max_age_days=max_age_days, rate_limit=rate_limit)
        rec["enrichment"] = block
        n += 1
        if block.get("source") == "live":
            live += 1
            if block.get("status") == "failed":
                transport_failed += 1
            for issue in block.get("schema_issues") or []:
                drift.append(f"case {ivr}: {issue}")
        if n % 10 == 0:
            progress(f"enriched {n}/{len(records)}")
    if drift:
        raise EnrichmentDrift("ePermits API shape changed: " + "; ".join(drift[:5]))
    if live >= 3 and transport_failed / live > 0.5:
        raise EnrichmentDrift(f"ePermits unreachable: {transport_failed} of {live} live fetches failed")
    return n


def rank_contractors(records: list[dict], *, min_permits: int = 1, limit: int = 100) -> list[dict]:
    return rank(records, min_permits=min_permits, limit=limit)
