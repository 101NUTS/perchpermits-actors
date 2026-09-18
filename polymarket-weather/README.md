# Polymarket Weather Markets + Settling Station Data

Every Polymarket daily **highest** and **lowest** temperature ladder, about 50 cities worldwide, one row per city-day, joined to the airport station the market's own rules settle on.

## Why the station matters

Polymarket names a specific station in each market's rules, and it is often not the one you would guess or the one another venue uses for the same city:

| City | Polymarket settles on | Kalshi settles on |
|---|---|---|
| New York | LaGuardia (KLGA) | Central Park |
| Chicago | O'Hare (KORD) | Midway |
| Denver | Buckley Space Force Base (KBKF) | Denver Intl |
| Dallas | Love Field (KDAL) | DFW |
| Paris | Le Bourget (LFPB) | none |
| London | London City (EGLC) | none |

This actor reads the station from every market's rules text on every run, so a changed station is picked up the day it changes.

## What a row contains

- **Ladder:** every bracket with bid, ask, implied probability, last trade, volume and liquidity; the market's favourite and probability-weighted expected temperature; the paid bracket once settled.
- **Station:** ICAO code, name, source type (NOAA station page, Weather Underground, Hong Kong Observatory), time zone, and the market's unit (°F or °C).
- **Observations** (joined rows): the station's reports for its local calendar day, recomputed the way the rules read them: whole degrees, routine hourly reports only where the rules say "Hourly Data". You get the running high and low with times, the latest reading, and whether the day is complete.
- **Floor rule-outs:** brackets the day's running high (or low) has already made impossible.
- **Forecast:** the National Weather Service daily high or low, for US stations.
- **Verdict:** where the running value, the forecast and the market favourite each land; the final bracket once the day is complete; and the paid bracket once Polymarket settles.

## Verified

Every settled ladder is archived daily with the bracket Polymarket paid and the value this actor recomputes from the settling station's own reports. Over Sep 14 to 17, 2026 (51 cities, highs and lows, °F and °C), the recomputed value landed in the paid bracket **325 times out of 326**.

- **The one miss:** Manila's low on Sep 16. The station's routine reports reached 25°C and Polymarket paid 26°C. It stays in the record.
- **No call:** 82 more ladders. Hong Kong settles on the Observatory's daily extract rather than an airport report, a few stations settle on a Weather Underground page with no reports in NOAA's feed, and some days in that first backfill started before the oldest report NOAA still serves.
- **Test suite:** a fixed set of 89 of those ladders is recomputed in `scripts/verify_settled.py` on every build.

## Input

| Field | Default | Meaning |
|---|---|---|
| `kind` | `both` | `high`, `low` or `both` |
| `cities` | all | Polymarket city slugs: `nyc`, `london`, `tokyo`, `los-angeles`... |
| `dateFrom` / `dateTo` | none | Look up each listed city's ladder for every day in the range, open or settled. Needs `cities`. |
| `join` | `true` | Add the station join (charged as `joined-ladder`) |
| `maxEvents` | 100 | Stop after this many ladders |

## Pricing

Pay per event: `ladder` for a plain ladder row, `joined-ladder` for a row joined to its station. The join is capped up front to what your run budget allows, and rows past the cap come back plain.

## Sources

All free, official and keyless: Polymarket's public Gamma API, NOAA Aviation Weather Center METARs, and api.weather.gov. The official settlement value is always the one on the market's resolution page. This actor recomputes it from the same reports; it is not affiliated with Polymarket or NOAA.

Also by Perch Data: [Kalshi Weather Markets + NWS Station Data](https://apify.com/perchpermits/kalshi-weather-markets-nws).
