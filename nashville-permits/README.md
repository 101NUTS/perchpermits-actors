# Nashville Building Permits (Enriched)

Structured building-permit intelligence for **Nashville / Davidson County, Tennessee**, built for AI agents and scripts that need one clean JSON answer per call.

Two things make this different from a raw open-data dump:

1. **Enrichment.** Metro's open dataset lists the paperwork *applicant*, not the licensed contractor, the property owner, the sub-trade permits still required, or how far inspections have progressed. Turn on `enrich` and every permit is joined to Metro's ePermits case record, which carries all four.
2. **Scope presets that ignore boilerplate.** Almost every Nashville residential permit says "not to add a second kitchen". A naive keyword search returns thousands of false kitchen remodels. The `kitchen` preset (and 21 others) strips that boilerplate first, then requires a real remodel phrase.

All data is public record. Nothing is scraped from listing sites, and no field is guessed: missing values are `null`.

## Quick start

Search the last 90 days of real residential kitchen remodels:

```json
{ "scope": "kitchen", "residentialOnly": true, "maxRecords": 100 }
```

Full case lookup for one permit (contractor, owner, sub-trades, inspections):

```json
{ "permitNumber": "2026073320", "enrich": true }
```

Everything filed or issued at a parcel:

```json
{ "parcel": "11801002600", "datasets": "both" }
```

Who is building pools in Nashville, ranked, last 12 months:

```json
{ "mode": "contractors", "scope": "pool", "dateFrom": "2025-09-01", "minPermits": 2 }
```

Pending applications above $250k in two ZIPs, with the licensed contractor attached:

```json
{ "datasets": "applications", "zips": ["37205", "37215"], "minValuation": 250000, "enrich": true }
```

## Input

| Field | Type | Notes |
|---|---|---|
| `mode` | `search` \| `contractors` | One row per permit, or one row per ranked contractor. Default `search`. |
| `datasets` | `both` \| `issued` \| `applications` | Applications are permits filed but not yet issued (early signal). Default `both`. |
| `dateFrom`, `dateTo` | `YYYY-MM-DD` | Issue date for issued permits, filing date for applications. Defaults to the last 90 days unless `permitNumber`, `parcel`, or `address` is given. |
| `scope` | preset key | `any`, `kitchen`, `bathroom`, `interior_remodel`, `addition`, `new_construction`, `adu`, `roofing`, `pool`, `fence`, `deck`, `garage`, `demolition`, `solar`, `hvac`, `electrical`, `plumbing`, `foundation`, `windows_doors`, `commercial`, `multifamily`, `short_term_rental`, `sign`. |
| `keyword` | string | Free-text substring on the description, server-side, case-insensitive. |
| `address` | string | Substring of the street address (Metro stores them upper-case, no unit numbers). |
| `parcel` | string | Exact Davidson County parcel ID. |
| `zips` | string[] | Restrict to ZIP codes. |
| `applicant` | string | Substring of the applicant / contact on the open dataset. |
| `permitNumber` | string | Exact permit number. |
| `permitType` | string | Substring of Metro's permit type or subtype, e.g. `Residential - Rehab`. |
| `minValuation`, `maxValuation` | integer | Declared construction cost in USD. |
| `residentialOnly` | boolean | Drop permits whose text reads as commercial. |
| `maxRecords` | integer | Cap on rows returned. Default 200, max 5000. |
| `enrich` | boolean | Join to ePermits. About one second per permit (politely rate-limited). |
| `enrichMaxAgeDays` | integer | Reuse cached ePermits data younger than this. Default 7. |
| `minPermits` | integer | Contractors mode: hide contractors below this many matching permits. |
| `maxPermitsScanned` | integer | Contractors mode: how many matching permits (newest first) feed the ranking. Default 2000. Not billed. |

## Output: permit record

```json
{
  "permit_number": "2026073320",
  "source": "issued",
  "status": "issued",
  "date_entered": "2026-09-02",
  "date_issued": "2026-09-04",
  "permit_type": "Building Residential - Rehab",
  "permit_subtype": "Single Family Residence",
  "type_code": "CARR",
  "subtype_code": "CAA01R301",
  "description": "Interior renovations to existing kitchen relocating gas range and moved kitchen island...",
  "address": "1320 SWEETBRIAR AVE",
  "city": "NASHVILLE",
  "state": "TN",
  "zip": "37212",
  "parcel": "11801002600",
  "subdivision_lot": null,
  "council_district": 18,
  "lon": -86.7913,
  "lat": 36.1244,
  "valuation": 100000.0,
  "applicant": "sapphire development",
  "applicant_is_owner": false,
  "is_residential": true,
  "scope_tags": ["kitchen", "interior_remodel"],
  "ivr_track": 4946321,
  "enrichment": null
}
```

