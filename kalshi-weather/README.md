# Kalshi Weather Markets + NWS Station Data

One JSON row per Kalshi daily temperature ladder (a city, a day, high or low), with everything an agent needs to price it in a single call:

- **The ladder**: every strike as Kalshi lists it (`78° or below`, `79° to 80°`, ... `87° or above`) with the integer Fahrenheit bounds worked out, YES bid, ask, mid, last, volume, open interest, and status. Settled ladders carry each strike's `yes`/`no` result.
- **The market's view**: each strike's probability normalised so the ladder sums to one, the favourite, the probability-weighted expected temperature, and how far the raw YES prices sum above one.
- **The station that settles it**: Kalshi's rules name an NWS climate site (Central Park for NYC, Midway for Chicago, and so on). The row joins that station from api.weather.gov: observations recorded so far on the target day with the running max and min, the NWS daily forecast high and low for that day, the hourly forecast's max and min, and the official climate report (CLI) once it is issued.
- **The comparison**: which strike the forecast lands on and what the market charges for it, which strike the running observation is in, which strike the official report settles, and the forecast minus the market's expected temperature.

24 US cities as of September 2026: New York (Central Park and Newark), Chicago, Miami, Austin, Denver, Los Angeles, Philadelphia, Atlanta, Boston, Dallas, Washington, Houston, Las Vegas, Minneapolis, New Orleans, Oklahoma City, Phoenix, San Diego, San Antonio, Louisville, San Francisco, Seattle, Trenton. Kalshi's international cities (London, Paris, Tokyo, ...) settle on The Weather Company with no NWS station; those ladders are returned plain and never charged as enriched.

Both sources are official, free, documented JSON APIs. Nothing is scraped, nothing is guessed, missing values are `null`.

## Why the join

Kalshi's temperature markets settle on a specific NWS climate station's daily max or min. Pricing them means knowing that station's forecast, what it has recorded so far today, and, after the fact, what the official report said. The other Kalshi actors on the store return market prices only; the weather actors return weather only, for a coordinate, not for the settlement station. This actor reads the station out of each market's rules text and fetches exactly that station, so the forecast and the strikes are about the same thermometer.

Checked against settlements: across 288 settled ladders over seven days and 24 cities, the official CLI report's max or min landed in the strike Kalshi paid out every time.

## Quick start

Every open ladder (today and tomorrow, all cities, high and low), joined to NWS:

```json
{}
```

New York and Chicago highs only:

```json
{ "kind": "high", "stations": ["NYC", "MDW"] }
```

Settled ladders for a date range with the official temperature attached, for a training set:

```json
{ "status": "settled", "dateFrom": "2026-09-01", "dateTo": "2026-09-09", "maxEvents": 500 }
```

Just the ladders, no NWS join, cheapest:

```json
{ "enrich": false, "maxEvents": 200 }
```

## Input

| Field | Type | Default | Meaning |
|---|---|---|---|
| `kind` | `both` `high` `low` | `both` | Daily maximum ladders (KXHIGH*), daily minimum ladders (KXLOW*), or both. |
| `status` | `open` `closed` `settled` | `open` | Trading now; past last trade but unsettled; finalized with results. |
| `stations` | list of strings | all | NWS climate-site codes from Kalshi's rules: `NYC`, `MDW`, `MIA`, `AUS`, `DEN`, `LAX`, `PHL`, `ATL`, `BOS`, `DFW`, `DCA`, `EWR`, `HOU`, `LAS`, `MSP`, `MSY`, `OKC`, `PHX`, `SAN`, `SAT`, `SDF`, `SFO`, `TTN`, `SEA`. |
| `dateFrom`, `dateTo` | ISO date | none | Keep ladders whose target day is in the range. The target day is the local calendar day the temperature is measured on. |
| `maxEvents` | integer | 100 | Stop after this many ladders. Open ladders on a normal day: about 48. |
| `enrich` | boolean | true | Join to the NWS station. Charged as `enriched-event` instead of `event`. |
| `includeHourlyPeriods` | boolean | false | Keep every hourly forecast period for the target day in the row. |
| `analysis` | boolean | false | Add a written `analysis_text` to each enriched row (below). Charged as `ladder-analysis` on top of `enriched-event`. |
| `requestDelaySeconds` | number | 0.25 | Delay between requests to either API. |
| `archiveDatasetName` | string | none | Also append every row to this named dataset in your account, for a history that outlives run retention. Not charged again. |

## Output

