# Apify Store Trends: demand, ratings, failing incumbents

Which Apify Store niches have demand, and which incumbents are weak. One JSON row per store actor and one per niche, for anyone deciding what to build, what to wrap, or which competitor to go after:

- **Demand**: users in the last 7, 30, and 90 days and lifetime, plus public runs in the last 30 days. Apify publishes no revenue, so `users_30d` is the demand proxy throughout.
- **Reliability**: the 30-day fail rate (failed + timed-out over total public runs), the maintenance notice, and days since the actor last ran.
- **Quality**: the store rating and review count, with ratings under three reviews ignored where a judgement is made.
- **Price**: the listed price in one readable label (`$0.0027/Result`, `$0.005/item`, `$30/mo`, `FREE`) and as a number.
- **Gap signals** per actor: `leader_failing`, `unrated`, `under_maintenance`, `idle_14d`, `low_rated`.
- **Target rows** per niche: total demand, actor count, the leader and its share, the leader's rating and fail rate, the user-weighted rating, the best rating anyone holds, how many actors are broken, and the A to G shortlist flags (poorly served, thin market, monopoly, and so on).
- **Change over time**: point every run at the same named dataset and each row carries what it looked like last snapshot: previous users, the change, previous rating and fail rate, and whether the actor is new.

This is the same scan the developer ran to pick their own niches on the store, turned into an actor. Two modes: `search` answers one question in seconds ("who sells Google Trends data and are they any good?"); `full` sweeps the whole catalog for a monthly snapshot.

Source: the public store endpoint `api.apify.com/v2/store`, no key, no login. Nothing is guessed; missing values are `null`.

## Quick start

Everyone selling Google Trends data, with a niche summary row on top:

```json
{ "query": "google trends" }
```

A niche with only serious incumbents, sorted by demand:

```json
{ "query": "bizbuysell", "minUsers30d": 20, "maxActors": 50 }
```

A monthly snapshot of the whole store with deltas against last month (schedule it; about ten minutes a run):

```json
{ "mode": "full", "maxActors": 5000, "minUsers30d": 5, "snapshotDatasetName": "store-snapshots" }
```

## Input

| Field | Type | Notes |
|---|---|---|
| `mode` | `search` \| `full` | `search` runs the store's own search for `query`: a few requests, seconds. `full` sweeps the whole catalog across every category and pricing model: tens of thousands of actors, about ten minutes, meant for a scheduled monthly snapshot. Default `search`. |
| `query` | string | Required when `mode` is `search`. Ignored in `full` mode. |
| `minUsers30d` | integer | Drop actors with fewer users than this in the last 30 days. Default 5. |
| `maxActors` | 1 to 5000 | Keep at most this many actors, the most used first. Default 200. Set 5000 for a complete snapshot in `full` mode. |
| `includeTargets` | boolean | Also emit one aggregate row per target bucket, and in `search` mode one for the query itself. Default true. |
| `snapshotDatasetName` | string | Optional named dataset in your account. Every actor row is appended to it; before writing, the newest snapshot already there is read to fill `delta`. Use the same name every run. Not charged again. |
| `requestDelaySeconds` | number | Politeness delay toward the store API. Default 0.2. |

## Output

Rows come in two shapes, told apart by `row_type`. Target rows are pushed first, then actor rows sorted by `users_30d` descending (in `search` mode, actors whose title or name match the query come before the rest). The dataset has an `actors` view and a `targets` view.

### Actor rows (`row_type: "actor"`)

| Field | Meaning |
|---|---|
| `actor`, `actor_id`, `title`, `url` | `username/name`, the store's internal id, the display title, and the store page. |
| `target` | The niche bucket the actor falls in, matched by regex over title and name (`google trends`, `zillow`, `government/permits`, about 230 buckets). `(unmatched)` when nothing fits. |
| `query_match` | `search` mode only: whether every word of `query` appears in the actor's title or name. The store's search also matches single words and descriptions, so `google trends` returns every Google actor; the ones that are about the query come first and carry `true`. `null` in `full` mode. |
| `categories` | The store categories the author chose, as a list. |
| `users_7d`, `users_30d`, `users_90d`, `users_total` | Distinct users who ran the actor in each window. `users_30d` is the demand proxy. |
| `runs_30d` | Public runs in the last 30 days. |
| `fail_rate_30d` | (failed + timed-out) / total public runs in the last 30 days. `null` when there were no runs. |
| `rating`, `reviews` | Store rating (0 to 5, two decimals; `null` when unrated) and review count. |
| `bookmarks` | Bookmark count. |
| `price`, `price_usd`, `pricing_model` | A readable label (`$0.0027/Result` for pay-per-event at the free tier, `$0.005/item`, `$30/mo`, `FREE`), the same number alone, and the raw model (`PAY_PER_EVENT`, `PRICE_PER_DATASET_ITEM`, `FLAT_PRICE_PER_MONTH`, `FREE`). |
| `notice` | The store's status notice, e.g. `NONE`, `UNDER_MAINTENANCE`. |
| `days_since_last_run` | Days since the actor last started a run. |
| `agentic_payments` | Whether the store lists the actor as enabled for agentic payments. |
| `gap_signals` | Why this incumbent might be beatable, any of: `leader_failing` (fail rate 30%+ with 10+ monthly users), `unrated` (under three reviews with 20+ monthly users), `under_maintenance`, `idle_14d` (no run for two weeks), `low_rated` (under 3.6 with three or more reviews). Empty when nothing stands out. |
| `snapshot_at` | When this run read the store (UTC). Every row in a run shares it; it is the key that groups a snapshot. |
| `delta` | Change since the previous snapshot in `snapshotDatasetName`: `users_30d_prev`, `users_30d_change`, `rating_prev`, `fail_rate_prev`, `is_new` (the actor was not in the previous snapshot). All `null` when no snapshot dataset is given or it is still empty. |

