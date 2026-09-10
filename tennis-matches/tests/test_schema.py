"""Schema contract: fields may be added, never removed or renamed.

`SCHEMA_V1` is the promise printed in the README. If a change makes this test
fail, that change breaks every agent already calling the actor; bump the
version and keep the old fields instead.
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tennis_matches import parse, service  # noqa: E402
from tests.test_offline import FakeClient, fixture  # noqa: E402

SCHEMA_V1 = {
    "match": {"match_id", "match_url", "date", "time_site", "start_utc", "fetched_at_utc", "list_kind", "status",
              "tournament", "home", "away", "odds", "h2h_list", "result"},
    "tournament": {"name", "url", "slug", "year", "tour", "format"},
    "player": {"name", "slug", "url", "seed"},
    "result": {"winner", "sets_home", "sets_away", "sets", "score"},
    "enrichment": {"status", "home", "away", "surface_record_label", "h2h", "bookmakers", "bookmaker_count",
                   "best_odds", "market", "final_score"},
    "enrichment_player": {"full_name", "slug", "url", "singles_rank", "birthdate", "height", "weight", "plays",
                          "turned_pro", "surface_record", "form", "latest_matches"},
    "form": {"played", "wins", "losses", "streak", "sequence"},
    "latest_match": {"tournament", "tournament_url", "round", "round_full", "date", "won", "player", "opponent",
                     "sets", "score", "match_id"},
    "h2h": {"home_wins", "away_wins", "matches"},
    "bookmaker": {"bookmaker", "home", "away", "home_opening", "away_opening", "home_trend", "away_trend"},
    "market": {"books", "consensus_home", "consensus_away", "fair_prob_home", "fair_prob_away", "overround",
               "opening_consensus_home", "opening_consensus_away", "opening_fair_prob_home", "prob_shift_home",
               "favorite"},
    "best_odds": {"home", "home_bookmaker", "away", "away_bookmaker"},
}


class SchemaTests(unittest.TestCase):
    def test_v1_fields_all_present(self):
        client = FakeClient(fixture("schedule_2026-09-10.html"), {3317719: fixture("detail_3317719.html")})
        rows = service.list_matches(client, kind="schedule", start=date(2026, 9, 10), player="braund")
        service.enrich_matches(client, rows)
        m = rows[0]
        self.assertTrue(SCHEMA_V1["match"] <= set(m), SCHEMA_V1["match"] - set(m))
        self.assertTrue(SCHEMA_V1["tournament"] <= set(m["tournament"]))
        self.assertTrue(SCHEMA_V1["player"] <= set(m["home"]))
        self.assertTrue(SCHEMA_V1["result"] <= set(m["result"]))
        e = m["enrichment"]
        self.assertTrue(SCHEMA_V1["enrichment"] <= set(e), SCHEMA_V1["enrichment"] - set(e))
        self.assertTrue(SCHEMA_V1["enrichment_player"] <= set(e["home"]), SCHEMA_V1["enrichment_player"] - set(e["home"]))
        self.assertTrue(SCHEMA_V1["form"] <= set(e["home"]["form"]))
        self.assertTrue(SCHEMA_V1["latest_match"] <= set(e["home"]["latest_matches"][0]))
        self.assertTrue(SCHEMA_V1["h2h"] <= set(e["h2h"]))
        self.assertTrue(SCHEMA_V1["bookmaker"] <= set(e["bookmakers"][0]))
        self.assertTrue(SCHEMA_V1["market"] <= set(e["market"]))
        self.assertTrue(SCHEMA_V1["best_odds"] <= set(e["best_odds"]))

    def test_form_summary_is_pure(self):
        self.assertEqual(parse.summarize_form([{"won": True}, {"won": False}])["sequence"], "WL")


if __name__ == "__main__":
    unittest.main()
