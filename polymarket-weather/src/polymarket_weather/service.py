"""Orchestration: find a Polymarket temperature event, read its settling station from the
rules, pull that station's reports for the local day, and say where the ladder stands.

Verified offline 2026-09-18 against 89 settled fixture ladders (15 cities, highs and lows, F and
C): the recomputed value landed in the bracket Polymarket paid every time
(scripts/verify_settled.py). Hong Kong settles on the Observatory's daily extract, not a
station reading, and is served without a recomputed value.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from .fetch import FetchError, GammaClient, MetarClient, NwsClient
from .parse import (MONTHS, TIMEZONES, ParseDrift, bracket_for, event_row, local_day_hours_left, parse_slug,
                    rule_outs, summarize_day)

SOURCE = "Perch Data; free official APIs (Polymarket Gamma, NOAA Aviation Weather Center METARs, api.weather.gov)"


class NotServable(Exception):
    """The event exists but cannot be joined (no station, unknown time zone...)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def slug_for(city: str, day: date, kind: str = "high") -> str:
    month = [m for m, i in MONTHS.items() if i == day.month][0]
    word = "highest" if kind == "high" else "lowest"
    return f"{word}-temperature-in-{city}-on-{month}-{day.day}-{day.year}"


def timezone_for(icao: str | None, nws: NwsClient | None) -> str | None:
    if not icao:
        return None
    if icao in TIMEZONES:
        return TIMEZONES[icao]
    if icao.startswith("K") and nws is not None:
        try:
            return (nws.station(icao).get("properties") or {}).get("timeZone")
        except FetchError:
            return None
    return None


def us_forecast(nws: NwsClient, lat: float, lon: float, day: date, kind: str) -> dict[str, Any] | None:
    """NWS daily forecast high or low for the target date (US stations only)."""
    try:
        pts = nws.points(lat, lon)
        url = (pts.get("properties") or {}).get("forecast")
        if not url:
            return None
        periods = (nws.forecast(url).get("properties") or {}).get("periods") or []
    except FetchError:
        return None
    want_day = kind == "high"
    for p in periods:
        start = str(p.get("startTime") or "")[:10]
        if start == day.isoformat() and bool(p.get("isDaytime")) == want_day:
            return {"temperature": p.get("temperature"), "unit": p.get("temperatureUnit"), "name": p.get("name"),
                    "short": p.get("shortForecast"), "source": "api.weather.gov"}
    if not want_day:  # the overnight low for day D is often filed under the previous evening
        for p in periods:
            if not p.get("isDaytime") and str(p.get("endTime") or "")[:10] == day.isoformat():
                return {"temperature": p.get("temperature"), "unit": p.get("temperatureUnit"), "name": p.get("name"),
                        "short": p.get("shortForecast"), "source": "api.weather.gov"}
    return None


def to_unit(value: float | None, from_unit: str | None, to: str | None) -> float | None:
    if value is None or from_unit == to or not from_unit or not to:
        return value
    return round(value * 9 / 5 + 32, 1) if to == "F" else round((value - 32) * 5 / 9, 1)


