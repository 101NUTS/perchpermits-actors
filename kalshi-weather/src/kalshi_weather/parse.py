"""Pure functions over the two sources' payloads. No I/O.

Kalshi side: turn an event with nested markets into one row per city-day
ladder, work out each bracket's integer temperature bounds, and derive the
market's implied distribution. NWS side: reduce observations, forecasts, and
the CLI climate report to the few numbers a bettor compares against strikes.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
MONTH_NAMES = {m.upper(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}

# Station ids (the NWS CLI location code inside Kalshi's rules) to the ICAO
# identifier of the observing station. Every US station Kalshi used on
# 2026-09-11 is "K" + code; the table exists so a future exception is one line.
ICAO_BY_CLI: dict[str, str] = {}

# Series -> settlement station as observed on 2026-09-11. Only an optimisation:
# with a `stations` filter, series known to settle elsewhere are not fetched.
# The rules text of each market remains the source of truth for the station,
# so a series missing here (or moved to another station) is still handled.
KNOWN_STATION_BY_SERIES: dict[str, str | None] = {
    "KXHIGHAUS": "AUS", "KXLOWTAUS": "AUS", "KXLOWAUS": "AUS",
    "KXHIGHCHI": "MDW", "KXLOWTCHI": "MDW", "KXLOWCHI": "MDW",
    "KXHIGHDEN": "DEN", "KXLOWTDEN": "DEN", "KXLOWDEN": "DEN",
    "KXHIGHLAX": "LAX", "KXLOWTLAX": "LAX", "KXLOWLAX": "LAX",
    "KXHIGHMIA": "MIA", "KXLOWTMIA": "MIA", "KXLOWMIA": "MIA",
    "KXHIGHNY": "NYC", "KXLOWTNYC": "NYC", "KXLOWNYC": "NYC", "KXLOWNY": "NYC",
    "KXHIGHPHIL": "PHL", "KXLOWTPHIL": "PHL", "KXLOWPHIL": "PHL",
    "KXHIGHTATL": "ATL", "KXLOWTATL": "ATL",
    "KXHIGHTBOS": "BOS", "KXLOWTBOS": "BOS",
    "KXHIGHTDAL": "DFW", "KXLOWTDAL": "DFW",
    "KXHIGHTDC": "DCA", "KXLOWTDC": "DCA",
    "KXHIGHTEWR": "EWR", "KXLOWTEWR": "EWR",
    "KXHIGHTHOU": "HOU", "KXLOWTHOU": "HOU", "KXHIGHHOU": "HOU", "KXHIGHOU": "HOU",
    "KXHIGHTLV": "LAS", "KXLOWTLV": "LAS",
    "KXHIGHTMIN": "MSP", "KXLOWTMIN": "MSP",
    "KXHIGHTNOLA": "MSY", "KXLOWTNOLA": "MSY",
    "KXHIGHTOKC": "OKC", "KXLOWTOKC": "OKC",
    "KXHIGHTPHX": "PHX", "KXLOWTPHX": "PHX",
    "KXHIGHTSAN": "SAN", "KXLOWTSAN": "SAN", "KXHIGHTKSAN": "SAN", "KXLOWTKSAN": "SAN",
    "KXHIGHTSATX": "SAT", "KXLOWTSATX": "SAT",
    "KXHIGHTSDF": "SDF", "KXLOWTSDF": "SDF",
    "KXHIGHTSEA": "SEA", "KXLOWTSEA": "SEA",
    "KXHIGHTSFO": "SFO", "KXLOWTSFO": "SFO",
    "KXHIGHTTTN": "TTN", "KXLOWTTTN": "TTN",
    # International cities settle on The Weather Company only; no NWS station exists.
    "KXHIGHTCYYZ": None, "KXLOWTCYYZ": None,
    "KXHIGHTEBBR": None, "KXLOWTEBBR": None,
    "KXHIGHTEDDB": None, "KXLOWTEDDB": None,
    "KXHIGHTEDDF": None, "KXLOWTEDDF": None,
    "KXHIGHTEGLL": None, "KXLOWTEGLL": None,
    "KXHIGHTEHAM": None, "KXLOWTEHAM": None,
    "KXHIGHTLFPG": None, "KXLOWTLFPG": None,
    "KXHIGHTLSGG": None, "KXLOWTLSGG": None,
    "KXHIGHTLTFM": None, "KXLOWTLTFM": None,
    "KXHIGHTMMMX": None, "KXLOWTMMMX": None,
    "KXHIGHTOMDB": None, "KXLOWTOMDB": None,
    "KXHIGHTRJTT": None, "KXLOWTRJTT": None,
    "KXHIGHTRKSI": None, "KXLOWTRKSI": None,
    "KXHIGHTSBGR": None, "KXLOWTSBGR": None,
    "KXHIGHTVABB": None, "KXLOWTVABB": None,
    "KXHIGHTVHHH": None, "KXLOWTVHHH": None,
    "KXHIGHTWSSS": None, "KXLOWTWSSS": None,
    "KXHIGHTYSSY": None, "KXLOWTYSSY": None,
    "KXHIGHTZBAA": None, "KXLOWTZBAA": None,
    "KXHIGHTZSPD": None, "KXLOWTZSPD": None,
    "KXLOWTLSSG": None,
}


class ParseDrift(Exception):
    """A payload no longer has the shape this code was written against."""


# ---------------------------------------------------------------- kalshi

_RULES_STATION = re.compile(r"recorded at (?P<city>[^()]+?)\s*\(CLI(?P<cli>[A-Z0-9]{3,4})\)")
_EVENT_DATE = re.compile(r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?:-|$)")


def station_from_rules(rules_primary: str | None) -> dict[str, Any] | None:
    """`... recorded at New York City (CLINYC) for Sep 11 ...` -> NYC / KNYC."""
    if not rules_primary:
        return None
    m = _RULES_STATION.search(rules_primary)
    if not m:
        return None
    cli = m.group("cli")
    return {"cli_id": cli, "icao": ICAO_BY_CLI.get(cli, "K" + cli), "city": m.group("city").strip()}


def kind_from_series(series_ticker: str) -> str | None:
    if series_ticker.startswith("KXHIGH"):
        return "high"
    if series_ticker.startswith("KXLOW"):
        return "low"
    return None


def target_date_from_event(event_ticker: str) -> date | None:
    """`KXHIGHNY-26SEP11` -> 2026-09-11 (the local calendar day the market is about)."""
    m = _EVENT_DATE.search(event_ticker)
    if not m or m.group("mon") not in MONTHS:
        return None
    try:
        return date(2000 + int(m.group("yy")), MONTHS[m.group("mon")], int(m.group("dd")))
    except ValueError:  # e.g. 26FEB29: not a calendar day, so not a ladder we can place
        return None


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bracket_bounds(strike_type: str | None, floor: float | None, cap: float | None) -> tuple[int | None, int | None]:
    """Inclusive integer Fahrenheit bounds of a YES outcome. None = open end.

    Kalshi temperature ladders: `less` with cap 79 is "78° or below",
    `between` 79..80 is "79° to 80°", `greater` with floor 86 is "87° or above".
    A `less` market carries its edge in cap_strike (floor is null) and a
    `greater` market carries it in floor_strike (cap is null).
    """
    if strike_type == "less":
        edge = cap if cap is not None else floor
        return (None, math.ceil(edge) - 1) if edge is not None else (None, None)
    if strike_type == "greater":
        edge = floor if floor is not None else cap
        return (math.floor(edge) + 1, None) if edge is not None else (None, None)
    if strike_type == "between" and floor is not None and cap is not None:
        return math.ceil(floor), math.floor(cap)
    return None, None


def market_row(m: dict[str, Any]) -> dict[str, Any]:
    required = ("ticker", "strike_type", "status")
    missing = [k for k in required if k not in m]
    if missing:
        raise ParseDrift(f"market missing {missing}: {list(m)[:12]}")
    floor, cap = _num(m.get("floor_strike")), _num(m.get("cap_strike"))
    lo, hi = bracket_bounds(m.get("strike_type"), floor, cap)
    bid, ask = _num(m.get("yes_bid_dollars")), _num(m.get("yes_ask_dollars"))
    last = _num(m.get("last_price_dollars"))
    mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
    implied = mid if mid is not None else last
    return {
        "ticker": m["ticker"],
        "label": m.get("yes_sub_title") or m.get("subtitle"),
        "strike_type": m.get("strike_type"),
        "floor_strike": floor,
        "cap_strike": cap,
        "low_f": lo,
        "high_f": hi,
        "yes_bid": bid,
        "yes_ask": ask,
        "yes_mid": mid,
        "last_price": last,
        "previous_price": _num(m.get("previous_price_dollars")),
        "implied_prob": implied,
        "volume": _num(m.get("volume_fp")),
        "volume_24h": _num(m.get("volume_24h_fp")),
        "open_interest": _num(m.get("open_interest_fp")),
        "liquidity_dollars": _num(m.get("liquidity_dollars")),
        "status": m.get("status"),
        "result": m.get("result") or None,
        "expiration_value": _num(m.get("expiration_value")),
        "open_time": m.get("open_time"),
        "close_time": m.get("close_time"),
        "expected_expiration_time": m.get("expected_expiration_time"),
        "updated_time": m.get("updated_time"),
    }


def _bracket_center(lo: int | None, hi: int | None) -> float | None:
    if lo is not None and hi is not None:
        return (lo + hi) / 2
    if lo is not None:
        return lo + 0.5   # open top: treat as just above the edge
    if hi is not None:
        return hi - 0.5
    return None


def market_summary(markets: list[dict[str, Any]]) -> dict[str, Any]:
    """The ladder as a distribution: normalised probability per bracket, the
    market's expected temperature, the favourite, and how much the raw YES
    mids sum to (above 1 means the ladder is priced rich)."""
    priced = [m for m in markets if m.get("implied_prob") is not None]
    out: dict[str, Any] = {
        "brackets": len(markets),
        "priced": len(priced),
        "prob_sum": None,
        "expected_temp_f": None,
        "favorite_ticker": None,
        "favorite_label": None,
        "favorite_prob": None,
        "total_volume": round(sum(m.get("volume") or 0 for m in markets), 2),
        "total_open_interest": round(sum(m.get("open_interest") or 0 for m in markets), 2),
    }
    if not priced:
        return out
    total = sum(m["implied_prob"] for m in priced)
    out["prob_sum"] = round(total, 4)
    if total <= 0:
        return out
    fav = max(priced, key=lambda m: m["implied_prob"])
    out.update({
        "favorite_ticker": fav["ticker"],
        "favorite_label": fav.get("label"),
        "favorite_prob": round(fav["implied_prob"] / total, 4),
    })
    centers = [(_bracket_center(m["low_f"], m["high_f"]), m["implied_prob"] / total) for m in priced]
    if all(c is not None for c, _ in centers):
        out["expected_temp_f"] = round(sum(c * p for c, p in centers), 2)
    for m in markets:
        m["normalized_prob"] = round(m["implied_prob"] / total, 4) if m.get("implied_prob") is not None else None
    return out


def bracket_for(temp_f: float | None, markets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The bracket an integer Fahrenheit reading settles into (NWS reports whole degrees)."""
    if temp_f is None:
        return None
    t = int(round(temp_f))
    for m in markets:
        lo, hi = m.get("low_f"), m.get("high_f")
        if lo is None and hi is None:
            continue  # no bounds known (unexpected strike type): cannot claim a match
        if (lo is None or t >= lo) and (hi is None or t <= hi):
            return m
    return None