### Target rows (`row_type: "target"`)

| Field | Meaning |
|---|---|
| `target`, `target_kind` | The bucket name. `site` rows aggregate the actors whose title or name matched that bucket's regex; in `search` mode one `query` row (first in the dataset) aggregates the actors with `query_match: true`. No query row when nothing matched. |
| `hostility` | `high`, `med`, or `low`: a hand-labelled guess at how hard the target site fights scrapers, which sets the on-call load. `med` for query rows. |
| `actors`, `active_actors` | Actors in the bucket (after `minUsers30d`), and how many had at least one user in 30 days. |
| `users_30d`, `users_total` | Summed over the bucket. |
| `leader`, `leader_url`, `leader_share` | The most-used actor, its page, and its share of the bucket's 30-day users. |
| `leader_rating`, `leader_reviews`, `leader_fail_rate`, `leader_price`, `leader_signals` | The leader's rating, review count, 30-day fail rate, price label, and gap signals. |
| `weighted_rating` | User-weighted mean rating over actors with three or more reviews. `null` when none qualify. |
| `best_rated`, `best_rating` | The best-rated actor with five or more reviews, and its rating. |
| `broken_actors` | Actors with 10+ monthly users failing 40%+ of runs. |
| `signals` | The A to G shortlist rules: `A_poorly_served` (150+ monthly users and weighted rating under 4.0, or the leader fails 30%+, or nothing with five reviews rates 4.2+), `B_big_actor_low_rated` (an actor with 500+ lifetime users rated under 3.6), `C_actor_failing_in_use` (an actor with 25+ monthly users and 100+ runs failing 40%+), `D_incumbent_idle` (an actor with 20+ monthly users under maintenance or idle two weeks), `E_thin_market` (40+ monthly users and six or fewer active actors), `F_low_hostility_demand` (low hostility and 60+ monthly users), `G_monopoly` (300+ monthly users and the leader holds 80%+). |
| `snapshot_at` | Same as on actor rows. |
| `delta` | `users_30d_prev`, `users_30d_change`, `actors_prev`, `leader_prev`, `is_new`, computed from the previous snapshot's actor rows in the same bucket. All `null` without a snapshot dataset. |

## Pricing

Pay per event. Free-tier credit covers thousands of rows.

| Event | Price | When |
|---|---|---|
| `actor` | $0.001 | One actor row. |
| `target` | $0.01 | One target row (a niche summary, or the query summary). |

A search for one niche is typically 20 to 60 actor rows and a handful of target rows: a few cents. A full monthly snapshot at `maxActors: 5000` is about $5 plus the target rows.

If `maxTotalChargeUsd` is set, the actor works out up front how many rows fit: target rows first, then as many actor rows as the remaining budget allows. Rows past the cap are dropped, never charged. A run is never charged for rows it did not return.

## Limits and honesty

- Demand is `totalUsers30Days` from the store; Apify publishes no revenue, so this is a count of people, not dollars. Runs and fail rates are the store's public run statistics.
- Target buckets come from a regex over title and name only (descriptions drag generic actors into every bucket they mention), so a few actors land in the wrong bucket; check the actor rows before acting on a target row. Hostility is a hand-labelled guess.
- The store's search matches single words and descriptions, so `google trends` also returns every Google actor. Those rows are kept (they are what a buyer browsing the store sees) but ranked after the ones whose title or name carry the whole query, and flagged with `query_match`. The query target row counts only the matches.
- `full` mode unions the catalog across the global popularity sort, every category, and every pricing model, because the global sort is unstable past about 12,000 rows. Expect tens of thousands of items and about ten minutes; set `maxActors` to what you want kept.
- Deltas compare against the newest snapshot in the named dataset, whatever its age; if you run weekly, deltas are weekly. Rows with the same `snapshot_at` are one snapshot.
- If the store API changes shape (no `items` list, or an item without `stats.totalUsers`) the run fails loudly with `Source drift detected` and nothing is charged, rather than returning empty rows.

## Support

Open an issue on the actor page. Changes at the source are fixed within days.
