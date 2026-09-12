"""Offline tests: no network. Run with `python -m unittest discover -s tests -t .` from the actor folder."""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.store_trends import parse, service  # noqa: E402
from src.store_trends.parse import ParseDrift  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def item(username="u", name="a", *, users30=50, users_total=500, total=100, failed=0, timed_out=0,
         rating=None, reviews=0, notice="NONE", last_run=None, title=None, pricing=None, categories=None) -> dict:
    """A synthetic store item shaped like the real payload."""
    stats = {
        "totalUsers": users_total, "totalUsers30Days": users30, "totalUsers7Days": users30 // 2, "totalUsers90Days": users30 * 2,
        "publicActorRunStats30Days": {"TOTAL": total, "FAILED": failed, "TIMED-OUT": timed_out, "SUCCEEDED": total - failed - timed_out},
        "actorReviewCount": reviews, "actorReviewRating": rating, "bookmarkCount": 3,
        "lastRunStartedAt": last_run if last_run is not None else NOW.isoformat().replace("+00:00", "Z"),
    }
    return {"id": f"id-{username}-{name}", "username": username, "name": name, "title": title or name.replace("-", " ").title(),
            "stats": stats, "notice": notice, "categories": categories or ["OTHER"], "currentPricingInfo": pricing,
            "isWhiteListedForAgenticPayments": True}


PPE = {"pricingModel": "PAY_PER_EVENT", "pricingPerEvent": {"actorChargeEvents": {
    "result": {"eventTitle": "Result", "isPrimaryEvent": True, "eventTieredPricingUsd": {"FREE": {"tieredEventPriceUsd": 0.0027}, "GOLD": {"tieredEventPriceUsd": 0.001}}},
    "start": {"eventTitle": "Start", "eventPriceUsd": 0.5}}}}
PPE_FLAT = {"pricingModel": "PAY_PER_EVENT", "pricingPerEvent": {"actorChargeEvents": {
    "row": {"eventTitle": "A very long event title that gets cut", "eventPriceUsd": 0.005}}}}


class FlattenTests(unittest.TestCase):
    def setUp(self):
        self.items = fixture("store_search_google_trends.json")["data"]["items"]
        self.rows = [parse.flatten(i, NOW) for i in self.items]

    def test_fixture_shape(self):
        self.assertGreaterEqual(len(self.items), 20)
        for it in self.items:
            self.assertIn("totalUsers", it["stats"])

    def test_known_leader(self):
        r = next(x for x in self.rows if x["actor"] == "apify/google-trends-scraper")
        self.assertEqual(r["title"], "Google Trends Scraper")
        self.assertEqual(r["target"], "google trends")
        self.assertEqual(r["url"], "https://apify.com/apify/google-trends-scraper")
        self.assertGreater(r["users_30d"], 100)
        self.assertGreaterEqual(r["users_total"], r["users_90d"])
        self.assertGreaterEqual(r["users_90d"], r["users_30d"])
        self.assertGreaterEqual(r["users_30d"], r["users_7d"])
        self.assertEqual(r["pricing_model"], "PAY_PER_EVENT")
        self.assertTrue(r["price"].startswith("$"))
        self.assertIsInstance(r["price_usd"], float)
        self.assertEqual(r["reviews"], 32)
        self.assertEqual(r["rating"], 3.68)
        self.assertEqual(r["notice"], "NONE")
        self.assertEqual(r["days_since_last_run"], 0)
        self.assertIsInstance(r["categories"], list)
        self.assertTrue(r["actor_id"])
        # 3.68 with 32 reviews is mediocre, not under the 3.6 bar
        self.assertNotIn("low_rated", parse.gap_signals(r))
        self.assertNotIn("unrated", parse.gap_signals(r))

    def test_fail_rate_and_idle(self):
        r = parse.flatten(item(total=200, failed=30, timed_out=30, last_run=(NOW - timedelta(days=20)).isoformat().replace("+00:00", "Z")), NOW)
        self.assertEqual(r["fail_rate_30d"], 0.3)
        self.assertEqual(r["days_since_last_run"], 20)
        r = parse.flatten(item(total=0, last_run=None), NOW)
        self.assertIsNone(r["fail_rate_30d"])
        self.assertEqual(r["days_since_last_run"], 0)
        r = parse.flatten({**item(), "stats": {**item()["stats"], "lastRunStartedAt": None}}, NOW)
        self.assertIsNone(r["days_since_last_run"])

    def test_rating_rounding_and_zero(self):
        self.assertEqual(parse.flatten(item(rating=4.702767, reviews=5), NOW)["rating"], 4.7)
        self.assertIsNone(parse.flatten(item(rating=0, reviews=0), NOW)["rating"])
        self.assertIsNone(parse.flatten(item(rating=None), NOW)["rating"])

    def test_drift_raises(self):
        with self.assertRaises(ParseDrift):
            parse.flatten({"username": "u", "name": "a", "stats": {}}, NOW)
        with self.assertRaises(ParseDrift):
            parse.flatten({"username": "u", "name": "a"}, NOW)
        with self.assertRaises(ParseDrift):
            parse.flatten({"stats": {"totalUsers": 1}}, NOW)


