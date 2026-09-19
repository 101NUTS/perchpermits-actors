# Pre-registrations

Tests whose rules were fixed before any result was seen. The rules document is published
with the result; anyone can check it against the fingerprint below.

## WeatherNext 3 vs NWS National Blend (frozen 2026-09-19)

- Question: next-day highs and lows at the 24 stations that settle Kalshi temperature
  markets, target days 2026-07-01 to 2026-09-18. Scored on or after 2026-09-25; published
  pass or fail.
- SHA-256 of the rules document:
  `24033443dcc8ab2366a17acd0ffabcf4bc5fabefc9168181beef2baae110ffb6`
- Announced on X: https://x.com/perchpermits/status/2101259192082309551
- Bitcoin timestamp (OpenTimestamps proof, attested in Bitcoin block 967696): `weathernext-backtest-2026-09-19.ots`.
  Verify with the rules document once published: `ots verify weathernext-backtest-2026-09-19.ots -f <rules file>`.
