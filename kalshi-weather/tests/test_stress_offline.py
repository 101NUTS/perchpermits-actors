"""Adversarial offline tests: failures the live APIs will not produce on demand.

Scripted HTTP sessions inject timeouts, 429s, 5xx, 404s, non-JSON bodies, and
truncated pagination. Payload tests feed the parsers garbage prices, negative
and fractional strikes, missing fields, mojibake, DST days, and big ladders.
Nothing here touches the network.
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kalshi_weather import fetch, parse, service  # noqa: E402
from tests.test_offline import FakeKalshi, FakeNws, fixture, open_kalshi  # noqa: E402


class FakeResponse:
    def __init__(self, status, body="", url="https://api.example/x"):
        self.status_code, self._body, self.url = status, body, url

    def json(self):
        return json.loads(self._body)


class ScriptedSession:
    """Returns scripted responses in order; a callable entry raises."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        item = self.script.pop(0) if self.script else FakeResponse(200, "{}")
        if callable(item):
            raise item()
        return item


# ------------------------------------------------------------------ fetch

class FetchRetryTests(unittest.TestCase):
    def client(self, script, retries=2):
        return fetch.KalshiClient(delay_s=0, retries=retries, session=ScriptedSession(script))

    def test_retries_then_succeeds_on_429_and_503(self):
        c = self.client([FakeResponse(429), FakeResponse(503), FakeResponse(200, '{"ok": 1}')])
        t0 = time.time()
        self.assertEqual(c.get_json("/series"), {"ok": 1})
        self.assertEqual(len(c.session.calls), 3)
        self.assertGreater(time.time() - t0, 3.0)

    def test_gives_up_after_retries(self):
        c = self.client([FakeResponse(503)] * 5)
        with self.assertRaises(fetch.FetchError):
            c.get_json("/series")
        self.assertEqual(len(c.session.calls), 3)

    def test_404_is_not_retried(self):
        c = self.client([FakeResponse(404), FakeResponse(200, "{}")])
        with self.assertRaises(fetch.FetchError):
            c.get_json("/stations/KZZZ")
        self.assertEqual(len(c.session.calls), 1)

    def test_400_is_not_retried(self):
        c = self.client([FakeResponse(400), FakeResponse(200, "{}")])
        with self.assertRaises(fetch.FetchError):
            c.get_json("/events")
        self.assertEqual(len(c.session.calls), 1)

    def test_non_json_body_raises_fetch_error(self):
        c = self.client([FakeResponse(200, "<html>maintenance</html>")])
        with self.assertRaises(fetch.FetchError):
            c.get_json("/series")

    def test_connection_errors_are_retried(self):
        c = self.client([lambda: requests.ConnectionError("reset"), lambda: requests.Timeout("slow"), FakeResponse(200, "[]")])
        self.assertEqual(c.get_json("/x"), [])
        self.assertEqual(len(c.session.calls), 3)

    def test_delay_between_requests_is_enforced(self):
        c = fetch.KalshiClient(delay_s=0.2, retries=0, session=ScriptedSession([FakeResponse(200, "{}")] * 4))
        t0 = time.time()
        for _ in range(4):
            c.get_json("/x")
        self.assertGreaterEqual(time.time() - t0, 0.55)

    def test_events_pagination_follows_cursor_and_stops(self):
        pages = [
            FakeResponse(200, json.dumps({"events": [{"event_ticker": "A"}], "cursor": "c1"})),
            FakeResponse(200, json.dumps({"events": [{"event_ticker": "B"}], "cursor": "c2"})),
            FakeResponse(200, json.dumps({"events": [], "cursor": "c3"})),
        ]
        c = self.client(pages)
        evs = c.events("KXHIGHNY", status="settled")
        self.assertEqual([e["event_ticker"] for e in evs], ["A", "B"])
        self.assertEqual(c.session.calls[1][1]["cursor"], "c1")
        self.assertEqual(len(c.session.calls), 3)

    def test_events_pagination_stops_without_cursor(self):
        c = self.client([FakeResponse(200, json.dumps({"events": [{"event_ticker": "A"}]}))])
        self.assertEqual(len(c.events("KXHIGHNY")), 1)
        self.assertEqual(len(c.session.calls), 1)

    def test_nws_client_sends_geojson_accept_and_user_agent(self):
        n = fetch.NwsClient(delay_s=0, session=ScriptedSession([FakeResponse(200, "{}")]))
        n.station("KNYC")
        self.assertIn("geo+json", n.session.headers["Accept"])
        self.assertIn("contact:", n.session.headers["User-Agent"])


