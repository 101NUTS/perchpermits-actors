"""Offline tests for the canary's thresholds: no network, just the rules."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.canary import EARLY_UTC_HOUR, UTC_FLOOR_EARLY, utc_required  # noqa: E402


class UtcCoverageThreshold(unittest.TestCase):
    def test_early_hours_need_a_floor_count_not_a_share(self):
        need, rule = utc_required(20, 11)
        self.assertEqual(need, UTC_FLOOR_EARLY)
        self.assertIn("before 16:00 UTC", rule)

    def test_from_sixteen_utc_the_seventy_percent_share_applies(self):
        need, rule = utc_required(20, 16)
        self.assertEqual(need, 14)
        self.assertIn("70%", rule)

    def test_boundary_hour_is_the_first_strict_hour(self):
        self.assertEqual(utc_required(20, EARLY_UTC_HOUR - 1)[0], UTC_FLOOR_EARLY)
        self.assertEqual(utc_required(20, EARLY_UTC_HOUR)[0], 14)

    def test_share_rounds_up_so_a_fraction_short_still_fails(self):
        # 25 rows: 70% is 17.5, so 17 is short and 18 passes.
        self.assertEqual(utc_required(25, 19)[0], 18)

    def test_floor_never_exceeds_the_rows_listed(self):
        self.assertEqual(utc_required(3, 11)[0], 3)
        self.assertEqual(utc_required(0, 11)[0], 0)

    def test_2026_09_14_monday_morning_run_now_passes(self):
        # The 11:00 UTC canary saw 13/20 with start_utc; the 70% share (14) failed it.
        need, _ = utc_required(20, 11)
        self.assertGreaterEqual(13, need)

    def test_2026_09_14_afternoon_rerun_still_held_to_the_share(self):
        # 15:43 UTC is still in the early window; at 16:00 the same 18/25 must clear 70%.
        self.assertGreaterEqual(18, utc_required(25, 15)[0])
        self.assertGreaterEqual(18, utc_required(25, 16)[0])

    def test_a_total_time_parsing_break_still_trips_in_the_morning(self):
        self.assertLess(0, utc_required(20, 11)[0])


if __name__ == "__main__":
    unittest.main()
