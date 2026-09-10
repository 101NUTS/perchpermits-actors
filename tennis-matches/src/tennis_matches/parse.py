"""HTML parsing for TennisExplorer list pages and match-detail pages.

Everything here is pure: HTML in, dicts out, no network. The list pages
(`/matches/` for the schedule, `/results/` for finished matches) share one
table layout: a `tr.head` row names the tournament, then each match is a pair
of rows, the first carrying the start time, the odds, and the detail link.

Field names are stable API surface; add, do not rename.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from bs4 import BeautifulSoup, Tag

BASE = "https://www.tennisexplorer.com"


class ParseDrift(Exception):
    """The page no longer looks like the layout this parser was written for."""


def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # lxml missing; html.parser is slower but stdlib
        return BeautifulSoup(html, "html.parser")


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _float(s: str) -> float | None:
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return None


def _int(s: str) -> int | None:
    s = s.strip()
    return int(s) if s.isdigit() else None


def _player_from_cell(cell: Tag) -> dict[str, Any]:
    """`<td class="t-name"><a href="/player/tiafoe/">Tiafoe F.</a> (11)</td>`"""
    a = cell.find("a")
    name = _text(a) if a else _text(cell)
    href = a.get("href", "") if a else ""
    slug = href.strip("/").split("/")[-1] if href.startswith("/player/") else None
    seed = None
    m = re.search(r"\((\d+|WC|Q|LL|PR|SE|Alt)\)", cell.get_text(" ", strip=True))
    if m:
        seed = m.group(1)
    return {"name": name, "slug": slug, "url": BASE + href if href else None, "seed": seed}


def _score_cells(row: Tag) -> list[dict[str, Any]]:
    out = []
    for td in row.find_all("td", class_="score"):
        games = None
        tiebreak = None
        sup = td.find("sup")
        if sup:
            tiebreak = _int(sup.get_text())
            sup.extract()
        t = _text(td)
        if t:
            games = _int(t)
        out.append({"games": games, "tiebreak": tiebreak})
    return out


def _tour_from_href(href: str) -> tuple[str | None, str]:
    """`/us-open/2026/atp-men/` -> ("atp", "singles"); `?type=double` -> doubles."""
    tour = None
    if "atp-men" in href:
        tour = "atp"
    elif "wta-women" in href:
        tour = "wta"
    fmt = "doubles" if "type=double" in href else "singles"
    return tour, fmt


def parse_list_page(html: str, page_kind: str, page_date: date) -> list[dict[str, Any]]:
    """Parse `/matches/` (page_kind="schedule") or `/results/` (page_kind="results").

    Returns one dict per match in page order.
    """
    soup = _soup(html)
    center = soup.find("div", id="center") or soup
    tables = center.find_all("table", class_="result")
    if not tables:
        raise ParseDrift("no table.result on list page")

    matches: list[dict[str, Any]] = []
    tournament: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None

    rows = [row for table in tables for row in table.find_all("tr")]
    for row in rows:
        classes = row.get("class") or []
        if "head" in classes:
            a = row.find("td", class_="t-name")
            link = a.find("a") if a else None
            href = link.get("href", "") if link else ""
            tour, fmt = _tour_from_href(href)
            path = href.split("?")[0].strip("/").split("/")
            tournament = {
                "name": _text(link) if link else _text(a),
                "url": BASE + href if href else None,
                "slug": path[0] if path and path[0] else None,
                "year": _int(path[1]) if len(path) > 1 else None,
                "tour": tour,
                "format": fmt,
            }
            pending = None
            continue

        rid = row.get("id") or ""
        if not rid:
            continue
        first = not rid.endswith("b")
        name_cell = row.find("td", class_="t-name")
        if name_cell is None:
            continue
        player = _player_from_cell(name_cell)
        result_cell = row.find("td", class_="result")
        sets_won = _int(_text(result_cell)) if result_cell else None
        scores = _score_cells(row)

        if first:
            time_cell = row.find("td", class_="time")
            info = row.find("a", href=re.compile(r"/match-detail/\?id=\d+"))
            mid = None
            if info:
                mm = re.search(r"id=(\d+)", info["href"])
                mid = int(mm.group(1)) if mm else None
            odds_cells = [td for td in row.find_all("td") if any(c in ("course", "coursew") for c in (td.get("class") or []))]
            odds = [_float(_text(td)) for td in odds_cells[:2]]
            while len(odds) < 2:
                odds.append(None)
            h2h_cell = row.find("td", class_="h2h")
            # the time cell can also carry "Live streams <bookmaker>" link text
            tm = re.search(r"\b(\d{1,2}:\d{2})\b", _text(time_cell))
            pending = {
                "match_id": mid,
                "match_url": f"{BASE}/match-detail/?id={mid}" if mid else None,
                "date": page_date.isoformat(),
                "time_site": tm.group(1) if tm else None,
                "list_kind": page_kind,
                "tournament": dict(tournament) if tournament else None,
                "home": player,
                "away": None,
                "odds": {"home": odds[0], "away": odds[1]},
                "h2h_list": _text(h2h_cell) or None,
                "_home_sets": sets_won,
                "_home_scores": scores,
            }
            continue

        if pending is None:
            continue
        pending["away"] = player
        home_sets, away_sets = pending.pop("_home_sets"), sets_won
        home_scores, away_scores = pending.pop("_home_scores"), scores
        sets = []
        for hs, as_ in zip(home_scores, away_scores):
            if hs["games"] is None and as_["games"] is None:
                continue
            sets.append({"home": hs["games"], "away": as_["games"], "tiebreak_loser_points": hs["tiebreak"] if hs["tiebreak"] is not None else as_["tiebreak"]})
        played = home_sets is not None or away_sets is not None or bool(sets)
        best_of_5 = pending["tournament"] and pending["tournament"].get("slug") in ("us-open", "wimbledon", "french-open", "australian-open", "roland-garros") and pending["tournament"].get("tour") == "atp" and pending["tournament"].get("format") == "singles"
        need = 3 if best_of_5 else 2
        winner = None
        status = "scheduled"
        if played:
            if (home_sets or 0) >= need or (away_sets or 0) >= need:
                status = "finished"
                winner = "home" if (home_sets or 0) > (away_sets or 0) else "away"
            elif page_kind == "results":
                # results page only lists completed matches; short set counts mean retirement or walkover
                status = "finished"
                if (home_sets or 0) != (away_sets or 0):
                    winner = "home" if (home_sets or 0) > (away_sets or 0) else "away"
                pending["result_note"] = "retired_or_walkover"
            else:
                status = "in_progress"
        pending["status"] = status
        pending["result"] = {
            "winner": winner,
            "sets_home": home_sets,
            "sets_away": away_sets,
            "sets": sets,
            "score": _score_string(sets, home_sets, away_sets) if sets else None,
        }
        matches.append(pending)
        pending = None

    return matches


def _score_string(sets: list[dict], home_sets, away_sets) -> str:
    parts = []
    for s in sets:
        h, a = s.get("home"), s.get("away")
        if h is None or a is None:
            continue
        tb = s.get("tiebreak_loser_points")
        parts.append(f"{h}-{a}" + (f"({tb})" if tb is not None else ""))
    return ", ".join(parts)


# ---------------------------------------------------------------- detail page

_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def _site_date(s: str) -> str | None:
    m = _DATE_RE.search(s)
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _parse_header(soup: BeautifulSoup) -> dict[str, Any]:
    center = soup.find("div", id="center") or soup
    h1 = center.find("h1")
    box = h1.find_next("div", class_="boxBasic") if h1 else None
    if box is None:
        raise ParseDrift("no header box on match-detail page")
    for iframe in box.find_all("iframe"):
        iframe.extract()
    raw = _text(box)
    # "08.09.2026, 20:25, US Open, quarterfinal, hard"; the date can also read "Today" or "Tomorrow"
    parts = [p.strip() for p in raw.split(",")]
    link = box.find("a")
    title = _text(h1)
    # Doubles pages title "Krueger, Montgomery - Dabrowski, Stefani"; singles "Tiafoe - Michelsen"
    sides = [s.strip() for s in title.split(" - ", 1)] if " - " in title else [title]
    out = {
        "title": title,
        "home_names": [n.strip() for n in sides[0].split(",")] if sides else [],
        "away_names": [n.strip() for n in sides[1].split(",")] if len(sides) > 1 else [],
        "date": _site_date(parts[0]) if parts else None,
        "date_word": parts[0].lower() if parts and parts[0].lower() in ("today", "tomorrow", "yesterday") else None,
        "time_site": parts[1] if len(parts) > 1 and re.match(r"^\d{1,2}:\d{2}$", parts[1]) else None,
        "tournament_name": _text(link) if link else (parts[2] if len(parts) > 2 else None),
        "tournament_url": BASE + link["href"] if link and link.get("href") else None,
        "round": None,
        "surface": None,
    }
    tail = parts[3:] if len(parts) > 3 else []
    if tail:
        surface = tail[-1].lower()
        if surface in ("hard", "clay", "grass", "indoors", "indoor", "carpet"):
            out["surface"] = "indoors" if surface == "indoor" else surface
            tail = tail[:-1]
    if tail:
        rnd = tail[0]
        out["round"] = None if rnd in ("-", "") else rnd
    return out


def _parse_players_block(soup: BeautifulSoup) -> tuple[dict, dict]:
    tbl = soup.find("table", class_="gDetail")
    if tbl is None:
        raise ParseDrift("no gDetail table on match-detail page")
    names = tbl.find_all("th", class_="plName")
    def pl(th):
        a = th.find("a")
        href = a.get("href", "") if a else ""
        return {"full_name": _text(th), "slug": href.rstrip("/").split("/")[-1] if href else None,
                "url": BASE + href if href else None}
    home = pl(names[0]) if names else {}
    away = pl(names[1]) if len(names) > 1 else {}
    gscore = tbl.find("td", class_="gScore")
    final_score = None
    if gscore is not None:
        for sup in gscore.find_all("sup"):
            sup.replace_with(f"({sup.get_text()})")
        t = re.sub(r"\s*\((\d+)\)\s*", r"(\1)", _text(gscore))
        final_score = t if t else None
    labels = {
        "Singles ranking": "singles_rank",
        "Doubles ranking": "doubles_rank",
        "Birthdate": "birthdate",
        "Height": "height",
        "Weight": "weight",
        "Plays": "plays",
        "Turned pro": "turned_pro",
    }
    for tr in tbl.find_all("tr"):
        th = tr.find("th")
        if th is None or "plName" in (th.get("class") or []):
            continue
        key = labels.get(_text(th))
        if key is None:
            continue
        left = tr.find("td", class_="tr")
        right = tr.find("td", class_="tl")
        for target, cell in ((home, left), (away, right)):
            v = _text(cell).rstrip(".")
            if key in ("singles_rank", "doubles_rank"):
                target[key] = _int(v)
            elif key == "birthdate":
                target[key] = _site_date(v.replace(" ", ""))
            else:
                target[key] = None if v in ("-", "") else v
    home["_final_score"] = final_score
    return home, away


def _parse_surface_record(soup: BeautifulSoup) -> dict[str, Any]:
    """`balMenu-1-data`: this year's W/L by surface for both players."""
    div = soup.find("div", id="balMenu-1-data")
    out: dict[str, Any] = {"home": {}, "away": {}}
    if div is None:
        return out
    tab = soup.find("li", id="balMenu-1")
    out["label"] = _text(tab) if tab else None
    for tr in div.find_all("tr"):
        first = tr.find("td", class_="first")
        if first is None:
            continue
        surface = _text(first).lower().replace("not set", "unknown")
        cells = tr.find_all("td", class_="player")
        for who, td in zip(("home", "away"), cells):
            v = _text(td)
            if "/" in v:
                w, l = v.split("/", 1)
                out[who][surface] = {"wins": _int(w), "losses": _int(l)}
            else:
                out[who][surface] = None
    return out