One row per ladder. Every field below is present on every row; values are `null` when the source has nothing.

**Top level**

| Field | Meaning |
|---|---|
| `event_ticker` | Kalshi event, e.g. `KXHIGHNY-26SEP11`. |
| `series_ticker`, `series_title` | The city's series, e.g. `KXHIGHNY`, "Highest temperature in NYC". |
| `kind` | `high` or `low`. |
| `target_date` | The local calendar day the temperature is measured on (from the ticker). |
| `strike_date` | Kalshi's settlement timestamp. |
| `station` | `{cli_id, icao, city}` read from the rules, e.g. `{"cli_id": "NYC", "icao": "KNYC", "city": "New York City"}`. `null` for international cities. |
| `settlement_source` | `{name, url}` as Kalshi lists it. |
| `rules_primary` | Kalshi's settlement rule text. |
| `markets` | The strikes, sorted from coldest to warmest (below). |
| `market_summary` | The ladder as a distribution (below). |
| `settlement` | `{value_f, yes_ticker, yes_label}` once settled: the temperature Kalshi settled on and the strike that paid out. `null` while open. |
| `status` | The status you asked for. |
| `event_url` | The Kalshi page. |
| `fetched_at_utc` | When this row was built. |
| `enrichment` | The NWS join (below). Present only when `enrich` is on. |

**Each strike in `markets`**

| Field | Meaning |
|---|---|
| `ticker`, `label` | e.g. `KXHIGHNY-26SEP11-B79.5`, `79° to 80°`. |
| `strike_type`, `floor_strike`, `cap_strike` | Kalshi's raw strike fields. |
| `low_f`, `high_f` | Inclusive integer Fahrenheit bounds of a YES; `null` for an open end. The ladder is contiguous: each strike starts one degree after the previous one ends. |
| `yes_bid`, `yes_ask`, `yes_mid`, `last_price`, `previous_price` | Dollars per contract, 0 to 1. |
| `implied_prob` | The YES mid, or the last price if there is no two-sided quote. |
| `normalized_prob` | `implied_prob` divided by the ladder's sum, so the strikes add to one. |
| `volume`, `volume_24h`, `open_interest`, `liquidity_dollars` | As reported by Kalshi. |
| `status`, `result` | `active`, `closed`, `finalized`; `result` is `yes` or `no` once settled, else `null`. |
| `expiration_value` | Once settled, the number Kalshi settled the ladder on (the official temperature), stamped on every strike. `null` while open. |
| `open_time`, `close_time`, `expected_expiration_time`, `updated_time` | UTC timestamps. |

**`market_summary`**

| Field | Meaning |
|---|---|
| `brackets`, `priced` | Strike count and how many have a price. |
| `prob_sum` | Sum of raw `implied_prob` across strikes. Above 1 means the ladder is priced rich. |
| `expected_temp_f` | Probability-weighted temperature using each strike's midpoint (open ends count as half a degree past the edge). |
| `favorite_ticker`, `favorite_label`, `favorite_prob` | The most likely strike and its normalised probability. |
| `total_volume`, `total_open_interest` | Across the ladder. |

**`enrichment`**