| Field | Meaning |
|---|---|
| `source` / `status` | `issued` or `applications` / `applied`. |
| `applicant` | Who filed the paperwork. Often, but not always, the contractor. `applicant_is_owner` is true for owner-builder filings. |
| `is_residential` | `true`, `false`, or `null` when the text does not say. |
| `scope_tags` | Every preset that matches this permit, so an agent can classify without re-querying. |
| `ivr_track` | Metro's case ID, the key into ePermits. |
| `valuation` | Declared construction cost as filed. Not an appraisal. |

## Output: `enrichment` block (when `enrich: true`)

```json
{
  "status": "ok",
  "source": "live",
  "fetched_at": "2026-09-07T22:14:03+00:00",
  "missing_endpoints": [],
  "case": {
    "case_number": "2026073320",
    "status": "Issued",
    "decision": "Issued",
    "description": "Building Residential - Rehab / Single Family",
    "project_scope": "Interior renovations to existing kitchen ...",
    "applicant": "sapphire development",
    "accepted": "2026-08-21",
    "issued": "2026-09-04",
    "expires": "2028-09-04",
    "is_expired": false,
    "fees": 812.5,
    "payments": 812.5,
    "amount_due": 0.0
  },
  "contractor": {
    "company": "SAPPHIRE DEVELOPMENT LLC",
    "is_owner_builder": false,
    "contact": null,
    "license": "MCN152005",
    "license_expires": "2999-12-31",
    "phone": "(615) 555-0100",
    "email": "office@example.com",
    "city": "NASHVILLE",
    "state": "TN",
    "zip": "37212",
    "classifications": "BC-A, BC-B"
  },
  "owner": { "name": "...", "mailing_city": "NASHVILLE", "mailing_state": "TN", "mailing_zip": "37212" },
  "sub_trade_permits": [
    { "code": "CAPLMB", "description": "Plumbing Permit - CAPL", "satisfied": true,  "date_completed": "2026-09-04" },
    { "code": "CAHVAC", "description": "Mechanical Permit - CAMC", "satisfied": false, "date_completed": null }
  ],
  "outstanding_sub_trades": ["CAAL", "CAHVAC", "CALV", "CAAF"],
  "inspections": {
    "completed_count": 0,
    "scheduled_count": 1,
    "latest_completed": null,
    "history": []
  },
  "inspection_stage": "permit_issued"
}
```

| Field | Meaning |
|---|---|
| `status` | `ok` (all five ePermits endpoints answered), `partial`, `failed`, or `no_ivr_key`. |
| `contractor` | The **licensed** contractor of record, with Metro license number. `is_owner_builder` marks the SELF CONTRACTOR placeholder. |
| `owner` | Current property owner as listed on the case. |
| `sub_trade_permits` | Each required trade permit (plumbing, electrical, mechanical, gas, low-voltage, fire alarm, ...) and whether it has been pulled. |
| `outstanding_sub_trades` | Codes not yet satisfied. Each one is a live lead for that sub-trade. |
| `inspection_stage` | Description of the latest passed inspection, or `permit_issued` when work has not started. |

Common sub-trade codes: `CAPLMB` plumbing, `CAELEC` electrical, `CAHVAC` mechanical, `CAGM` gas/mechanical, `CAGA`/`CAGH`/`CAGJ` gas, `CALV` low voltage, `CAAF`/`CAAL` fire alarm / life safety.

## Output: contractor row (`mode: contractors`)

```json
{
  "rank": 1,
  "contractor": "SPARKS, KEVIN SIGNATURE POOLS, LLC",
  "license": "MCN0XXXXX",
  "permits": 14,
  "issued_permits": 12,
  "pending_applications": 2,
  "total_valuation": 1273609.0,
  "median_valuation": 85000.0,
  "first_permit": "2025-09-10",
  "last_permit": "2026-09-04",
  "zips": ["37076", "37204", "37206", "37207"],
  "scopes": ["pool", "deck"],
  "sample_permits": [ { "permit_number": "...", "date": "...", "address": "...", "valuation": 91000.0, "description": "..." } ]
}
```

Groups by the licensed contractor when enrichment is on, otherwise by applicant. Owner-builders are excluded. Spelling variants of one company ("RONDO POOLS LLC", "Rondo Pools, LLC.") are merged into one row; placeholder names ("see ePermits", "not published") are dropped. `license` is filled only when enrichment is on.

## Pricing

Pay per row delivered. Nothing is charged for a run that returns no rows.

| Event | When |
|---|---|
| `permit` | One permit record from the open dataset. |
| `enriched-permit` | One permit record joined to ePermits. |
| `contractor` | One ranked contractor row. |

