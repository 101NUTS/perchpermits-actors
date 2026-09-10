"""Rank contractors by permit activity within a set of permits.

Groups by the licensed contractor when enrichment is present, otherwise by
the applicant on the open dataset. Owner-builders ("SELF CONTRACTOR ...")
are excluded because they are not a contractor anyone can hire.
"""

from __future__ import annotations

import statistics


def _key_and_name(rec: dict) -> tuple[str, str, str | None] | None:
    enr = rec.get("enrichment") or {}
    contractor = enr.get("contractor") or {}
    if contractor.get("company"):
        if contractor.get("is_owner_builder") or contractor["company"].upper().startswith("SELF CONTRACTOR"):
            return None
        name = contractor["company"]
        return name.upper(), name, contractor.get("license")
    applicant = rec.get("applicant")
    if not applicant or rec.get("applicant_is_owner"):
        return None
    return applicant.upper(), applicant, None


def rank(records: list[dict], *, min_permits: int = 1, limit: int = 100) -> list[dict]:
    groups: dict[str, dict] = {}
    for rec in records:
        kn = _key_and_name(rec)
        if not kn:
            continue
        key, name, license_no = kn
        g = groups.setdefault(
            key,
            {
                "contractor": name,
                "license": license_no,
                "permits": 0,
                "issued_permits": 0,
                "pending_applications": 0,
                "total_valuation": 0.0,
                "_valuations": [],
                "first_permit": None,
                "last_permit": None,
                "_zips": set(),
                "_scopes": {},
                "sample_permits": [],
            },
        )
        g["permits"] += 1
        g["issued_permits"] += rec.get("source") == "issued"
        g["pending_applications"] += rec.get("source") == "applications"
        if license_no and not g["license"]:
            g["license"] = license_no
        v = rec.get("valuation")
        if isinstance(v, (int, float)):
            g["total_valuation"] += v
            g["_valuations"].append(v)
        d = rec.get("date_issued") or rec.get("date_entered")
        if d:
            g["first_permit"] = min(filter(None, [g["first_permit"], d]))
            g["last_permit"] = max(filter(None, [g["last_permit"], d]))
        if rec.get("zip"):
            g["_zips"].add(rec["zip"])
        for s in rec.get("scope_tags") or []:
            g["_scopes"][s] = g["_scopes"].get(s, 0) + 1
        if len(g["sample_permits"]) < 3:
            g["sample_permits"].append(
                {
                    "permit_number": rec.get("permit_number"),
                    "date": d,
                    "address": rec.get("address"),
                    "valuation": v,
                    "description": (rec.get("description") or "")[:160] or None,
                }
            )

    out = []
    for g in groups.values():
        if g["permits"] < min_permits:
            continue
        vals = g.pop("_valuations")
        g["median_valuation"] = statistics.median(vals) if vals else None
        g["total_valuation"] = round(g["total_valuation"], 2)
        g["zips"] = sorted(g.pop("_zips"))
        scopes = g.pop("_scopes")
        g["scopes"] = sorted(scopes, key=scopes.get, reverse=True)
        out.append(g)
    out.sort(key=lambda g: (g["permits"], g["total_valuation"]), reverse=True)
    for i, g in enumerate(out, 1):
        g["rank"] = i
    return out[:limit]
