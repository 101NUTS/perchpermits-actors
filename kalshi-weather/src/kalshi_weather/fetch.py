"""Polite HTTP for the two sources: Kalshi's public trade API and the NWS API.

Both are official JSON APIs. One session each, an identifying User-Agent (the
NWS API refuses requests without one), a fixed delay between requests, and
retries with backoff on 429/5xx. Nothing here interprets the payloads.
"""

from __future__ import annotations

import time
from typing import Any

import requests

CONTACT = "perchpermits@gmail.com"
USER_AGENT = f"kalshi-weather-actor/0.1 (prediction-market data tool; contact: {CONTACT})"

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
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
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise FetchError(f"non-JSON body from {resp.url}") from exc
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


class KalshiClient(Http):
    """Public, unauthenticated read endpoints of the Kalshi trade API.

    Unauthenticated reads are rate limited per IP (429 appeared at roughly
    seven requests a second in testing), so the default delay is 0.25 s.
    """

    BASE = KALSHI_BASE

    def series(self, category: str = "Climate and Weather") -> list[dict[str, Any]]:
        data = self.get_json("/series", {"category": category})
        return list(data.get("series") or [])

    def events(self, series_ticker: str, status: str = "open", with_markets: bool = True, limit: int = 200) -> list[dict[str, Any]]:
        """Every event of one series in the given status, markets nested, all pages."""
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"series_ticker": series_ticker, "status": status, "limit": limit}
            if with_markets:
                params["with_nested_markets"] = "true"
            if cursor:
                params["cursor"] = cursor
            data = self.get_json("/events", params)
            out.extend(data.get("events") or [])
            cursor = data.get("cursor") or None
            if not cursor or not data.get("events"):
                return out

    def event(self, event_ticker: str) -> dict[str, Any]:
        data = self.get_json(f"/events/{event_ticker}", {"with_nested_markets": "true"})
        return data.get("event") or data


class NwsClient(Http):
    """api.weather.gov: stations, observations, gridpoint forecasts, and the
    CLI (daily climate report) text products that carry the official max/min."""

    BASE = NWS_BASE
    HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/ld+json, application/json"}

    def station(self, icao: str) -> dict[str, Any]:
        return self.get_json(f"/stations/{icao}")

    def points(self, lat: float, lon: float) -> dict[str, Any]:
        return self.get_json(f"/points/{lat:.4f},{lon:.4f}")

    def forecast(self, url: str) -> dict[str, Any]:
        return self.get_json(url)

    def observations(self, icao: str, start_iso: str, end_iso: str, limit: int = 500) -> dict[str, Any]:
        return self.get_json(f"/stations/{icao}/observations", {"start": start_iso, "end": end_iso, "limit": limit})

    def latest_observation(self, icao: str) -> dict[str, Any]:
        return self.get_json(f"/stations/{icao}/observations/latest")

    def cli_products(self, location: str) -> list[dict[str, Any]]:
        data = self.get_json(f"/products/types/CLI/locations/{location}")
        return list(data.get("@graph") or [])

    def product(self, url_or_id: str) -> dict[str, Any]:
        path = url_or_id if url_or_id.startswith("http") else f"/products/{url_or_id}"
        return self.get_json(path)
