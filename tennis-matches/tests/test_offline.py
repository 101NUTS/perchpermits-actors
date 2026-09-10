"""Offline tests: no network. Run with `python -m unittest discover -s tests -t .` from the actor folder."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tennis_matches import parse, service  # noqa: E402
from src.tennis_matches.parse import ParseDrift  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class ListPageTests(unittest.TestCase):
    def setUp(self):
        self.sched = parse.parse_list_page(fixture("schedule_2026-09-10.html"), "schedule", date(2026, 9, 10))
        self.res = parse.parse_list_page(fixture("results_2026-09-08.html"), "results", date(2026, 9, 8))

    def test_schedule_rows_and_statuses(self):
        self.assertGreater(len(self.sched), 20)
        statuses = {m["status"] for m in self.sched}
        self.assertIn("scheduled", statuses)
        for m in self.sched:
            self.assertEqual(m["list_kind"], "schedule")
            self.assertEqual(m["date"], "2026-09-10")
            self.assertIsNotNone(m["match_id"])
            self.assertTrue(m["home"]["name"] and m["away"]["name"])
            self.assertIn(m["tournament"]["tour"], ("atp", "wta", None))
            self.assertIn(m["tournament"]["format"], ("singles", "doubles"))

    def test_schedule_known_match(self):
        m = next(x for x in self.sched if x["match_id"] == 3317719)
        self.assertEqual(m["home"]["name"], "Jones S.")
        self.assertEqual(m["home"]["slug"], "jones-2fc9a")
        self.assertEqual(m["away"]["name"], "Braund A.")
        self.assertEqual(m["odds"], {"home": 1.01, "away": 9.0})
        self.assertEqual(m["time_site"], "01:00")
        self.assertEqual(m["tournament"]["name"], "UTR Pro Tennis Series")
        self.assertEqual(m["tournament"]["tour"], "atp")
        self.assertEqual(m["status"], "scheduled")
        self.assertEqual(m["match_url"], "https://www.tennisexplorer.com/match-detail/?id=3317719")

    def test_doubles_flagged_from_tournament_href(self):
        fmts = {m["tournament"]["format"] for m in self.sched}
        self.assertIn("doubles", fmts)
        d = next(m for m in self.sched if m["tournament"]["format"] == "doubles")
        self.assertIn("type=double", d["tournament"]["url"])

    def test_results_known_match_five_sets(self):
        m = next(x for x in self.res if x["match_id"] == 3317637)
        self.assertEqual(m["status"], "finished")
        self.assertEqual(m["home"]["name"], "Tiafoe F.")
        self.assertEqual(m["home"]["seed"], "11")
        self.assertEqual(m["result"]["winner"], "home")
        self.assertEqual((m["result"]["sets_home"], m["result"]["sets_away"]), (3, 2))
        self.assertEqual(m["result"]["score"], "5-7, 3-6, 7-5, 6-3, 7-6(6)")
        self.assertEqual(m["result"]["sets"][4]["tiebreak_loser_points"], 6)
        self.assertEqual(m["odds"], {"home": 1.63, "away": 2.28})
        self.assertEqual(m["tournament"]["slug"], "us-open")

    def test_results_every_row_finished(self):
        self.assertGreater(len(self.res), 10)
        for m in self.res:
            self.assertEqual(m["status"], "finished")
            self.assertIsNotNone(m["result"]["winner"], m)

    def test_time_strips_live_stream_link_text(self):
        for m in self.sched:
            if m["time_site"] is not None:
                self.assertRegex(m["time_site"], r"^\d{1,2}:\d{2}$")
                self.assertIsNotNone(parse.as_utc(m["date"], m["time_site"]))
        self.assertGreater(sum(1 for m in self.sched if m["time_site"]), len(self.sched) // 2)

    def test_drift_raises(self):
        with self.assertRaises(ParseDrift):
            parse.parse_list_page("<html><body><p>maintenance</p></body></html>", "schedule", date(2026, 9, 10))


class DetailPageTests(unittest.TestCase):
    def setUp(self):
        self.d = parse.parse_detail_page(fixture("detail_3317637.html"), latest_limit=5, odds_history=True)
        self.small = parse.parse_detail_page(fixture("detail_3317719.html"))

    def test_header(self):
        self.assertEqual(self.d["date"], "2026-09-08")
        self.assertEqual(self.d["time_site"], "20:25")
        self.assertEqual(self.d["tournament_name"], "US Open")
        self.assertEqual(self.d["round"], "quarterfinal")
        self.assertEqual(self.d["surface"], "hard")
        self.assertIsNone(self.small["round"])
        self.assertEqual(self.small["surface"], "hard")

    def test_players(self):
        self.assertEqual(self.d["home"]["full_name"], "Tiafoe Frances")
        self.assertEqual(self.d["home"]["singles_rank"], 12)
        self.assertEqual(self.d["home"]["birthdate"], "1998-01-20")
        self.assertEqual(self.d["home"]["height"], "188 cm")
        self.assertEqual(self.d["away"]["slug"], "michelsen-a98bb")
        self.assertEqual(self.d["away"]["singles_rank"], 46)
        self.assertIsNone(self.d["away"]["height"])

    def test_surface_record(self):
        rec = self.d["surface_record"]
        self.assertEqual(rec["label"], "W/L - 2026")
        self.assertEqual(rec["home"]["hard"], {"wins": 25, "losses": 10})
        self.assertEqual(rec["home"]["grass"], {"wins": 9, "losses": 2})
        self.assertIsNone(rec["home"]["unknown"])

    def test_h2h(self):
        h = self.d["h2h"]
        self.assertEqual((h["home_wins"], h["away_wins"]), (3, 0))
        self.assertEqual(len(h["matches"]), 3)
        first = h["matches"][0]
        self.assertEqual(first["winner_name"], "Tiafoe")
        self.assertEqual(first["surface"], "hard")
        self.assertEqual(first["round"], "QF")
        self.assertEqual(first["score"], "5-7, 3-6, 7-5, 6-3, 7-6(6)")
        self.assertEqual(first["match_id"], 3317637)
        self.assertEqual(h["matches"][1]["tournament"], "Houston")
        self.assertEqual(h["matches"][1]["score"], "7-5, 6-1")

    def test_bookmakers_with_opening_and_history(self):
        bk = self.d["bookmakers"]
        self.assertGreaterEqual(len(bk), 3)
        ten = next(b for b in bk if b["bookmaker"] == "10Bet")
        self.assertEqual((ten["home"], ten["away"]), (1.6, 2.25))
        self.assertEqual((ten["home_opening"], ten["away_opening"]), (1.65, 2.2))
        self.assertEqual(ten["home_trend"], "down")
        self.assertEqual(ten["home_history"][0]["odds"], 1.6)
        self.assertEqual(ten["home_history"][0]["change"], -0.05)
        no_hist = parse.parse_detail_page(fixture("detail_3317637.html"))["bookmakers"][0]
        self.assertNotIn("home_history", no_hist)

    def test_latest_matches_and_form(self):
        home = self.d["latest_matches"]["home"]
        self.assertEqual(len(home), 5)
        self.assertEqual(home[0]["opponent"]["name"], "Michelsen")
        self.assertEqual(home[0]["round"], "QF")
        self.assertEqual(home[0]["round_full"], "quarterfinal")
        self.assertEqual(home[0]["date"], "2026-09-08")
        self.assertTrue(home[0]["won"])
        self.assertEqual(home[0]["score"], "5-7, 3-6, 7-5, 6-3, 7-6(6)")
        form = parse.summarize_form(home)
        self.assertEqual(form["sequence"], "WWWWW")
        self.assertEqual(form["streak"], "5W")
        self.assertEqual(parse.summarize_form([]), {"played": 0, "wins": 0, "losses": 0, "streak": None, "sequence": ""})

    def test_utc_conversion(self):
        # CEST in September: 20:25 local = 18:25Z
        self.assertEqual(parse.as_utc("2026-09-08", "20:25"), "2026-09-08T18:25:00Z")
        # CET in January
        self.assertEqual(parse.as_utc("2026-01-15", "10:00"), "2026-01-15T09:00:00Z")
        self.assertIsNone(parse.as_utc("2026-09-08", None))

    def test_title_names_and_final_score(self):
        self.assertEqual(self.d["home_names"], ["Tiafoe"])
        self.assertEqual(self.d["away_names"], ["Michelsen"])
        self.assertNotIn("partner", self.d["home"])
        self.assertIsNone(self.d["date_word"])
        html = "<html><body><div id='center'><h1 class='bg'>Krueger, Montgomery - Dabrowski, Stefani</h1>" \
               "<div class='box boxBasic lGray'><span class='upper'>Today</span>, 00:15, <a href='/us-open/2026/wta-women/'>US Open</a>, semifinal, hard</div>" \
               "<table class='result gDetail'><thead><tr><th class='plName'><a href='/player/krueger-7fc13'>Krueger Ashlyn</a></th>" \
               "<td class='gScore'>2 : 1<br/><span>(4-6, 6-4, 7-6<sup>8</sup>)</span></td>" \
               "<th class='plName'><a href='/player/dabrowski-9c70b'>Dabrowski Gabriela</a></th></tr></thead>" \
               "<tbody><tr><td class='tr'>610.</td><th>Doubles ranking</th><td class='tl'>3.</td></tr></tbody></table></div></body></html>"
        d = parse.parse_detail_page(html)
        self.assertIsNone(d["date"])
        self.assertEqual(d["date_word"], "today")
        self.assertEqual(d["time_site"], "00:15")
        self.assertEqual(d["round"], "semifinal")
        self.assertEqual(d["home"]["partner"], "Montgomery")
        self.assertEqual(d["away"]["partner"], "Stefani")
        self.assertEqual(d["home"]["doubles_rank"], 610)
        self.assertEqual(d["away"]["doubles_rank"], 3)
        self.assertEqual(d["final_score"], "2 : 1 (4-6, 6-4, 7-6(8))")

    def test_market_summary(self):
        books = [
            {"bookmaker": "A", "home": 1.6, "away": 2.25, "home_opening": 1.65, "away_opening": 2.2},
            {"bookmaker": "B", "home": 1.64, "away": 2.3, "home_opening": None, "away_opening": None},
            {"bookmaker": "C", "home": None, "away": 2.4, "home_opening": None, "away_opening": None},
        ]
        m = service.market_summary(books)
        self.assertEqual(m["books"], 2)
        self.assertEqual(m["consensus_home"], 1.62)
        self.assertEqual(m["consensus_away"], 2.275)
        self.assertAlmostEqual(m["fair_prob_home"] + m["fair_prob_away"], 1.0, places=3)
        self.assertGreater(m["fair_prob_home"], 0.55)
        self.assertGreater(m["overround"], 0)
        self.assertEqual(m["favorite"], "home")
        self.assertEqual(m["opening_consensus_home"], 1.65)
        self.assertLess(m["prob_shift_home"], 0.02)
        self.assertGreater(m["prob_shift_home"], 0)
        empty = service.market_summary([])
        self.assertEqual(empty["books"], 0)
        self.assertIsNone(empty["fair_prob_home"])

    def test_detail_drift_raises(self):
        with self.assertRaises(ParseDrift):
            parse.parse_detail_page("<html><body><div id='center'><h1>x</h1></div></body></html>")


class FakeClient:
    def __init__(self, list_html: str, detail: dict[int, str]):
        self.list_html = list_html
        self.detail = detail
        self.requests_made = 0

    def list_page(self, kind, day, tour="all", doubles=False):
        self.requests_made += 1
        return self.list_html

    def detail_page(self, match_id):
        self.requests_made += 1
        if match_id not in self.detail:
            raise RuntimeError("boom")
        return self.detail[match_id]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient(fixture("schedule_2026-09-10.html"), {3317719: fixture("detail_3317719.html"), 3317637: fixture("detail_3317637.html")})

    def test_filters(self):
        all_rows = service.list_matches(self.client, kind="schedule", start=date(2026, 9, 10), max_matches=1000)
        self.assertTrue(all(m["tournament"]["format"] == "singles" for m in all_rows))
        atp = service.list_matches(self.client, kind="schedule", start=date(2026, 9, 10), tour="atp", max_matches=1000)
        self.assertTrue(atp and all(m["tournament"]["tour"] == "atp" for m in atp))
        doubles = service.list_matches(self.client, kind="schedule", start=date(2026, 9, 10), doubles=True, max_matches=1000)
        self.assertTrue(doubles and all(m["tournament"]["format"] == "doubles" for m in doubles))
        by_player = service.list_matches(self.client, kind="schedule", start=date(2026, 9, 10), player="braund", max_matches=1000)
        self.assertEqual([m["match_id"] for m in by_player], [3317719])
        by_tourn = service.list_matches(self.client, kind="schedule", start=date(2026, 9, 10), tournament="utr pro", max_matches=3)
        self.assertEqual(len(by_tourn), 3)
        self.assertTrue(all("UTR" in m["tournament"]["name"] for m in by_tourn))
        self.assertEqual(by_tourn[0]["start_utc"], "2026-09-09T23:00:00Z")

    def test_live_mode(self):
        rows = service.list_matches(self.client, kind="live", start=date(2026, 9, 10), max_matches=100)
        self.assertTrue(rows)
        self.assertTrue(all(r["status"] == "in_progress" for r in rows))
        self.assertTrue(all(r["result"]["sets"] for r in rows))
        self.assertEqual(self.client.requests_made, 2)  # yesterday + today on the site's calendar
        self.assertRegex(rows[0]["fetched_at_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_days_dedupes(self):
        rows = service.list_matches(self.client, kind="schedule", start=date(2026, 9, 10), days=2, max_matches=10000)
        ids = [m["match_id"] for m in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.client.requests_made, 2)

    def test_enrich_ok_and_failures(self):
        rows = service.list_matches(self.client, kind="schedule", start=date(2026, 9, 10), player="braund")
        rows.append({"match_id": 3317637, "home": {"name": "Tiafoe F."}, "away": {"name": "Michelsen A."}, "date": "2026-09-08", "time_site": None})
        rows.append({"match_id": 999, "home": {"name": "a"}, "away": {"name": "b"}, "date": "2026-09-08"})
        rows.append({"match_id": None, "home": {"name": "a"}, "away": {"name": "b"}, "date": "2026-09-08"})
        n = service.enrich_matches(self.client, rows, latest_limit=3)
        self.assertEqual(n, 2)
        e = rows[0]["enrichment"]
        self.assertEqual(e["status"], "ok")
        self.assertEqual(rows[0]["surface"], "hard")
        self.assertEqual(e["h2h"]["home_wins"], 1)
        self.assertEqual(e["bookmaker_count"], 2)
        self.assertEqual(e["best_odds"]["home_bookmaker"], "Betway")
        self.assertEqual(e["home"]["form"]["played"], 3)
        big = rows[1]
        self.assertEqual(big["round"], "quarterfinal")
        self.assertEqual(big["time_site"], "20:25")
        self.assertEqual(big["start_utc"], "2026-09-08T18:25:00Z")
        self.assertEqual(big["enrichment"]["home"]["singles_rank"], 12)
        self.assertEqual(rows[2]["enrichment"]["status"], "fetch_failed")
        self.assertEqual(rows[3]["enrichment"]["status"], "no_match_id")

    def test_enrich_limit(self):
        rows = service.list_matches(self.client, kind="schedule", start=date(2026, 9, 10), tournament="utr pro", max_matches=3)
        before = self.client.requests_made
        service.enrich_matches(self.client, rows, limit=1)
        self.assertEqual(self.client.requests_made - before, 1)
        self.assertIn("enrichment", rows[0])
        self.assertNotIn("enrichment", rows[1])

    def test_source_drift_on_detail_layout_change(self):
        client = FakeClient(fixture("schedule_2026-09-10.html"), {1: "<html><body><div id='center'><h1>x</h1></div></body></html>", 2: "<html></html>", 3: "<p></p>"})
        rows = [{"match_id": i, "home": {"name": "a"}, "away": {"name": "b"}, "date": "2026-09-10"} for i in (1, 2, 3)]
        with self.assertRaises(service.SourceDrift):
            service.enrich_matches(client, rows)

    def test_list_drift(self):
        client = FakeClient("<html><body>down</body></html>", {})
        with self.assertRaises(service.SourceDrift):
            service.list_matches(client, kind="schedule", start=date(2026, 9, 10))


if __name__ == "__main__":
    unittest.main()