# ------------------------------------------------------------------ parse

class MarketParseTests(unittest.TestCase):
    def base(self, **over):
        m = {"ticker": "T", "strike_type": "between", "status": "active", "floor_strike": 79, "cap_strike": 80,
             "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.34", "last_price_dollars": "0.31", "volume_fp": "10"}
        m.update(over)
        return m

    def test_garbage_prices_become_none(self):
        r = parse.market_row(self.base(yes_bid_dollars="abc", yes_ask_dollars=None, last_price_dollars="", volume_fp="n/a"))
        self.assertIsNone(r["yes_bid"])
        self.assertIsNone(r["yes_mid"])
        self.assertIsNone(r["implied_prob"])
        self.assertIsNone(r["volume"])

    def test_one_sided_quote_falls_back_to_last(self):
        r = parse.market_row(self.base(yes_bid_dollars="0.30", yes_ask_dollars=None))
        self.assertIsNone(r["yes_mid"])
        self.assertEqual(r["implied_prob"], 0.31)

    def test_negative_and_fractional_strikes(self):
        self.assertEqual(parse.bracket_bounds("less", None, -5), (None, -6))
        self.assertEqual(parse.bracket_bounds("between", -5, -4), (-5, -4))
        self.assertEqual(parse.bracket_bounds("greater", -1, None), (0, None))
        self.assertEqual(parse.bracket_bounds("less", None, 79.5), (None, 79))
        self.assertEqual(parse.bracket_bounds("greater", 86.5, None), (87, None))
        self.assertEqual(parse.bracket_bounds("between", 0, 0), (0, 0))

    def test_bracket_for_negative_temps(self):
        ms = [parse.market_row(self.base(ticker="L", strike_type="less", floor_strike=None, cap_strike=-5)),
              parse.market_row(self.base(ticker="B", floor_strike=-5, cap_strike=-4)),
              parse.market_row(self.base(ticker="G", strike_type="greater", floor_strike=-4, cap_strike=None))]
        self.assertEqual(parse.bracket_for(-7, ms)["ticker"], "L")
        self.assertEqual(parse.bracket_for(-4.4, ms)["ticker"], "B")
        self.assertEqual(parse.bracket_for(-3, ms)["ticker"], "G")

    def test_bracket_for_gap_in_ladder_returns_none(self):
        ms = [parse.market_row(self.base(ticker="A", floor_strike=70, cap_strike=71)),
              parse.market_row(self.base(ticker="B", floor_strike=74, cap_strike=75))]
        self.assertIsNone(parse.bracket_for(72, ms))
        self.assertEqual(parse.bracket_for(75, ms)["ticker"], "B")

    def test_market_summary_with_no_prices_and_zero_prices(self):
        none = [parse.market_row(self.base(yes_bid_dollars=None, yes_ask_dollars=None, last_price_dollars=None))]
        s = parse.market_summary(none)
        self.assertEqual(s["priced"], 0)
        self.assertIsNone(s["favorite_ticker"])
        zero = [parse.market_row(self.base(yes_bid_dollars="0", yes_ask_dollars="0", last_price_dollars="0"))]
        s = parse.market_summary(zero)
        self.assertEqual(s["prob_sum"], 0.0)
        self.assertIsNone(s["favorite_ticker"])
        self.assertIsNone(s["expected_temp_f"])

    def test_market_summary_single_open_ended_strike(self):
        ms = [parse.market_row(self.base(ticker="G", strike_type="greater", floor_strike=90, cap_strike=None, yes_bid_dollars="0.5", yes_ask_dollars="0.5"))]
        s = parse.market_summary(ms)
        self.assertEqual(s["favorite_prob"], 1.0)
        self.assertEqual(s["expected_temp_f"], 91.5)

    def test_unknown_strike_type_keeps_row_but_no_bounds(self):
        r = parse.market_row(self.base(strike_type="scalar", floor_strike=None, cap_strike=None))
        self.assertEqual((r["low_f"], r["high_f"]), (None, None))
        self.assertIsNone(parse.bracket_for(80, [r]))

    def test_empty_result_string_becomes_none(self):
        self.assertIsNone(parse.market_row(self.base(result=""))["result"])
        self.assertEqual(parse.market_row(self.base(result="yes"))["result"], "yes")

    def test_label_falls_back_to_subtitle_and_keeps_mojibake(self):
        r = parse.market_row(self.base(subtitle="79Â° to 80Â°"))
        self.assertEqual(r["label"], "79Â° to 80Â°")
        self.assertIsNone(parse.market_row(self.base())["label"])


