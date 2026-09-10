"""Adversarial offline tests: failures the live site will not produce on demand.

Fake HTTP sessions inject timeouts, 429s, 5xx, truncated bodies, garbage
bytes, and wrong encodings. Nothing here touches the network.
"""

import os
import random
import sys
import time
import unittest
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tennis_matches import fetch, parse, service  # noqa: E402
from tests.test_offline import FakeClient, fixture  # noqa: E402


class FakeResponse:
    def __init__(self, status, text="", url="https://www.tennisexplorer.com/x"):
        self.status_code, self.text, self.url = status, text, url


class ScriptedSession:
    """Returns scripted responses in order; a callable entry raises."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        item = self.script.pop(0) if self.script else FakeResponse(200, "<html></html>")
        if callable(item):
            raise item()
        return item


class FetchRetryTests(unittest.TestCase):
    def client(self, script, retries=2):
        return fetch.Client(delay_s=0, retries=retries, session=ScriptedSession(script))

    def test_retries_then_succeeds_on_429_and_503(self):
        c = self.client([FakeResponse(429), FakeResponse(503), FakeResponse(200, "ok")])
        t0 = time.time()
        self.assertEqual(c.get("/matches/"), "ok")
        self.assertEqual(len(c.session.calls), 3)
        self.assertGreater(time.time() - t0, 3.0)  # backoff actually waited

    def test_gives_up_after_retries(self):
        c = self.client([FakeResponse(503)] * 5)
        with self.assertRaises(fetch.FetchError):
            c.get("/matches/")
        self.assertEqual(len(c.session.calls), 3)

    def test_404_is_not_retried(self):
        c = self.client([FakeResponse(404)])
        with self.assertRaises(fetch.FetchError):
            c.get("/match-detail/", {"id": 1})
        self.assertEqual(len(c.session.calls), 1)

    def test_timeout_is_retried(self):
        c = self.client([requests.Timeout, requests.ConnectionError, FakeResponse(200, "late")])
        self.assertEqual(c.get("/x"), "late")

    def test_identifying_user_agent_and_timeout_are_sent(self):
        c = self.client([FakeResponse(200, "x")])
        c.get("/x")
        self.assertIn("tennis-matches-actor", c.session.headers["User-Agent"])
        self.assertIn("perchpermits@gmail.com", c.session.headers["User-Agent"])
        self.assertEqual(c.session.calls[0][2], 30.0)

    def test_delay_is_enforced_between_requests(self):
        c = fetch.Client(delay_s=0.3, retries=0, session=ScriptedSession([FakeResponse(200, "a"), FakeResponse(200, "b")]))
        t0 = time.time()
        c.get("/a")
        c.get("/b")
        self.assertGreaterEqual(time.time() - t0, 0.28)

    def test_tour_type_mapping(self):
        c = self.client([FakeResponse(200, "x")] * 5)
        c.list_page("schedule", date(2026, 9, 10), "wta", True)
        self.assertEqual(c.session.calls[-1][1]["type"], "wta-double")
        c.list_page("results", date(2026, 1, 5), "all", True)
        self.assertEqual(c.session.calls[-1][1]["type"], "all")
        self.assertEqual(c.session.calls[-1][1]["month"], "01")
        self.assertTrue(c.session.calls[-1][0].endswith("/results/"))


class ParserAbuseTests(unittest.TestCase):
    def test_truncated_list_page_still_parses_what_is_there(self):
        html = fixture("schedule_2026-09-10.html")
        cut = html[: len(html) // 2]
        rows = parse.parse_list_page(cut, "schedule", date(2026, 9, 10))
        self.assertGreater(len(rows), 0)
        for r in rows:
            self.assertIsNotNone(r["away"], "half-parsed pair leaked into output")

    def test_truncated_detail_page_degrades_not_crashes(self):
        html = fixture("detail_3317637.html")
        for frac in (0.9, 0.7, 0.5, 0.3):
            cut = html[: int(len(html) * frac)]
            try:
                d = parse.parse_detail_page(cut)
                self.assertIn("home", d)
            except parse.ParseDrift:
                pass  # acceptable: loud failure, never a wrong row

    def test_garbage_inputs(self):
        random.seed(7)
        for _ in range(50):
            junk = "".join(chr(random.randint(1, 0x2FFF)) for _ in range(random.randint(0, 3000)))
            with self.assertRaises(parse.ParseDrift):
                parse.parse_list_page(junk, "schedule", date(2026, 9, 10))
            with self.assertRaises(parse.ParseDrift):
                parse.parse_detail_page(junk)
        for empty in ("", " ", "<html></html>", "<html><body><div id='center'></div></body></html>", "\x00\x00", "<table class='result'></table>"):
            with self.assertRaises(parse.ParseDrift):
                parse.parse_detail_page(empty)
        # an empty result table is a valid "no matches today", not drift
        self.assertEqual(parse.parse_list_page("<div id='center'><table class='result'></table></div>", "schedule", date(2026, 9, 10)), [])

    def test_binary_and_wrong_encoding_bytes(self):
        raw = fixture("detail_3317719.html").encode("utf-8")
        mojibake = raw.decode("latin-1")  # what a wrong charset would give
        d = parse.parse_detail_page(mojibake)
        self.assertEqual(d["home"]["slug"], "jones-2fc9a")
        with self.assertRaises(parse.ParseDrift):
            parse.parse_detail_page(os.urandom(4000).decode("latin-1"))

    def test_diacritics_survive(self):
        html = fixture("schedule_2026-09-10.html").replace("Jones S.", "Švec Ł.")
        rows = parse.parse_list_page(html, "schedule", date(2026, 9, 10))
        m = next(r for r in rows if r["match_id"] == 3317719)
        self.assertEqual(m["home"]["name"], "Švec Ł.")

    def test_html_parser_fallback_matches_lxml(self):
        html = fixture("schedule_2026-09-10.html")
        a = parse.parse_list_page(html, "schedule", date(2026, 9, 10))
        orig = parse._soup
        try:
            from bs4 import BeautifulSoup
            parse._soup = lambda h: BeautifulSoup(h, "html.parser")
            b = parse.parse_list_page(html, "schedule", date(2026, 9, 10))
        finally:
            parse._soup = orig
        for x, y in zip(a, b):
            x.pop("fetched_at_utc", None); y.pop("fetched_at_utc", None)
        self.assertEqual(a, b)

    def test_huge_page_time_bound(self):
        html = fixture("schedule_2026-09-10.html")
        head, rest = html.split("<tbody>", 1)
        rows_html, tail = rest.split("</tbody>", 1)
        big = head + "<tbody>" + rows_html * 40 + "</tbody>" + tail  # ~2 MB, ~1600 matches in one table
        t0 = time.time()
        rows = parse.parse_list_page(big, "schedule", date(2026, 9, 10))
        self.assertGreater(len(rows), 500)
        self.assertLess(time.time() - t0, 10.0)

    def test_odds_edge_values(self):
        html = fixture("schedule_2026-09-10.html").replace('rowspan="2">1.01<', 'rowspan="2">-<').replace('rowspan="2">9.00<', 'rowspan="2">1,000.5<')
        rows = parse.parse_list_page(html, "schedule", date(2026, 9, 10))
        m = next(r for r in rows if r["match_id"] == 3317719)
        self.assertIsNone(m["odds"]["home"])
        self.assertIsNone(m["odds"]["away"])  # comma-formatted odds are not silently misread


class ServiceUnderFailureTests(unittest.TestCase):
    def test_mixed_failures_are_isolated(self):
        client = FakeClient(fixture("schedule_2026-09-10.html"), {3317719: fixture("detail_3317719.html")})
        rows = service.list_matches(client, kind="schedule", start=date(2026, 9, 10), max_matches=6)
        n = service.enrich_matches(client, rows)
        self.assertEqual(n, 1)
        statuses = [r["enrichment"]["status"] for r in rows]
        self.assertEqual(statuses.count("ok"), 1)
        self.assertEqual(statuses.count("fetch_failed"), 5)
        for r in rows:
            self.assertIn("match_id", r)  # plain fields intact on failed rows

    def test_drift_only_when_nothing_worked(self):
        bad = "<html><body><div id='center'><h1>x</h1></div></body></html>"
        client = FakeClient(fixture("schedule_2026-09-10.html"), {3317719: fixture("detail_3317719.html"), 3317745: bad, 3317751: bad, 3317705: bad})
        rows = service.list_matches(client, kind="schedule", start=date(2026, 9, 10), max_matches=4)
        n = service.enrich_matches(client, rows)  # first one succeeds, so later drift does not abort
        self.assertEqual(n, 1)
        self.assertEqual(sum(1 for r in rows if r["enrichment"]["status"] == "parse_failed"), 3)

    def test_slow_source_is_bounded_by_timeout_setting(self):
        class Slow(ScriptedSession):
            def get(self, url, params=None, timeout=None):
                time.sleep(0.05)
                return super().get(url, params, timeout)
        c = fetch.Client(delay_s=0, retries=0, timeout_s=0.01, session=Slow([FakeResponse(200, "x")]))
        self.assertEqual(c.get("/x"), "x")  # timeout is passed to the session; the fake ignores it
        self.assertEqual(c.session.calls[0][2], 0.01)

    def test_idempotent_listing(self):
        client = FakeClient(fixture("schedule_2026-09-10.html"), {})
        a = service.list_matches(client, kind="schedule", start=date(2026, 9, 10), max_matches=1000)
        b = service.list_matches(client, kind="schedule", start=date(2026, 9, 10), max_matches=1000)
        self.assertEqual([r["match_id"] for r in a], [r["match_id"] for r in b])

    def test_max_matches_zero_and_negative(self):
        client = FakeClient(fixture("schedule_2026-09-10.html"), {})
        self.assertEqual(service.list_matches(client, kind="schedule", start=date(2026, 9, 10), max_matches=0), [])
        self.assertEqual(service.list_matches(client, kind="schedule", start=date(2026, 9, 10), max_matches=-5), [])

    def test_days_is_clamped(self):
        client = FakeClient(fixture("schedule_2026-09-10.html"), {})
        service.list_matches(client, kind="schedule", start=date(2026, 9, 10), days=500, max_matches=10 ** 6)
        self.assertEqual(client.requests_made, 14)
        client.requests_made = 0
        service.list_matches(client, kind="schedule", start=date(2026, 9, 10), days=0, max_matches=10 ** 6)
        self.assertEqual(client.requests_made, 1)

    def test_filters_with_regex_metacharacters_and_unicode(self):
        client = FakeClient(fixture("schedule_2026-09-10.html"), {})
        for needle in ("(", "[", "*", ".*", "Š", "ł", "  ", "A" * 5000):
            service.list_matches(client, kind="schedule", start=date(2026, 9, 10), tournament=needle)
            service.list_matches(client, kind="schedule", start=date(2026, 9, 10), player=needle)

    def test_market_summary_with_hostile_odds(self):
        for books in (
            [{"home": 0.0, "away": 2.0, "home_opening": None, "away_opening": None}],
            [{"home": -1.5, "away": 2.0, "home_opening": None, "away_opening": None}],
            [{"home": 1e9, "away": 1.0001, "home_opening": 1e9, "away_opening": 1.0001}],
            [{"home": float("nan"), "away": 2.0, "home_opening": None, "away_opening": None}],
        ):
            m = service.market_summary(books)
            self.assertIn("fair_prob_home", m)
            if m["fair_prob_home"] is not None and m["fair_prob_home"] == m["fair_prob_home"]:
                self.assertGreaterEqual(m["fair_prob_home"], 0)


if __name__ == "__main__":
    unittest.main()
