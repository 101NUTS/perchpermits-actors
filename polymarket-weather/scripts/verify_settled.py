"""Check the join against real payouts: for every settled event fixture whose station has
METAR fixtures, recompute the day's high (or low) and see whether it lands in the bracket
Polymarket paid. Offline; run from the actor folder: python scripts/verify_settled.py"""

from __future__ import annotations

import glob
import json
import pathlib
import sys
from datetime import date

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from src.polymarket_weather.parse import bracket_for, event_row, summarize_day  # noqa: E402

FIX = HERE / "tests" / "fixtures"
hits = misses = skipped = 0
rows = []
for f in sorted(glob.glob(str(FIX / "pm_event_*.json"))):
    row = event_row(json.loads(pathlib.Path(f).read_text(encoding="utf-8")))
    if row["status"] != "settled":
        continue
    icao, tz = row["station"]["icao"], row["station"]["timezone"]
    mf = FIX / f"metar_{icao}_110h.json"
    if not icao or not tz or not mf.exists():
        skipped += 1
        rows.append(f"SKIP  {row['slug']}  ({row['station']['source_type']}, {icao})")
        continue
    obs = summarize_day(json.loads(mf.read_text(encoding="utf-8")), date.fromisoformat(row["target_date"]), tz, row["unit"], row["hourly_only"])
    val = obs.get("max") if row["kind"] == "high" else obs.get("min")
    got = bracket_for(val, row["brackets"])
    ok = got is not None and got["label"] == row["settlement"]["winning_bracket"]
    hits += ok
    misses += not ok
    rows.append(f"{'OK  ' if ok else 'MISS'}  {row['slug']}  {icao}  ours={val}{row['unit']} -> {got and got['label']}  paid={row['settlement']['winning_bracket']}  n={obs['count']}")
print("\n".join(rows))
print(f"\n{hits} match, {misses} miss, {skipped} skipped")
