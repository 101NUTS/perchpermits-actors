"""Enrich a permit from the Metro Nashville ePermits case API.

The open dataset publishes the applicant but not the licensed contractor,
the property owner, the required sub-trade permits, or inspection progress.
All four live behind epermits.nashville.gov, whose SPA calls a public
OData-style backend keyed by caseID (the IVR_Trk_ field in the open data).

Ported from permit-pulse's pipeline/enrich.py with these changes:

* the ``Case`` endpoint is used too (status, expiry, fees, full scope text);
* conditions carry ``dateCompleted``, so sub-trade permits are reported as
  satisfied or outstanding instead of "presence = outstanding";
* the cache is pluggable (local directory or an Apify key-value store);
* no headless-browser fallback, an actor should fail loud and cheap.

Politeness: one request per second, identifying User-Agent, cached results
reused for `max_age_days`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

API_BASE = "https://epermits.nashville.gov/api/permit/1.0"
HEADERS = {
    "User-Agent": "nashville-permits-actor/0.1 (public-records tool; contact: perchpermits@gmail.com)",
    "Accept": "application/json",
}
RATE_LIMIT_SECONDS = 1.0
TIMEOUT = 30

_PATHS = {
    "contractors": "CaseContractors",
    "people": "CasePeople",
    "conditions": "CaseApprovalCondition",
    "tasks": "CaseTask",
}
# The SPA quotes caseID on one endpoint and not the others; mirror it.
_FILTERS = {
    "contractors": "caseID eq '{ivr}'",
    "people": "caseID eq {ivr} and roleCode ne BILLING",
    "conditions": "caseID eq {ivr}",
    "tasks": "caseID eq {ivr}",
}
ENDPOINTS = ["case", *_PATHS]


class Cache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def set(self, key: str, value: dict) -> None: ...


class DirCache:
    """One JSON file per case under a directory. Used by the CLI and tests."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def get(self, key: str) -> dict | None:
        p = self.root / f"{key}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def set(self, key: str, value: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{key}.json").write_text(json.dumps(value), encoding="utf-8")


class NullCache:
    def get(self, key: str) -> dict | None:
        return None

    def set(self, key: str, value: dict) -> None:
        pass


def _url(endpoint: str, ivr: int) -> str:
    if endpoint == "case":
        return f"{API_BASE}/Case/{ivr}"
    url = f"{API_BASE}/{_PATHS[endpoint]}?$filter=" + urllib.parse.quote(_FILTERS[endpoint].format(ivr=ivr))
    if endpoint == "tasks":
        url += "&$orderby=" + urllib.parse.quote("start_point asc, completedDate desc")
    return url


def _get(url: str) -> dict | None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return None


def fetch_raw(ivr: int, rate_limit: float = RATE_LIMIT_SECONDS) -> dict:
    """Fetch all five endpoints for one case. Missing endpoints are None."""
    raw: dict = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for ep in ENDPOINTS:
        if rate_limit > 0:
            time.sleep(rate_limit)
        raw[ep] = _get(_url(ep, ivr))
    return raw


# Fields each endpoint must carry when it returns rows. Missing fields on a
# non-empty response mean Metro changed the API, not that the permit is odd.
EXPECTED_FIELDS = {
    "case": ("caseNumber", "status", "projectScope"),
    "contractors": ("companyName", "licenseNumber"),
    "people": ("name", "roleCode"),
    "conditions": ("conditionCode", "description", "dateCompleted"),
    "tasks": ("description", "isInspection", "completedDate"),
}


def shape_issues(raw: dict) -> list[str]:
    """Drift guard for one fetched case. Returns human-readable problems
    found in the response shapes; transport failures (None) are not shape
    issues and are counted separately by the caller."""
    issues: list[str] = []
    for ep, fields in EXPECTED_FIELDS.items():
        data = raw.get(ep)
        if data is None:
            continue
        if not isinstance(data, dict) or "value" not in data:
            issues.append(f"{ep}: response has no 'value' list")
            continue
        rows = data["value"]
        if not rows:
            continue
        missing = [f for f in fields if f not in rows[0]]
        if missing:
            issues.append(f"{ep}: fields missing: {', '.join(missing)}")
    return issues


def _values(raw: dict, ep: str) -> list[dict]:
    d = raw.get(ep)
    return d.get("value", []) if isinstance(d, dict) else []


def _date(s) -> str | None:
    if not s or str(s)[:4] <= "1900":
        return None
    return str(s)[:10]


def _blank(s) -> str | None:
    s = (s or "").strip()
    return s or None


