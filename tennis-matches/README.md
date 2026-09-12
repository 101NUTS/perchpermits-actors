# Tennis Matches + Odds, Form & Head-to-Head (ATP/WTA)

One JSON row per tennis match with everything a betting model or an AI agent asks for before it prices a match, in a single call:

- **The match**: date, start time in UTC, tournament, tour (ATP/WTA), round, surface, both players with seeds, status (scheduled, in progress, finished), and the score with tiebreaks.
- **Odds**: the site's reference odds on the list, plus, when enriched, 15+ bookmakers' **current and opening** odds per player, the trend since opening, the best available price and who offers it, and optionally the full timestamped movement history.
- **Form**: each player's last N matches (opponent, round, date, score, won/lost), a W/L sequence like `WWLWW`, the current streak, and this year's win/loss record by surface.
- **Head-to-head**: the total and every previous meeting with year, tournament, surface, round, and score.
- **Rankings and bio**: singles rank, birthdate, height, weight, handedness.

Other tennis actors on the store sell one of those pieces each. This one joins them so an agent does not need four actors and a merge step.

Source: TennisExplorer, public pages, no login. Coverage is ATP, WTA, Challenger, ITF, and UTR events. Nothing is guessed; missing values are `null`.

## Quick start

Today's ATP singles with odds, form, and head-to-head:

```json
{ "tour": "atp" }
```

Every match at a tournament over the next three days, plain rows only (cheapest):

```json
{ "tournament": "US Open", "days": 3, "enrich": false, "maxMatches": 500 }
```

Yesterday's finished WTA results with closing odds and the score:

```json
{ "mode": "results", "date": "2026-09-08", "tour": "wta", "enrich": false }
```

Everything in progress right now, with current set scores and live bookmaker prices:

```json
{ "mode": "live" }
```

One player's next match with the full odds movement from every bookmaker:

```json
{ "player": "Alcaraz", "days": 7, "status": "scheduled", "oddsHistory": true, "maxMatches": 1 }
```

## Input

| Field | Type | Notes |
|---|---|---|
| `mode` | `schedule` \| `results` \| `live` | Schedule lists upcoming, live, and completed matches of the day. Results lists finished matches only. Live lists matches in progress right now with the current set scores, a snapshot as of `fetched_at_utc`; it ignores `date` and `days`. Default `schedule`. |
| `date` | `YYYY-MM-DD` | Defaults to today. The site's day runs on Central European time. |
| `days` | 1 to 14 | Consecutive days starting at `date`. Default 1. |
| `tour` | `all` \| `atp` \| `wta` | Default `all`. |
| `doubles` | boolean | Doubles instead of singles. Default false. |
| `tournament` | string | Case-insensitive substring of the tournament name. |
| `player` | string | Case-insensitive substring of either player's name. |
| `status` | `scheduled` \| `in_progress` \| `finished` | Keep only one state. |
| `maxMatches` | integer | Stop after this many. Default 50. |
| `enrich` | boolean | Fetch each match's detail page. Default true. One request per match, about a second each. |
| `latestMatches` | 1 to 30 | Recent matches per player when enriching. Default 10. |
| `oddsHistory` | boolean | Include every recorded odds change per bookmaker. Default false. |
| `preview` | boolean | Add a written `preview` to each enriched row (below). Default false. Charged as `match-preview` on top of `enriched-match`. |
| `requestDelaySeconds` | number | Politeness delay toward the source. Default 0.4. |

## Output

Every row has these top-level fields:

| Field | Meaning |
|---|---|
| `match_id`, `match_url` | Source identifiers. |
| `date`, `time_site`, `start_utc` | Calendar day and start time as listed (Central European time) and converted to UTC. |
| `fetched_at_utc` | When this row was read from the source. Age a live or odds snapshot with it. |
| `status` | `scheduled`, `in_progress`, or `finished`. |
| `tournament` | `name`, `url`, `slug`, `year`, `tour` (`atp`/`wta`), `format` (`singles`/`doubles`). |
| `round`, `surface` | From the detail page (enriched rows only). |
| `home`, `away` | `name`, `slug`, `url`, `seed`. "Home" is the first-listed player. |
| `odds` | Reference odds from the list page: `home`, `away` (decimal). |
| `h2h_list` | Head-to-head count as shown on the schedule list, when present. |
| `result` | `winner` (`home`/`away`), `sets_home`, `sets_away`, `sets[]` with `home`, `away`, `tiebreak_loser_points`, and `score` like `5-7, 3-6, 7-5, 6-3, 7-6(6)`. |
| `result_note` | `retired_or_walkover` when a finished match ended short. |