def _parse_h2h(soup: BeautifulSoup) -> dict[str, Any]:
    h2 = soup.find("h2", string=re.compile(r"Head-to-head"))
    out: dict[str, Any] = {"home_wins": None, "away_wins": None, "matches": []}
    if h2 is None:
        return out
    m = re.search(r"(\d+)\s*-\s*(\d+)", _text(h2))
    if m:
        out["home_wins"], out["away_wins"] = int(m.group(1)), int(m.group(2))
    table = h2.find_next("table", class_="result")
    if table is None:
        return out
    cur: dict[str, Any] | None = None
    for tr in table.find_all("tr"):
        annual = tr.find("td", class_="annual")
        name = tr.find("td", class_="t-name")
        if name is None:
            continue
        result = tr.find("td", class_="result")
        scores = _score_cells(tr)
        if annual is not None:
            tl = tr.find("td", class_="tl")
            surf = tr.find("td", class_="sColorLong")
            span = surf.find("span") if surf else None
            rnd = tr.find("td", class_="round")
            cur = {
                "year": _int(_text(annual)),
                "tournament": _text(tl) or None,
                "surface": (span.get("title") or "").lower() or None if span else None,
                "round": _text(rnd) or None,
                "winner_name": _text(name),
                "winner_sets": _int(_text(result)),
                "_wscores": scores,
            }
            continue
        if cur is None:
            continue
        cur["loser_name"] = _text(name)
        cur["loser_sets"] = _int(_text(result))
        wsc = cur.pop("_wscores")
        sets = []
        for a, b in zip(wsc, scores):
            if a["games"] is None and b["games"] is None:
                continue
            sets.append({"winner": a["games"], "loser": b["games"], "tiebreak_loser_points": a["tiebreak"] if a["tiebreak"] is not None else b["tiebreak"]})
        cur["score"] = ", ".join(
            f"{s['winner']}-{s['loser']}" + (f"({s['tiebreak_loser_points']})" if s["tiebreak_loser_points"] is not None else "")
            for s in sets if s["winner"] is not None and s["loser"] is not None
        ) or None
        link = tr.find("a", href=re.compile(r"/match-detail/\?id=\d+"))
        if link:
            mm = re.search(r"id=(\d+)", link["href"])
            cur["match_id"] = int(mm.group(1)) if mm else None
        rnd2 = tr.find("td", class_="round")
        if rnd2 is not None and not cur.get("round"):
            cur["round"] = _text(rnd2) or None
        out["matches"].append(cur)
        cur = None
    return out