Set `maxTotalChargeUsd` on the run to cap spend. Enrichment is trimmed to what the cap allows before any slow fetching starts, and records beyond the cap are returned un-enriched at the `permit` price.

## Coverage and freshness

- Issued permits: Metro Nashville "Building Permits Issued", about 29,000 rows over a rolling three-year window, refreshed daily.
- Applications: about 6,000 rows, rolling three-year window, refreshed daily.
- ePermits: real-time, fetched on demand at one request per second and cached for `enrichMaxAgeDays`.
- Davidson County only. Other Tennessee counties will be added if there is demand.

## Sources and data policy

- Metro Nashville Open Data, [Building Permits Issued](https://data.nashville.gov/datasets/Nashville::building-permits-issued/about) and [Building Permit Applications](https://datanashvillegov-nashville.hub.arcgis.com/datasets/Nashville::building-permit-applications/about), ArcGIS feature services published under Metro's open data terms.
- Metro Nashville [ePermits](https://epermits.nashville.gov/) public case search.
- Building permits are open public records under Tennessee's Public Records Act (T.C.A. 10-7-503). This actor republishes what those sources already publish and adds organization, not surveillance. Do not use contact details for unsolicited marketing where prohibited.

## Use it from Clay or Zapier

No code needed. Both tools call this actor with your own Apify account, so you pay the per-record prices above and nothing else.

### Clay: contractors into a table, then emails and phones

The `contractors` mode produces the list an outbound team wants: every licensed contractor doing a kind of work in Nashville, ranked by permit count, with their license number and the ZIP codes they work in. Clay then does what it is good at, finding the people behind each company.

1. In a Clay table, click **Add enrichment**, search for **Apify**, and pick **Import data from Apify Actor**. Connect your Apify account when asked.
2. Choose the actor `perchpermits/nashville-building-permits` and paste this input:

   ```json
   {
     "mode": "contractors",
     "scope": "pool",
     "dateFrom": "2025-09-01",
     "minPermits": 2,
     "enrich": true
   }
   ```

   Change `scope` to any preset from the Input table (`kitchen`, `roofing`, `solar`, `adu`, and so on). `enrich: true` groups by the licensed contractor rather than the paperwork applicant; leave it on for this use.
3. Each contractor row lands in the table with `contractor`, `license`, `permits`, `total_valuation`, `zips`, and `scopes` as columns. Add Clay's own enrichments on top: a company-domain lookup on `contractor`, then a work-email or phone waterfall, then your outreach step.
4. To keep the list fresh, use **Run Apify Actor** instead with **auto-update** on and a scheduled row, or run the import on a Clay schedule. One run a week is plenty; permits move slowly.

For a per-address workflow (one property per row, for example a list of addresses you already own), use **Run Apify Actor** with `"mode": "search"` and put the Clay column token where the value goes, keeping the key in quotes and the token unquoted:

```json
{ "mode": "search", "address": /Address, "dateFrom": "2024-01-01", "enrich": true }
```

That returns the permits on that address with the licensed contractor, the owner, and the inspection status in the `enrichment` block.

### Zapier: a weekly permit feed into Sheets, Slack, or a CRM

1. Create a Zap with **Schedule by Zapier** as the trigger (every Monday, say).
2. Add the **Apify** action **Run Actor** (synchronous), choose this actor, and give it an input such as:

   ```json
   { "mode": "search", "scope": "kitchen", "dateFrom": "{{last week}}", "enrich": true, "maxRecords": 100 }
   ```

   Zapier's date formatter step can produce the `dateFrom` value (today minus 7 days, formatted `YYYY-MM-DD`).
3. Add the Apify action **Fetch Dataset Items** on the run's default dataset, then send each item wherever you work: a Google Sheets row, a Slack message, a HubSpot or Pipedrive company.

Zapier's own trigger **Actor Run Finished** also works if you prefer to schedule the run in the Apify Console and let Zapier pick up the output.

## Wrap this in an afternoon

Reselling these records behind your own search box is allowed and expected; it is what the per-record price is for. A zero-dependency starter kit does it: a one-page storefront, Stripe credit packs that never expire (no subscription), and one call to this actor per search. Point it at `perchpermits/nashville-building-permits`, set your price per record, and deploy anywhere Node runs. Source and setup: [wrapper-kit](https://github.com/101NUTS/perchpermits-actors/tree/master/wrapper-kit). Keep the data-policy line above on your page.

## Running locally

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python -m src.nashville_permits.cli search --scope kitchen --days 30 --max 10 --enrich
python -m src.nashville_permits.cli contractors --scope pool --days 365 --min-permits 2
python -m unittest tests.test_offline
```

The `nashville_permits` package is standard library only; the Apify SDK is needed just for `src/main.py`.
