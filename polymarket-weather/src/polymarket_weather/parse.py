"""Pure parsing: a Polymarket daily-temperature event in, one row out; METAR reports in, the
day's running high and low at the settling station out. No network here.

Every station is read from the market's own rules text, never from a city table: the same
city settles on different stations on different venues (Polymarket NYC is LaGuardia, Kalshi
NYC is Central Park; Polymarket Chicago is O'Hare, Kalshi is Midway), and Polymarket has
used three kinds of source (NOAA station page, Weather Underground page, Hong Kong
Observatory). The only table is ICAO -> time zone, because the rules settle on the station's
own calendar day and neither API states the zone for foreign airports.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
SLUG = re.compile(r"^(highest|lowest)-temperature-in-(.+?)-on-([a-z]+)-(\d{1,2})-(\d{4})$")

# Stations seen in Polymarket's open daily-temperature markets on 2026-09-18 (51 cities).
TIMEZONES = {
    "EHAM": "Europe/Amsterdam", "LTAC": "Europe/Istanbul", "LTFM": "Europe/Istanbul", "KATL": "America/New_York",
    "KAUS": "America/Chicago", "ZBAA": "Asia/Shanghai", "SAEZ": "America/Argentina/Buenos_Aires", "RKPK": "Asia/Seoul",
    "FACT": "Africa/Johannesburg", "ZUUU": "Asia/Shanghai", "KORD": "America/Chicago", "ZUCK": "Asia/Shanghai",
    "KDAL": "America/Chicago", "KBKF": "America/Denver", "ZGGG": "Asia/Shanghai", "EFHK": "Europe/Helsinki",
    "KHOU": "America/Chicago", "OEJN": "Asia/Riyadh", "ZSJN": "Asia/Shanghai", "OPKC": "Asia/Karachi",
    "WMKK": "Asia/Kuala_Lumpur", "EGLC": "Europe/London", "KLAX": "America/Los_Angeles", "VILK": "Asia/Kolkata",
    "LEMD": "Europe/Madrid", "RPLL": "Asia/Manila", "MMMX": "America/Mexico_City", "KMIA": "America/New_York",
    "LIMC": "Europe/Rome", "UUWW": "Europe/Moscow", "EDDM": "Europe/Berlin", "KLGA": "America/New_York",
    "MPMG": "America/Panama", "LFPB": "Europe/Paris", "ZSQD": "Asia/Shanghai", "KSFO": "America/Los_Angeles",
    "SBGR": "America/Sao_Paulo", "KSEA": "America/Los_Angeles", "RKSI": "Asia/Seoul", "ZSPD": "Asia/Shanghai",
    "ZGSZ": "Asia/Shanghai", "WSSS": "Asia/Singapore", "RCSS": "Asia/Taipei", "RCTP": "Asia/Taipei",
    "LLBG": "Asia/Jerusalem", "RJTT": "Asia/Tokyo", "CYYZ": "America/Toronto", "EPWA": "Europe/Warsaw",
    "NZWN": "Pacific/Auckland", "ZHHH": "Asia/Shanghai", "ZHCC": "Asia/Shanghai",
}


class ParseDrift(Exception):
    """The payload no longer looks like what this parser was written against."""


# ------------------------------------------------------------------ event side
def parse_slug(slug: str) -> dict[str, Any] | None:
    m = SLUG.match((slug or "").strip().lower())
    if not m or m.group(3) not in MONTHS:
        return None
    kind = "high" if m.group(1) == "highest" else "low"
    return {"kind": kind, "city": m.group(2), "target_date": date(int(m.group(5)), MONTHS[m.group(3)], int(m.group(4)))}


def station_from_rules(description: str | None, resolution_source: str | None) -> dict[str, Any]:
    """Which station the rules settle on, and through what kind of page."""
    text = description or ""
    src = resolution_source or ""
    name = None
    m = re.search(r"recorded (?:by NOAA )?at the (.+?)(?: Station)? in degrees", text)
    if m:
        name = m.group(1).strip()
    if "Hong Kong Observatory" in text or "weather.gov.hk" in text + src:
        return {"source_type": "hong_kong_observatory", "icao": None, "name": "Hong Kong Observatory",
                "url": _first_url(text) or src or None}
    for blob in (src, text):
        m = re.search(r"weather\.gov/wrh/timeseries\?site=([A-Za-z0-9]{3,4})", blob)
        if m:
            return {"source_type": "noaa_timeseries", "icao": m.group(1).upper(), "name": name,
                    "url": f"https://www.weather.gov/wrh/timeseries?site={m.group(1).upper()}"}
    for blob in (src, text):
        m = re.search(r"wunderground\.com/history/daily/[a-z]{2}/[^/\s]+/([A-Za-z0-9]{4})", blob)
        if m:
            return {"source_type": "wunderground", "icao": m.group(1).upper(), "name": name,
                    "url": re.search(r"https://www\.wunderground\.com/\S+", blob).group(0).rstrip(".,)")}
    return {"source_type": "unknown", "icao": None, "name": name, "url": src or _first_url(text)}


def _first_url(text: str) -> str | None:
    m = re.search(r"https?://\S+", text or "")
    return m.group(0).rstrip(".,)") if m else None


def unit_from_rules(description: str | None) -> str | None:
    t = description or ""
    if "whole degrees Fahrenheit" in t or "degrees Fahrenheit" in t:
        return "F"
    if "whole degrees Celsius" in t or "degrees Celsius" in t:
        return "C"
    return None


def bracket_bounds(label: str | None) -> tuple[int | None, int | None]:
    """'71°F or below' -> (None, 71); '72-73°F' -> (72, 73); '16°C' -> (16, 16); '90°F or higher' -> (90, None)."""
    t = (label or "").replace("°", "").replace("º", "").strip()
    t = re.sub(r"\s*[FC]\b", "", t).strip()
    m = re.fullmatch(r"(-?\d+)\s+or\s+(?:below|lower|less)", t)
    if m:
        return None, int(m.group(1))
    m = re.fullmatch(r"(-?\d+)\s+or\s+(?:higher|above|more)", t)
    if m:
        return int(m.group(1)), None
    m = re.fullmatch(r"(-?\d+)\s*(?:-|to|–)\s*(-?\d+)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(-?\d+)", t)
    if m:
        return int(m.group(1)), int(m.group(1))
    raise ParseDrift(f"bracket label not understood: {label!r}")


def _num(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _jsonlist(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v or "[]")
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


def market_row(m: dict[str, Any]) -> dict[str, Any]:
    label = m.get("groupItemTitle") or m.get("question")
    lo, hi = bracket_bounds(label)
    prices = [_num(p) for p in _jsonlist(m.get("outcomePrices"))]
    bid, ask, last = _num(m.get("bestBid")), _num(m.get("bestAsk")), _num(m.get("lastTradePrice"))
    if bid is not None and ask is not None:
        implied = round((bid + ask) / 2, 4)
    elif prices and prices[0] is not None:
        implied = prices[0]
    else:
        implied = last
    closed = bool(m.get("closed"))
    result = None
    if closed and len(prices) == 2 and None not in prices:
        result = "yes" if prices[0] >= 0.99 else ("no" if prices[1] >= 0.99 else None)
    return {
        "slug": m.get("slug"), "label": label, "low": lo, "high": hi,
        "yes_bid": bid, "yes_ask": ask, "last_trade": last, "implied_prob": implied,
        "volume": _num(m.get("volumeNum") if m.get("volumeNum") is not None else m.get("volume")),
        "liquidity": _num(m.get("liquidityNum")), "closed": closed, "result": result,
        "clob_token_ids": _jsonlist(m.get("clobTokenIds")),
    }


def _center(lo: int | None, hi: int | None) -> float | None:
    if lo is not None and hi is not None:
        return (lo + hi) / 2
    return float(hi) if hi is not None else (float(lo) if lo is not None else None)


def market_summary(brackets: list[dict[str, Any]]) -> dict[str, Any]:
    priced = [b for b in brackets if b.get("implied_prob") is not None]
    if not priced:
        return {"favorite": None, "expected_temp": None, "prob_sum": None}
    fav = max(priced, key=lambda b: b["implied_prob"])
    total = sum(b["implied_prob"] for b in priced)
    exp = None
    if total > 0:
        exp = round(sum(b["implied_prob"] * (_center(b["low"], b["high"]) or 0) for b in priced) / total, 2)
    return {"favorite": fav["label"], "favorite_prob": fav["implied_prob"], "expected_temp": exp, "prob_sum": round(total, 4)}


def bracket_for(value: int | None, brackets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if value is None:
        return None
    for b in brackets:
        lo, hi = b.get("low"), b.get("high")
        if (lo is None or value >= lo) and (hi is None or value <= hi):
            return b
    return None


def event_row(ev: dict[str, Any]) -> dict[str, Any]:
    slug = ev.get("slug") or ""
    meta = parse_slug(slug)
    if not meta:
        raise ParseDrift(f"not a daily temperature event slug: {slug!r}")
    markets = ev.get("markets")
    if not isinstance(markets, list) or not markets:
        raise ParseDrift(f"{slug} has no markets")
    desc = ev.get("description") or markets[0].get("description") or ""
    station = station_from_rules(desc, ev.get("resolutionSource") or markets[0].get("resolutionSource"))
    brackets = sorted((market_row(m) for m in markets), key=lambda b: (b["low"] if b["low"] is not None else -10**6))
    winner = next((b for b in brackets if b["result"] == "yes"), None)
    closed = bool(ev.get("closed")) or all(b["closed"] for b in brackets)
    return {
        "slug": slug, "title": ev.get("title"), "kind": meta["kind"], "city": meta["city"],
        "target_date": meta["target_date"].isoformat(), "unit": unit_from_rules(desc),
        "station": {**station, "timezone": TIMEZONES.get(station.get("icao") or "")},
        "hourly_only": "Hourly Data" in desc,
        "rules": desc,
        "event_url": f"https://polymarket.com/event/{slug}",
        "status": "settled" if winner else ("closed" if closed else "open"),
        "volume": _num(ev.get("volume")), "liquidity": _num(ev.get("liquidity")),
        "brackets": brackets, "market_summary": market_summary(brackets),
        "settlement": {"winning_bracket": winner["label"], "low": winner["low"], "high": winner["high"]} if winner else None,
    }


# ------------------------------------------------------------------ observation side
def _round_half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def celsius_tenths(report: dict[str, Any]) -> float | None:
    """Temperature in tenths of a degree C from the METAR remarks T-group when present
    (US stations send it), else the whole-degree body value."""
    raw = report.get("rawOb") or ""
    m = re.search(r"\bT([01])(\d{3})([01])(\d{3})\b", raw)
    if m:
        v = int(m.group(2)) / 10
        return -v if m.group(1) == "1" else v
    return _num(report.get("temp"))


def reading(report: dict[str, Any], unit: str) -> int | None:
    """One report as the NOAA station page shows it, to whole degrees in the market's unit."""
    c = celsius_tenths(report)
    if c is None:
        return None
    if unit == "F":
        return _round_half_up(c * 9 / 5 + 32)
    body = _num(report.get("temp"))
    return _round_half_up(body if body is not None else c)