def extract(raw: dict) -> dict:
    """Flatten raw endpoint payloads into the agent-facing enrichment block."""
    got = {ep: raw.get(ep) is not None for ep in ENDPOINTS}
    status = "ok" if all(got.values()) else ("failed" if not any(got.values()) else "partial")
    out: dict = {
        "status": status,
        "fetched_at": raw.get("fetched_at"),
        "missing_endpoints": [ep for ep, ok in got.items() if not ok],
        "case": None,
        "contractor": None,
        "owner": None,
        "sub_trade_permits": [],
        "outstanding_sub_trades": [],
        "inspections": None,
        "inspection_stage": None,
    }

    cases = _values(raw, "case")
    if cases:
        c = cases[0]
        out["case"] = {
            "case_number": _blank(c.get("caseNumber")),
            "status": _blank(c.get("status")),
            "decision": _blank(c.get("decision")),
            "description": _blank(c.get("description")),
            "project_name": _blank(c.get("name")),
            "project_scope": _blank(c.get("projectScope")),
            "applicant": _blank(c.get("applicant")),
            "accepted": _date(c.get("accepted")),
            "issued": _date(c.get("issued")),
            "completed": _date(c.get("completed")),
            "expires": _date(c.get("expires")),
            "is_expired": c.get("isExpired") == "Y",
            "fees": c.get("fees"),
            "payments": c.get("payments"),
            "amount_due": c.get("amountDue"),
        }

    contractors = _values(raw, "contractors")
    if contractors:
        c = contractors[0]
        company = _blank(c.get("companyName"))
        contact = _blank(c.get("name"))
        out["contractor"] = {
            "company": company or contact,
            "is_owner_builder": (company or contact or "").upper().startswith("SELF CONTRACTOR"),
            "contact": contact if contact and contact != company else None,
            "license": _blank(c.get("licenseNumber")),
            "license_expires": _date(c.get("expiration")),
            "phone": _blank(c.get("workPhone")) or _blank(c.get("cellPhone")),
            "email": _blank(c.get("email")),
            "city": _blank(c.get("city")),
            "state": _blank(c.get("stateCode")),
            "zip": _blank(c.get("zip")),
            "classifications": _blank(c.get("address3")),
        }

    for p in _values(raw, "people"):
        if p.get("roleCode") == "PROP_CURRO":
            out["owner"] = {
                "name": _blank(p.get("name")) or _blank(p.get("companyName")),
                "mailing_city": _blank(p.get("city")),
                "mailing_state": _blank(p.get("stateCode")),
                "mailing_zip": _blank(p.get("zip")),
            }
            break

    for cond in _values(raw, "conditions"):
        done = _date(cond.get("dateCompleted"))
        item = {
            "code": _blank(cond.get("conditionCode")),
            "description": _blank(cond.get("description")),
            "satisfied": done is not None,
            "date_completed": done,
        }
        out["sub_trade_permits"].append(item)
        if not item["satisfied"] and item["code"]:
            out["outstanding_sub_trades"].append(item["code"])

    tasks = _values(raw, "tasks")
    if tasks:
        insp = [t for t in tasks if t.get("isInspection") == "Y"]
        completed = [t for t in insp if _date(t.get("completedDate"))]
        scheduled = [t for t in insp if not _date(t.get("completedDate")) and _date(t.get("scheduledDate"))]
        latest = max(completed, key=lambda t: t.get("completedDate", "")) if completed else None
        out["inspections"] = {
            "completed_count": len(completed),
            "scheduled_count": len(scheduled),
            "latest_completed": (
                {
                    "description": _blank(latest.get("description")),
                    "result": _blank(latest.get("result")),
                    "date": _date(latest.get("completedDate")),
                }
                if latest
                else None
            ),
            "history": [
                {
                    "description": _blank(t.get("description")),
                    "result": _blank(t.get("result")),
                    "date": _date(t.get("completedDate")),
                }
                for t in sorted(completed, key=lambda t: t.get("completedDate", ""))
            ][-12:],
        }
        out["inspection_stage"] = _blank(latest.get("description")) if latest else "permit_issued"
    return out


def _fresh(entry: dict | None, max_age_days: int) -> bool:
    if not entry or not entry.get("fetched_at"):
        return False
    try:
        at = datetime.fromisoformat(entry["fetched_at"])
    except ValueError:
        return False
    return datetime.now(timezone.utc) - at < timedelta(days=max_age_days)


def enrich_one(
    ivr: int,
    cache: Cache | None = None,
    *,
    max_age_days: int = 7,
    rate_limit: float = RATE_LIMIT_SECONDS,
) -> dict:
    """Return the enrichment block for one case, reusing cache when fresh.
    A cached entry with missing endpoints is always refetched."""
    cache = cache or NullCache()
    key = f"case-{ivr}"
    raw = cache.get(key)
    source = "cache"
    if not (_fresh(raw, max_age_days) and all(raw.get(ep) is not None for ep in ENDPOINTS)):
        raw = fetch_raw(ivr, rate_limit)
        source = "live"
        if any(raw.get(ep) is not None for ep in ENDPOINTS):
            cache.set(key, raw)
    block = extract(raw)
    block["source"] = source
    block["schema_issues"] = shape_issues(raw)
    return block
