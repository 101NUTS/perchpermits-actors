# Wrap an Apify actor in an afternoon

A small Node.js storefront that resells rows from a pay-per-event Apify actor for prepaid credits. A visitor buys a credit pack through Stripe Checkout, gets a key, and spends credits on searches. You keep the difference between what they pay you and what the actor charges you.

- Zero npm dependencies. Node 18 or newer, built-in `fetch`, `node:http`, `node:crypto`, `node:test`.
- No database. Stripe is the ledger: a customer's balance lives in their Stripe customer `metadata.credits`.
- One process, one folder. Deploys to Render, Railway, Fly, or any small VPS.
- Ships pointed at `perchpermits~tennis-matches-odds-form` (ATP and WTA matches with bookmaker odds, form, and head-to-head), with a form and result table for it. Swapping in another actor is a config change plus a few form fields.

This is a starter, not a product. Read the limitations section before taking real money.


The page ships as a finished storefront named "Courtline" (a placeholder brand): a header, a hero, the search form, key and credit cards, a three-step explainer, and result cards with fair-probability bars. Light and dark follow the visitor's system setting. Rename it and restyle it in `public/index.html`; the JavaScript at the bottom of that file is the only part the server depends on (element ids and the four API calls).

## How it works

1. `POST /api/checkout` creates a Stripe Checkout Session in payment mode (one-time charge, no subscription) with `customer_creation=always` and the pack size in the session metadata. The browser is sent to Stripe.
2. Stripe redirects back to `/?session_id=...`. The page calls `GET /api/claim?session_id=...`, which verifies the session is paid, adds the credits to the customer's metadata, records the session id so it cannot be claimed twice, and returns a token.
3. The token is `<stripe customer id>.<HMAC-SHA256 of that id under TOKEN_SECRET>`. It is the customer's API key. Nothing is stored on the server; the HMAC proves the server issued it.
4. `POST /api/search` with `Authorization: Bearer <token>` reads the balance, runs the actor with a row limit the balance can afford, deducts `rows * CREDITS_PER_ROW`, and returns the rows.
5. `GET /api/balance` returns the current balance.

Credits never expire. There is nothing to cancel because there is nothing recurring.

## The money

Defaults: a pack is 200 credits for $20.00 (10 cents per credit), and one row costs 5 credits (50 cents). The tennis actor charges $0.02 per enriched match row.

Per row:

| | |
|---|---|
| Customer pays you | $0.50 |
| Actor charges you | $0.02 |
| Gross margin per row | $0.48 (96%) |

Per pack, before hosting:

| | |
|---|---|
| Pack revenue | $20.00 |
| Stripe fee (2.9% + $0.30 on a US card) | $0.88 |
| Actor cost if every credit is spent (40 rows at $0.02) | $0.80 |
| Net | $18.32 |

Things the table leaves out:

- Apify bills the actor's per-row events against your Apify account. The free plan includes a small monthly allowance; past that you need a paid plan, which is a fixed monthly cost on top of the per-row price. Check the current plans on apify.com before you price your pack.
- Hosting: a small instance on any of the hosts above is a few dollars a month.
- Actor runs that return zero rows cost you nothing and the customer nothing.
- The 96% number is only true if customers value a row at 50 cents. If they do not, lower `CREDITS_PER_ROW` or raise `PACK_CREDITS`. The arithmetic above is the whole model; there is no hidden fee on either side.

## Setup

You need a Stripe account and an Apify account. Both have free tiers that are enough to try this end to end.

1. Copy the config and fill it in.

   ```
   cp .env.example .env
   ```

   - `STRIPE_SECRET_KEY`: from the Stripe dashboard, Developers, API keys. Use the `sk_test_...` key first.
   - `APIFY_TOKEN`: Apify Console, Settings, Integrations, API tokens.
   - `TOKEN_SECRET`: a long random string. `node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"` prints one. Changing it later invalidates every key you have issued.
   - `BASE_URL`: where this server is reachable. Stripe redirects there after payment. `http://localhost:3000` for local testing.

2. Run it.

   ```
   npm start
   ```

   Open http://localhost:3000. Click the buy button; in Stripe test mode, card number `4242 4242 4242 4242` with any future expiry and CVC completes the payment. You are redirected back with a key and 200 credits. Run a search.

3. Run the tests.

   ```
   npm test
   ```

   The tests mock Stripe and Apify behind `globalThis.fetch`; no network, no keys needed.

4. Deploy.

   Any host that runs `node server.js` and lets you set environment variables works. Set every variable from `.env.example` in the host's dashboard instead of shipping a `.env` file. Set `BASE_URL` to the public URL (with `https`), and once real payments should work, swap `STRIPE_SECRET_KEY` for the live key. `PORT` is read from the environment, which is what Render, Railway, and Fly expect.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `STRIPE_SECRET_KEY` | required | Stripe secret key. |
