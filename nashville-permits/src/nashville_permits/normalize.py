"""Turn a raw ArcGIS attribute dict into the record an agent receives.

Field names are stable, snake_case, and documented in README.md. Missing
values are null, never guessed.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .scopes import classify, is_residential


def to_date(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    s = str(value).strip()
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else None


def clean(value) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _int(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def normalize(attrs: dict, layer: str) -> dict:
    purpose = clean(attrs.get("Purpose"))
    ptype = clean(attrs.get("Permit_Type_Description"))
    psub = clean(attrs.get("Permit_Subtype_Description"))
    text = " ".join(filter(None, [ptype, psub, purpose]))
    applicant = clean(attrs.get("Contact"))
    return {
        "permit_number": clean(attrs.get("Permit__")),
        "source": layer,
        "status": "issued" if layer == "issued" else "applied",
        "date_entered": to_date(attrs.get("Date_Entered")),
        "date_issued": to_date(attrs.get("Date_Issued")),
        "permit_type": ptype,
        "permit_subtype": psub,
        "type_code": clean(attrs.get("Per_Ty")),
        "subtype_code": clean(attrs.get("Per_SubTy")),
        "description": purpose,
        "address": clean(attrs.get("Address")),
        "city": clean(attrs.get("City")),
        "state": clean(attrs.get("State")) or "TN",
        "zip": clean(attrs.get("ZIP")),
        "parcel": clean(attrs.get("Parcel")),
        "subdivision_lot": clean(attrs.get("Subdivision_Lot")),
        "council_district": _int(attrs.get("Council_Dist")),
        "lon": _num(attrs.get("Lon")) or None,
        "lat": _num(attrs.get("Lat")) or None,
        "valuation": _num(attrs.get("Const_Cost")),
        # "Contact" is the paperwork filer. The licensed contractor of record
        # lives in ePermits and only appears under `enrichment` when requested.
        "applicant": applicant,
        "applicant_is_owner": bool(applicant and applicant.upper().startswith("SELF CONTRACTOR")),
        "is_residential": is_residential(text),
        "scope_tags": classify(text),
        "ivr_track": _int(attrs.get("IVR_Trk_")),
        "enrichment": None,
    }