class EventParseTests(unittest.TestCase):
    def test_station_rule_variants(self):
        cases = {
            "the maximum temperature recorded at Dallas-Fort Worth (CLIDFW) for Sep 11": ("DFW", "Dallas-Fort Worth"),
            "recorded at St. Louis (CLISTL) for": ("STL", "St. Louis"),
            "recorded at   Washington DC (CLIDCA)  for": ("DCA", "Washington DC"),
            "recorded at New York City (CLINYC) for Sep 11, 2026, is greater than 86Â° fahrenheit": ("NYC", "New York City"),
        }
        for text, (cli, city) in cases.items():
            st = parse.station_from_rules(text)
            self.assertEqual((st["cli_id"], st["city"]), (cli, city), text)
        self.assertIsNone(parse.station_from_rules("recorded at (CLI) for"))
        self.assertIsNone(parse.station_from_rules("recorded at London (EGLL) for"))
        self.assertIsNone(parse.station_from_rules(""))

    def test_target_date_invalid_calendar_day_is_none(self):
        self.assertIsNone(parse.target_date_from_event("KXLOWTNYC-26FEB29"))
        self.assertIsNone(parse.target_date_from_event("KXHIGHNY-26SEP31"))
        self.assertIsNone(parse.target_date_from_event("KXHIGHNY-26XYZ11"))
        self.assertEqual(parse.target_date_from_event("KXHIGHNY-28FEB29"), date(2028, 2, 29))

    def test_event_with_empty_markets(self):
        ev = {"event_ticker": "KXHIGHNY-26SEP11", "series_ticker": "KXHIGHNY", "markets": []}
        row = parse.event_row(ev)
        self.assertEqual(row["markets"], [])
        self.assertIsNone(row["station"])
        self.assertEqual(row["market_summary"]["brackets"], 0)

    def test_event_missing_fields_raises_drift(self):
        with self.assertRaises(parse.ParseDrift):
            parse.event_row({"series_ticker": "KXHIGHNY", "markets": []})
        with self.assertRaises(parse.ParseDrift):
            parse.event_row({"event_ticker": "X", "series_ticker": "KXHIGHNY", "markets": [{"ticker": "T"}]})

    def test_big_ladder_sorted_and_fast(self):
        ev = fixture("kalshi_events_KXHIGHNY_open.json")["events"][0]
        proto = ev["markets"][1]
        ev = dict(ev)
        ev["markets"] = [{**proto, "ticker": f"T{i}", "floor_strike": i, "cap_strike": i + 1} for i in range(5000, 0, -1)]
        t0 = time.time()
        row = parse.event_row(ev)
        self.assertLess(time.time() - t0, 2.0)
        lows = [m["low_f"] for m in row["markets"]]
        self.assertEqual(lows, sorted(lows))
        self.assertEqual(row["market_summary"]["brackets"], 5000)

    def test_settled_fixture_every_ladder_has_exactly_one_yes(self):
        for ev in fixture("kalshi_events_KXHIGHNY_settled.json")["events"]:
            row = parse.event_row(ev)
            self.assertEqual(sum(1 for m in row["markets"] if m["result"] == "yes"), 1, ev["event_ticker"])