def _parse_odds_cell(td: Tag) -> dict[str, Any]:
    box = td.find("div", class_="odds-in")
    if box is None:
        return {"current": _float(_text(td)), "opening": None, "trend": None, "history": []}
    hist_div = box.find("div", class_="odds-change-div")
    history = []
    opening = None
    if hist_div is not None:
        in_opening = False
        for tr in hist_div.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) == 1 and "Opening" in _text(tds[0]):
                in_opening = True
                continue
            if len(tds) >= 2:
                when = _text(tds[0])
                val = _float(_text(tds[1]))
                diff = _text(tds[2]) if len(tds) > 2 else ""
                history.append({"at_site": when, "odds": val, "change": _float(diff.replace("+", "")) if diff and diff[0] in "+-" else None})
                if in_opening:
                    opening = val
                    in_opening = False
        hist_div.extract()
    classes = box.get("class") or []
    trend = "down" if "odown" in classes else "up" if "oup" in classes else None
    return {"current": _float(_text(box)), "opening": opening, "trend": trend, "history": history}


def _parse_bookmakers(soup: BeautifulSoup, with_history: bool) -> list[dict[str, Any]]:
    div = soup.find("div", id="oddsMenu-1-data")
    out = []
    if div is None:
        return out
    for tr in div.find_all("tr"):
        if "head" in (tr.get("class") or []):
            continue
        name_cell = tr.find("td", class_="first")
        k1, k2 = tr.find("td", class_="k1"), tr.find("td", class_="k2")
        if name_cell is None or k1 is None or k2 is None:
            continue
        label = name_cell.find("span", class_="t")
        home, away = _parse_odds_cell(k1), _parse_odds_cell(k2)
        row = {
            "bookmaker": _text(label) if label else _text(name_cell),
            "home": home["current"],
            "away": away["current"],
            "home_opening": home["opening"],
            "away_opening": away["opening"],
            "home_trend": home["trend"],
            "away_trend": away["trend"],
        }
        if with_history:
            row["home_history"] = home["history"]
            row["away_history"] = away["history"]
        out.append(row)
    return out