def event_row(ev: dict[str, Any]) -> dict[str, Any]:
    """One row per Kalshi event: the city-day ladder with every strike nested."""
    for k in ("event_ticker", "series_ticker", "markets"):
        if k not in ev:
            raise ParseDrift(f"event missing '{k}': {list(ev)[:12]}")
    markets_raw = ev.get("markets") or []
    rules = next((m.get("rules_primary") for m in markets_raw if m.get("rules_primary")), None)
    markets = [market_row(m) for m in markets_raw]
    markets.sort(key=lambda m: (m["low_f"] if m["low_f"] is not None else -10_000))
    sources = ev.get("settlement_sources") or []
    target = target_date_from_event(ev["event_ticker"])
    # Once settled, Kalshi stamps every strike with the number the ladder settled on
    # (the official temperature). Lift it to the ladder so it survives NWS's roughly
    # one-week retention of climate reports.
    settled_values = [m["expiration_value"] for m in markets if m.get("expiration_value") is not None]
    yes = next((m for m in markets if m.get("result") == "yes"), None)
    return {
        "event_ticker": ev["event_ticker"],
        "series_ticker": ev["series_ticker"],
        "kind": kind_from_series(ev["series_ticker"]),
        "title": ev.get("title"),
        "target_date": target.isoformat() if target else None,
        "strike_date": ev.get("strike_date"),
        "station": station_from_rules(rules),
        "settlement_source": {"name": sources[0].get("name"), "url": sources[0].get("url")} if sources else None,
        "rules_primary": rules,
        "mutually_exclusive": ev.get("mutually_exclusive"),
        "markets": markets,
        "market_summary": market_summary(markets),
        "settlement": {"value_f": settled_values[0] if settled_values else None,
                       "yes_ticker": yes["ticker"] if yes else None,
                       "yes_label": yes.get("label") if yes else None} if (settled_values or yes) else None,
        "event_url": f"https://kalshi.com/markets/{ev['series_ticker'].lower()}/{ev['event_ticker'].lower()}",
    }