| Field | Meaning |
|---|---|
| `status` | `ok`, `partial` (one NWS piece failed; see `problems`), `no_nws_station`, `station_lookup_failed`, `fetch_failed`. Only `ok` and `partial` rows are charged as enriched. |
| `station` | `{icao, name, timezone, lat, lon, grid_id, grid_x, grid_y, forecast_url, forecast_hourly_url, forecast_office}` from api.weather.gov. |
| `observations` | For the target day in station local time: `count`, `max_f`, `max_at`, `min_f`, `min_at`, `latest_f`, `latest_at`, `first_at`. Fahrenheit to one decimal (NWS reports Celsius). Empty before the day begins. |
| `forecast` | NWS daily forecast: `high_f` (the daytime period on the target day), `low_f` (the overnight period covering the target day's early hours), period names and short text, `generated_at`, `updated_at`. |
| `hourly_forecast` | `hours`, `max_f`, `max_at`, `min_f`, `min_at` over the target day's hourly periods; `periods` when `includeHourlyPeriods` is on. |
| `climate_report` | The NWS CLI product for the target day, once issued: `summary_for`, `issued_at`, `issuing_office`, `max_f`, `max_at`, `min_f`, `min_at`, `final` (false for the afternoon partial, true for the next-morning final), `as_of`, `product_id`. `null` until the first issuance, and `null` again once NWS's API stops listing it, about seven days later; after that `settlement.value_f` carries the official number. |
| `analysis` | `forecast`, `hourly_forecast`, `running`, `projected`, `official`: each `{temp_f, ticker, label, implied_prob, normalized_prob}` for the strike that reading lands on. `forecast` is the NWS daily figure, or the hourly figure when the daily period for the day has already passed (`forecast_source` says which). `projected` is the best current estimate of the day's extreme: the warmer (for highs) or cooler (for lows) of what has been observed so far and what the hourly forecast still expects; it equals the forecast before the day starts and converges on the observation as the day ends. `official` comes from the climate report while NWS lists it and from Kalshi's stamped settlement value afterwards (`official_source` says which); `official_final` mirrors the report's `final`. `market_expected_temp_f`, `forecast_minus_market_f`, and `projected_minus_market_f` compare those readings to the market. |

The temperature Kalshi settles on is the official climate report's whole-degree max or min. Observations are hourly (some stations report more often) and can miss the exact peak minute, so `observations.max_f` is a floor on the day's high, not the final value.

With `analysis: true`, each enriched row also carries:

| Field | Meaning |
|---|---|
| `analysis_text` | `{text, model, generated_at}`: 90 to 140 words written by Claude from the row's own numbers: where the market puts its weight and what it expects, where the NWS forecast and the observations so far land and how far that sits from the market, and what settled if the report is in. It recommends nothing; a null field in the row is reported as missing, never guessed. `null` when the model could not produce one (that row is not charged the analysis event). Absent when `analysis` is off or the budget ran out. |

## Pricing

| Event | Price | What it is |
|---|---|---|
| `event` | $0.002 | One ladder, plain. |
| `enriched-event` | $0.02 | One ladder joined to its NWS station. |
| `ladder-analysis` | $0.10 | An enriched ladder that also carries a written `analysis_text`. Charged in addition to `enriched-event`, only for rows where it is not null. |

Apify's free credit covers a few hundred enriched ladders. With a budget set, the actor works out how many ladders it can join, joins those, and returns the rest plain; analyses are capped the same way after the join. Rows without an NWS station are always plain. Nothing is charged for a row that is not returned.

## Use it from Zapier or Clay

No code needed; both call the actor with your own Apify account and you pay the per-row prices above. In Zapier, a **Schedule** trigger (for example 06:00 and 14:00 local), the Apify action **Run Actor** (synchronous) with `{ "status": "open", "stations": ["NYC", "MDW", "MIA"], "enrich": true }`, then **Fetch Dataset Items** into a Google Sheet or a Slack channel gives you each open ladder next to the NWS forecast and running observations. In Clay, **Import data from Apify Actor** with the same input fills a table with one ladder per row.

## Wrap this in an afternoon

Reselling these rows behind your own search box is allowed and expected; it is what the per-row price is for. A zero-dependency starter kit does it: a one-page storefront, Stripe credit packs that never expire (no subscription), and one call to this actor per search. Point it at `perchpermits/kalshi-weather-markets-nws`, set your price per ladder, and deploy anywhere Node runs. Source and setup: [wrapper-kit](https://github.com/101NUTS/perchpermits-actors/tree/master/wrapper-kit). At $0.02 an enriched ladder here, a $0.10 charge per ladder on your side leaves about 80% before Stripe's fee.

## What it refuses to do

If Kalshi's or NWS's payload changes shape, the run fails with a drift error and charges nothing rather than returning empty rows at the enriched price. If every NWS join in a run fails, the whole run fails. Older settled ladders (Kalshi keeps nested markets for roughly the newest two months) are skipped, not invented.

Prices are Kalshi's listed quotes at fetch time, for information only. Kalshi is a regulated US exchange; check your eligibility before trading on any of this.

## Source policy

Two official APIs: `api.elections.kalshi.com/trade-api/v2` (public read endpoints, no key) and `api.weather.gov` (public, identifies itself with a contact User-Agent as NWS asks). One request every 0.25 s by default. Station metadata and forecasts are fetched once per station per run, so a full run of 48 ladders is about 100 Kalshi requests and 100 NWS requests.

Same account, other tools: [ATP and WTA matches with 15+ bookmakers' odds, form, and head-to-head](https://apify.com/perchpermits/tennis-matches-odds-form), [Nashville building permits with the licensed contractor attached](https://apify.com/perchpermits/nashville-building-permits), and [a kitchen floor plan and cabinet takeoff from a JSON room spec](https://apify.com/perchpermits/kitchen-floor-plan-takeoff).
