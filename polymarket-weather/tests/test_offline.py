"""Offline tests over saved Polymarket and METAR payloads (fixtures captured 2026-09-18)."""

from __future__ import annotations

import glob
import json
import pathlib
import sys
import unittest
from datetime import date, datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.polymarket_weather import parse, service  # noqa: E402

FIX = HERE / "fixtures"


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeMetar:
    def __init__(self):
        self.calls = []

    def metars(self, icao, hours=48):
        self.calls.append((icao, hours))
        f = FIX / f"metar_{icao}_110h.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


class Slugs(unittest.TestCase):
    def test_parse_slug(self):
        self.assertEqual(parse.parse_slug("highest-temperature-in-nyc-on-september-18-2026"),
                         {"kind": "high", "city": "nyc", "target_date": date(2026, 9, 18)})
        self.assertEqual(parse.parse_slug("lowest-temperature-in-los-angeles-on-september-5-2026")["city"], "los-angeles")
        self.assertIsNone(parse.parse_slug("will-it-rain-in-nyc"))

    def test_slug_for_round_trips(self):
        s = service.slug_for("sao-paulo", date(2026, 9, 17), "low")
        self.assertEqual(s, "lowest-temperature-in-sao-paulo-on-september-17-2026")
        self.assertEqual(parse.parse_slug(s)["kind"], "low")


