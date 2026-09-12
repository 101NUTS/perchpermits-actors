"""What the actor and the CLI both call: list Kalshi temperature ladders, then
join each one to its NWS settlement station.

`list_events` returns one plain row per Kalshi event (a city, a day, high or
low) with every strike nested and the market's implied distribution.
`enrich_events` adds the `enrichment` block from api.weather.gov: the
observations recorded so far on the target day, the daily and hourly forecast
for it, the CLI climate report once it exists, and which bracket each of
those lands in. Station lookups are cached per run, so a city costs about
four NWS requests however many of its ladders are listed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Callable

from .fetch import FetchError, KalshiClient, NwsClient
from .parse import (
    KNOWN_STATION_BY_SERIES,
    ParseDrift,
    bracket_for,
    event_row,
    kind_from_series,
    local_day_window,
    parse_cli,
    summarize_daily_forecast,
    summarize_hourly_forecast,
    summarize_observations,
)

Progress = Callable[[str], None]

# Kalshi series that are temperature ladders but not daily city markets.
SKIP_SERIES = {"KXHIGHUS", "KXHIGHNYD", "KXHIGHTEMPDEN"}


class SourceDrift(Exception):
    """A source changed shape; fail loudly rather than sell empty rows."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def temperature_series(kalshi: KalshiClient, kind: str = "both") -> list[dict[str, Any]]:
    """Daily KXHIGH*/KXLOW* series from Kalshi's weather category."""
    try:
        series = kalshi.series("Climate and Weather")
    except FetchError as exc:
        raise SourceDrift(f"Kalshi series list unavailable: {exc}") from exc
    if not series:
        raise SourceDrift("Kalshi returned no series in the Climate and Weather category")
    out = []
    for s in series:
        t = s.get("ticker") or ""
        k = kind_from_series(t)
        if not k or t in SKIP_SERIES:
            continue
        if s.get("frequency") not in (None, "daily"):
            continue
        if kind != "both" and k != kind:
            continue
        out.append(s)
    if not out:
        raise SourceDrift("no daily temperature series in Kalshi's weather category (naming changed?)")
    return sorted(out, key=lambda s: s["ticker"])


def list_events(
    kalshi: KalshiClient,
    *,
    kind: str = "both",
    status: str = "open",
    stations: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    max_events: int = 200,
    progress: Progress = lambda m: None,
) -> list[dict[str, Any]]:
    if max_events <= 0:
        return []
    want = {s.strip().upper() for s in (stations or []) if s and s.strip()}
    series = temperature_series(kalshi, kind)
    progress(f"{len(series)} daily temperature series on Kalshi; listing {status} events")
    out: list[dict[str, Any]] = []
    drift = 0
    for s in series:
        if want and s["ticker"] in KNOWN_STATION_BY_SERIES and KNOWN_STATION_BY_SERIES[s["ticker"]] not in want:
            continue  # known to settle elsewhere (or outside the NWS); skip the request
        try:
            events = kalshi.events(s["ticker"], status=status)
        except FetchError as exc:
            progress(f"{s['ticker']}: {exc}")
            continue
        for ev in events:  # newest first
            if not ev.get("markets"):
                # Kalshi nests markets only for roughly the newest two months of settled
                # events; older ones come back bare and their markets are gone from the
                # markets endpoint too. Nothing to sell there, and not a shape change.
                continue
            try:
                row = event_row(ev)
            except ParseDrift as exc:
                drift += 1
                if drift >= 3 and not out:
                    raise SourceDrift(f"Kalshi event payload changed: {exc}") from exc
                continue
            td = row.get("target_date")
            if date_from and td and date.fromisoformat(td) < date_from and status != "open":
                break  # settled/closed lists run newest first; everything after this is older still
            st = row.get("station")
            if want and (not st or st["cli_id"] not in want):
                continue
            if date_from and (not td or date.fromisoformat(td) < date_from):
                continue
            if date_to and (not td or date.fromisoformat(td) > date_to):
                continue
            row["series_title"] = s.get("title")
            row["status"] = status
            row["fetched_at_utc"] = _now_iso()
            out.append(row)
            if len(out) >= max_events:
                return out
    return out


