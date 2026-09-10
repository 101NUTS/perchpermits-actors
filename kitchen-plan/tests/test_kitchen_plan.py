"""Offline tests over the bundled example specs."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.kitchen_plan import to_metres, validate  # noqa: E402
from src.kitchen_plan.service import SpecError, build  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
SPECS = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in FIX.glob("*.json")}


class FixtureTests(unittest.TestCase):
    def test_every_fixture_builds(self):
        self.assertGreaterEqual(len(SPECS), 5)
        for name, spec in SPECS.items():
            with self.subTest(name=name):
                r = build(spec)
                self.assertTrue(r["svg"].startswith("<svg"))
                self.assertIn("</svg>", r["svg"])
                self.assertGreater(len(r["rows"]), 3)
                self.assertTrue(r["csv"].startswith("location,item,width_in"))
                self.assertGreater(r["summary"]["floor_area_sqft"] if "floor_area_sqft" in r["summary"] else r["summary"]["room"]["floor_area_sqft"], 20)

    def test_kitchen_summary_numbers(self):
        s = build(SPECS["kitchen_example"])["summary"]
        self.assertEqual(s["room_type"], "kitchen")
        self.assertIn("refrigerator", s["appliances"])
        self.assertIn("range", s["appliances"])
        self.assertGreater(s["base_cabinet_linear_ft"], 5)
        self.assertGreater(s["upper_cabinet_linear_ft"], 3)
        self.assertGreater(s["countertop_sqft"], 15)
        strips = sum(i["count"] for i in s["item_counts"] if i["item"] == "Under-cabinet light strip")
        self.assertEqual(s["light_fixtures"], 2 + strips)

    def test_bathroom_summary(self):
        s = build(SPECS["test_bathroom"])["summary"]
        self.assertEqual(s["room_type"], "bathroom")
        self.assertIn("alcove tub", s["fixtures"])
        self.assertIn("toilet", s["fixtures"])
        self.assertGreater(s["wall_tile_sqft"], 10)

    def test_features_spec_has_peninsula_and_corner(self):
        r = build(SPECS["test_features"])
        items = {row["item"] for row in r["rows"]}
        self.assertIn("Peninsula base, door fronts one side", items)
        self.assertIn("Lazy-susan corner base, 45-degree door", items)
        self.assertIn("PENINSULA", r["svg"])
        self.assertEqual(r["summary"]["seating"], 3)


class IntegrityGuardTests(unittest.TestCase):
    def test_good_build_has_no_problems(self):
        from src.kitchen_plan.service import verify_outputs

        for spec in SPECS.values():
            self.assertEqual(verify_outputs(build(spec)), [])

    def test_broken_outputs_are_caught(self):
        from src.kitchen_plan.service import verify_outputs

        good = build(SPECS["kitchen_example"])
        bad = dict(good, svg="<svg><rect></svg>")
        self.assertTrue(any("well-formed" in p for p in verify_outputs(bad)))
        bad = dict(good, csv=good["csv"].splitlines()[0] + "\n")
        self.assertTrue(any("CSV has 0 rows" in p for p in verify_outputs(bad)))
        bad = dict(good, summary=dict(good["summary"], line_items=1))
        self.assertTrue(any("line_items" in p for p in verify_outputs(bad)))
        bad = dict(good, rows=[dict(good["rows"][0], width_in=0)] + good["rows"][1:])
        self.assertTrue(any("bad width" in p for p in verify_outputs(bad)))

    def test_build_raises_integrity_error(self):
        from src.kitchen_plan import service

        original = service.render_svg
        try:
            service.render_svg = lambda spec, title=None: "<svg><rect></svg>"
            with self.assertRaises(service.OutputIntegrityError):
                build(SPECS["kitchen_example"])
        finally:
            service.render_svg = original


class UnitTests(unittest.TestCase):
    def test_feet_round_trip(self):
        spec = copy.deepcopy(SPECS["kitchen_example"])
        ft = to_metres(spec, "m")  # identity
        ft["room"] = {k: round(v / 0.3048, 4) for k, v in ft["room"].items()}
        for run in ft["cabinet_runs"]:
            run["start"], run["end"] = round(run["start"] / 0.3048, 4), round(run["end"] / 0.3048, 4)
            for a in run["appliances"]:
                a["at"], a["width"] = round(a["at"] / 0.3048, 4), round(a["width"] / 0.3048, 4)
        for w in ft["windows"]:
            for k in ("u0", "u1", "z0", "z1"):
                w[k] = round(w[k] / 0.3048, 4)
        for k in ("x", "y", "width", "depth"):
            ft["island"][k] = round(ft["island"][k] / 0.3048, 4)
        back = to_metres(ft, "ft")
        self.assertAlmostEqual(back["room"]["width"], spec["room"]["width"], places=3)
        self.assertAlmostEqual(back["cabinet_runs"][0]["appliances"][1]["at"], 1.9, places=3)
        self.assertEqual(len(build(ft, "ft")["rows"]), len(build(spec)["rows"]))

    def test_bad_units(self):
        with self.assertRaises(ValueError):
            to_metres({}, "cm")


class ValidationTests(unittest.TestCase):
    def _spec(self, **changes):
        spec = copy.deepcopy(SPECS["kitchen_example"])
        spec.update(changes)
        return spec

    def test_valid_examples_have_no_errors(self):
        for name, spec in SPECS.items():
            errors, _ = validate(spec)
            self.assertEqual(errors, [], name)

    def test_missing_room(self):
        self.assertTrue(validate({"cabinet_runs": []})[0])

    def test_appliance_outside_run(self):
        spec = self._spec()
        spec["cabinet_runs"][0]["appliances"][0]["at"] = 4.0
        errors, _ = validate(spec)
        self.assertTrue(any("outside the run" in e for e in errors))

    def test_overlap(self):
        spec = self._spec()
        spec["cabinet_runs"][0]["appliances"][1]["at"] = 0.5
        errors, _ = validate(spec)
        self.assertTrue(any("overlaps" in e for e in errors))

    def test_bad_wall_and_type(self):
        spec = self._spec()
        spec["cabinet_runs"][0]["wall"] = "up"
        spec["windows"][0]["wall"] = "north"
        errors, _ = validate(spec)
        self.assertTrue(any("wall must be one of" in e for e in errors))
        spec = self._spec()
        spec["cabinet_runs"][0]["appliances"][0]["type"] = "microwave"
        errors, _ = validate(spec)
        self.assertTrue(any("type must be one of" in e for e in errors))

    def test_bath_fixture_in_kitchen_run(self):
        spec = self._spec()
        spec["cabinet_runs"][0]["appliances"][0]["type"] = "toilet"
        errors, _ = validate(spec)
        self.assertTrue(any('"kind": "bath"' in e for e in errors))

    def test_island_outside_room(self):
        spec = self._spec(island={"x": 5.0, "y": 1.5, "width": 2.0, "depth": 0.9})
        errors, _ = validate(spec)
        self.assertTrue(any("outside the room" in e for e in errors))

    def test_island_clearance_warning(self):
        spec = self._spec(island={"x": 2.1, "y": 2.6, "width": 2.0, "depth": 0.9})
        errors, warnings = validate(spec)
        self.assertEqual(errors, [])
        self.assertTrue(any("clearance" in w for w in warnings))

    def test_units_warning(self):
        spec = self._spec()
        spec["room"] = {"width": 14, "depth": 12, "height": 9}
        _, warnings = validate(spec)
        self.assertTrue(any("check units" in w for w in warnings))

    def test_build_raises_spec_error(self):
        with self.assertRaises(SpecError) as cm:
            build({"room": {"width": 3, "depth": 3, "height": 2.5}})
        self.assertIn("at least one of", cm.exception.errors[0])


if __name__ == "__main__":
    unittest.main()