# ------------------------------------------------------------------- nws

def c_to_f(c: float | None) -> float | None:
    return None if c is None else round(c * 9 / 5 + 32, 1)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def local_day_window(day: date, tz: str) -> tuple[str, str]:
    """UTC ISO bounds of one local calendar day at the station."""
    z = ZoneInfo(tz)
    start = datetime(day.year, day.month, day.day, tzinfo=z)
    return _iso(start), _iso(start + timedelta(days=1))


def summarize_observations(payload: dict[str, Any], day: date, tz: str) -> dict[str, Any]:
    feats = payload.get("features")
    if feats is None:
        raise ParseDrift("observations payload has no 'features'")
    z = ZoneInfo(tz)
    readings: list[tuple[datetime, float]] = []
    for f in feats:
        p = f.get("properties") or {}
        tp = p.get("temperature") or {}
        ts, temp = p.get("timestamp"), tp.get("value")
        if not ts or temp is None:
            continue
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(z)
            temp_c = float(temp)
        except (ValueError, TypeError):
            continue
        if str(tp.get("unitCode", "")).lower().endswith("degf"):
            temp_c = (temp_c - 32) * 5 / 9  # NWS reports Celsius; be safe if that ever changes
        if t.date() != day:
            continue
        readings.append((t, temp_c))
    readings.sort()
    if not readings:
        return {"count": 0, "max_f": None, "max_at": None, "min_f": None, "min_at": None, "latest_f": None, "latest_at": None, "first_at": None}
    tmax = max(readings, key=lambda r: r[1])
    tmin = min(readings, key=lambda r: r[1])
    latest = readings[-1]
    return {
        "count": len(readings),
        "max_f": c_to_f(tmax[1]), "max_at": tmax[0].isoformat(timespec="minutes"),
        "min_f": c_to_f(tmin[1]), "min_at": tmin[0].isoformat(timespec="minutes"),
        "latest_f": c_to_f(latest[1]), "latest_at": latest[0].isoformat(timespec="minutes"),
        "first_at": readings[0][0].isoformat(timespec="minutes"),
    }