class NwsParseTests(unittest.TestCase):
    def obs(self, readings):
        return {"features": [{"properties": {"timestamp": ts, "temperature": {"value": v, "unitCode": unit}}} for ts, v, unit in readings]}

    def test_observation_edge_cases(self):
        empty = parse.summarize_observations({"features": []}, date(2026, 9, 10), "America/New_York")
        self.assertEqual(empty["count"], 0)
        bad = self.obs([("2026-09-10T15:00:00+00:00", None, "wmoUnit:degC"), ("2026-09-10T16:00:00+00:00", 30.0, "wmoUnit:degC")])
        s = parse.summarize_observations(bad, date(2026, 9, 10), "America/New_York")
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["max_f"], 86.0)
        missing_props = {"features": [{"id": "x"}, {"properties": {}}]}
        self.assertEqual(parse.summarize_observations(missing_props, date(2026, 9, 10), "UTC")["count"], 0)

    def test_observations_in_fahrenheit_are_not_converted_twice(self):
        s = parse.summarize_observations(self.obs([("2026-09-10T15:00:00+00:00", 86.0, "wmoUnit:degF")]), date(2026, 9, 10), "America/New_York")
        self.assertEqual(s["max_f"], 86.0)

    def test_observation_day_boundary_in_station_timezone(self):
        # 03:30Z on Sep 11 is 23:30 on Sep 10 in New York
        s = parse.summarize_observations(self.obs([("2026-09-11T03:30:00+00:00", 20.0, "wmoUnit:degC"), ("2026-09-11T04:30:00+00:00", 10.0, "wmoUnit:degC")]), date(2026, 9, 10), "America/New_York")
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["max_f"], 68.0)

    def test_dst_fall_back_day_has_25_hours_and_both_130_readings(self):
        # 2026-11-01: clocks go back at 02:00 in New York; 05:30Z and 06:30Z are both 01:30 local
        start, end = parse.local_day_window(date(2026, 11, 1), "America/New_York")
        self.assertEqual((start, end), ("2026-11-01T04:00:00Z", "2026-11-02T05:00:00Z"))
        s = parse.summarize_observations(self.obs([("2026-11-01T05:30:00+00:00", 5.0, "wmoUnit:degC"), ("2026-11-01T06:30:00+00:00", 4.0, "wmoUnit:degC")]), date(2026, 11, 1), "America/New_York")
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["min_f"], 39.2)

    def test_dst_spring_forward_day_has_23_hours(self):
        start, end = parse.local_day_window(date(2026, 3, 8), "America/New_York")
        self.assertEqual((start, end), ("2026-03-08T05:00:00Z", "2026-03-09T04:00:00Z"))

    def test_forecast_period_without_start_time_is_skipped(self):
        payload = {"properties": {"periods": [{"name": "odd", "temperature": 80, "isDaytime": True},
                                               {"name": "Friday", "startTime": "2026-09-11T06:00:00-04:00", "temperature": 81, "temperatureUnit": "F", "isDaytime": True}]}}
        d = parse.summarize_daily_forecast(payload, date(2026, 9, 11), "America/New_York")
        self.assertEqual(d["high_f"], 81)

    def test_forecast_in_celsius_is_converted(self):
        payload = {"properties": {"periods": [{"name": "Friday", "startTime": "2026-09-11T06:00:00-04:00", "temperature": 27, "temperatureUnit": "C", "isDaytime": True}]}}
        self.assertEqual(parse.summarize_daily_forecast(payload, date(2026, 9, 11), "America/New_York")["high_f"], 80.6)
        hourly = {"properties": {"periods": [{"startTime": "2026-09-11T06:00:00-04:00", "temperature": 27, "temperatureUnit": "C"},
                                              {"startTime": "2026-09-11T07:00:00-04:00", "temperature": None, "temperatureUnit": "C"}]}}
        h = parse.summarize_hourly_forecast(hourly, date(2026, 9, 11), "America/New_York")
        self.assertEqual((h["hours"], h["max_f"]), (2, 80.6))

    def test_forecast_missing_periods_is_drift(self):
        with self.assertRaises(parse.ParseDrift):
            parse.summarize_daily_forecast({"properties": {}}, date(2026, 9, 11), "UTC")
        with self.assertRaises(parse.ParseDrift):
            parse.summarize_hourly_forecast({}, date(2026, 9, 11), "UTC")

    def test_cli_missing_values_negative_temps_and_crlf(self):
        prod = fixture("nws_cli_product_NYC_latest.json")
        text = prod["productText"].replace("MAXIMUM         84    325 PM", "MAXIMUM         MM").replace("MINIMUM         69    452 AM", "MINIMUM         -5    452 AM")
        rep = parse.parse_cli({**prod, "productText": text.replace("\n", "\r\n")})
        self.assertIsNone(rep["max_f"])
        self.assertEqual(rep["min_f"], -5)
        self.assertEqual(rep["summary_for"], "2026-09-10")
        self.assertTrue(rep["final"])

    def test_cli_without_summary_header(self):
        rep = parse.parse_cli({"productText": "GARBAGE\n MAXIMUM 70 1 PM\n MINIMUM 50 5 AM\n", "issuanceTime": "x"})
        self.assertIsNone(rep["summary_for"])
        self.assertEqual((rep["max_f"], rep["min_f"]), (70, 50))

    def test_cli_afternoon_partial_with_as_of(self):
        prod = fixture("nws_cli_product_NYC_latest.json")
        text = prod["productText"].replace("CLIMATE SUMMARY FOR SEPTEMBER 10 2026", "CLIMATE SUMMARY FOR SEPTEMBER 11 2026").replace("YESTERDAY", "TODAY\n  VALID AS OF 400 PM")
        rep = parse.parse_cli({**prod, "productText": text})
        self.assertFalse(rep["final"])
        self.assertEqual(rep["as_of"], "400 PM")


