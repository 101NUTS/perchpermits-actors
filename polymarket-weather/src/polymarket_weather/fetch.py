"""Polite HTTP for the three sources: Polymarket's public Gamma API, the Aviation Weather
Center's METAR API (NOAA, every airport worldwide), and api.weather.gov for US forecasts.

All three are free, keyless, official JSON APIs. One session each, an identifying
User-Agent, a fixed delay between requests, retries with backoff on 429/5xx. Nothing here
interprets the payloads. Same shape as the Kalshi actor's fetch module, copied rather than
imported so each actor folder builds on its own.
"""

from __future__ import annotations

import time
from typing import Any

import requests

CONTACT = "perchpermits@gmail.com"
USER_AGENT = f"polymarket-weather-actor/0.1 (prediction-market data tool; contact: {CONTACT})"

GAMMA_BASE = "https://gamma-api.polymarket.com"
AWC_BASE = "https://aviationweather.gov/api/data"
NWS_BASE = "https://api.weather.gov"


class FetchError(Exception):
    pass


class Http:
    """Rate-limited JSON GET with retries. Subclasses set BASE and default headers."""

    BASE = ""
    HEADERS: dict[str, str] = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    def __init__(self, delay_s: float = 0.25, timeout_s: float = 30.0, retries: int = 3, session: requests.Session | None = None):
        self.delay_s = delay_s
        self.timeout_s = timeout_s
        self.retries = retries
        self.session = session or requests.Session()
        self.session.headers.update(self.HEADERS)
        self._last = 0.0
        self.requests_made = 0

    def get_json(self, path: str, params: dict | None = None) -> Any:
        url = path if path.startswith("http") else self.BASE + path
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self.delay_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout_s)
                self._last = time.monotonic()
                self.requests_made += 1
                if resp.status_code == 200:
                    if not resp.content.strip():
                        return []  # the METAR API answers an empty 200 when a station has no reports
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise FetchError(f"non-JSON body from {resp.url}") from exc
                if resp.status_code == 204:
                    return []
                if resp.status_code == 404:
                    raise FetchError(f"HTTP 404 for {resp.url}")
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = FetchError(f"HTTP {resp.status_code} for {resp.url}")
                else:
                    raise FetchError(f"HTTP {resp.status_code} for {resp.url}")
            except requests.RequestException as exc:
                self._last = time.monotonic()
                last_exc = FetchError(f"{exc.__class__.__name__} for {url}")
            time.sleep(1.5 * (attempt + 1))
        raise last_exc or FetchError(f"failed: {url}")


class GammaClient(Http):
    """Polymarket's public market-metadata API. Events carry their markets (one per bracket)."""

    BASE = GAMMA_BASE

    def event(self, slug: str) -> dict[str, Any]:
        return self.get_json(f"/events/slug/{slug}")

    def open_temperature_events(self, page: int = 100, max_pages: int = 10) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(max_pages):
            batch = self.get_json("/events", {"closed": "false", "limit": page, "offset": i * page, "tag_slug": "daily-temperature"})
            if not batch:
                break
            out.extend(batch)
            if len(batch) < page:
                break
        return out


class MetarClient(Http):
    """NOAA Aviation Weather Center METARs. One station per call: the API caps a response
    at 400 reports, which a multi-station request over several days would hit."""

    BASE = AWC_BASE

    def metars(self, icao: str, hours: int = 48) -> list[dict[str, Any]]:
        data = self.get_json("/metar", {"ids": icao, "hours": hours, "format": "json"})
        return list(data or [])


class NwsClient(Http):
    """api.weather.gov, for US stations only: station metadata (time zone) and forecasts."""

    BASE = NWS_BASE
    HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json"}

    def station(self, icao: str) -> dict[str, Any]:
        return self.get_json(f"/stations/{icao}")

    def points(self, lat: float, lon: float) -> dict[str, Any]:
        return self.get_json(f"/points/{lat:.4f},{lon:.4f}")

    def forecast(self, url: str) -> dict[str, Any]:
        return self.get_json(url)