def _period_start(p: dict[str, Any], z: ZoneInfo) -> datetime | None:
    try:
        return datetime.fromisoformat(p["startTime"]).astimezone(z)
    except (KeyError, TypeError, ValueError):
        return None


def summarize_daily_forecast(payload: dict[str, Any], day: date, tz: str) -> dict[str, Any]:
    periods = (payload.get("properties") or {}).get("periods")
    if periods is None:
        raise ParseDrift("forecast payload has no properties.periods")
    z = ZoneInfo(tz)
    out: dict[str, Any] = {"high_f": None, "low_f": None, "day_period": None, "night_period": None, "generated_at": (payload.get("properties") or {}).get("generatedAt"), "updated_at": (payload.get("properties") or {}).get("updateTime")}
    for p in periods:
        start = _period_start(p, z)
        if start is None:
            continue
        temp = p.get("temperature")
        if p.get("temperatureUnit") == "C" and temp is not None:
            temp = c_to_f(temp)
        if p.get("isDaytime") and start.date() == day and out["high_f"] is None:
            out.update({"high_f": temp, "day_period": p.get("name"), "day_short_forecast": p.get("shortForecast")})
        elif not p.get("isDaytime") and start.date() == day and out["low_f"] is None and start.hour < 12:
            # The overnight period that starts at 00:00-06:00 local carries this calendar day's low.
            out.update({"low_f": temp, "night_period": p.get("name"), "night_short_forecast": p.get("shortForecast")})
        elif not p.get("isDaytime") and start.date() == day - timedelta(days=1) and start.hour >= 12 and out["low_f"] is None:
            # A "Thursday Night" period (starts 18:00 the day before) spans into this day's early hours; the
            # low of that night is the closest NWS daily figure to the calendar-day low.
            out.update({"low_f": temp, "night_period": p.get("name"), "night_short_forecast": p.get("shortForecast")})
    return out