class PriceTests(unittest.TestCase):
    def test_pay_per_event_primary_free_tier(self):
        self.assertEqual(parse.price_label(item(pricing=PPE)), "$0.0027/Result")
        self.assertEqual(parse.primary_event_price(item(pricing=PPE)), 0.0027)

    def test_pay_per_event_flat_and_truncated_title(self):
        self.assertEqual(parse.price_label(item(pricing=PPE_FLAT)), "$0.005/A very long event title ")
        self.assertEqual(parse.primary_event_price(item(pricing=PPE_FLAT)), 0.005)

    def test_pay_per_event_without_events(self):
        self.assertEqual(parse.price_label(item(pricing={"pricingModel": "PAY_PER_EVENT"})), "PPE")
        self.assertIsNone(parse.primary_event_price(item(pricing={"pricingModel": "PAY_PER_EVENT"})))

    def test_per_item(self):
        p = {"pricingModel": "PRICE_PER_DATASET_ITEM", "pricePerUnitUsd": 0.005}
        self.assertEqual(parse.price_label(item(pricing=p)), "$0.005/item")
        self.assertEqual(parse.primary_event_price(item(pricing=p)), 0.005)

    def test_per_month(self):
        p = {"pricingModel": "FLAT_PRICE_PER_MONTH", "pricePerUnitUsd": 30}
        self.assertEqual(parse.price_label(item(pricing=p)), "$30/mo")
        self.assertEqual(parse.primary_event_price(item(pricing=p)), 30.0)
        self.assertEqual(parse.price_label(item(pricing={"pricingModel": "FLAT_PRICE_PER_MONTH"})), "$?/mo")
        self.assertIsNone(parse.primary_event_price(item(pricing={"pricingModel": "FLAT_PRICE_PER_MONTH"})))

    def test_free_and_unknown(self):
        self.assertEqual(parse.price_label(item(pricing={"pricingModel": "FREE"})), "FREE")
        self.assertIsNone(parse.primary_event_price(item(pricing={"pricingModel": "FREE"})))
        self.assertEqual(parse.price_label(item(pricing=None)), "?")
        self.assertEqual(parse.flatten(item(pricing=None), NOW)["pricing_model"], None)


class TargetRegexTests(unittest.TestCase):
    def test_specific_before_generic(self):
        self.assertEqual(parse.target_for("Google Trends Scraper", "google-trends-scraper"), "google trends")
        self.assertEqual(parse.target_for("Google Maps Reviews", "gmaps"), "google maps")
        self.assertEqual(parse.target_for("Instagram Scraper", "instagram-scraper"), "instagram")
        self.assertEqual(parse.target_for("Kalshi Weather Markets", "kalshi-weather"), "polymarket")
        self.assertEqual(parse.target_for("Tennis Matches + Odds", "tennis-matches"), "tennis")
        self.assertEqual(parse.target_for("Nashville Building Permits", "nashville-permits"), "government/permits")

    def test_hyphen_and_underscore_read_as_spaces(self):
        self.assertEqual(parse.target_for(None, "google_trends_fast"), "google trends")
        self.assertEqual(parse.target_for("", "hacker-news-top"), "hacker news")

    def test_unmatched(self):
        self.assertEqual(parse.target_for("BizBuySell Listings", "bizbuysell-scraper"), parse.UNMATCHED)

    def test_query_match(self):
        self.assertTrue(parse.query_match("google trends", "Google Trends Scraper", "google-trends-scraper"))
        self.assertTrue(parse.query_match("google trends", None, "google_realtime_trends_data"))
        self.assertTrue(parse.query_match("GOOGLE trends", "Trends for Google", "x"))
        self.assertFalse(parse.query_match("google trends", "Google Search Scraper", "google-search-scraper"))
        self.assertFalse(parse.query_match("bizbuysell", "Business listings", "biz-listings"))
        self.assertTrue(parse.query_match("bizbuysell", "BizBuySell Scraper", "bizbuysell-scraper"))
        self.assertIsNone(parse.query_match(None, "a", "b"))
        self.assertIsNone(parse.query_match("   ", "a", "b"))

    def test_hostility(self):
        self.assertEqual(parse.hostility("instagram"), "high")
        self.assertEqual(parse.hostility("google trends"), "low")
        self.assertEqual(parse.hostility("tennis"), "med")
        self.assertEqual(parse.hostility("nothing"), "med")

    def test_every_target_compiles_and_is_unique(self):
        names = [n for n, _ in parse.TARGETS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(parse.TARGET_RE), len(parse.TARGETS))


