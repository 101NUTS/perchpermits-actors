"""Offline tests for the match-preview event: no network, no model. The model
call is replaced by a fake caller; the test checks what the actor does around it."""

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tennis_matches import preview, service  # noqa: E402
from tests.test_offline import FakeClient, fixture  # noqa: E402


def enriched_rows() -> list[dict]:
    client = FakeClient(fixture("schedule_2026-09-10.html"), {3317719: fixture("detail_3317719.html")})
    rows = service.list_matches(client, kind="schedule", start=date(2026, 9, 10), player="braund")
    service.enrich_matches(client, rows)
    return rows


class CompactTests(unittest.TestCase):
    def test_compact_keeps_the_signal_and_drops_the_bulk(self):
        row = enriched_rows()[0]
        c = preview.compact(row)
        text = json.dumps(c)
        self.assertNotIn("http", text)
        self.assertNotIn("home_history", text)
        self.assertEqual(c["home"]["name"], row["home"]["name"])
        self.assertEqual(c["market"], row["enrichment"]["market"])
        self.assertLessEqual(len(c["home"]["latest_matches"]), 6)
        self.assertLessEqual(len(c["h2h"]["matches"]), 8)
        self.assertIn("surface_record", c["home"])
        self.assertLess(len(text), len(json.dumps(row)))

    def test_compact_survives_a_plain_row(self):
        c = preview.compact({"home": {"name": "A"}, "away": {"name": "B"}})
        self.assertIsNone(c["market"])
        self.assertEqual(c["home"]["latest_matches"], [])


class AttachTests(unittest.TestCase):
    def test_limit_failures_and_untouched_rows(self):
        rows = [{"match_id": i, "home": {"name": "A"}, "away": {"name": "B"}} for i in range(5)]

        def fake(client, row):
            return None if row["match_id"] == 1 else f"preview {row['match_id']}"

        n = preview.attach_previews(rows, client=object(), limit=3, caller=fake)
        self.assertEqual(n, 2)
        self.assertEqual(rows[0]["preview"]["text"], "preview 0")
        self.assertEqual(rows[0]["preview"]["model"], preview.MODEL)
        self.assertTrue(rows[0]["preview"]["generated_at"].endswith("Z"))
        self.assertIsNone(rows[1]["preview"])           # asked, failed: present and None, not charged
        self.assertEqual(rows[2]["preview"]["text"], "preview 2")
        self.assertNotIn("preview", rows[3])             # past the limit: untouched
        self.assertNotIn("preview", rows[4])

    def test_zero_limit_calls_nothing(self):
        calls = []
        rows = [{"match_id": 1}]
        n = preview.attach_previews(rows, client=object(), limit=0, caller=lambda c, r: calls.append(r) or "x")
        self.assertEqual((n, calls), (0, []))
        self.assertNotIn("preview", rows[0])


class ModelCallTests(unittest.TestCase):
    def test_text_of_reads_text_blocks_and_refusals(self):
        msg = SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="  A preview. ")])
        self.assertEqual(preview._text_of(msg), "A preview.")
        self.assertIsNone(preview._text_of(SimpleNamespace(stop_reason="refusal", content=[SimpleNamespace(type="text", text="no")])))
        self.assertIsNone(preview._text_of(SimpleNamespace(stop_reason="end_turn", content=[])))

    def test_preview_one_never_raises(self):
        class Boom:
            class beta:
                class messages:
                    @staticmethod
                    def create(**kw):
                        raise RuntimeError("api down")

        self.assertIsNone(preview.preview_one(Boom(), {"home": {}, "away": {}}))

    def test_preview_one_request_shape(self):
        seen = {}

        class Fake:
            class beta:
                class messages:
                    @staticmethod
                    def create(**kw):
                        seen.update(kw)
                        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")])

        row = enriched_rows()[0]
        self.assertEqual(preview.preview_one(Fake(), row), "ok")
        self.assertEqual(seen["model"], "claude-opus-5")
        self.assertEqual(seen["output_config"], {"effort": "low"})
        self.assertEqual(seen["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", seen["betas"])
        self.assertEqual(seen["system"][0]["cache_control"], {"type": "ephemeral"})
        body = json.loads(seen["messages"][0]["content"])
        self.assertEqual(body["home"]["name"], row["home"]["name"])

    def test_available_follows_the_env(self):
        import os
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            self.assertFalse(preview.available())
            os.environ["ANTHROPIC_API_KEY"] = "x"
            self.assertTrue(preview.available())
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