| `APIFY_TOKEN` | required | Apify API token. The actor's charges land on this account. |
| `APIFY_ACTOR` | `perchpermits~tennis-matches-odds-form` | Actor to run, as `owner~name`. |
| `PACK_PRICE_CENTS` | `2000` | Price of one credit pack. |
| `PACK_CREDITS` | `200` | Credits in one pack. |
| `CREDITS_PER_ROW` | `5` | Credits deducted per row returned. |
| `MAX_ROWS_PER_SEARCH` | `50` | Hard cap on rows per search, whatever the balance. |
| `TOKEN_SECRET` | required | HMAC key for customer tokens. |
| `BASE_URL` | `http://localhost:3000` | Public URL, used for Stripe redirects. |
| `PORT` | `3000` | Port to listen on. |

`.env` is loaded by a small parser in `lib.js` (quotes, `#` comments, and blank lines are handled). Variables already present in the environment win over the file.

## API

All responses are JSON. Errors are `{ "error": "message" }` with a 4xx or 5xx status.

| Method and path | Auth | Body or query | Returns |
|---|---|---|---|
| `POST /api/checkout` | none | none | `{ url }` to send the browser to |
| `GET /api/claim?session_id=` | none | Stripe session id | `{ token, credits }` |
| `GET /api/balance` | Bearer token | none | `{ credits }` |
| `POST /api/search` | Bearer token | JSON actor input | `{ rows, charged, credits_left }` |

For the default actor the search body accepts `player`, `tournament`, `date` (YYYY-MM-DD), `mode` (`schedule`, `results`, `live`), `tour` (`all`, `atp`, `wta`), and `maxMatches`. The row limit sent to the actor is the smallest of the requested `maxMatches`, what the balance affords, and `MAX_ROWS_PER_SEARCH`. A search with a balance below `CREDITS_PER_ROW` returns 402. If the actor fails, the response is 502 with the actor's message and nothing is deducted.

Example with curl:

```
curl -s -X POST http://localhost:3000/api/search \
  -H "Authorization: Bearer cus_xxx.abcdef..." \
  -H "Content-Type: application/json" \
  -d '{"player":"Alcaraz","mode":"results","maxMatches":5}'
```

## Pointing it at a different actor

Three places to change.

1. `APIFY_ACTOR` in `.env`.
2. `INPUT_FIELDS` at the top of `lib.js`: the list of input keys forwarded to the actor. Anything not in the list is dropped. If the actor's row-limit input is not called `maxMatches`, change `ROW_LIMIT_FIELD` too.
3. `public/index.html`: the form fields and the `COLUMNS` list that decides what the result table and CSV show. Column accessors are dotted paths into each row.

The kit expects the actor to return one dataset item per billable row and to accept a row-limit input, which is how pay-per-event actors are usually built. Run-sync calls time out after 120 seconds, so keep `MAX_ROWS_PER_SEARCH` at a size the actor finishes in that window.

Perch Data actors that fit this shape:

| Actor | Rows | Row-limit input |
|---|---|---|
| `perchpermits~tennis-matches-odds-form` | ATP and WTA matches with odds, form, head-to-head | `maxMatches` |
| `perchpermits~kalshi-weather-markets-nws` | Kalshi weather markets joined to the NWS forecast | see the actor's input schema |
| `perchpermits~nashville-building-permits` | Nashville building permits | see the actor's input schema |

Check each actor's page on the Apify Store for its input schema, output fields, and current per-row price before setting `CREDITS_PER_ROW`.

## Limitations

Know these before you take real money.

- **No webhook.** Credits are granted when the browser lands on the success URL and calls `/api/claim`. If the customer pays and closes the tab before the redirect completes, the credits are not added until they open the success URL again (the link is in their Stripe receipt, and the claim is idempotent). A Stripe webhook on `checkout.session.completed` fixes this and is the first thing to add.
- **Concurrent searches can double-spend.** The balance is read, the actor runs, then the balance is written. Two searches with the same token at the same time both read the old balance and one of the deductions is lost. Stripe metadata writes are not atomic. Fine for a person clicking a button; not fine for a script hammering the endpoint. A database with a transaction, or a per-customer lock in memory, fixes it.
- **The token is the account.** Anyone with the token can spend the credits. There is no login, password, or reset. Losing the token means losing the balance unless you look the customer up in Stripe by email and reissue it (the token is deterministic from the customer id, so `issueToken(customerId, TOKEN_SECRET)` regenerates it).
- **Claimed-session history is capped.** Stripe metadata values hold 500 characters, so only the most recent session ids are kept for the duplicate-claim check. A customer replaying a very old session id after many later purchases could be credited twice. Storing session ids elsewhere removes the cap.
- **One actor per deployment.** The endpoint forwards a fixed field list to a fixed actor.
- **No rate limiting, no CORS.** The page and the API are served from the same origin. Put the server behind a proxy that limits requests if it gets attention.

The short version: this is a starter. When you have paying customers, add a database, a webhook, and a login, in that order.

## Files

```
server.js          HTTP routes (node:http), no logic
lib.js             config, .env parser, tokens, Stripe client, claim, search
public/index.html  the storefront page
test/lib.test.js   node:test suite with Stripe and Apify mocked
.env.example       every variable, documented
```

## License

MIT License

Copyright (c) 2026 Perch Data

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
