"""Polite HTTP for tennisexplorer.com.

One session, an identifying User-Agent, a fixed delay between requests, and
retries with backoff on 429/5xx. Nothing here parses HTML.
"""

from __future__ import annotations

import time
from datetime import date

import requests

BASE = "https://www.tennisexplorer.com"
HEADERS = {
    "User-Agent": "tennis-matches-actor/0.1 (sports data tool; contact: perchpermits@gmail.com)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.8",
}
TOUR_TYPES = {
    ("all", False): "all",
    ("atp", False): "atp-single",
    ("wta", False): "wta-single",
    ("atp", True): "atp-double",
    ("wta", True): "wta-double",
}


class FetchError(Exception):
    pass


class Client:
    def __init__(self, delay_s: float = 0.4, timeout_s: float = 30.0, retries: int = 3, session: requests.Session | None = None):
        self.delay_s = delay_s
        self.timeout_s = timeout_s
        self.retries = retries
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self._last = 0.0
        self.requests_made = 0

    def get(self, path: str, params: dict | None = None) -> str:
        url = path if path.startswith("http") else BASE + path
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
                    return resp.text
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = FetchError(f"HTTP {resp.status_code} for {resp.url}")
                else:
                    raise FetchError(f"HTTP {resp.status_code} for {resp.url}")
            except requests.RequestException as exc:
                self._last = time.monotonic()
                last_exc = FetchError(f"{exc.__class__.__name__} for {url}")
            time.sleep(1.5 * (attempt + 1))
        raise last_exc or FetchError(f"failed: {url}")

    # ------------------------------------------------------------ pages

    def list_page(self, kind: str, day: date, tour: str = "all", doubles: bool = False) -> str:
        """`kind` is "schedule" (/matches/) or "results" (/results/)."""
        path = "/matches/" if kind == "schedule" else "/results/"
        t = TOUR_TYPES.get((tour, doubles))
        if t is None:
            # "all" with doubles: the site has no such filter; type=all lists everything
            t = "all"
        return self.get(path, {"type": t, "year": day.year, "month": f"{day.month:02d}", "day": f"{day.day:02d}"})

    def detail_page(self, match_id: int) -> str:
        return self.get("/match-detail/", {"id": match_id})
