#!/usr/bin/env bash
# Run the actor locally with realistic pay-per-event prices and a budget.
#
#   scripts/run_local.sh '{"scope":"kitchen","enrich":true,"maxRecords":6}' 0.10
#
# $1 = input JSON, $2 = max total charge in USD (default: unlimited).
set -euo pipefail
cd "$(dirname "$0")/.."
INPUT="${1:-{\}}"
BUDGET="${2:-}"
STORE="storage/key_value_stores/default"
mkdir -p "$STORE"
printf '%s' "$INPUT" > "$STORE/INPUT.json"
rm -rf storage/datasets
export ACTOR_TEST_PAY_PER_EVENT=1
export APIFY_CHARGED_ACTOR_EVENT_COUNTS='{"apify-actor-start":0}'
export APIFY_ACTOR_PRICING_INFO='{"pricingModel":"PAY_PER_EVENT","apifyMarginPercentage":0.2,"createdAt":"2026-09-07T00:00:00Z","startedAt":"2026-09-07T00:00:00Z","pricingPerEvent":{"actorChargeEvents":{"permit":{"eventTitle":"Permit record","eventDescription":"One permit from the open dataset","eventPriceUsd":0.002},"enriched-permit":{"eventTitle":"Permit + ePermits join","eventDescription":"One permit joined to ePermits","eventPriceUsd":0.02},"contractor":{"eventTitle":"Ranked contractor row","eventDescription":"One contractor row","eventPriceUsd":0.01},"apify-default-dataset-item":{"eventTitle":"Dataset item","eventDescription":"synthetic","eventPriceUsd":0},"apify-actor-start":{"eventTitle":"Actor start","eventDescription":"synthetic","eventPriceUsd":0.00005}}}}'
if [ -n "$BUDGET" ]; then export ACTOR_MAX_TOTAL_CHARGE_USD="$BUDGET"; fi
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
"$PY" -m src
