"""Query the Metro Nashville permit feature services.

Two layers share one schema (verified live 2026-09-07):

    Permit__, Permit_Type_Description, Permit_Subtype_Description, Parcel,
    Date_Entered, Date_Issued, Const_Cost, Address, City, State,
    Subdivision_Lot, Contact, Per_Ty, Per_SubTy, IVR_Trk_, Purpose,
    Council_Dist, Census_Tract (issued only), Lon, Lat, ObjectId, ZIP

Both layers page at 1,000 records and support resultOffset pagination.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator

BASE = "https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services"
LAYERS = {
    "issued": f"{BASE}/Building_Permits_Issued_2/FeatureServer/0/query",
    "applications": f"{BASE}/Building_Permit_Applications_Feature_Layer_view/FeatureServer/0/query",
}
# The date that best describes activity on each layer. Applications have
# no Date_Issued until they become permits.
DATE_FIELD = {"issued": "Date_Issued", "applications": "Date_Entered"}

FIELDS = [
    "Permit__", "Permit_Type_Description", "Permit_Subtype_Description", "Parcel",
    "Date_Entered", "Date_Issued", "Const_Cost", "Address", "City", "State",
    "Subdivision_Lot", "Contact", "Per_Ty", "Per_SubTy", "IVR_Trk_", "Purpose",
    "Council_Dist", "Lon", "Lat", "ZIP",
]

HEADERS = {
    "User-Agent": "nashville-permits-actor/0.1 (public-records tool; contact: perchpermits@gmail.com)",
    "Accept": "application/json",
}
PAGE_SIZE = 1000
TIMEOUT = 90
RETRIES = 4


class ArcGISError(RuntimeError):
    pass


def _sql_str(value: str) -> str:
    """Quote a string literal for an ArcGIS WHERE clause."""
    return "'" + str(value).replace("'", "''") + "'"


def _like(field: str, needle: str) -> str:
    return f"UPPER({field}) LIKE {_sql_str('%' + needle.upper() + '%')}"


def build_where(
    layer: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    keyword: str | None = None,
    server_like: list[str] | None = None,
    address: str | None = None,
    parcel: str | None = None,
    zips: list[str] | None = None,
    applicant: str | None = None,
    permit_number: str | None = None,
    min_valuation: float | None = None,
    max_valuation: float | None = None,
    permit_type: str | None = None,
) -> str:
    """Assemble the server-side filter. Everything here is pushed to ArcGIS
    so the client only pages through candidates, not the whole table."""
    date_field = DATE_FIELD[layer]
    parts: list[str] = []
    if date_from:
        parts.append(f"{date_field} >= DATE '{date_from}'")
    if date_to:
        parts.append(f"{date_field} <= DATE '{date_to} 23:59:59'")
    if keyword:
        parts.append(_like("Purpose", keyword))
    if server_like:
        ors = " OR ".join(
            f"({_like('Purpose', s)} OR {_like('Permit_Subtype_Description', s)} OR {_like('Permit_Type_Description', s)})"
            for s in server_like
        )
        parts.append(f"({ors})")
    if address:
        parts.append(_like("Address", address))
    if parcel:
        parts.append(f"Parcel = {_sql_str(parcel)}")
    if zips:
        parts.append("ZIP IN (" + ",".join(_sql_str(z) for z in zips) + ")")
    if applicant:
        parts.append(_like("Contact", applicant))
    if permit_number:
        parts.append(f"Permit__ = {_sql_str(permit_number)}")
    if min_valuation is not None:
        parts.append(f"Const_Cost >= {float(min_valuation)}")
    if max_valuation is not None:
        parts.append(f"Const_Cost <= {float(max_valuation)}")
    if permit_type:
        parts.append(
            f"({_like('Permit_Type_Description', permit_type)} OR {_like('Permit_Subtype_Description', permit_type)})"
        )
    return " AND ".join(parts) if parts else "1=1"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.load(resp)
            if "error" in data:
                raise ArcGISError(f"ArcGIS error: {data['error']}")
            return data
        except (urllib.error.URLError, ConnectionResetError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise ArcGISError(f"ArcGIS request failed after {RETRIES} attempts: {last}")


def check_schema(layers: list[str] | None = None) -> list[str]:
    """Drift guard. Confirms every field this package reads still exists on
    each layer. Returns a list of problems; empty means safe to proceed.
    One metadata request per layer."""
    problems: list[str] = []
    for layer in layers or list(LAYERS):
        meta_url = LAYERS[layer].rsplit("/query", 1)[0] + "?f=json"
        try:
            meta = _get_json(meta_url)
        except ArcGISError as exc:
            problems.append(f"{layer}: metadata request failed ({exc})")
            continue
        names = {f.get("name") for f in meta.get("fields", [])}
        if not names:
            problems.append(f"{layer}: metadata has no fields (service moved or renamed?)")
            continue
        missing = [f for f in FIELDS if f not in names]
        if missing:
            problems.append(f"{layer}: fields missing from Metro's service: {', '.join(missing)}")
    return problems


def newest_date(layer: str) -> str | None:
    """ISO date of the most recent record on the layer, for freshness checks."""
    field = DATE_FIELD[layer]
    q = urllib.parse.urlencode(
        {"where": f"{field} IS NOT NULL", "outFields": field, "orderByFields": f"{field} DESC",
         "resultRecordCount": "1", "returnGeometry": "false", "f": "json"}
    )
    feats = _get_json(f"{LAYERS[layer]}?{q}").get("features", [])
    if not feats:
        return None
    ms = feats[0]["attributes"].get(field)
    if not isinstance(ms, (int, float)):
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def count(layer: str, where: str) -> int:
    q = urllib.parse.urlencode({"where": where, "returnCountOnly": "true", "f": "json"})
    return int(_get_json(f"{LAYERS[layer]}?{q}").get("count", 0))


def iter_records(
    layer: str,
    where: str,
    *,
    max_scan: int = 20000,
    on_page: Callable[[int], None] | None = None,
) -> Iterator[dict]:
    """Yield raw attribute dicts newest first, paging until exhausted or
    `max_scan` rows have been read."""
    order = f"{DATE_FIELD[layer]} DESC, ObjectId DESC"
    offset = 0
    while offset < max_scan:
        q = urllib.parse.urlencode(
            {
                "where": where,
                "outFields": ",".join(FIELDS),
                "orderByFields": order,
                "resultOffset": str(offset),
                "resultRecordCount": str(min(PAGE_SIZE, max_scan - offset)),
                "returnGeometry": "false",
                "f": "json",
            }
        )
        data = _get_json(f"{LAYERS[layer]}?{q}")
        feats = data.get("features", [])
        for f in feats:
            yield f["attributes"]
        offset += len(feats)
        if on_page:
            on_page(offset)
        if not feats or (len(feats) < PAGE_SIZE and not data.get("exceededTransferLimit")):
            break
