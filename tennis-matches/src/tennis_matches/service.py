"""What the actor and the CLI both call: list matches, filter, enrich.

`list_matches` returns plain match rows from the schedule or results pages.
`enrich_matches` adds the `enrichment` block from each match-detail page:
round, surface, rankings, this year's W/L by surface, head-to-head history,
per-bookmaker odds with openings, and each player's latest matches with a
form summary. Enrichment is one request per match, so callers cap it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .fetch import Client
from .parse import ParseDrift, as_utc, parse_detail_page, parse_list_page, summarize_form

Progress = Callable[[str], None]


class SourceDrift(Exception):
    """TennisExplorer changed its layout; fail loudly rather than sell empty rows."""


def _contains(hay: str | None, needle: str | None) -> bool:
    if not needle:
        return True
    return bool(hay) and needle.lower() in hay.lower()


def list_matches(
    client: Client,
    *,
    kind: str = "schedule",
    start: date | None = None,
    days: int = 1,
    tour: str = "all",
    doubles: bool = False,
    tournament: str | None = None,
    player: str | None = None,
    status: str | None = None,
    max_matches: int = 100,
    progress: Progress = lambda m: None,
) -> list[dict[str, Any]]:
    start = start or date.today()
    days = max(1, min(int(days), 14))
    if max_matches <= 0:
        return []
    if kind == "live":
        # Live is the schedule page filtered to matches in progress right now. The site's
        # calendar day is Central European, so the UTC day before can still have matches on.
        status = "in_progress"
        days = 2
        start = start - timedelta(days=1)
    page_kind = "schedule" if kind == "live" else kind
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i in range(days):
        day = start + timedelta(days=i)
        progress(f"Fetching {page_kind} for {day.isoformat()} ({tour}, {'doubles' if doubles else 'singles'})")
        html = client.list_page(page_kind, day, tour, doubles)
        try:
            rows = parse_list_page(html, page_kind, day)
        except ParseDrift as exc:
            raise SourceDrift(str(exc)) from exc
        if not rows and "class=\"result\"" not in html:
            raise SourceDrift(f"list page for {day} has no result table")
        for m in rows:
            t = m.get("tournament") or {}
            if tour != "all" and t.get("tour") != tour:
                continue
            if not doubles and t.get("format") == "doubles":
                continue
            if doubles and t.get("format") != "doubles":
                continue
            if not _contains(t.get("name"), tournament):
                continue
            if player and not (_contains(m["home"]["name"], player) or _contains(m["away"]["name"], player)):
                continue
            if status and m.get("status") != status:
                continue
            if m.get("match_id") in seen:
                continue
            if m.get("match_id") is not None:
                seen.add(m["match_id"])
            m["start_utc"] = as_utc(m["date"], m.get("time_site"))
            m["fetched_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            out.append(m)
            if len(out) >= max_matches:
                return out
    return out


def enrich_matches(
    client: Client,
    matches: list[dict[str, Any]],
    *,
    limit: int | None = None,
    latest_limit: int = 10,
    odds_history: bool = False,
    progress: Progress = lambda m: None,
) -> int:
    """Attach `enrichment` to the first `limit` matches in place. Returns how many succeeded."""
    n = len(matches) if limit is None else min(limit, len(matches))
    done = 0
    drift = 0
    for i, m in enumerate(matches[:n]):
        mid = m.get("match_id")
        if mid is None:
            m["enrichment"] = {"status": "no_match_id"}
            continue
        progress(f"Enriching {i + 1}/{n}: {m['home']['name']} vs {m['away']['name']} (id {mid})")
        try:
            html = client.detail_page(mid)
            d = parse_detail_page(html, latest_limit=latest_limit, odds_history=odds_history)
        except ParseDrift as exc:
            drift += 1
            m["enrichment"] = {"status": "parse_failed", "error": str(exc)}
            if drift >= 3 and done == 0:
                raise SourceDrift(f"match-detail layout changed: {exc}")
            continue
        except Exception as exc:  # network after retries
            m["enrichment"] = {"status": "fetch_failed", "error": str(exc)}
            continue
        # Promote the fields agents ask for first; keep the rest nested.
        m["round"] = d.get("round")
        m["surface"] = d.get("surface")
        if not m.get("time_site") and d.get("time_site"):
            m["time_site"] = d["time_site"]
            m["start_utc"] = as_utc(m.get("date"), d["time_site"])
        home_latest = d["latest_matches"]["home"]
        away_latest = d["latest_matches"]["away"]
        partial = not d["bookmakers"] and not home_latest and not away_latest
        m["enrichment"] = {
            "status": "partial" if partial else "ok",
            "home": {**d["home"], "surface_record": d["surface_record"].get("home", {}), "form": summarize_form(home_latest), "latest_matches": home_latest},
            "away": {**d["away"], "surface_record": d["surface_record"].get("away", {}), "form": summarize_form(away_latest), "latest_matches": away_latest},
            "surface_record_label": d["surface_record"].get("label"),
            "h2h": d["h2h"],
            "bookmakers": d["bookmakers"],
            "bookmaker_count": len(d["bookmakers"]),
            "best_odds": _best_odds(d["bookmakers"]),
            "market": market_summary(d["bookmakers"]),
            "final_score": d.get("final_score"),
        }
        done += 1
    return done


def market_summary(bookmakers: list[dict[str, Any]]) -> dict[str, Any]:
    """Consensus odds, fair (margin-free) implied probabilities, overround, and
    the move since opening, across every bookmaker that priced both sides."""
    pairs = [(b["home"], b["away"]) for b in bookmakers if b.get("home") and b.get("away")]
    opens = [(b["home_opening"], b["away_opening"]) for b in bookmakers if b.get("home_opening") and b.get("away_opening")]
    out: dict[str, Any] = {
        "books": len(pairs),
        "consensus_home": None, "consensus_away": None,
        "fair_prob_home": None, "fair_prob_away": None,
        "overround": None,
        "opening_consensus_home": None, "opening_consensus_away": None,
        "opening_fair_prob_home": None,
        "prob_shift_home": None,
        "favorite": None,
    }
    if not pairs:
        return out
    ch = sum(h for h, _ in pairs) / len(pairs)
    ca = sum(a for _, a in pairs) / len(pairs)
    ih, ia = 1 / ch, 1 / ca
    total = ih + ia
    out.update({
        "consensus_home": round(ch, 3), "consensus_away": round(ca, 3),
        "fair_prob_home": round(ih / total, 4), "fair_prob_away": round(ia / total, 4),
        "overround": round(total - 1, 4),
        "favorite": "home" if ch < ca else "away" if ca < ch else None,
    })
    if opens:
        oh = sum(h for h, _ in opens) / len(opens)
        oa = sum(a for _, a in opens) / len(opens)
        oih, oia = 1 / oh, 1 / oa
        ofair = oih / (oih + oia)
        out.update({
            "opening_consensus_home": round(oh, 3), "opening_consensus_away": round(oa, 3),
            "opening_fair_prob_home": round(ofair, 4),
            "prob_shift_home": round(out["fair_prob_home"] - ofair, 4),
        })
    return out


def _best_odds(bookmakers: list[dict[str, Any]]) -> dict[str, Any]:
    best: dict[str, Any] = {"home": None, "home_bookmaker": None, "away": None, "away_bookmaker": None}
    for b in bookmakers:
        for side in ("home", "away"):
            v = b.get(side)
            if v is not None and (best[side] is None or v > best[side]):
                best[side] = v
                best[f"{side}_bookmaker"] = b.get("bookmaker")
    return best
