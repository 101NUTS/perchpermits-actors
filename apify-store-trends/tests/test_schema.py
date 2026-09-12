"""Schema contract: fields may be added, never removed or renamed.

`SCHEMA_V1` is the promise printed in the README. If a change makes this test
fail, that change breaks every agent already calling the actor; bump the
version and keep the old fields instead.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.store_trends import service  # noqa: E402
from tests.test_offline import NOW, FakeClient, fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

SCHEMA_V1 = {
    "actor": {"row_type", "actor", "actor_id", "title", "target", "query_match", "categories", "users_total", "users_90d", "users_30d",
              "users_7d", "runs_30d", "fail_rate_30d", "rating", "reviews", "bookmarks", "price", "price_usd",
              "pricing_model", "notice", "days_since_last_run", "agentic_payments", "url", "gap_signals",
              "snapshot_at", "delta"},
    "actor_delta": {"users_30d_prev", "users_30d_change", "rating_prev", "fail_rate_prev", "is_new"},
    "target": {"row_type", "target", "target_kind", "hostility", "actors", "active_actors", "users_30d", "users_total",
               "leader", "leader_url", "leader_share", "leader_rating", "leader_reviews", "leader_fail_rate",
               "leader_price", "leader_signals", "weighted_rating", "best_rated", "best_rating", "broken_actors",
               "signals", "snapshot_at", "delta"},
    "target_delta": {"users_30d_prev", "users_30d_change", "actors_prev", "leader_prev", "is_new"},
}
GAP_SIGNALS = {"leader_failing", "unrated", "under_maintenance", "idle_14d", "low_rated"}
TARGET_SIGNALS = {"A_poorly_served", "B_big_actor_low_rated", "C_actor_failing_in_use", "D_incumbent_idle",
                  "E_thin_market", "F_low_hostility_demand", "G_monopoly"}


class SchemaTests(unittest.TestCase):
    def setUp(self):
        items = fixture("store_search_google_trends.json")["data"]["items"]
        client = FakeClient({("google trends", None, None, 0): items})
        self.rows, self.targets = service.run(client, mode="search", query="google trends", min_users_30d=0, max_actors=5000, now=NOW)

    def test_v1_fields_all_present(self):
        for r in self.rows:
            self.assertTrue(SCHEMA_V1["actor"] <= set(r), SCHEMA_V1["actor"] - set(r))
            self.assertTrue(SCHEMA_V1["actor_delta"] <= set(r["delta"]))
            self.assertTrue(set(r["gap_signals"]) <= GAP_SIGNALS, r["gap_signals"])
        self.assertTrue(self.targets)
        for t in self.targets:
            self.assertTrue(SCHEMA_V1["target"] <= set(t), SCHEMA_V1["target"] - set(t))
            self.assertTrue(SCHEMA_V1["target_delta"] <= set(t["delta"]))
            self.assertTrue(set(t["signals"]) <= TARGET_SIGNALS, t["signals"])
            self.assertIn(t["target_kind"], ("query", "site"))
            self.assertIn(t["hostility"], ("high", "med", "low"))
        self.assertEqual(self.targets[0]["target_kind"], "query")
        for r in self.rows:
            self.assertIn(r["query_match"], (True, False))
        full_rows = service.build_rows(fixture("store_search_google_trends.json")["data"]["items"], now=NOW, min_users_30d=0)
        self.assertTrue(all(r["query_match"] is None for r in full_rows))

    def test_fields_after_deltas_unchanged(self):
        prior = [dict(r, users_30d=1) for r in self.rows]
        service.apply_deltas(self.rows, self.targets, prior)
        for r in self.rows:
            self.assertEqual(set(r["delta"]), SCHEMA_V1["actor_delta"])
        for t in self.targets:
            self.assertEqual(set(t["delta"]), SCHEMA_V1["target_delta"])

    def test_rows_are_json_serializable(self):
        json.dumps(self.rows + self.targets)


class ActorFilesTests(unittest.TestCase):
    def test_input_schema_valid_with_defaults(self):
        s = json.loads((ROOT / ".actor" / "input_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(s["type"], "object")
        p = s["properties"]
        self.assertEqual(p["mode"]["default"], "search")
        self.assertEqual(p["mode"]["enum"], ["search", "full"])
        self.assertEqual(p["minUsers30d"]["default"], 5)
        self.assertEqual(p["maxActors"]["default"], 200)
        self.assertEqual(p["maxActors"]["maximum"], 5000)
        self.assertTrue(p["includeTargets"]["default"])
        self.assertEqual(p["requestDelaySeconds"]["default"], 0.2)
        self.assertIn("query", p)
        self.assertIn("snapshotDatasetName", p)
        for name, prop in p.items():
            self.assertIn("title", prop, name)
            self.assertIn("description", prop, name)
            self.assertIn("type", prop, name)

    def test_actor_json_limits(self):
        a = json.loads((ROOT / ".actor" / "actor.json").read_text(encoding="utf-8"))
        self.assertEqual(a["name"], "apify-store-trends")
        self.assertLess(len(a["title"]), 63)
        self.assertLess(len(a["description"]), 300)
        self.assertEqual(a["input"], "./input_schema.json")
        self.assertEqual(a["output"], "./output_schema.json")

    def test_dataset_and_output_schemas_are_json(self):
        json.loads((ROOT / ".actor" / "dataset_schema.json").read_text(encoding="utf-8"))
        json.loads((ROOT / ".actor" / "output_schema.json").read_text(encoding="utf-8"))

    def test_dataset_views_use_real_fields(self):
        ds = json.loads((ROOT / ".actor" / "dataset_schema.json").read_text(encoding="utf-8"))
        allowed = {"actors": SCHEMA_V1["actor"] | {f"delta.{k}" for k in SCHEMA_V1["actor_delta"]},
                   "targets": SCHEMA_V1["target"] | {f"delta.{k}" for k in SCHEMA_V1["target_delta"]}}
        for view, ok in allowed.items():
            fields = ds["views"][view]["transformation"]["fields"]
            self.assertTrue(set(fields) <= ok, set(fields) - ok)
            self.assertEqual(set(ds["views"][view]["display"]["properties"]), set(fields))

    def test_no_personal_strings(self):
        # hex so the strings themselves never appear in the repo, this file included
        bad_strings = [bytes.fromhex(h).decode() for h in ("6a6669677565726f61", "6669677565726f61", "6d6172696168")]
        for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.json")) + list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.sh")) + list(ROOT.rglob("Dockerfile")):
            if ".venv" in path.parts or "storage" in path.parts or "fixtures" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            for bad in bad_strings:
                self.assertNotIn(bad, text, path)


if __name__ == "__main__":
    unittest.main()
