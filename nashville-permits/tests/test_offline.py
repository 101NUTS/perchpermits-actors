"""Offline tests: no network. Run with `python -m pytest` or `python -m unittest`."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nashville_permits import arcgis, epermits, rank  # noqa: E402
from src.nashville_permits.normalize import normalize  # noqa: E402
from src.nashville_permits.scopes import SCOPES, classify, is_residential  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


class ScopeTests(unittest.TestCase):
    def test_kitchen_ignores_boilerplate(self):
        self.assertFalse(SCOPES["kitchen"].matches("Single family residence. Not to add a second kitchen."))
        self.assertTrue(SCOPES["kitchen"].matches("Interior remodel of existing kitchen and bath, new cabinets."))

    def test_pool_not_carpool(self):
        self.assertFalse(SCOPES["pool"].matches("Carpool lane striping"))
        self.assertTrue(SCOPES["pool"].matches("Residential - Swimming Pool in-ground gunite pool"))

    def test_sign_not_design(self):
        self.assertFalse(SCOPES["sign"].matches("Interior design finish out"))
        self.assertTrue(SCOPES["sign"].matches("Wall sign for tenant"))

    def test_classify_multi(self):
        tags = classify("Residential - Rehab. Kitchen remodel, new roof shingles, add deck.")
        for t in ("kitchen", "roofing", "deck", "interior_remodel"):
            self.assertIn(t, tags)

    def test_residential(self):
        self.assertTrue(is_residential("Single family residence rehab"))
        self.assertFalse(is_residential("Commercial tenant finish out"))
        self.assertIsNone(is_residential("Install sign"))


class NormalizeTests(unittest.TestCase):
    def test_record_shape(self):
        attrs = {
            "Permit__": "2026045225", "Permit_Type_Description": "Building Residential - Rehab",
            "Permit_Subtype_Description": "Single Family", "Parcel": "08309024300",
            "Date_Entered": 1749513600000, "Date_Issued": 1749600000000, "Const_Cost": 45000.0,
            "Address": "1234 WOODLAND ST", "City": "NASHVILLE", "State": "TN", "ZIP": "37206",
            "Contact": "ACME BUILDERS LLC", "Per_Ty": "CARR", "Per_SubTy": "CARR01",
            "IVR_Trk_": 4734385, "Purpose": "Kitchen  remodel, no change to footprint.",
            "Council_Dist": 6.0, "Lon": -86.74, "Lat": 36.18,
        }
        rec = normalize(attrs, "issued")
        self.assertEqual(rec["permit_number"], "2026045225")
        self.assertEqual(rec["status"], "issued")
        self.assertEqual(rec["date_issued"], "2025-06-11")
        self.assertEqual(rec["valuation"], 45000.0)
        self.assertEqual(rec["council_district"], 6)
        self.assertEqual(rec["description"], "Kitchen remodel, no change to footprint.")
        self.assertIn("kitchen", rec["scope_tags"])
        self.assertTrue(rec["is_residential"])
        self.assertFalse(rec["applicant_is_owner"])
        self.assertIsNone(rec["enrichment"])

    def test_owner_builder_flag(self):
        rec = normalize({"Contact": "SELF CONTRACTOR / OWNER"}, "applications")
        self.assertTrue(rec["applicant_is_owner"])
        self.assertEqual(rec["status"], "applied")


class WhereTests(unittest.TestCase):
    def test_escapes_and_fields(self):
        w = arcgis.build_where(
            "issued", date_from="2026-01-01", date_to="2026-02-01", keyword="o'brien",
            zips=["37206", "37216"], min_valuation=1000, server_like=["KITCHEN"], parcel="A'B",
        )
        self.assertIn("Date_Issued >= DATE '2026-01-01'", w)
        self.assertIn("UPPER(Purpose) LIKE '%O''BRIEN%'", w)
        self.assertIn("ZIP IN ('37206','37216')", w)
        self.assertIn("Parcel = 'A''B'", w)
        self.assertIn("Const_Cost >= 1000.0", w)

    def test_applications_use_entered_date(self):
        self.assertIn("Date_Entered >=", arcgis.build_where("applications", date_from="2026-01-01"))

    def test_empty(self):
        self.assertEqual(arcgis.build_where("issued"), "1=1")


class EnrichExtractTests(unittest.TestCase):
    def test_extract_fixture(self):
        raw = json.loads((FIX / "epermits_4734385.json").read_text(encoding="utf-8"))
        block = epermits.extract(raw)
        self.assertEqual(block["status"], "ok")
        self.assertEqual(block["case"]["case_number"], "2025056396")
        self.assertIsNotNone(block["contractor"]["company"])
        self.assertTrue(any(s["satisfied"] for s in block["sub_trade_permits"]))
        self.assertIsInstance(block["outstanding_sub_trades"], list)
        self.assertIn(block["inspection_stage"] is not None, (True,))

    def test_partial_and_failed(self):
        self.assertEqual(epermits.extract({"case": None, "contractors": None, "people": None, "conditions": None, "tasks": None})["status"], "failed")
        self.assertEqual(epermits.extract({"case": {"value": []}, "contractors": None, "people": {"value": []}, "conditions": {"value": []}, "tasks": {"value": []}})["status"], "partial")

    def test_dircache_roundtrip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            c = epermits.DirCache(d)
            self.assertIsNone(c.get("case-1"))
            c.set("case-1", {"a": 1})
            self.assertEqual(c.get("case-1"), {"a": 1})


class DriftGuardTests(unittest.TestCase):
    def test_fixture_has_no_shape_issues(self):
        raw = json.loads((FIX / "epermits_4734385.json").read_text(encoding="utf-8"))
        self.assertEqual(epermits.shape_issues(raw), [])

    def test_renamed_field_is_reported(self):
        raw = json.loads((FIX / "epermits_4734385.json").read_text(encoding="utf-8"))
        for row in raw["contractors"]["value"]:
            row["licenseNo"] = row.pop("licenseNumber")
        issues = epermits.shape_issues(raw)
        self.assertEqual(len(issues), 1)
        self.assertIn("contractors", issues[0])
        self.assertIn("licenseNumber", issues[0])

    def test_transport_failure_is_not_a_shape_issue(self):
        raw = {ep: None for ep in epermits.ENDPOINTS}
        self.assertEqual(epermits.shape_issues(raw), [])
        self.assertEqual(epermits.shape_issues({"case": {"nope": 1}})[0], "case: response has no 'value' list")

    def test_check_schema_detects_missing_field(self):
        original = arcgis._get_json
        try:
            arcgis._get_json = lambda url: {"fields": [{"name": f} for f in arcgis.FIELDS if f != "IVR_Trk_"]}
            problems = arcgis.check_schema(["issued"])
            self.assertEqual(len(problems), 1)
            self.assertIn("IVR_Trk_", problems[0])
            arcgis._get_json = lambda url: {"fields": [{"name": f} for f in arcgis.FIELDS]}
            self.assertEqual(arcgis.check_schema(["issued", "applications"]), [])
            arcgis._get_json = lambda url: {"error": "gone"}
            self.assertTrue(arcgis.check_schema(["issued"]))
        finally:
            arcgis._get_json = original

    def test_enrich_permits_raises_on_drift(self):
        from src.nashville_permits import service

        original = service.enrich_one
        try:
            service.enrich_one = lambda ivr, cache, **kw: {
                "status": "ok", "source": "live", "schema_issues": ["contractors: fields missing: licenseNumber"],
            }
            with self.assertRaises(service.EnrichmentDrift):
                service.enrich_permits([{"ivr_track": 1}], None)
            service.enrich_one = lambda ivr, cache, **kw: {"status": "failed", "source": "live", "schema_issues": []}
            with self.assertRaises(service.EnrichmentDrift):
                service.enrich_permits([{"ivr_track": i} for i in range(4)], None)
            # A single failed permit among cached successes is not drift.
            calls = iter(["failed", "ok", "ok"])
            service.enrich_one = lambda ivr, cache, **kw: {"status": next(calls), "source": "cache", "schema_issues": []}
            self.assertEqual(service.enrich_permits([{"ivr_track": i} for i in range(1, 4)], None), 3)
        finally:
            service.enrich_one = original


class RankTests(unittest.TestCase):
    def test_rank_groups_and_skips_owners(self):
        recs = [
            {"applicant": "Acme Builders", "applicant_is_owner": False, "valuation": 10000, "date_issued": "2026-01-02", "zip": "37206", "scope_tags": ["kitchen"], "source": "issued", "permit_number": "1", "address": "A", "description": "x"},
            {"applicant": "ACME BUILDERS", "applicant_is_owner": False, "valuation": 30000, "date_issued": "2026-03-02", "zip": "37216", "scope_tags": ["bathroom"], "source": "applications", "permit_number": "2", "address": "B", "description": "y"},
            {"applicant": "SELF CONTRACTOR / OWNER", "applicant_is_owner": True, "valuation": 5000, "date_issued": "2026-02-02", "zip": "37206", "scope_tags": [], "source": "issued", "permit_number": "3", "address": "C", "description": "z"},
            {"applicant": None, "applicant_is_owner": False, "enrichment": {"contractor": {"company": "Licensed Co", "license": "MCN1"}}, "valuation": 1, "date_issued": "2026-02-02", "zip": "37206", "scope_tags": [], "source": "issued", "permit_number": "4", "address": "D", "description": "w"},
        ]
        rows = rank.rank(recs)
        self.assertEqual(rows[0]["contractor"], "Acme Builders")
        self.assertEqual(rows[0]["permits"], 2)
        self.assertEqual(rows[0]["median_valuation"], 20000)
        self.assertEqual(rows[0]["first_permit"], "2026-01-02")
        self.assertEqual(rows[0]["zips"], ["37206", "37216"])
        self.assertEqual(rows[0]["issued_permits"], 1)
        self.assertEqual(rows[0]["pending_applications"], 1)
        self.assertEqual([r["contractor"] for r in rows], ["Acme Builders", "Licensed Co"])
        self.assertEqual(rows[1]["license"], "MCN1")
        self.assertEqual(rank.rank(recs, min_permits=2)[0]["rank"], 1)
        self.assertEqual(len(rank.rank(recs, min_permits=2)), 1)

    def test_merge_key_collapses_spelling_variants(self):
        for a, b in [
            ("RONDO POOLS LLC", "Rondo Pools, LLC."),
            ("RONDO POOLS, L.L.C.", "Rondo Pools Inc"),
            ("Smith & Sons Roofing Co", "SMITH SONS ROOFING"),
        ]:
            self.assertEqual(rank._merge_key(a), rank._merge_key(b), (a, b))
        self.assertNotEqual(rank._merge_key("Rondo Pools"), rank._merge_key("Rondo Roofing"))
        self.assertEqual(rank._merge_key("LLC"), "LLC")  # never an empty key

    def test_rank_merges_variants_and_drops_placeholders(self):
        base = {"applicant_is_owner": False, "valuation": 1000, "date_issued": "2026-01-02", "zip": "37206", "scope_tags": [], "source": "issued", "address": "A", "description": "x"}
        recs = [
            dict(base, applicant="Rondo Pools, LLC.", permit_number="1"),
            dict(base, applicant="RONDO POOLS LLC", permit_number="2"),
            dict(base, applicant="Rondo Pools Inc", permit_number="3"),
            dict(base, applicant="Blue Water Pools", permit_number="4"),
            dict(base, applicant="Blue Water Pools", permit_number="5"),
            dict(base, applicant="See ePermits", permit_number="6"),
            dict(base, applicant="not published", permit_number="7"),
            dict(base, applicant=None, enrichment={"contractor": {"company": "Owner is contractor"}}, permit_number="8"),
        ]
        rows = rank.rank(recs)
        self.assertEqual([(r["contractor"], r["permits"]) for r in rows], [("Rondo Pools, LLC.", 3), ("Blue Water Pools", 2)])


if __name__ == "__main__":
    unittest.main()