With `enrich` (default), the `enrichment` block adds:

| Field | Meaning |
|---|---|
| `status` | `ok`, `partial` (page found but no odds and no recent matches), `parse_failed`, `fetch_failed`. Only `ok` and `partial` rows are charged as enriched. |
| `home`, `away` | `full_name`, `singles_rank`, `birthdate`, `height`, `weight`, `plays`, `turned_pro`, `surface_record` (this year's `{wins, losses}` per surface), `form` (`played`, `wins`, `losses`, `streak` like `4W`, `sequence` like `WWWWL`), `latest_matches[]` (`date`, `tournament`, `round`, `round_full`, `opponent`, `won`, `sets`, `score`, `match_id`). |
| `h2h` | `home_wins`, `away_wins`, `matches[]` with `year`, `tournament`, `surface`, `round`, `winner_name`, `loser_name`, `score`, `match_id`. |
| `bookmakers[]` | Per bookmaker: `home`, `away` (current decimal odds), `home_opening`, `away_opening`, `home_trend`, `away_trend` (`up`/`down`), and with `oddsHistory` the `home_history[]`/`away_history[]` of `{at_site, odds, change}`. |
| `bookmaker_count`, `best_odds` | How many books priced the match, and the highest `home`/`away` price with the bookmaker offering it. |
| `market` | Derived from every bookmaker that priced both sides: `consensus_home`/`consensus_away` (mean odds), `fair_prob_home`/`fair_prob_away` (implied probabilities with the margin removed), `overround`, `favorite`, `opening_consensus_*`, `opening_fair_prob_home`, and `prob_shift_home` (how far the fair probability moved since opening; negative means the market turned against the first player). |
| `final_score` | For finished matches, the detail page's own score line, e.g. `2 : 1 (4-6, 6-4, 7-6(8))`. |
| `home.partner`, `away.partner` | Doubles only: the second player of each pair. Rankings and bio describe the first-listed player. |

With `preview: true`, each enriched row also carries:

| Field | Meaning |
|---|---|
| `preview` | `{text, model, generated_at}`: 90 to 140 words written by Claude from the row's own numbers: what the market prices and how it moved, then which of the form, surface, ranking, and head-to-head facts support or cut against that price. It names no winner and recommends nothing; a null field in the row is reported as missing, never guessed. `null` when the model could not produce one (that row is not charged the preview event). Absent when `preview` is off or the budget ran out. |

## Pricing

Pay per event. Free-tier credit covers a few hundred matches.

| Event | Price | When |
|---|---|---|
| `enriched-match` | $0.02 | A match row with the `enrichment` block (odds, form, head-to-head). |
| `match` | $0.002 | A plain match row (`enrich: false`, or the budget ran out). |
| `match-preview` | $0.10 | An enriched row that also carries a written `preview`. Charged in addition to `enriched-match`, only for rows where `preview` is not null. |

If `maxTotalChargeUsd` is set, the actor works out up front how many matches it can enrich within the budget, enriches those, and returns the rest plain; previews are capped the same way after enrichment. A run is never charged for rows it did not return. Failed enrichment (site error, layout change) is returned plain and charged as `match`.

## Wrap this in an afternoon

Reselling these rows behind your own search box is allowed and expected; it is what the per-row price is for. A zero-dependency starter kit does it: a one-page storefront, Stripe credit packs that never expire (no subscription), and one call to this actor per search. Point it at `perchpermits/tennis-matches-odds-form`, set your price per row, and deploy anywhere Node runs. Source and setup: [wrapper-kit](https://github.com/101NUTS/perchpermits-actors/tree/master/wrapper-kit). At $0.02 an enriched row here, a $0.10 charge per row on your side leaves about 80% before Stripe's fee.

## Limits and honesty

- Times are converted from Central European time to UTC. If the source changes its display timezone the `start_utc` values shift; `time_site` is always the raw value.
- Live scores are set-level snapshots at run time, not a point-by-point stream. Run again for an update; `fetched_at_utc` tells you how old a row is.
- If every enrichment in a run fails, the run fails and nothing is charged, so a blocked or changed source never turns into silent plain rows at the enriched price.
- The source does not publish match statistics (aces, serve percentages) on these pages, so none are returned.
- Odds are the bookmakers' listed prices at the time the source recorded them; they are for information, not a betting instruction. Check your local law before acting on them.
- If the source changes its layout the run fails loudly with `Source drift detected` and nothing is charged, rather than returning empty rows.

## Support

Open an issue on the actor page. Layout changes at the source are fixed within days.
