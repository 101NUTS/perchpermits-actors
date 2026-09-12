"""Offline tests for the ladder-analysis event: no network, no model. The model
call is replaced by a fake caller; the test checks what the actor does around it."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kalshi_weather import preview, service  # noqa: E402
from tests.test_offline import FakeNws, open_kalshi  # noqa: E402


def enriched_rows() -> list[dict]:
    rows = service.list_events(open_kalshi(), kind="high", stations=["NYC"])
    service.enrich_events(FakeNws(), rows)
    return rows


class CompactTests(unittest.TestCase):
    def test_compact_keeps_the_signal_and_drops_the_bulk(self):
        row = enriched_rows()[0]
        c = preview.compact(row)
        text = json.dumps(c)
        self.assertNotIn("http", text)
        self.assertNotIn("rules_primary", text)
        self.assertEqual(c["event_ticker"], row["event_ticker"])
        self.assertEqual(len(c["strikes"]), len(row["markets"]))
        self.assertEqual(c["market_summary"], row["market_summary"])
        self.assertEqual(c["analysis"], row["enrichment"]["analysis"])
        self.assertIn("max_f", c["observations"])
        self.assertLess(len(text), len(json.dumps(row)))

    def test_compact_survives_a_plain_row(self):
        c = preview.compact({"event_ticker": "X"})
        self.assertEqual(c["strikes"], [])
        self.assertIsNone(c["analysis"])
        self.assertIsNone(c["forecast"]["high_f"])


class AttachTests(unittest.TestCase):
    def test_limit_failures_and_untouched_rows(self):
        rows = [{"event_ticker": f"E{i}"} for i in range(5)]

        def fake(client, row):
            return None if row["event_ticker"] == "E1" else f"read {row['event_ticker']}"

        n = preview.attach_analyses(rows, client=object(), limit=3, caller=fake)
        self.assertEqual(n, 2)
        self.assertEqual(rows[0]["analysis_text"]["text"], "read E0")
        self.assertEqual(rows[0]["analysis_text"]["model"], preview.MODEL)
        self.assertIsNone(rows[1]["analysis_text"])       # asked, failed: present and None, not charged
        self.assertEqual(rows[2]["analysis_text"]["text"], "read E2")
        self.assertNotIn("analysis_text", rows[3])         # past the limit: untouched
        self.assertNotIn("analysis_text", rows[4])

    def test_zero_limit_calls_nothing(self):
        calls = []
        rows = [{"event_ticker": "E"}]
        n = preview.attach_analyses(rows, client=object(), limit=0, caller=lambda c, r: calls.append(r) or "x")
        self.assertEqual((n, calls), (0, []))
        self.assertNotIn("analysis_text", rows[0])


class ModelCallTests(unittest.TestCase):
    def test_text_of_reads_text_blocks_and_refusals(self):
        msg = SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=" An analysis. ")])
        self.assertEqual(preview._text_of(msg), "An analysis.")
        self.assertIsNone(preview._text_of(SimpleNamespace(stop_reason="refusal", content=[SimpleNamespace(type="text", text="no")])))

    def test_analyse_one_never_raises(self):
        class Boom:
            class beta:
                class messages:
                    @staticmethod
                    def create(**kw):
                        raise RuntimeError("api down")

        self.assertIsNone(preview.analyse_one(Boom(), {"event_ticker": "E"}))

    def test_analyse_one_request_shape(self):
        seen = {}

        class Fake:
            class beta:
                class messages:
                    @staticmethod
                    def create(**kw):
                        seen.update(kw)
                        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")])

        row = enriched_rows()[0]
        self.assertEqual(preview.analyse_one(Fake(), row), "ok")
        self.assertEqual(seen["model"], "claude-opus-5")
        self.assertEqual(seen["output_config"], {"effort": "low"})
        self.assertEqual(seen["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", seen["betas"])
        self.assertEqual(seen["system"][0]["cache_control"], {"type": "ephemeral"})
        body = json.loads(seen["messages"][0]["content"])
        self.assertEqual(body["event_ticker"], row["event_ticker"])


if __name__ == "__main__":
    unittest.main()