def summarize_hourly_forecast(payload: dict[str, Any], day: date, tz: str, keep_periods: bool = False) -> dict[str, Any]:
    periods = (payload.get("properties") or {}).get("periods")
    if periods is None:
        raise ParseDrift("hourly forecast payload has no properties.periods")
    z = ZoneInfo(tz)
    rows = []
    for p in periods:
        start = _period_start(p, z)
        if start is None or start.date() != day:
            continue
        temp = p.get("temperature")
        if p.get("temperatureUnit") == "C" and temp is not None:
            temp = c_to_f(temp)
        rows.append({"start": start.isoformat(timespec="minutes"), "temp_f": temp, "short_forecast": p.get("shortForecast")})
    out: dict[str, Any] = {"hours": len(rows), "max_f": None, "max_at": None, "min_f": None, "min_at": None}
    temps = [r for r in rows if r["temp_f"] is not None]
    if temps:
        hi = max(temps, key=lambda r: r["temp_f"])
        lo = min(temps, key=lambda r: r["temp_f"])
        out.update({"max_f": hi["temp_f"], "max_at": hi["start"], "min_f": lo["temp_f"], "min_at": lo["start"]})
    if keep_periods:
        out["periods"] = rows
    return out


_CLI_SUMMARY = re.compile(r"CLIMATE SUMMARY FOR (?P<mon>[A-Z]+) (?P<dd>\d{1,2}),? (?P<yyyy>\d{4})")
_CLI_MAX = re.compile(r"^\s*MAXIMUM\s+(?P<v>-?\d+|MM)\s*(?P<t>\d{1,4} [AP]M)?", re.M)
_CLI_MIN = re.compile(r"^\s*MINIMUM\s+(?P<v>-?\d+|MM)\s*(?P<t>\d{1,4} [AP]M)?", re.M)
_CLI_AS_OF = re.compile(r"AS OF\s+(?P<asof>\d{1,4} [AP]M)")


def parse_cli(product: dict[str, Any]) -> dict[str, Any]:
    """The NWS daily climate report: the official max and min for the day it
    summarises. The morning issuance (after local midnight) is final for the
    previous day; an afternoon issuance is a partial "as of" reading."""
    text = product.get("productText")
    if not text:
        raise ParseDrift("CLI product has no productText")
    text = text.replace("\r", "")
    ms = _CLI_SUMMARY.search(text)
    summary_for = None
    if ms and ms.group("mon") in MONTH_NAMES:
        summary_for = date(int(ms.group("yyyy")), MONTH_NAMES[ms.group("mon")], int(ms.group("dd"))).isoformat()
    section = text[ms.end():] if ms else text
    mx, mn = _CLI_MAX.search(section), _CLI_MIN.search(section)
    # The temperature block is headed YESTERDAY in the final (post-midnight) issuance and
    # TODAY in the afternoon partial; anything before the first MAXIMUM line tells which.
    head = section[: mx.start()].upper() if mx else section[:600].upper()
    partial = "TODAY" in head or "AS OF" in head
    asof = _CLI_AS_OF.search(head)
    return {
        "summary_for": summary_for,
        "issued_at": product.get("issuanceTime"),
        "issuing_office": product.get("issuingOffice"),
        "max_f": int(mx.group("v")) if mx and mx.group("v") != "MM" else None,
        "max_at": mx.group("t") if mx else None,
        "min_f": int(mn.group("v")) if mn and mn.group("v") != "MM" else None,
        "min_at": mn.group("t") if mn else None,
        "final": not partial,
        "as_of": asof.group("asof") if asof else None,
        "product_id": product.get("id"),
    }