class GapSignalTests(unittest.TestCase):
    def sig(self, **kw):
        return parse.gap_signals(parse.flatten(item(**kw), NOW))

    def test_none_when_healthy(self):
        self.assertEqual(self.sig(users30=50, rating=4.8, reviews=10), [])

    def test_leader_failing_needs_users(self):
        self.assertIn("leader_failing", self.sig(users30=10, total=100, failed=30, rating=4.8, reviews=10))
        self.assertNotIn("leader_failing", self.sig(users30=9, total=100, failed=30, rating=4.8, reviews=10))
        self.assertNotIn("leader_failing", self.sig(users30=50, total=100, failed=29, rating=4.8, reviews=10))
        self.assertNotIn("leader_failing", self.sig(users30=50, total=0, rating=4.8, reviews=10))

    def test_unrated_needs_users(self):
        self.assertIn("unrated", self.sig(users30=20, reviews=2))
        self.assertNotIn("unrated", self.sig(users30=19, reviews=2))
        self.assertNotIn("unrated", self.sig(users30=20, reviews=3, rating=4.0))

    def test_maintenance_and_idle(self):
        self.assertIn("under_maintenance", self.sig(notice="UNDER_MAINTENANCE"))
        self.assertIn("idle_14d", self.sig(last_run=(NOW - timedelta(days=14)).isoformat().replace("+00:00", "Z")))
        self.assertNotIn("idle_14d", self.sig(last_run=(NOW - timedelta(days=13)).isoformat().replace("+00:00", "Z")))

    def test_low_rated_needs_reviews(self):
        self.assertIn("low_rated", self.sig(rating=3.5, reviews=3))
        self.assertNotIn("low_rated", self.sig(rating=3.5, reviews=2, users30=5))
        self.assertNotIn("low_rated", self.sig(rating=3.6, reviews=10))

    def test_all_at_once(self):
        s = self.sig(users30=30, total=100, failed=50, rating=2.0, reviews=5, notice="UNDER_MAINTENANCE",
                     last_run=(NOW - timedelta(days=40)).isoformat().replace("+00:00", "Z"))
        self.assertEqual(s, ["leader_failing", "under_maintenance", "idle_14d", "low_rated"])


