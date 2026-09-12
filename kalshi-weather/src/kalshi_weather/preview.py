"""Ladder analysis: a short written read of an enriched ladder, produced by Claude.

Sold as the `ladder-analysis` event on top of `enriched-event`. The analysis
explains what the row's own numbers say (the ladder's distribution, the NWS
forecast and where it lands, observations so far, the climate report once
issued); it does not tell the reader what to trade and it never invents a
fact that is not in the row. Rows the model cannot read come back with
`analysis_text = None` and are not charged.

Needs ANTHROPIC_API_KEY in the actor's environment (set it as a secret env var
in the Apify Console). Without it, `available()` is False and the actor skips
analyses with a logged error rather than failing the run.
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
    "You write short analyses for a prediction-market data feed read by trading models and AI agents. "
    "You receive one JSON row: a Kalshi daily temperature ladder for one city and day (every strike with its "
    "normalised probability, the favourite, the probability-weighted expected temperature), joined to the NWS station "
    "that settles it (the daily and hourly forecast and which strike each lands on, observations so far with the running "
    "extreme, and the official climate report once issued). "
    "Write 90 to 140 words of plain prose, no headings, no bullet points, no markdown. "
    "Say where the market puts its weight and what it expects, then where the forecast and the observations so far land "
    "and how far that sits from the market's expectation. If the official report is present, say what settled and whether "
    "the favourite paid. Use only facts present in the row; if a field is null say the data is missing rather than guessing. "
    "Do not recommend a trade, do not mention that you are an AI, and do not restate the whole row. Temperatures are Fahrenheit."
)


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def compact(row: dict[str, Any]) -> dict[str, Any]:
    """The parts of an enriched row worth the model's time: no URLs, no hourly periods, no rules text."""
    e = row.get("enrichment") or {}
    obs = e.get("observations") or {}
    fc = e.get("forecast") or {}
    hf = e.get("hourly_forecast") or {}
    rep = e.get("climate_report") or {}
    return {
        "event_ticker": row.get("event_ticker"),
        "city": (row.get("station") or {}).get("city"),
        "station": (row.get("station") or {}).get("cli_id"),
        "kind": row.get("kind"),
        "target_date": row.get("target_date"),
        "status": row.get("status"),
        "fetched_at_utc": row.get("fetched_at_utc"),
        "strikes": [{k: m.get(k) for k in ("label", "low_f", "high_f", "implied_prob", "normalized_prob", "volume", "result")}
                    for m in (row.get("markets") or [])],
        "market_summary": row.get("market_summary"),
        "settlement": row.get("settlement"),
        "observations": {k: obs.get(k) for k in ("count", "max_f", "max_at", "min_f", "min_at", "latest_f", "latest_at")},
        "forecast": {k: fc.get(k) for k in ("high_f", "low_f", "generated_at")},
        "hourly_forecast": {k: hf.get(k) for k in ("hours", "max_f", "max_at", "min_f", "min_at")},
        "climate_report": {k: rep.get(k) for k in ("max_f", "min_f", "final", "issued_at")},
        "analysis": e.get("analysis"),
    }


def _text_of(message: Any) -> str | None:
    if getattr(message, "stop_reason", None) == "refusal":
        return None
    parts = [b.text for b in getattr(message, "content", []) if getattr(b, "type", None) == "text"]
    text = "\n".join(parts).strip()
    return text or None


def make_client() -> Any:
    import anthropic  # imported here so the actor runs without the package when analyses are off

    return anthropic.Anthropic(max_retries=2, timeout=90.0)


def analyse_one(client: Any, row: dict[str, Any]) -> str | None:
    """One analysis, or None when the model refused or errored. Never raises."""
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
    except Exception:  # noqa: BLE001  (any SDK error: the row is returned without an analysis and not charged)
        return None
    return _text_of(msg)


def attach_analyses(rows: list[dict[str, Any]], *, client: Any | None = None, limit: int | None = None,
                    caller: Callable[[Any, dict[str, Any]], str | None] = analyse_one,
                    progress: Callable[[str], None] | None = None) -> int:
    """Write `analysis_text` onto up to `limit` rows (in order). Returns how many got one.

    `analysis_text` is {"text", "model", "generated_at"} or None. Rows past the
    limit are left untouched (no key) so the caller can tell "not asked" from
    "asked and failed".
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
        row["analysis_text"] = {"text": text, "model": MODEL, "generated_at": stamp} if text else None
        done += int(bool(text))
    if progress:
        progress(f"analyses: {done} of {len(todo)} written")
    return done
