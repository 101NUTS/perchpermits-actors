"""Schema contract: fields may be added, never removed or renamed.

`SCHEMA_V1` is the promise printed in the README. If a change makes this test
fail, that change breaks every agent already calling the actor; bump the
version and keep the old fields instead.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kalshi_weather import service  # noqa: E402
from tests.test_offline import FakeNws, open_kalshi  # noqa: E402

SCHEMA_V1 = {
    "event": {"event_ticker", "series_ticker", "series_title", "kind", "title", "target_date", "strike_date", "station",
              "settlement_source", "rules_primary", "mutually_exclusive", "markets", "market_summary", "settlement", "event_url",
              "status", "fetched_at_utc"},
    "station": {"cli_id", "icao", "city"},
    "market": {"ticker", "label", "strike_type", "floor_strike", "cap_strike", "low_f", "high_f", "yes_bid", "yes_ask",
               "yes_mid", "last_price", "previous_price", "implied_prob", "normalized_prob", "volume", "volume_24h",
               "open_interest", "liquidity_dollars", "status", "result", "expiration_value", "open_time", "close_time",
               "expected_expiration_time", "updated_time"},
    "market_summary": {"brackets", "priced", "prob_sum", "expected_temp_f", "favorite_ticker", "favorite_label",
                       "favorite_prob", "total_volume", "total_open_interest"},
    "enrichment": {"status", "station", "observations", "forecast", "hourly_forecast", "climate_report", "analysis"},
    "enrichment_station": {"icao", "name", "timezone", "lat", "lon", "grid_id", "grid_x", "grid_y", "forecast_url",
                           "forecast_hourly_url", "forecast_office"},
    "observations": {"count", "max_f", "max_at", "min_f", "min_at", "latest_f", "latest_at", "first_at"},
    "forecast": {"high_f", "low_f", "day_period", "night_period", "generated_at", "updated_at"},
    "hourly_forecast": {"hours", "max_f", "max_at", "min_f", "min_at"},
    "climate_report": {"summary_for", "issued_at", "issuing_office", "max_f", "max_at", "min_f", "min_at", "final",
                       "as_of", "product_id"},
    "analysis": {"forecast", "forecast_source", "hourly_forecast", "running", "projected", "official", "official_source", "official_final",
                 "market_expected_temp_f", "forecast_minus_market_f", "projected_minus_market_f"},
    "hit": {"temp_f", "ticker", "label", "implied_prob", "normalized_prob"},
}


class SchemaTests(unittest.TestCase):
    def test_v1_fields_all_present(self):
        rows = service.list_events(open_kalshi(), kind="high", stations=["NYC"])
        service.enrich_events(FakeNws(), rows)
        r = rows[0]
        self.assertTrue(SCHEMA_V1["event"] <= set(r), SCHEMA_V1["event"] - set(r))
        self.assertTrue(SCHEMA_V1["station"] <= set(r["station"]))
        self.assertTrue(SCHEMA_V1["market"] <= set(r["markets"][0]), SCHEMA_V1["market"] - set(r["markets"][0]))
        self.assertTrue(SCHEMA_V1["market_summary"] <= set(r["market_summary"]))
        e = r["enrichment"]
        self.assertTrue(SCHEMA_V1["enrichment"] <= set(e), SCHEMA_V1["enrichment"] - set(e))
        self.assertTrue(SCHEMA_V1["enrichment_station"] <= set(e["station"]))
        self.assertTrue(SCHEMA_V1["observations"] <= set(e["observations"]))
        self.assertTrue(SCHEMA_V1["forecast"] <= set(e["forecast"]))
        self.assertTrue(SCHEMA_V1["hourly_forecast"] <= set(e["hourly_forecast"]))
        self.assertTrue(SCHEMA_V1["analysis"] <= set(e["analysis"]), SCHEMA_V1["analysis"] - set(e["analysis"]))
        self.assertTrue(SCHEMA_V1["hit"] <= set(e["analysis"]["forecast"]))

    def test_climate_report_fields(self):
        from src.kalshi_weather.parse import parse_cli
        from tests.test_offline import fixture
        rep = parse_cli(fixture("nws_cli_product_NYC_latest.json"))
        self.assertTrue(SCHEMA_V1["climate_report"] <= set(rep), SCHEMA_V1["climate_report"] - set(rep))


if __name__ == "__main__":
    unittest.main()