class StationCache:
    """Per-run memo of everything about a station that does not depend on the day."""

    def __init__(self, nws: NwsClient):
        self.nws = nws
        self.meta: dict[str, dict[str, Any] | None] = {}
        self.errors: dict[str, str] = {}
        self.forecasts: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.cli_lists: dict[str, list[dict[str, Any]]] = {}
        self.cli_products: dict[str, dict[str, Any]] = {}

    def station(self, icao: str) -> dict[str, Any] | None:
        """Station name, timezone, coordinates, and forecast grid; None if NWS has no such station."""
        if icao in self.meta:
            return self.meta[icao]
        try:
            raw = self.nws.station(icao)
            p = raw.get("properties") or {}
            coords = (raw.get("geometry") or {}).get("coordinates") or [None, None]
            if coords[0] is None:
                raise ParseDrift(f"station {icao} has no coordinates")
            pp = (self.nws.points(coords[1], coords[0])).get("properties") or {}
            meta = {
                "icao": icao,
                "name": p.get("name"),
                "timezone": p.get("timeZone") or pp.get("timeZone"),
                "lat": coords[1], "lon": coords[0],
                "grid_id": pp.get("gridId"), "grid_x": pp.get("gridX"), "grid_y": pp.get("gridY"),
                "forecast_url": pp.get("forecast"), "forecast_hourly_url": pp.get("forecastHourly"),
                "forecast_office": pp.get("cwa") or pp.get("gridId"),
            }
            if not meta["timezone"] or not meta["forecast_url"] or not meta["forecast_hourly_url"]:
                raise ParseDrift(f"station/points payload for {icao} lacks timezone or forecast urls")
        except (FetchError, ParseDrift) as exc:
            self.errors[icao] = str(exc)
            self.meta[icao] = None
            return None
        self.meta[icao] = meta
        return meta

    def error(self, icao: str) -> str | None:
        return self.errors.get(icao)

    def forecast_pair(self, meta: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        icao = meta["icao"]
        if icao not in self.forecasts:
            self.forecasts[icao] = (self.nws.forecast(meta["forecast_url"]), self.nws.forecast(meta["forecast_hourly_url"]))
        return self.forecasts[icao]

    def cli_for(self, cli_id: str, day: date, tz: str) -> dict[str, Any] | None:
        """The climate report whose summary is for `day`, preferring the final issuance.

        The report for a day is issued that afternoon (partial) and the next morning
        (final), so only products issued on those two local dates are fetched.
        """
        if cli_id not in self.cli_lists:
            try:
                self.cli_lists[cli_id] = self.nws.cli_products(cli_id)
            except FetchError:
                self.cli_lists[cli_id] = []
        best: dict[str, Any] | None = None
        z = ZoneInfo(tz)
        wanted_days = {day, day + timedelta(days=1)}
        for item in self.cli_lists[cli_id]:  # newest first
            pid = item.get("id") or item.get("@id")
            issued = item.get("issuanceTime")
            if not pid or not issued:
                continue
            issued_day = datetime.fromisoformat(issued.replace("Z", "+00:00")).astimezone(z).date()
            if issued_day not in wanted_days:
                if issued_day < day:
                    break
                continue
            if pid not in self.cli_products:
                try:
                    self.cli_products[pid] = parse_cli(self.nws.product(item.get("@id") or pid))
                except (FetchError, ParseDrift) as exc:
                    self.cli_products[pid] = {"error": str(exc)}
            rep = self.cli_products[pid]
            if rep.get("summary_for") != day.isoformat():
                continue
            if best is None or (rep.get("final") and not best.get("final")):
                best = rep
            if best.get("final"):
                break
        return best


def enrich_events(
    nws: NwsClient,
    events: list[dict[str, Any]],
    *,
    limit: int | None = None,
    hourly_periods: bool = False,
    progress: Progress = lambda m: None,
) -> int:
    """Attach `enrichment` to the first `limit` events in place. Returns how many succeeded."""
    n = len(events) if limit is None else min(limit, len(events))
    cache = StationCache(nws)
    done = 0
    drift = 0
    today_utc = datetime.now(timezone.utc).date()
    for i, ev in enumerate(events[:n]):
        st = ev.get("station")
        td = ev.get("target_date")
        if not st or not td:
            ev["enrichment"] = {"status": "no_nws_station", "error": "settlement station is not an NWS climate site"}
            continue
        day = date.fromisoformat(td)
        progress(f"Enriching {i + 1}/{n}: {ev['event_ticker']} ({st['city']}, {st['icao']}, {day})")
        meta = cache.station(st["icao"])
        if meta is None:
            ev["enrichment"] = {"status": "station_lookup_failed", "error": cache.error(st["icao"])}
            continue
        tz = meta["timezone"]
        enrichment: dict[str, Any] = {"status": "ok", "station": meta, "observations": None, "forecast": None, "hourly_forecast": None, "climate_report": None, "analysis": None}
        problems: list[str] = []
        try:
            start, end = local_day_window(day, tz)
            obs = nws.observations(st["icao"], start, end)
            enrichment["observations"] = summarize_observations(obs, day, tz)
        except FetchError as exc:
            problems.append(f"observations: {exc}")
        except ParseDrift as exc:
            drift += 1
            problems.append(f"observations: {exc}")
        try:
            daily, hourly = cache.forecast_pair(meta)
            enrichment["forecast"] = summarize_daily_forecast(daily, day, tz)
            enrichment["hourly_forecast"] = summarize_hourly_forecast(hourly, day, tz, keep_periods=hourly_periods)
        except FetchError as exc:
            problems.append(f"forecast: {exc}")
        except ParseDrift as exc:
            drift += 1
            problems.append(f"forecast: {exc}")
        if day <= today_utc:
            rep = cache.cli_for(st["cli_id"], day, tz)
            enrichment["climate_report"] = rep
        if drift >= 3:
            # Shape changes (not network errors) on three ladders: the NWS payload moved.
            # Partial rows would still be sold at the enriched price, so stop instead.
            raise SourceDrift(f"NWS payloads changed: {problems[-1]}")
        enrichment["analysis"] = analyze(ev, enrichment)
        if problems:
            enrichment["status"] = "partial"
            enrichment["problems"] = problems
        if enrichment["observations"] is None and enrichment["forecast"] is None:
            enrichment["status"] = "fetch_failed"
            enrichment["error"] = "; ".join(problems)
            ev["enrichment"] = enrichment
            continue
        ev["enrichment"] = enrichment
        done += 1
    return done


def analyze(ev: dict[str, Any], e: dict[str, Any]) -> dict[str, Any]:
    """Where the forecast, the running observation, and the official report each
    land on the ladder, next to what the market charges for that bracket."""
    kind = ev.get("kind")
    markets = ev.get("markets") or []
    fc = e.get("forecast") or {}
    hf = e.get("hourly_forecast") or {}
    ob = e.get("observations") or {}
    rep = e.get("climate_report") or {}
    pick = max if kind == "high" else min
    daily_f = fc.get("high_f") if kind == "high" else fc.get("low_f")
    hourly_f = hf.get("max_f") if kind == "high" else hf.get("min_f")
    running_f = ob.get("max_f") if kind == "high" else ob.get("min_f")
    official_f = rep.get("max_f") if kind == "high" else rep.get("min_f")
    if official_f is None and (ev.get("settlement") or {}).get("value_f") is not None:
        official_f = ev["settlement"]["value_f"]  # Kalshi's stamped settlement number outlives NWS's report window
    # Once the target day is underway the daily forecast may no longer carry its period
    # (the overnight low is gone from the feed by mid-morning); the hourly feed still
    # covers the remaining hours, so it stands in.
    forecast_f = daily_f if daily_f is not None else hourly_f
    # The best current estimate of the day's extreme: what has been recorded so far
    # against what the hourly forecast still expects. Equals the forecast before the
    # day starts and converges on the observation as the day ends.
    candidates = [t for t in (running_f, hourly_f) if t is not None]
    projected_f = pick(candidates) if candidates else forecast_f

    def hit(temp: float | None) -> dict[str, Any]:
        b = bracket_for(temp, markets)
        return {"temp_f": temp, "ticker": b["ticker"] if b else None, "label": b.get("label") if b else None,
                "implied_prob": b.get("implied_prob") if b else None, "normalized_prob": b.get("normalized_prob") if b else None}

    out = {
        "forecast": hit(forecast_f),
        "forecast_source": "daily" if daily_f is not None else ("hourly" if hourly_f is not None else None),
        "hourly_forecast": hit(hourly_f),
        "running": hit(running_f),
        "projected": hit(projected_f),
        "official": hit(official_f) if official_f is not None else None,
        "official_source": ("climate_report" if rep and (rep.get("max_f") if kind == "high" else rep.get("min_f")) is not None
                            else "kalshi_settlement" if official_f is not None else None),
        "official_final": rep.get("final") if rep else None,
        "market_expected_temp_f": (ev.get("market_summary") or {}).get("expected_temp_f"),
        "forecast_minus_market_f": None,
        "projected_minus_market_f": None,
    }
    mkt = out["market_expected_temp_f"]
    if forecast_f is not None and mkt is not None:
        out["forecast_minus_market_f"] = round(forecast_f - mkt, 2)
    if projected_f is not None and mkt is not None:
        out["projected_minus_market_f"] = round(projected_f - mkt, 2)
    return out
