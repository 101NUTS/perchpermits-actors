#!/usr/bin/env bash
# Run the actor locally with realistic pay-per-event prices and a budget.
#
#   scripts/run_local.sh '{"stations":["NYC","MDW"],"maxEvents":4}' 0.10
#
# $1 = input JSON, $2 = max total charge in USD (default: unlimited).
# INPUT_FILE=path reads the input JSON from a file (MSYS mangles JSON on argv
# when bash is launched from a non-MSYS parent).
set -euo pipefail
cd "$(dirname "$0")/.."
INPUT="${1:-{\}}"
if [ -n "${INPUT_FILE:-}" ]; then INPUT="$(cat "$INPUT_FILE")"; fi
BUDGET="${2:-}"
STORE="storage/key_value_stores/default"
mkdir -p "$STORE"
printf '%s' "$INPUT" > "$STORE/INPUT.json"
rm -rf storage/datasets
export ACTOR_TEST_PAY_PER_EVENT=1
export APIFY_CHARGED_ACTOR_EVENT_COUNTS='{"apify-actor-start":0}'
export APIFY_ACTOR_PRICING_INFO='{"pricingModel":"PAY_PER_EVENT","apifyMarginPercentage":0.2,"createdAt":"2026-09-11T00:00:00Z","startedAt":"2026-09-11T00:00:00Z","pricingPerEvent":{"actorChargeEvents":{"event":{"eventTitle":"Ladder row","eventDescription":"One city-day temperature ladder from Kalshi","eventPriceUsd":0.002},"enriched-event":{"eventTitle":"Ladder + NWS station data","eventDescription":"One ladder joined to its settlement station","eventPriceUsd":0.02},"apify-default-dataset-item":{"eventTitle":"Dataset item","eventDescription":"synthetic","eventPriceUsd":0},"apify-actor-start":{"eventTitle":"Actor start","eventDescription":"synthetic","eventPriceUsd":0.00005}}}}'
if [ -n "$BUDGET" ]; then export ACTOR_MAX_TOTAL_CHARGE_USD="$BUDGET"; fi
# STRIP_ENRICHED_PRICE=1 simulates a Console where the enriched-event event was never priced.
if [ -n "${STRIP_ENRICHED_PRICE:-}" ]; then
  export APIFY_ACTOR_PRICING_INFO="$(printf '%s' "$APIFY_ACTOR_PRICING_INFO" | sed 's/"enriched-event":{[^}]*},//')"
fi
PY=../nashville-permits/.venv/Scripts/python; [ -x "$PY" ] || PY=../nashville-permits/.venv/bin/python
"$PY" -m src