# ---------------------------------------------------------------- service

class ServiceFailureTests(unittest.TestCase):
    def test_series_fetch_error_is_drift(self):
        class Dead(FakeKalshi):
            def series(self, category="Climate and Weather"):
                raise fetch.FetchError("HTTP 503")
        with self.assertRaises(service.SourceDrift):
            service.list_events(Dead({}))

    def test_empty_series_list_is_drift(self):
        class Empty(FakeKalshi):
            def series(self, category="Climate and Weather"):
                return []
        with self.assertRaises(service.SourceDrift):
            service.list_events(Empty({}))

    def test_series_without_temperature_tickers_is_drift(self):
        class Renamed(FakeKalshi):
            def series(self, category="Climate and Weather"):
                return [{"ticker": "KXTEMPMAXNY", "frequency": "daily"}]
        with self.assertRaises(service.SourceDrift):
            service.list_events(Renamed({}))

    def test_per_series_fetch_errors_are_skipped_not_fatal(self):
        class Flaky(FakeKalshi):
            def events(self, series_ticker, status="open", with_markets=True, limit=200):
                if series_ticker != "KXHIGHNY":
                    raise fetch.FetchError("HTTP 500")
                return super().events(series_ticker, status, with_markets, limit)
        rows = service.list_events(Flaky({("KXHIGHNY", "open"): fixture("kalshi_events_KXHIGHNY_open.json")}), stations=["NYC"])
        self.assertEqual([r["event_ticker"] for r in rows], ["KXHIGHNY-26SEP11"])

    def test_three_malformed_events_with_nothing_listed_is_drift(self):
        bad = {"events": [{"event_ticker": f"KXHIGHNY-26SEP1{i}", "markets": [{"ticker": "T"}]} for i in range(3)]}
        with self.assertRaises(service.SourceDrift):
            service.list_events(FakeKalshi({("KXHIGHNY", "open"): bad}), stations=["NYC"])

    def test_malformed_events_after_good_ones_are_skipped(self):
        good = fixture("kalshi_events_KXHIGHNY_open.json")["events"]
        bad = [{"event_ticker": f"KXHIGHNY-26SEP1{i}", "markets": [{"ticker": "T"}]} for i in range(5)]
        rows = service.list_events(FakeKalshi({("KXHIGHNY", "open"): {"events": good + bad}}), stations=["NYC"])
        self.assertEqual(len(rows), 1)

    def test_enrich_isolates_station_404_and_counts_none(self):
        rows = service.list_events(open_kalshi(), stations=["NYC"])
        for r in rows:
            r["station"] = {"cli_id": "ZZZ", "icao": "KZZZ", "city": "Nowhere"}
        n = service.enrich_events(FakeNws(), rows)
        self.assertEqual(n, 0)
        self.assertTrue(all(r["enrichment"]["status"] == "station_lookup_failed" for r in rows))

    def test_enrich_both_sources_down_is_fetch_failed(self):
        class Down(FakeNws):
            def forecast(self, url):
                raise fetch.FetchError("HTTP 503")
        rows = service.list_events(open_kalshi(), kind="high", stations=["NYC"])
        n = service.enrich_events(Down(fail_obs=True), rows)
        self.assertEqual(n, 0)
        self.assertEqual(rows[0]["enrichment"]["status"], "fetch_failed")
        self.assertIn("observations", rows[0]["enrichment"]["error"])

    def test_cli_listing_failure_leaves_report_none_and_row_ok(self):
        class NoCli(FakeNws):
            def cli_products(self, location):
                raise fetch.FetchError("HTTP 500")
        rows = service.list_events(open_kalshi(), kind="high", status="settled", stations=["NYC"], date_from=date(2026, 9, 9), date_to=date(2026, 9, 9))
        n = service.enrich_events(NoCli(), rows)
        self.assertEqual(n, 1)
        self.assertIsNone(rows[0]["enrichment"]["climate_report"])
        self.assertEqual(rows[0]["enrichment"]["status"], "ok")

    def test_observation_shape_drift_three_times_aborts(self):
        class Shape(FakeNws):
            def observations(self, icao, start, end, limit=500):
                return {"type": "FeatureCollection"}  # no features key
        rows = service.list_events(open_kalshi(), stations=["NYC"]) * 2
        with self.assertRaises(service.SourceDrift):
            service.enrich_events(Shape(), rows)

    def test_enrich_limit_zero_and_empty_list(self):
        rows = service.list_events(open_kalshi(), stations=["NYC"])
        self.assertEqual(service.enrich_events(FakeNws(), rows, limit=0), 0)
        self.assertTrue(all("enrichment" not in r for r in rows))
        self.assertEqual(service.enrich_events(FakeNws(), []), 0)

    def test_analyze_survives_missing_market_summary(self):
        ev = {"kind": "high", "markets": []}
        a = service.analyze(ev, {"forecast": {"high_f": 80}, "hourly_forecast": {}, "observations": {}, "climate_report": {}})
        self.assertIsNone(a["forecast"]["ticker"])
        self.assertIsNone(a["market_expected_temp_f"])
        self.assertEqual(a["projected"]["temp_f"], 80)

    def test_high_volume_observations_fast(self):
        feats = [{"properties": {"timestamp": f"2026-09-10T{h:02d}:{m:02d}:00+00:00", "temperature": {"value": 20 + (h % 5), "unitCode": "wmoUnit:degC"}}}
                 for h in range(24) for m in range(0, 60, 1)]
        t0 = time.time()
        s = parse.summarize_observations({"features": feats}, date(2026, 9, 10), "America/Chicago")
        self.assertLess(time.time() - t0, 1.0)
        self.assertGreater(s["count"], 1000)