class FakeClient:
    """Serves canned pages. `pages` maps (search, category, pricing_model, offset) to an item list."""

    def __init__(self, pages: dict, body_shape: str = "ok"):
        self.pages = pages
        self.body_shape = body_shape
        self.requests_made = 0
        self.calls: list[dict] = []

    def store_page(self, *, offset=0, limit=1000, search=None, category=None, pricing_model=None, sort_by=None):
        self.requests_made += 1
        self.calls.append({"offset": offset, "limit": limit, "search": search, "category": category, "pricing_model": pricing_model, "sort_by": sort_by})
        if self.body_shape == "no_items":
            return {"data": {"total": 0}}
        if self.body_shape == "no_data":
            return {"error": "gone"}
        items = self.pages.get((search, category, pricing_model, offset), [])
        return {"data": {"total": len(items), "count": len(items), "offset": offset, "limit": limit, "items": items}}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.items = fixture("store_search_google_trends.json")["data"]["items"]

    def test_search_single_page_and_relevance_order(self):
        client = FakeClient({("google trends", None, None, 0): self.items})
        got = service.search_items(client, "google trends", max_items=200)
        self.assertEqual(len(got), len(self.items))
        self.assertEqual(client.requests_made, 1)
        self.assertIsNone(client.calls[0]["sort_by"])

    def test_search_pages_until_short_page_and_dedupes(self):
        a = [item("u", f"a{i}") for i in range(150)]
        b = [item("u", "a0")] + [item("u", f"b{i}") for i in range(10)]
        client = FakeClient({("q", None, None, 0): a, ("q", None, None, 150): b})
        got = service.search_items(client, "q", max_items=300)
        self.assertEqual(len(got), 160)
        self.assertEqual(client.requests_made, 2)
        self.assertEqual([c["limit"] for c in client.calls], [150, 150])
        # a page shorter than the limit ends the search
        client = FakeClient({("q", None, None, 0): a[:149]})
        self.assertEqual(len(service.search_items(client, "q", max_items=300)), 149)
        self.assertEqual(client.requests_made, 1)
        with self.assertRaises(ValueError):
            service.search_items(client, "  ")

    def test_build_rows_filters_sorts_and_caps(self):
        rows = service.build_rows(self.items, now=NOW, min_users_30d=20, max_actors=5)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["actor"], "apify/google-trends-scraper")
        self.assertTrue(all(r["users_30d"] >= 20 for r in rows))
        self.assertEqual([r["users_30d"] for r in rows], sorted((r["users_30d"] for r in rows), reverse=True))
        for r in rows:
            self.assertEqual(r["row_type"], "actor")
            self.assertEqual(r["snapshot_at"], "2026-09-11T12:00:00Z")
            self.assertIsInstance(r["gap_signals"], list)
            self.assertEqual(r["delta"], service.empty_actor_delta())
            self.assertIsNone(r["query_match"])
        everything = service.build_rows(self.items, now=NOW, min_users_30d=0, max_actors=5000)
        self.assertEqual(len(everything), len(self.items))

    def test_build_rows_query_matches_rank_first(self):
        big_unrelated = item("apify", "google-search-scraper", users30=99999, title="Google Search Scraper")
        rows = service.build_rows(self.items + [big_unrelated], now=NOW, min_users_30d=0, max_actors=5000, query="google trends")
        self.assertEqual(rows[0]["actor"], "apify/google-trends-scraper")
        self.assertTrue(rows[0]["query_match"])
        flags = [r["query_match"] for r in rows]
        self.assertEqual(flags, sorted(flags, reverse=True))  # all True before all False
        unrelated = next(r for r in rows if r["actor"] == "apify/google-search-scraper")
        self.assertFalse(unrelated["query_match"])
        matched = [r for r in rows if r["query_match"]]
        self.assertEqual([r["users_30d"] for r in matched], sorted((r["users_30d"] for r in matched), reverse=True))
        # the query target row covers the matches only
        targets = service.aggregate_targets(rows, query="google trends")
        self.assertEqual(targets[0]["target_kind"], "query")
        self.assertEqual(targets[0]["actors"], len(matched))
        self.assertEqual(targets[0]["leader"], "apify/google-trends-scraper")
        # no matches at all: no query row
        rows2 = service.build_rows([big_unrelated], now=NOW, min_users_30d=0, query="google trends")
        self.assertEqual([t["target_kind"] for t in service.aggregate_targets(rows2, query="google trends")], ["site"])

    def test_build_rows_drift(self):
        with self.assertRaises(service.SourceDrift):
            service.build_rows([item(), {"username": "u", "name": "x", "stats": {"totalRuns": 1}}], now=NOW, min_users_30d=0)

    def test_payload_drift(self):
        with self.assertRaises(service.SourceDrift):
            service.search_items(FakeClient({}, "no_items"), "q")
        with self.assertRaises(service.SourceDrift):
            service.search_items(FakeClient({}, "no_data"), "q")
        with self.assertRaises(service.SourceDrift):
            service.sweep_items(FakeClient({}, "no_items"))

    def test_sweep_unions_slices_and_stops_on_low_usage(self):
        pages = {(None, None, None, 0): [item("u", "top", users_total=1000)] + [item("u", f"p{i}", users_total=1) for i in range(3)]}
        for c in parse.CATS:
            pages[(None, c, None, 0)] = [item("u", "top", users_total=1000), item("u", f"c-{c.lower()}", users_total=2)]
        for pm in parse.PRICING:
            pages[(None, None, pm, 0)] = [item("u", f"pm-{pm.lower()}", users_total=1)]
        client = FakeClient(pages)
        got = service.sweep_items(client)
        names = {i["name"] for i in got}
        self.assertIn("top", names)
        self.assertIn("c-sports", names)
        self.assertIn("pm-free", names)
        self.assertEqual(len(got), 1 + 3 + len(parse.CATS) + len(parse.PRICING))
        # slices whose top actor has 3+ users page on and stop after two empty pages (3 requests);
        # the pricing slices stop on the first page because their top user count is under 3
        self.assertEqual(client.requests_made, 3 * (1 + len(parse.CATS)) + len(parse.PRICING))
        self.assertTrue(all(c["sort_by"] == "popularity" for c in client.calls))

    def test_sweep_skips_a_slice_the_api_rejects(self):
        from src.store_trends.fetch import FetchError

        class Rejecting(FakeClient):
            def store_page(self, **kw):
                if kw.get("category") == "SPORTS":
                    raise FetchError("HTTP 400 for category=SPORTS")
                return super().store_page(**kw)

        pages = {(None, None, None, 0): [item("u", "top", users_total=1000)]}
        for c in parse.CATS:
            pages[(None, c, None, 0)] = [item("u", f"c-{c.lower()}", users_total=1)]
        for pm in parse.PRICING:
            pages[(None, None, pm, 0)] = [item("u", f"pm-{pm.lower()}", users_total=1)]
        log = []
        got = service.sweep_items(Rejecting(pages), progress=log.append)
        names = {i["name"] for i in got}
        self.assertNotIn("c-sports", names)
        self.assertIn("c-news", names)
        self.assertIn("pm-free", names)
        self.assertTrue(any("skipping category=SPORTS" in m for m in log), log)

    def test_sweep_base_slice_failure_is_fatal(self):
        from src.store_trends.fetch import FetchError

        class Down(FakeClient):
            def store_page(self, **kw):
                raise FetchError("HTTP 503")

        with self.assertRaises(FetchError):
            service.sweep_items(Down({}))

    def test_aggregate_targets(self):
        rows = service.build_rows(self.items, now=NOW, min_users_30d=0, max_actors=5000, query="google trends")
        targets = service.aggregate_targets(rows, query="google trends")
        self.assertEqual(targets[0]["target_kind"], "query")
        self.assertEqual(targets[0]["target"], "google trends")
        self.assertEqual(targets[0]["actors"], sum(1 for r in rows if r["query_match"]))
        self.assertGreaterEqual(targets[0]["actors"], 20)
        gt = next(t for t in targets if t["target_kind"] == "site" and t["target"] == "google trends")
        self.assertEqual(gt["hostility"], "low")
        self.assertEqual(gt["leader"], "apify/google-trends-scraper")
        self.assertEqual(gt["leader_url"], "https://apify.com/apify/google-trends-scraper")
        self.assertEqual(gt["leader_rating"], 3.68)
        self.assertEqual(gt["leader_reviews"], 32)
        self.assertEqual(gt["leader_signals"], [])
        unrated = [r for r in rows if r["target"] == "google trends" and "unrated" in r["gap_signals"]]
        self.assertTrue(unrated)
        self.assertGreater(gt["leader_share"], 0.4)
        self.assertLessEqual(gt["leader_share"], 1.0)
        self.assertEqual(gt["users_30d"], sum(r["users_30d"] for r in rows if r["target"] == "google trends"))
        self.assertEqual(gt["row_type"], "target")
        self.assertEqual(gt["snapshot_at"], rows[0]["snapshot_at"])
        self.assertEqual(gt["delta"], service.empty_target_delta())
        self.assertIsNotNone(gt["weighted_rating"])
        self.assertLess(gt["weighted_rating"], 4.0)  # the 3.68 leader carries most of the weight
        self.assertIn("A_poorly_served", gt["signals"])
        self.assertIn("F_low_hostility_demand", gt["signals"])
        self.assertNotIn("B_big_actor_low_rated", gt["signals"])
        self.assertNotIn("(unmatched)", [t["target"] for t in targets])
        sites = [t for t in targets if t["target_kind"] == "site"]
        self.assertEqual([t["users_30d"] for t in sites], sorted((t["users_30d"] for t in sites), reverse=True))
        self.assertEqual(service.aggregate_targets([], query="x"), [])

    def test_target_signals_rules(self):
        base = {"users_30d": 400, "weighted_rating": 4.5, "leader_fail_rate": 0.0, "best_rating": 4.9, "active_actors": 10, "hostility": "med", "leader_share": 0.5}
        self.assertEqual(service.target_signals(base, []), [])
        self.assertEqual(service.target_signals({**base, "weighted_rating": 3.9}, []), ["A_poorly_served"])
        self.assertEqual(service.target_signals({**base, "leader_fail_rate": 0.3}, []), ["A_poorly_served"])
        self.assertEqual(service.target_signals({**base, "best_rating": 4.1}, []), ["A_poorly_served"])
        self.assertEqual(service.target_signals({**base, "users_30d": 149, "weighted_rating": 1.0}, []), [])
        self.assertEqual(service.target_signals({**base, "active_actors": 6}, []), ["E_thin_market"])
        self.assertEqual(service.target_signals({**base, "hostility": "low"}, []), ["F_low_hostility_demand"])
        self.assertEqual(service.target_signals({**base, "leader_share": 0.8}, []), ["G_monopoly"])
        self.assertEqual(service.target_signals({**base, "users_30d": 299, "leader_share": 0.9}, []), [])
        big_bad = parse.flatten(item(users_total=500, rating=3.5, reviews=3), NOW)
        failing = parse.flatten(item(users30=25, total=100, failed=40), NOW)
        idle = parse.flatten(item(users30=20, notice="UNDER_MAINTENANCE"), NOW)
        self.assertEqual(service.target_signals(base, [big_bad]), ["B_big_actor_low_rated"])
        self.assertEqual(service.target_signals(base, [failing]), ["C_actor_failing_in_use"])
        self.assertEqual(service.target_signals(base, [idle]), ["D_incumbent_idle"])
        self.assertEqual(service.target_signals(base, [parse.flatten(item(users30=19, notice="UNDER_MAINTENANCE"), NOW)]), [])

    def test_run_search_end_to_end(self):
        client = FakeClient({("google trends", None, None, 0): self.items})
        rows, targets = service.run(client, mode="search", query="google trends", min_users_30d=20, max_actors=10, now=NOW)
        self.assertEqual(len(rows), 10)
        self.assertEqual(targets[0]["target_kind"], "query")
        rows2, targets2 = service.run(client, mode="search", query="google trends", include_targets=False, now=NOW)
        self.assertEqual(targets2, [])
        self.assertTrue(rows2)


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.items = fixture("store_search_google_trends.json")["data"]["items"]
        self.rows = service.build_rows(self.items, now=NOW, min_users_30d=0, max_actors=5000, query="google trends")
        self.targets = service.aggregate_targets(self.rows, query="google trends")

    def prior(self, **overrides):
        """A fake earlier snapshot: same actors, users halved, one actor missing, one extra that vanished."""
        old = service.build_rows(self.items, now=NOW - timedelta(days=30), min_users_30d=0, max_actors=5000, query="google trends")
        out = []
        for r in old:
            if r["actor"] == "data_xplorer/google-trends-fast-scraper":
                continue  # absent last month, so it is new now
            p = dict(r)
            p["users_30d"] = r["users_30d"] // 2
            p["rating"] = 4.0
            p["fail_rate_30d"] = 0.05
            out.append(p)
        out.append({**old[0], "actor": "gone/actor", "users_30d": 999, "target": "google trends"})
        return out

    def test_no_prior_leaves_nulls(self):
        service.apply_deltas(self.rows, self.targets, None)
        service.apply_deltas(self.rows, self.targets, [])
        for r in self.rows:
            self.assertEqual(r["delta"], service.empty_actor_delta())
        for t in self.targets:
            self.assertEqual(t["delta"], service.empty_target_delta())

    def test_actor_deltas(self):
        service.apply_deltas(self.rows, self.targets, self.prior())
        lead = next(r for r in self.rows if r["actor"] == "apify/google-trends-scraper")
        self.assertEqual(lead["delta"]["users_30d_prev"], lead["users_30d"] // 2)
        self.assertEqual(lead["delta"]["users_30d_change"], lead["users_30d"] - lead["users_30d"] // 2)
        self.assertEqual(lead["delta"]["rating_prev"], 4.0)
        self.assertEqual(lead["delta"]["fail_rate_prev"], 0.05)
        self.assertFalse(lead["delta"]["is_new"])
        new = next(r for r in self.rows if r["actor"] == "data_xplorer/google-trends-fast-scraper")
        self.assertEqual(new["delta"], {"users_30d_prev": None, "users_30d_change": None, "rating_prev": None, "fail_rate_prev": None, "is_new": True})

    def test_target_deltas(self):
        prior = self.prior()
        service.apply_deltas(self.rows, self.targets, prior)
        gt = next(t for t in self.targets if t["target_kind"] == "site" and t["target"] == "google trends")
        prev_sum = sum(p["users_30d"] for p in prior if p["target"] == "google trends")
        self.assertEqual(gt["delta"]["users_30d_prev"], prev_sum)
        self.assertEqual(gt["delta"]["users_30d_change"], gt["users_30d"] - prev_sum)
        self.assertEqual(gt["delta"]["leader_prev"], "gone/actor")
        self.assertFalse(gt["delta"]["is_new"])
        q = self.targets[0]
        self.assertEqual(q["target_kind"], "query")
        self.assertEqual(q["delta"]["actors_prev"], sum(1 for p in prior if p.get("query_match")))
        rows = [dict(r, target="brand-new-bucket") for r in self.rows[:2]]
        t = service.aggregate_targets(rows)
        service.apply_deltas(rows, t, prior)
        self.assertTrue(t[0]["delta"]["is_new"])

    def test_latest_snapshot_takes_newest_stamp_only(self):
        desc = [
            {"row_type": "target", "snapshot_at": "2026-09-01T00:00:00Z", "actor": None},
            {"row_type": "actor", "snapshot_at": "2026-09-01T00:00:00Z", "actor": "a/one"},
            {"row_type": "actor", "snapshot_at": "2026-09-01T00:00:00Z", "actor": "a/two"},
            {"row_type": "actor", "snapshot_at": "2026-08-01T00:00:00Z", "actor": "a/one"},
            {"row_type": "actor", "snapshot_at": "2026-08-01T00:00:00Z", "actor": "a/old"},
        ]
        got = service.latest_snapshot(desc)
        self.assertEqual([r["actor"] for r in got], ["a/one", "a/two"])
        self.assertEqual(service.latest_snapshot([]), [])


class ChargePlanTests(unittest.TestCase):
    def test_unlimited(self):
        self.assertEqual(service.charge_plan(None, 100, 5, 0.001, 0.01), (100, 5))
        self.assertEqual(service.charge_plan(Decimal("Infinity"), 100, 5, Decimal("0.001"), Decimal("0.01")), (100, 5))
        self.assertEqual(service.charge_plan(Decimal("1"), 100, 5, None, Decimal("0.01")), (100, 5))

    def test_everything_fits(self):
        self.assertEqual(service.charge_plan(Decimal("1.00"), 100, 5, Decimal("0.001"), Decimal("0.01")), (100, 5))
        self.assertEqual(service.charge_plan(Decimal("0.15"), 100, 5, Decimal("0.001"), Decimal("0.01")), (100, 5))

    def test_targets_first_then_actors_fill(self):
        # 5 targets = $0.05, leaves $0.05 = 50 actor rows
        self.assertEqual(service.charge_plan(Decimal("0.10"), 100, 5, Decimal("0.001"), Decimal("0.01")), (50, 5))
        # budget covers only 3 targets and nothing else
        self.assertEqual(service.charge_plan(Decimal("0.035"), 100, 5, Decimal("0.001"), Decimal("0.01")), (5, 3))
        # exact fit
        self.assertEqual(service.charge_plan(Decimal("0.0325"), 100, 3, Decimal("0.001"), Decimal("0.01")), (2, 3))

    def test_no_budget_left(self):
        self.assertEqual(service.charge_plan(Decimal("0"), 100, 5, Decimal("0.001"), Decimal("0.01")), (0, 0))
        self.assertEqual(service.charge_plan(Decimal("-0.5"), 100, 5, Decimal("0.001"), Decimal("0.01")), (0, 0))

    def test_zero_prices_are_free(self):
        self.assertEqual(service.charge_plan(Decimal("0.001"), 100, 5, Decimal("0"), Decimal("0")), (100, 5))

    def test_never_exceeds_budget(self):
        pa, pt = Decimal("0.001"), Decimal("0.01")
        for cents in range(0, 60):
            rem = Decimal(cents) / 1000
            a, t = service.charge_plan(rem, 200, 8, pa, pt)
            self.assertLessEqual(a * pa + t * pt, rem)
            self.assertLessEqual(a, 200)
            self.assertLessEqual(t, 8)


if __name__ == "__main__":
    unittest.main()
