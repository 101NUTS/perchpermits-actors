"""Match preview: a short written read of an enriched row, produced by Claude.

Sold as the `match-preview` event on top of `enriched-match`. The preview
explains what the row's own numbers say (market, movement, form, surface,
head-to-head, rankings); it does not pick a winner and it never invents a fact
that is not in the row. Rows the model cannot read come back with
`preview = None` and are not charged.

Needs ANTHROPIC_API_KEY in the actor's environment (set it as a secret env var
in the Apify Console). Without it, `available()` is False and the actor skips
previews with a logged error rather than failing the run.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

MODEL = "claude-opus-5"
EFFORT = "low"
MAX_TOKENS = 700
WORKERS = 4

SYSTEM = (
    "You write match previews for a tennis data feed read by trading models and AI agents. "
    "You receive one JSON row: a match with bookmaker consensus odds (fair probabilities with the margin removed), "
    "the move since opening, each player's ranking, recent form, surface record for the year, and the head-to-head. "
    "Write 90 to 140 words of plain prose, no headings, no bullet points, no markdown. "
    "Say what the market prices and how it has moved, then which of the row's facts support or cut against that price. "
    "Use only facts present in the row; if a field is null say the data is missing rather than guessing. "
    "Do not pick a winner, do not recommend a bet, do not mention that you are an AI, and do not restate the whole row. "
    "Name players as the row names them."
)


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def compact(row: dict[str, Any]) -> dict[str, Any]:
    """The parts of an enriched row worth the model's time: no URLs, no odds histories, capped lists."""
    e = row.get("enrichment") or {}

    def player(side: str) -> dict[str, Any]:
        p = e.get(side) or {}
        return {
            "name": (row.get(side) or {}).get("name"),
            "seed": (row.get(side) or {}).get("seed"),
            "singles_rank": p.get("singles_rank"),
            "plays": p.get("plays"),
            "surface_record": p.get("surface_record"),
            "form": p.get("form"),
            "latest_matches": [
                {k: m.get(k) for k in ("date", "tournament", "round", "opponent", "won", "score")}
                for m in (p.get("latest_matches") or [])[:6]
            ],
        }

    h2h = e.get("h2h") or {}
    return {
        "date": row.get("date"),
        "start_utc": row.get("start_utc"),
        "status": row.get("status"),
        "tournament": {k: (row.get("tournament") or {}).get(k) for k in ("name", "tour", "format")},
        "round": row.get("round"),
        "surface": row.get("surface"),
        "home": player("home"),
        "away": player("away"),
        "market": e.get("market"),
        "best_odds": e.get("best_odds"),
        "bookmaker_count": e.get("bookmaker_count"),
        "h2h": {
            "home_wins": h2h.get("home_wins"),
            "away_wins": h2h.get("away_wins"),
            "matches": [{k: m.get(k) for k in ("year", "tournament", "surface", "round", "winner_name", "score")}
                        for m in (h2h.get("matches") or [])[:8]],
        },
        "result": row.get("result"),
    }


def _text_of(message: Any) -> str | None:
    if getattr(message, "stop_reason", None) == "refusal":
        return None
    parts = [b.text for b in getattr(message, "content", []) if getattr(b, "type", None) == "text"]
    text = "\n".join(parts).strip()
    return text or None


def make_client() -> Any:
    import anthropic  # imported here so the actor runs without the package when previews are off

    return anthropic.Anthropic(max_retries=2, timeout=90.0)


def preview_one(client: Any, row: dict[str, Any]) -> str | None:
    """One preview, or None when the model refused or errored. Never raises."""
    try:
        msg = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": json.dumps(compact(row), ensure_ascii=False, separators=(",", ":"))}],
            output_config={"effort": EFFORT},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except Exception:  # noqa: BLE001  (any SDK error: the row is returned without a preview and not charged)
        return None
    return _text_of(msg)


def attach_previews(rows: list[dict[str, Any]], *, client: Any | None = None, limit: int | None = None,
                    caller: Callable[[Any, dict[str, Any]], str | None] = preview_one,
                    progress: Callable[[str], None] | None = None) -> int:
    """Write `preview` onto up to `limit` rows (in order). Returns how many got one.

    `preview` is {"text", "model", "generated_at"} or None. Rows past the limit
    are left untouched (no `preview` key) so the caller can tell "not asked"
    from "asked and failed".
    """
    todo = rows if limit is None else rows[:limit]
    if not todo:
        return 0
    client = client if client is not None else make_client()
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        texts = list(pool.map(lambda r: caller(client, r), todo))
    done = 0
    for row, text in zip(todo, texts):
        row["preview"] = {"text": text, "model": MODEL, "generated_at": stamp} if text else None
        done += int(bool(text))
    if progress:
        progress(f"previews: {done} of {len(todo)} written")
    return done
