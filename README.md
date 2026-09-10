# Perch Permits actors

Source for the Apify Store actors published by [Perch Permits](https://apify.com/perchpermits). Both are standard-library Python; the Apify SDK is only used by each actor's `src/main.py`.

| Actor | Store page | What it does |
|---|---|---|
| [nashville-permits](nashville-permits/) | [apify.com/perchpermits/nashville-building-permits](https://apify.com/perchpermits/nashville-building-permits) | Nashville / Davidson County TN permits and applications joined to the licensed contractor, owner, outstanding sub-trade permits, and inspection stage. 22 scope presets. Contractor ranking. |
| [kitchen-plan](kitchen-plan/) | [apify.com/perchpermits/kitchen-floor-plan-takeoff](https://apify.com/perchpermits/kitchen-floor-plan-takeoff) | JSON room spec in; dimensioned floor plan (SVG), cabinet takeoff (CSV), and quote totals out. |

Each folder has its own README, the same one shown on the store page, with every input and output field documented.

## Run locally

```bash
cd nashville-permits
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # or .venv/bin/pip
python -m src.nashville_permits.cli search --scope kitchen --days 30 --max 10 --enrich
python -m unittest tests.test_offline
```

```bash
cd kitchen-plan
python -m src.kitchen_plan.cli tests/fixtures/kitchen_example.json --out out/example
python -m unittest tests.test_kitchen_plan
```

## Data sources and policy

- Metro Nashville Open Data (ArcGIS feature services for permits issued and permit applications) and the Metro ePermits public case API. Building permits are public records under Tennessee's Public Records Act (T.C.A. 10-7-503).
- The permits actor reads politely: one ePermits request per second, an identifying User-Agent, a shared cache, and a drift guard that aborts the run rather than returning nulls if Metro changes a field.
- No private sites are scraped and no field is guessed. Missing values are `null`.

## License

MIT for the code in this repository. Data returned by the actors is public record and carries no license from Perch Permits.