def _obs_time(report: dict[str, Any]) -> datetime | None:
    t = report.get("obsTime")
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t, tz=timezone.utc)
    s = report.get("reportTime") or report.get("receiptTime")
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def summarize_day(reports: list[dict[str, Any]], day: date, tz: str, unit: str, hourly_only: bool) -> dict[str, Any]:
    """Running max and min over the station's local calendar day, as the rules read it.

    The day counts as complete only when the reports reach past both ends of it: one from
    the next local day AND one from before local midnight. Without the second check a day
    whose first hours fell outside the fetched window looked final (2026-09-18 archive run:
    Asian lows on Sep 14 were computed from a day missing its night hours, 12 wrong calls)."""
    z = ZoneInfo(tz)
    rows = []
    next_day_seen = prev_day_seen = False
    for r in reports:
        t = _obs_time(r)
        if t is None:
            continue
        local = t.astimezone(z)
        if local.date() > day:
            next_day_seen = True
            continue
        if local.date() < day:
            prev_day_seen = True
            continue
        if hourly_only and (r.get("metarType") or "METAR") != "METAR":
            continue
        v = reading(r, unit)
        if v is not None:
            rows.append((local, v))
    rows.sort()
    if not rows:
        return {"count": 0, "unit": unit, "day_complete": next_day_seen and prev_day_seen}
    hi = max(rows, key=lambda x: x[1])
    lo = min(rows, key=lambda x: x[1])
    return {
        "count": len(rows), "unit": unit,
        "max": hi[1], "max_at": hi[0].isoformat(timespec="minutes"),
        "min": lo[1], "min_at": lo[0].isoformat(timespec="minutes"),
        "latest": rows[-1][1], "latest_at": rows[-1][0].isoformat(timespec="minutes"),
        "first_at": rows[0][0].isoformat(timespec="minutes"),
        "day_complete": next_day_seen and prev_day_seen,
        "covers_day_start": prev_day_seen,
        "hourly_only": hourly_only,
    }


def rule_outs(row: dict[str, Any], obs: dict[str, Any]) -> list[str]:
    """Brackets already impossible given what the station has recorded so far today."""
    running = obs.get("max") if row["kind"] == "high" else obs.get("min")
    if running is None:
        return []
    out = []
    for b in row["brackets"]:
        if row["kind"] == "high" and b["high"] is not None and b["high"] < running:
            out.append(b["label"])
        if row["kind"] == "low" and b["low"] is not None and b["low"] > running:
            out.append(b["label"])
    return out


def local_day_hours_left(day: date, tz: str, now: datetime | None = None) -> float:
    z = ZoneInfo(tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=z)
    now = now or datetime.now(timezone.utc)
    return round(max(0.0, (end - now).total_seconds() / 3600), 2)