def _parse_latest(table: Tag, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ctx: dict[str, Any] = {}
    for tr in table.find_all("tr"):
        if "head" in (tr.get("class") or []):
            td = tr.find("td")
            a = td.find("a") if td else None
            help_span = td.find("span", class_="help") if td else None
            ctx = {
                "tournament": _text(a) or None,
                "tournament_url": BASE + a["href"] if a and a.get("href") else None,
                "round": _text(help_span) or None,
                "round_full": help_span.get("title") if help_span else None,
                "date": _site_date(_text(td)),
            }
            continue
        icon = tr.find("td", class_="icon-result")
        if icon is None:
            continue
        won = "win" in (icon.get("class") or [])
        names = tr.find("td", class_="tl")
        links = names.find_all("a") if names else []
        me = None
        opponent = None
        for a in links:
            if "notU" in (a.get("class") or []):
                me = _text(a)
            else:
                opponent = {"name": _text(a), "slug": a["href"].rstrip("/").split("/")[-1] if a.get("href") else None}
        score_a = tr.find("td", class_="score")
        link = score_a.find("a") if score_a else None
        mm = re.search(r"id=(\d+)", link["href"]) if link and link.get("href") else None
        out.append({
            **ctx,
            "won": won,
            "player": me,
            "opponent": opponent,
            "sets": _text(link) or _text(score_a) or None,
            "score": link.get("title") if link else None,
            "match_id": int(mm.group(1)) if mm else None,
        })
        if len(out) >= limit:
            break
    return out


def parse_detail_page(html: str, latest_limit: int = 10, odds_history: bool = False) -> dict[str, Any]:
    """Parse `/match-detail/?id=N` into the enrichment block."""
    soup = _soup(html)
    header = _parse_header(soup)
    home, away = _parse_players_block(soup)
    final_score = home.pop("_final_score", None)
    if len(header["home_names"]) > 1:
        home["partner"] = header["home_names"][1]
    if len(header["away_names"]) > 1:
        away["partner"] = header["away_names"][1]
    surface_record = _parse_surface_record(soup)
    h2h = _parse_h2h(soup)
    bookmakers = _parse_bookmakers(soup, odds_history)
    latest_tables = soup.find_all("table", class_="mutual")
    latest = {
        "home": _parse_latest(latest_tables[0], latest_limit) if latest_tables else [],
        "away": _parse_latest(latest_tables[1], latest_limit) if len(latest_tables) > 1 else [],
    }
    return {
        **header,
        "final_score": final_score,
        "home": home,
        "away": away,
        "surface_record": surface_record,
        "h2h": h2h,
        "bookmakers": bookmakers,
        "latest_matches": latest,
    }


def summarize_form(latest: list[dict[str, Any]]) -> dict[str, Any]:
    """Win count over the recent matches list, plus the W/L string agents like."""
    if not latest:
        return {"played": 0, "wins": 0, "losses": 0, "streak": None, "sequence": ""}
    seq = "".join("W" if m["won"] else "L" for m in latest)
    streak_char = seq[0]
    streak = 0
    for c in seq:
        if c != streak_char:
            break
        streak += 1
    return {
        "played": len(latest),
        "wins": seq.count("W"),
        "losses": seq.count("L"),
        "streak": f"{streak}{streak_char}",
        "sequence": seq,
    }


def as_utc(date_iso: str | None, time_site: str | None) -> str | None:
    """TennisExplorer displays times in Central European time. Convert to UTC ISO."""
    if not date_iso or not time_site:
        return None
    try:
        from zoneinfo import ZoneInfo

        local = datetime.fromisoformat(f"{date_iso}T{time_site}:00").replace(tzinfo=ZoneInfo("Europe/Prague"))
        return local.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
    except Exception:
        return None