class ChargingCapTests(unittest.TestCase):
    """_enrich_cap math with a fake charging manager (no Apify platform needed)."""

    def cap(self, remaining, prices, n=10):
        from src import main

        class Info:
            is_pay_per_event = True
            per_event_prices = prices

        class CM:
            def get_pricing_info(self):
                return Info()

            def get_max_total_charge_usd(self):
                return Decimal(str(remaining))

            def calculate_total_charged_amount(self):
                return Decimal("0")

            def calculate_max_event_charge_count_within_limit(self, event):
                p = prices.get(event)
                return None if p is None else int(Decimal(str(remaining)) / p)

        orig = main.Actor.get_charging_manager
        main.Actor.get_charging_manager = staticmethod(lambda: CM())
        try:
            return main._enrich_cap(n, "event", "enriched-event")
        finally:
            main.Actor.get_charging_manager = orig

    def test_budget_splits_enriched_and_plain(self):
        prices = {"event": Decimal("0.002"), "enriched-event": Decimal("0.02")}
        self.assertEqual(self.cap(0.05, prices, n=6), 2)     # 2*0.02 + 4*0.002 = 0.048
        self.assertEqual(self.cap(0.20, prices, n=6), 6)     # everything fits
        self.assertEqual(self.cap(0.001, prices, n=6), 0)    # not even the plain rows fit; nothing enriched
        self.assertEqual(self.cap(0, prices, n=6), 0)

    def test_unlimited_budget_and_missing_prices(self):
        prices = {"event": Decimal("0.002"), "enriched-event": Decimal("0.02")}
        self.assertIsNone(self.cap(Decimal("Infinity"), prices))
        self.assertEqual(self.cap(0.05, {"event": Decimal("0.002")}), None)   # no enriched price: platform count path returns None
        self.assertEqual(self.cap(0.05, {"event": Decimal("0.02"), "enriched-event": Decimal("0.02")}), 2)  # equal prices: platform count


if __name__ == "__main__":
    unittest.main()