class Brackets(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(parse.bracket_bounds("71°F or below"), (None, 71))
        self.assertEqual(parse.bracket_bounds("72-73°F"), (72, 73))
        self.assertEqual(parse.bracket_bounds("90°F or higher"), (90, None))
        self.assertEqual(parse.bracket_bounds("16°C"), (16, 16))
        self.assertEqual(parse.bracket_bounds("-2°C or below"), (None, -2))

    def test_unknown_label_is_drift(self):
        with self.assertRaises(parse.ParseDrift):
            parse.bracket_bounds("around seventy")


class Stations(unittest.TestCase):
    def test_nyc_is_laguardia_not_central_park(self):
        row = parse.event_row(load("pm_event_highest-temperature-in-nyc-on-september-18-2026.json"))
        self.assertEqual(row["station"]["icao"], "KLGA")
        self.assertEqual(row["station"]["source_type"], "noaa_timeseries")
        self.assertEqual(row["unit"], "F")
        self.assertTrue(row["hourly_only"])
        self.assertEqual(len(row["brackets"]), 11)
        self.assertEqual(row["brackets"][0]["high"], 71)  # sorted low to high

    def test_chicago_is_ohare(self):
        self.assertEqual(parse.event_row(load("pm_event_highest-temperature-in-chicago-on-september-18-2026.json"))["station"]["icao"], "KORD")

    def test_london_celsius_all_reports(self):
        row = parse.event_row(load("pm_event_highest-temperature-in-london-on-september-18-2026.json"))
        self.assertEqual((row["station"]["icao"], row["unit"], row["hourly_only"]), ("EGLC", "C", False))

    def test_station_only_in_description(self):
        desc = ("This market will resolve to the temperature range that contains the highest temperature recorded by NOAA at the "
                "Ben Gurion International Airport in degrees Celsius on 18 Sep '26. ... available here: "
                "https://www.weather.gov/wrh/timeseries?site=LLBG")
        st = parse.station_from_rules(desc, None)
        self.assertEqual((st["icao"], st["name"]), ("LLBG", "Ben Gurion International Airport"))

    def test_wunderground_source(self):
        st = parse.station_from_rules("", "https://www.wunderground.com/history/daily/cn/jinan/ZSJN")
        self.assertEqual((st["source_type"], st["icao"]), ("wunderground", "ZSJN"))

    def test_hong_kong_observatory(self):
        row = parse.event_row(load("pm_event_highest-temperature-in-hong-kong-on-september-17-2026.json"))
        self.assertEqual(row["station"]["source_type"], "hong_kong_observatory")
        out = service.join(load("pm_event_highest-temperature-in-hong-kong-on-september-17-2026.json"), FakeMetar())
        self.assertIsNone(out["observations"])
        self.assertIn("Hong Kong Observatory", out["note"])


class Observations(unittest.TestCase):
    def test_hourly_only_drops_specials(self):
        reports = [
            {"obsTime": int(datetime(2026, 9, 17, 18, 51, tzinfo=timezone.utc).timestamp()), "metarType": "METAR", "temp": 25, "rawOb": "METAR KXXX T02500200"},
            {"obsTime": int(datetime(2026, 9, 17, 19, 10, tzinfo=timezone.utc).timestamp()), "metarType": "SPECI", "temp": 30, "rawOb": "SPECI KXXX T03000200"},
        ]
        self.assertEqual(parse.summarize_day(reports, date(2026, 9, 17), "America/New_York", "F", True)["max"], 77)
        self.assertEqual(parse.summarize_day(reports, date(2026, 9, 17), "America/New_York", "F", False)["max"], 86)

    def test_tenths_group_beats_body(self):
        r = {"temp": 24, "rawOb": "METAR KLGA 180951Z 24/21 A3002 RMK AO2 T02440211"}
        self.assertEqual(parse.celsius_tenths(r), 24.4)
        self.assertEqual(parse.reading(r, "F"), 76)  # 75.92 -> 76
        self.assertEqual(parse.celsius_tenths({"temp": -3, "rawOb": "T10330211"}), -3.3)

    def test_local_day_boundary(self):
        # 03:30 UTC on the 18th is still the 17th in New York
        r = [{"obsTime": int(datetime(2026, 9, 18, 3, 30, tzinfo=timezone.utc).timestamp()), "metarType": "METAR", "temp": 20, "rawOb": ""}]
        self.assertEqual(parse.summarize_day(r, date(2026, 9, 17), "America/New_York", "C", False)["count"], 1)
        self.assertEqual(parse.summarize_day(r, date(2026, 9, 18), "America/New_York", "C", False)["count"], 0)


class PlatformRunFindings(unittest.TestCase):
    """Issues found by the first Apify platform run, 2026-09-18."""

    def test_stale_open_events_are_dropped(self):
        evs = [{"slug": "highest-temperature-in-jinan-on-may-20-2026"},
               {"slug": "highest-temperature-in-nyc-on-september-17-2026"},
               {"slug": "highest-temperature-in-nyc-on-september-19-2026"}]
        kept = [e["slug"] for e in service.current_open(evs, today=date(2026, 9, 18))]
        self.assertEqual(kept, ["highest-temperature-in-nyc-on-september-17-2026", "highest-temperature-in-nyc-on-september-19-2026"])

    def test_station_missing_from_noaa_feed_is_not_servable(self):
        ev = load("pm_event_highest-temperature-in-nyc-on-september-18-2026.json")
        ev = {**ev, "description": ev["description"].replace("site=klga", "site=zsjn"), "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=zsjn"}
        with self.assertRaises(service.NotServable) as cm:
            service.join(ev, FakeMetar(), None)
        self.assertIn("no reports in NOAA's METAR feed", str(cm.exception))


    def test_day_missing_its_first_hours_is_not_complete(self):
        # 2026-09-18 archive run: a day whose night hours fell before the fetched window
        # looked final and gave wrong lows. Reports after the day but none before it: not complete.
        mk = lambda h, d, c: {"obsTime": int(datetime(2026, 9, d, h, tzinfo=timezone.utc).timestamp()), "metarType": "METAR", "temp": c, "rawOb": ""}
        tail_only = [mk(6, 14, 20), mk(12, 14, 22), mk(1, 15, 21)]  # Tokyo 15:00 and 21:00 on the 14th, then the 15th
        s = parse.summarize_day(tail_only, date(2026, 9, 14), "Asia/Tokyo", "C", False)
        self.assertFalse(s["day_complete"])
        self.assertFalse(s["covers_day_start"])
        s = parse.summarize_day([mk(14, 13, 18)] + tail_only, date(2026, 9, 14), "Asia/Tokyo", "C", False)
        self.assertTrue(s["day_complete"])


class SettledRecord(unittest.TestCase):
    def test_every_settled_fixture_lands_in_the_paid_bracket(self):
        checked = 0
        for f in sorted(glob.glob(str(FIX / "pm_event_*.json"))):
            ev = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
            row = parse.event_row(ev)
            if row["status"] != "settled" or row["station"]["source_type"] == "hong_kong_observatory":
                continue
            out = service.join(ev, FakeMetar(), None, now=datetime(2026, 9, 18, 10, tzinfo=timezone.utc))
            with self.subTest(slug=row["slug"]):
                self.assertTrue(out["verdict"]["day_complete"])
                self.assertEqual(out["verdict"]["final_if_complete"], out["verdict"]["paid_bracket"])
            checked += 1
        self.assertEqual(checked, 89)

    def test_rule_outs_for_open_high(self):
        ev = load("pm_event_highest-temperature-in-nyc-on-september-18-2026.json")
        out = service.join(ev, FakeMetar(), None, now=datetime(2026, 9, 18, 10, tzinfo=timezone.utc))
        self.assertEqual(out["verdict"]["running_value"], 78)
        self.assertEqual(out["floor_rule_outs"], ["71°F or below", "72-73°F", "74-75°F", "76-77°F"])
        self.assertFalse(out["verdict"]["day_complete"])
        self.assertIsNone(out["verdict"]["final_if_complete"])


if __name__ == "__main__":
    unittest.main()
