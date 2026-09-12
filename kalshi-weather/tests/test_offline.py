"""Offline tests over saved Kalshi and NWS payloads (fixtures captured 2026-09-11)."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kalshi_weather import parse, service  # noqa: E402
from src.kalshi_weather.fetch import FetchError  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str):
    with open(FIX / name, encoding="utf-8") as f:
        return json.load(f)


class FakeKalshi:
    """Serves the saved series list and per-series event pages."""

    def __init__(self, events_by_series: dict[str, dict]):
        self.by_series = events_by_series
        self.requests_made = 0

    def series(self, category="Climate and Weather"):
        self.requests_made += 1
        return fixture("kalshi_series.json")["series"]

    def events(self, series_ticker, status="open", with_markets=True, limit=200):
        self.requests_made += 1
        page = self.by_series.get((series_ticker, status)) or self.by_series.get(series_ticker)
        return list((page or {}).get("events") or [])


class FakeNws:
    def __init__(self, *, fail_obs: bool = False, broken_forecast: bool = False):
        self.fail_obs = fail_obs
        self.broken_forecast = broken_forecast
        self.requests_made = 0
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.requests_made += 1
        self.calls[name] = self.calls.get(name, 0) + 1

    def station(self, icao):
        self._count('station')
        if icao != "KNYC":
            raise FetchError(f"HTTP 404 for /stations/{icao}")
        return fixture("nws_station_KNYC.json")

    def points(self, lat, lon):
        self._count('points')
        return fixture("nws_points_KNYC.json")

    def forecast(self, url):
        self._count('forecast')
        if self.broken_forecast:
            return {"properties": {}}
        return fixture("nws_forecast_hourly_OKX_34_45.json" if "hourly" in url else "nws_forecast_OKX_34_45.json")

    def observations(self, icao, start, end, limit=500):
        self._count('observations')
        if self.fail_obs:
            raise FetchError("HTTP 503 for observations")
        return fixture("nws_obs_KNYC_2026-09-10.json")

    def cli_products(self, location):
        self._count('cli_products')
        return fixture("nws_cli_list_NYC.json")["@graph"]

    def product(self, url_or_id):
        self._count('product')
        return fixture("nws_cli_product_NYC_latest.json")


def open_kalshi() -> FakeKalshi:
    return FakeKalshi({
        ("KXHIGHNY", "open"): fixture("kalshi_events_KXHIGHNY_open.json"),
        ("KXLOWTNYC", "open"): fixture("kalshi_events_KXLOWTNYC_open.json"),
        ("KXHIGHNY", "settled"): fixture("kalshi_events_KXHIGHNY_settled.json"),
    })


class ParseTests(unittest.TestCase):
    def test_station_from_rules(self):
        st = parse.station_from_rules("If the maximum temperature recorded at New York City (CLINYC) for Sep 11, 2026, is greater than 86° ...")
        self.assertEqual(st, {"cli_id": "NYC", "icao": "KNYC", "city": "New York City"})
        self.assertIsNone(parse.station_from_rules("... recorded at EHAM in Amsterdam for ..."))
        self.assertIsNone(parse.station_from_rules(None))

    def test_target_date(self):
        self.assertEqual(parse.target_date_from_event("KXHIGHNY-26SEP11"), date(2026, 9, 11))
        self.assertEqual(parse.target_date_from_event("KXLOWTDC-27JAN03"), date(2027, 1, 3))
        self.assertIsNone(parse.target_date_from_event("KXHIGHUS"))

    def test_bracket_bounds(self):
        self.assertEqual(parse.bracket_bounds("less", None, 79), (None, 78))
        self.assertEqual(parse.bracket_bounds("between", 79, 80), (79, 80))
        self.assertEqual(parse.bracket_bounds("greater", 86, None), (87, None))
        self.assertEqual(parse.bracket_bounds("less", 79, None), (None, 78))  # tolerate the edge in either field
        self.assertEqual(parse.bracket_bounds("between", 79.5, 80.5), (80, 80))
        self.assertEqual(parse.bracket_bounds("weird", None, None), (None, None))

    def test_event_row_ladder_is_sorted_and_labelled(self):
        ev = fixture("kalshi_events_KXHIGHNY_open.json")["events"][0]
        row = parse.event_row(ev)
        self.assertEqual(row["event_ticker"], "KXHIGHNY-26SEP11")
        self.assertEqual(row["kind"], "high")
        self.assertEqual(row["target_date"], "2026-09-11")
        self.assertEqual(row["station"]["icao"], "KNYC")
        labels = [m["label"] for m in row["markets"]]
        self.assertEqual(labels[0], "78° or below")
        self.assertEqual(labels[-1], "87° or above")
        bounds = [(m["low_f"], m["high_f"]) for m in row["markets"]]
        self.assertEqual(bounds, [(None, 78), (79, 80), (81, 82), (83, 84), (85, 86), (87, None)])
        # the ladder is contiguous: each bracket starts right after the previous one ends
        for (lo1, hi1), (lo2, hi2) in zip(bounds, bounds[1:]):
            self.assertEqual(lo2, hi1 + 1)
        self.assertEqual(row["settlement_source"]["name"], "The Weather Company")
        self.assertTrue(row["event_url"].endswith("/kxhighny/kxhighny-26sep11"))

    def test_market_summary_distribution(self):
        row = parse.event_row(fixture("kalshi_events_KXHIGHNY_open.json")["events"][0])
        ms = row["market_summary"]
        self.assertEqual(ms["brackets"], 6)
        self.assertEqual(ms["priced"], 6)
        mids = [m["yes_mid"] for m in row["markets"]]
        self.assertAlmostEqual(ms["prob_sum"], sum(mids), places=4)
        self.assertEqual(ms["favorite_label"], "79° to 80°")
        self.assertAlmostEqual(ms["favorite_prob"], max(mids) / sum(mids), places=4)
        self.assertAlmostEqual(sum(m["normalized_prob"] for m in row["markets"]), 1.0, places=3)
        self.assertTrue(79 < ms["expected_temp_f"] < 82, ms["expected_temp_f"])

    def test_bracket_for(self):
        row = parse.event_row(fixture("kalshi_events_KXHIGHNY_open.json")["events"][0])
        self.assertEqual(parse.bracket_for(81, row["markets"])["label"], "81° to 82°")
        self.assertEqual(parse.bracket_for(75.9, row["markets"])["label"], "78° or below")
        self.assertEqual(parse.bracket_for(90, row["markets"])["label"], "87° or above")
        self.assertEqual(parse.bracket_for(80.5, row["markets"])["label"], "79° to 80°")  # rounds half to even -> 80
        self.assertIsNone(parse.bracket_for(None, row["markets"]))

    def test_settled_ladder_has_results(self):
        evs = fixture("kalshi_events_KXHIGHNY_settled.json")["events"]
        row = parse.event_row(evs[0])
        results = {m["label"]: m["result"] for m in row["markets"]}
        self.assertEqual(sum(1 for v in results.values() if v == "yes"), 1)
        self.assertTrue(all(m["status"] == "finalized" for m in row["markets"]))
        # Kalshi stamps the settlement number on every strike; the ladder lifts it and names the YES strike
        st = row["settlement"]
        self.assertIsNotNone(st["value_f"])
        self.assertEqual(st["yes_label"], next(k for k, v in results.items() if v == "yes"))
        self.assertEqual(parse.bracket_for(st["value_f"], row["markets"])["ticker"], st["yes_ticker"])
        open_row = parse.event_row(fixture("kalshi_events_KXHIGHNY_open.json")["events"][0])
        self.assertIsNone(open_row["settlement"])

    def test_settlement_value_stands_in_when_report_is_gone(self):
        k = open_kalshi()
        rows = service.list_events(k, kind="high", status="settled", stations=["NYC"], date_from=date(2026, 9, 9), date_to=date(2026, 9, 9))
        service.enrich_events(FakeNws(), rows)  # the fixture report is for Sep 10, so Sep 9 has none
        a = rows[0]["enrichment"]["analysis"]
        self.assertEqual(a["official_source"], "kalshi_settlement")
        self.assertEqual(a["official"]["temp_f"], rows[0]["settlement"]["value_f"])
        self.assertEqual(a["official"]["ticker"], rows[0]["settlement"]["yes_ticker"])

    def test_observations_summary_local_day(self):
        obs = fixture("nws_obs_KNYC_2026-09-10.json")
        s = parse.summarize_observations(obs, date(2026, 9, 10), "America/New_York")
        self.assertEqual(s["count"], 26)
        self.assertEqual(s["max_f"], 84.0)
        self.assertEqual(s["min_f"], 69.1)  # 20.6 C; the CLI report rounds the day's minimum to 69
        self.assertTrue(s["max_at"].startswith("2026-09-10T"))
        empty = parse.summarize_observations(obs, date(2026, 9, 1), "America/New_York")
        self.assertEqual(empty["count"], 0)
        self.assertIsNone(empty["max_f"])

    def test_forecast_summaries(self):
        d = parse.summarize_daily_forecast(fixture("nws_forecast_OKX_34_45.json"), date(2026, 9, 11), "America/New_York")
        self.assertEqual(d["high_f"], 81)
        self.assertEqual(d["low_f"], 69)
        self.assertEqual(d["day_period"], "Friday")
        h = parse.summarize_hourly_forecast(fixture("nws_forecast_hourly_OKX_34_45.json"), date(2026, 9, 11), "America/New_York", keep_periods=True)
        self.assertEqual(h["hours"], 20)  # forecast starts at 04:00 local
        self.assertEqual(h["max_f"], 81)
        self.assertEqual(h["min_f"], 70)
        self.assertEqual(len(h["periods"]), 20)
        d2 = parse.summarize_daily_forecast(fixture("nws_forecast_OKX_34_45.json"), date(2026, 9, 12), "America/New_York")
        self.assertEqual(d2["night_period"], "Friday Night")  # the low of the night leading into the 12th

    def test_cli_report(self):
        rep = parse.parse_cli(fixture("nws_cli_product_NYC_latest.json"))
        self.assertEqual(rep["summary_for"], "2026-09-10")
        self.assertEqual(rep["max_f"], 84)
        self.assertEqual(rep["max_at"], "325 PM")
        self.assertEqual(rep["min_f"], 69)
        self.assertTrue(rep["final"])
        self.assertEqual(rep["issuing_office"], "KOKX")

    def test_cli_partial_detection(self):
        prod = fixture("nws_cli_product_NYC_latest.json")
        text = prod["productText"].replace("CLIMATE SUMMARY FOR SEPTEMBER 10 2026", "CLIMATE SUMMARY FOR SEPTEMBER 11 2026").replace(" YESTERDAY", " TODAY")
        rep = parse.parse_cli({**prod, "productText": text})
        self.assertEqual(rep["summary_for"], "2026-09-11")
        self.assertFalse(rep["final"])

    def test_drift_raises(self):
        with self.assertRaises(parse.ParseDrift):
            parse.event_row({"event_ticker": "X"})
        with self.assertRaises(parse.ParseDrift):
            parse.market_row({"ticker": "X"})
        with self.assertRaises(parse.ParseDrift):
            parse.summarize_observations({}, date(2026, 9, 10), "UTC")
        with self.assertRaises(parse.ParseDrift):
            parse.parse_cli({"productText": ""})


class ServiceTests(unittest.TestCase):
    def test_list_and_enrich_open(self):
        k = open_kalshi()
        rows = service.list_events(k, stations=["NYC"], max_events=10)
        self.assertEqual({r["event_ticker"] for r in rows}, {"KXHIGHNY-26SEP11", "KXLOWTNYC-26SEP11"})
        self.assertTrue(all(r["status"] == "open" and r["fetched_at_utc"].endswith("Z") for r in rows))
        nws = FakeNws()
        n = service.enrich_events(nws, rows)
        self.assertEqual(n, 2)
        high = next(r for r in rows if r["kind"] == "high")
        e = high["enrichment"]
        self.assertEqual(e["status"], "ok")
        self.assertEqual(e["station"]["timezone"], "America/New_York")
        self.assertEqual(e["forecast"]["high_f"], 81)
        self.assertEqual(e["analysis"]["forecast"]["label"], "81° to 82°")
        b81 = next(m for m in high["markets"] if m["label"] == "81° to 82°")
        self.assertAlmostEqual(e["analysis"]["forecast"]["normalized_prob"], b81["normalized_prob"], places=4)
        self.assertIsNotNone(e["analysis"]["forecast_minus_market_f"])
        low = next(r for r in rows if r["kind"] == "low")
        self.assertEqual(low["enrichment"]["analysis"]["forecast"]["temp_f"], 69)
        # station metadata and forecasts fetched once for both ladders of the same city;
        # observations once per ladder; the climate report only once the target day has begun
        self.assertEqual({k: nws.calls[k] for k in ("station", "points", "forecast", "observations")}, {"station": 1, "points": 1, "forecast": 2, "observations": 2})
        self.assertLessEqual(nws.calls.get("cli_products", 0), 1)

    def test_projected_extreme_and_hourly_fallback(self):
        k = open_kalshi()
        rows = service.list_events(k, stations=["NYC"])
        service.enrich_events(FakeNws(), rows)
        high = next(r for r in rows if r["kind"] == "high")["enrichment"]["analysis"]
        low = next(r for r in rows if r["kind"] == "low")["enrichment"]["analysis"]
        # fixtures: hourly max 81 / min 70 for Sep 11; the observation file is Sep 10, so the
        # target day has no readings yet and the projection is the hourly forecast alone
        self.assertEqual(high["forecast_source"], "daily")
        self.assertEqual(high["running"]["temp_f"], None)
        self.assertEqual(high["projected"]["temp_f"], 81)
        self.assertEqual(low["projected"]["temp_f"], 70)
        self.assertIsNotNone(high["projected_minus_market_f"])
        # once readings exist, the projection is the warmer of observed-so-far and hourly-expected
        e = dict(next(r for r in rows if r["kind"] == "high")["enrichment"])
        e["observations"] = {"max_f": 84.0, "min_f": 69.1, "count": 5}
        a = service.analyze(next(r for r in rows if r["kind"] == "high"), e)
        self.assertEqual(a["projected"]["temp_f"], 84.0)
        e_low = dict(next(r for r in rows if r["kind"] == "low")["enrichment"])
        e_low["observations"] = {"max_f": 84.0, "min_f": 69.1, "count": 5}
        a = service.analyze(next(r for r in rows if r["kind"] == "low"), e_low)
        self.assertEqual(a["projected"]["temp_f"], 69.1)
        # no daily period for the day: the hourly figure stands in as the forecast
        ev = dict(rows[0])
        e = {"forecast": {"high_f": None, "low_f": None}, "hourly_forecast": {"max_f": 81, "min_f": 70}, "observations": None, "climate_report": None}
        a = service.analyze(ev, e)
        self.assertEqual(a["forecast_source"], "hourly")
        self.assertEqual(a["forecast"]["temp_f"], 81 if ev["kind"] == "high" else 70)
        self.assertEqual(a["projected"]["temp_f"], a["forecast"]["temp_f"])
        # nothing at all: everything is None, nothing raises
        a = service.analyze(ev, {"forecast": None, "hourly_forecast": None, "observations": None, "climate_report": None})
        self.assertIsNone(a["forecast"]["ticker"])
        self.assertIsNone(a["projected"]["temp_f"])

    def test_kind_and_station_filters(self):
        k = open_kalshi()
        self.assertEqual([r["kind"] for r in service.list_events(k, kind="low", stations=["NYC"])], ["low"])
        self.assertEqual(service.list_events(k, stations=["MDW"]), [])
        self.assertEqual(len(service.list_events(k, stations=["NYC"], max_events=1)), 1)
        self.assertEqual(service.list_events(k, stations=["NYC"], date_from=date(2026, 9, 12)), [])
        self.assertEqual(len(service.list_events(k, stations=["NYC"], date_to=date(2026, 9, 11))), 2)

    def test_settled_with_official_report(self):
        k = open_kalshi()
        rows = service.list_events(k, kind="high", status="settled", stations=["NYC"], date_from=date(2026, 9, 10), date_to=date(2026, 9, 10))
        self.assertEqual(len(rows), 0)  # fixture holds Sep 7-9 only
        rows = service.list_events(k, kind="high", status="settled", stations=["NYC"], date_from=date(2026, 9, 9), date_to=date(2026, 9, 9))
        self.assertEqual(len(rows), 1)
        nws = FakeNws()
        service.enrich_events(nws, rows)
        e = rows[0]["enrichment"]
        # the fixture report is for Sep 10, so no report matches Sep 9; the official reading then
        # comes from Kalshi's stamped settlement value instead of the climate report
        self.assertIsNone(e["climate_report"])
        self.assertEqual(e["analysis"]["official_source"], "kalshi_settlement")
        self.assertEqual(e["analysis"]["official"]["temp_f"], rows[0]["settlement"]["value_f"])
        self.assertEqual(e["status"], "ok")

    def test_unknown_station_is_not_enriched(self):
        ev = fixture("kalshi_events_KXHIGHNY_open.json")["events"][0]
        row = parse.event_row(ev)
        row["station"] = {"cli_id": "ZZZ", "icao": "KZZZ", "city": "Nowhere"}
        n = service.enrich_events(FakeNws(), [row])
        self.assertEqual(n, 0)
        self.assertEqual(row["enrichment"]["status"], "station_lookup_failed")
        intl = parse.event_row(ev)
        intl["station"] = None
        service.enrich_events(FakeNws(), [intl])
        self.assertEqual(intl["enrichment"]["status"], "no_nws_station")

    def test_partial_when_observations_fail(self):
        k = open_kalshi()
        rows = service.list_events(k, kind="high", stations=["NYC"])
        n = service.enrich_events(FakeNws(fail_obs=True), rows)
        self.assertEqual(n, 1)
        e = rows[0]["enrichment"]
        self.assertEqual(e["status"], "partial")
        self.assertIsNone(e["observations"])
        self.assertEqual(e["forecast"]["high_f"], 81)
        self.assertIn("observations", e["problems"][0])

    def test_forecast_drift_across_rows_raises(self):
        k = open_kalshi()
        rows = service.list_events(k, stations=["NYC"])
        rows = rows * 2  # four attempts, every forecast payload broken
        with self.assertRaises(service.SourceDrift):
            service.enrich_events(FakeNws(broken_forecast=True), rows)

    def test_bare_events_are_skipped_and_known_series_not_fetched(self):
        settled = fixture("kalshi_events_KXHIGHNY_settled.json")
        bare = {"events": [{k: v for k, v in e.items() if k != "markets"} for e in settled["events"]]}
        k = FakeKalshi({("KXHIGHNY", "settled"): bare})
        self.assertEqual(service.list_events(k, status="settled", stations=["NYC"]), [])
        # only the NYC series were requested (plus the series list itself)
        self.assertLessEqual(k.requests_made, 1 + 4)
        k2 = open_kalshi()
        service.list_events(k2, stations=["MIA"])
        self.assertLessEqual(k2.requests_made, 1 + 3)  # KXHIGHMIA, KXLOWTMIA, KXLOWMIA

    def test_settled_stops_paging_before_date_from(self):
        k = open_kalshi()
        rows = service.list_events(k, kind="high", status="settled", stations=["NYC"], date_from=date(2026, 9, 9))
        self.assertEqual([r["target_date"] for r in rows], ["2026-09-09"])

    def test_series_filter_drops_non_daily(self):
        k = open_kalshi()
        series = service.temperature_series(k)
        tickers = {s["ticker"] for s in series}
        self.assertIn("KXHIGHNY", tickers)
        self.assertNotIn("KXHIGHNYD", tickers)
        self.assertNotIn("KXHIGHUS", tickers)
        self.assertTrue(all(t.startswith(("KXHIGH", "KXLOW")) for t in tickers))


if __name__ == "__main__":
    unittest.main()