def join(ev: dict[str, Any], metar: MetarClient, nws: NwsClient | None = None, now: datetime | None = None,
         with_forecast: bool = True) -> dict[str, Any]:
    """One event payload in, the joined row out."""
    row = event_row(ev)
    st = row["station"]
    day = date.fromisoformat(row["target_date"])
    out: dict[str, Any] = {k: v for k, v in row.items() if k != "rules"}
    out["rules_excerpt"] = (row["rules"] or "")[:600]
    out["checked_at"] = (now or _now()).isoformat(timespec="seconds").replace("+00:00", "Z")
    out["source"] = SOURCE
    if st["source_type"] == "hong_kong_observatory":
        out.update(observations=None, forecast=None, floor_rule_outs=[], verdict=None,
                   note="Settles on the Hong Kong Observatory's daily extract, not an airport report; no recomputed value.")
        return out
    if not st.get("icao"):
        raise NotServable(f"{row['slug']}: no station found in the rules text")
    tz = st.get("timezone") or timezone_for(st["icao"], nws)
    if not tz:
        raise NotServable(f"{row['slug']}: time zone unknown for station {st['icao']}")
    out["station"] = {**st, "timezone": tz}
    try:
        reports = metar.metars(st["icao"], hours=_hours_back(day, tz, now))
    except FetchError as exc:
        raise NotServable(f"reports for {st['icao']} unavailable: {exc}")
    if not reports:
        # e.g. Jinan's ZSJN: settles on a Weather Underground page, and NOAA's feed carries no reports for it
        raise NotServable(f"{row['slug']}: station {st['icao']} has no reports in NOAA's METAR feed; "
                          f"it settles on {st['source_type']} ({st.get('url')}), which has no free official source")
    obs = summarize_day(reports, day, tz, row["unit"] or "F", row["hourly_only"])
    if reports:
        r0 = reports[0]
        out["station"].update(lat=r0.get("lat"), lon=r0.get("lon"), report_name=r0.get("name"))
    out["observations"] = obs
    out["hours_left_in_local_day"] = local_day_hours_left(day, tz, now)
    fc = None
    if with_forecast and nws is not None and st["icao"].startswith("K") and out["station"].get("lat") is not None:
        fc = us_forecast(nws, out["station"]["lat"], out["station"]["lon"], day, row["kind"])
    out["forecast"] = fc
    out["floor_rule_outs"] = rule_outs(row, obs)
    running = obs.get("max") if row["kind"] == "high" else obs.get("min")
    fc_val = to_unit(fc.get("temperature"), fc.get("unit"), row["unit"]) if fc else None
    lands = bracket_for(running, row["brackets"])
    out["verdict"] = {
        "running_value": running,
        "running_lands_in": lands["label"] if lands else None,
        "day_complete": obs.get("day_complete"),
        "final_if_complete": lands["label"] if (lands and obs.get("day_complete")) else None,
        "forecast_value": fc_val,
        "forecast_lands_in": (bracket_for(round(fc_val), row["brackets"]) or {}).get("label") if fc_val is not None else None,
        "market_favorite": row["market_summary"].get("favorite"),
        "market_expected_temp": row["market_summary"].get("expected_temp"),
        "paid_bracket": (row.get("settlement") or {}).get("winning_bracket"),
        "note": ("Recomputed from the station's reports the way the rules read them (whole degrees, station's local day"
                 + (", routine hourly reports only" if row["hourly_only"] else "") + "). The official value is the one on the resolution page."),
    }
    return out


def _hours_back(day: date, tz: str, now: datetime | None) -> int:
    from zoneinfo import ZoneInfo
    start = datetime.combine(day, datetime.min.time(), tzinfo=ZoneInfo(tz))
    hours = ((now or _now()) - start).total_seconds() / 3600 + 2
    return int(min(max(hours, 6), 160))


def current_open(events: list[dict[str, Any]], today: date | None = None, grace_days: int = 2) -> list[dict[str, Any]]:
    """Drop 'open' events whose day is long past. Polymarket still listed four May ladders
    (Jinan, Zhengzhou) as open on 2026-09-18."""
    today = today or _now().date()
    out = []
    for ev in events:
        meta = parse_slug(ev.get("slug") or "")
        if meta and (today - meta["target_date"]).days <= grace_days:
            out.append(ev)
    return out


def find_event(gamma: GammaClient, city: str, day: date | None, kind: str) -> dict[str, Any]:
    """The event for a city slug and date; with no date, the nearest open one."""
    if day is not None:
        try:
            return gamma.event(slug_for(city, day, kind))
        except FetchError as exc:
            raise NotServable(f"no Polymarket {kind} temperature event for {city} on {day}: {exc}")
    word = "highest" if kind == "high" else "lowest"
    cands = []
    for ev in current_open(gamma.open_temperature_events()):
        meta = parse_slug(ev.get("slug") or "")
        if meta and meta["city"] == city and ev.get("slug", "").startswith(word):
            cands.append((meta["target_date"], ev))
    if not cands:
        raise NotServable(f"no open Polymarket {kind} temperature event for {city}")
    today = _now().date()
    cands.sort(key=lambda x: (x[0] < today, x[0]))
    return cands[0][1]


__all__ = ["join", "find_event", "slug_for", "NotServable", "ParseDrift", "FetchError"]
