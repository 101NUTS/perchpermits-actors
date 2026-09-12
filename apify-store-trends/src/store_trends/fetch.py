"""Polite HTTP for the public Apify Store endpoint.

One session, an identifying User-Agent, a fixed delay between requests, and
retries with backoff on 429/5xx. No API key is needed: `api.apify.com/v2/store`
is public. Nothing here interprets the payload.
"""

from __future__ import annotations

import time
from typing import Any

import requests

API = "https://api.apify.com/v2/store"
HEADERS = {
    "User-Agent": "apify-store-trends-actor/0.1 (store analytics tool)",
    "Accept": "application/json",
}
PAGE_LIMIT = 1000


class FetchError(Exception):
    pass


class Client:
    def __init__(self, delay_s: float = 0.2, timeout_s: float = 120.0, retries: int = 4, session: requests.Session | None = None):
        self.delay_s = delay_s
        self.timeout_s = timeout_s
        self.retries = retries
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self._last = 0.0
        self.requests_made = 0

    def get(self, params: dict[str, Any]) -> dict[str, Any]:
        """One store request. Returns the parsed JSON body (the `data` object is inside)."""
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self.delay_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.get(API, params=params, timeout=self.timeout_s)
                self._last = time.monotonic()
                self.requests_made += 1
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise FetchError(f"non-JSON body for {resp.url}") from exc
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = FetchError(f"HTTP {resp.status_code} for {resp.url}")
                else:
                    raise FetchError(f"HTTP {resp.status_code} for {resp.url}")
            except requests.RequestException as exc:
                self._last = time.monotonic()
                last_exc = FetchError(f"{exc.__class__.__name__} for {API}")
            time.sleep(3.0 * (attempt + 1))
        raise last_exc or FetchError(f"failed: {API}")

    # ------------------------------------------------------------ pages

    def store_page(
        self,
        *,
        offset: int = 0,
        limit: int = PAGE_LIMIT,
        search: str | None = None,
        category: str | None = None,
        pricing_model: str | None = None,
        sort_by: str | None = None,
    ) -> dict[str, Any]:
        """One page of the store listing. `search` uses the store's relevance
        ranking; passing `sortBy=popularity` together with `search` makes the API
        ignore relevance and return the global top actors, so the two are never
        combined here."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        if category:
            params["category"] = category
        if pricing_model:
            params["pricingModel"] = pricing_model
        if sort_by and not search:
            params["sortBy"] = sort_by
        return self.get(params)
