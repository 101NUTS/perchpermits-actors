// Fake Stripe and fake Apify in one process, for end-to-end tests and demos.
// No network, no keys, no money. Start it and point the kit at it:
//
//   node test/mock-upstreams.js 3499
//   STRIPE_API_BASE=http://localhost:3499 APIFY_API_BASE=http://localhost:3499 node server.js
//
// Stripe part: POST /v1/checkout/sessions creates a session whose `url` is a
// fake checkout page on this server; the page's Pay button marks the session
// paid, creates a customer, and redirects to the kit's success_url. Customers
// and their metadata live in memory. GET /v1/customers/:id and POST update it.
// Apify part: POST /v2/acts/:actor/run-sync-get-dataset-items returns up to
// `maxMatches` rows shaped like the tennis actor's enriched rows.

import http from "node:http";
import { URL } from "node:url";

export function makeRows(n, input = {}) {
  const base = [
    ["Sabalenka A.", "Pegula J.", "US Open", "semifinal", "hard", 1.51, 2.57, 0.631, 0.59, 1, 3, "WWWWWWLWWL", "LWWWWWLWWW", 10, 4, "7-5, 6-2"],
    ["Zverev A.", "Khachanov K.", "US Open", "semifinal", "hard", 1.2, 4.6, 0.792, 0.79, 2, 50, "WWWWWWLWWL", "LWWWWWLLLL", 7, 3, ""],
    ["Seyboth Wild T.", "Varillas J.", "Genoa challenger", "quarterfinal", "clay", 1.5, 2.46, 0.62, 0.609, 198, 234, "WWWLWWLWLW", "LWWLWWWWLL", 2, 4, ""],
    ["Alcaraz C.", "Sinner J.", "US Open", "final", "hard", 1.9, 1.9, 0.5, 0.52, 1, 2, "WWWWWWWWWL", "WWWWWLWWWW", 6, 5, ""],
    ["Swiatek I.", "Gauff C.", "US Open", "final", "hard", 1.6, 2.4, 0.61, 0.6, 2, 4, "WWLWWWWWWW", "WWWWLWWWLW", 11, 3, ""],
  ];
  const rows = [];
  for (let i = 0; i < n; i++) {
    const b = base[i % base.length];
    rows.push({
      match_id: 3320000 + i, date: "2026-09-11", status: b[15] ? "finished" : "scheduled", round: b[3], surface: b[4],
      start_utc: "2026-09-11T19:00:00Z",
      tournament: { name: b[2], tour: "atp" },
      home: { name: b[0] }, away: { name: b[1] },
      odds: { home: b[5], away: b[6] },
      result: b[15] ? { winner: "home", score: b[15] } : null,
      enrichment: {
        status: "ok", bookmaker_count: 15,
        home: { singles_rank: b[9], form: { sequence: b[11] } },
        away: { singles_rank: b[10], form: { sequence: b[12] } },
        h2h: { home_wins: b[13], away_wins: b[14] },
        market: { fair_prob_home: b[7], fair_prob_away: +(1 - b[7]).toFixed(3), opening_fair_prob_home: b[8],
                  prob_shift_home: +(b[7] - b[8]).toFixed(3), favorite: b[7] >= 0.5 ? "home" : "away" },
      },
      _input_echo: input,
    });
  }
  return rows;
}

export function startMocks(port = 0) {
  let actualPort = port;
  const state = { sessions: new Map(), customers: new Map(), actorCalls: [], nextId: 1 };

  function json(res, status, data) {
    const body = JSON.stringify(data);
    res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) });
    res.end(body);
  }
  function readBody(req) {
    return new Promise((resolve) => {
      const chunks = [];
      req.on("data", (c) => chunks.push(c));
      req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    });
  }

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, `http://localhost:${actualPort}`);
    const p = url.pathname;
    const body = await readBody(req);

    // ---- fake Stripe
    if (req.method === "POST" && p === "/v1/checkout/sessions") {
      const form = new URLSearchParams(body);
      const id = `cs_test_${state.nextId++}`;
      state.sessions.set(id, {
        id, payment_status: "unpaid", customer: null,
        metadata: { credits: form.get("metadata[credits]") },
        success_url: form.get("success_url"), cancel_url: form.get("cancel_url"),
        amount: form.get("line_items[0][price_data][unit_amount]"),
      });
      return json(res, 200, { id, url: `http://localhost:${actualPort}/checkout/${id}` });
    }
    if (req.method === "GET" && /^\/checkout\/[^/]+\/pay$/.test(p)) {
      const s = state.sessions.get(p.split("/")[2]);
      if (!s) return json(res, 404, { error: "no session" });
      if (!s.customer) {
        const cid = `cus_test_${state.nextId++}`;
        state.customers.set(cid, { id: cid, metadata: {} });
        s.customer = cid;
      }
      s.payment_status = "paid";
      res.writeHead(302, { Location: s.success_url.replace("{CHECKOUT_SESSION_ID}", s.id) });
      return res.end();
    }
    if (req.method === "GET" && p.startsWith("/checkout/")) {
      const s = state.sessions.get(p.split("/")[2]);
      if (!s) return json(res, 404, { error: "no session" });
      const html = `<!doctype html><meta charset="utf-8"><title>Fake Stripe Checkout</title>
<body style="font:16px system-ui;background:#f6f9fc;color:#30313d;display:grid;place-items:center;height:100vh;margin:0">
<div style="background:#fff;padding:32px;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.08);width:360px">
<div style="font-weight:600;color:#635bff;margin-bottom:12px">Fake Stripe Checkout (test double)</div>
<div style="font-size:14px;color:#6a7383">${s.metadata.credits} credits</div>
<div style="font-size:28px;font-weight:700;margin:4px 0 20px">$${(Number(s.amount) / 100).toFixed(2)}</div>
<a id="pay" href="/checkout/${s.id}/pay" style="display:block;text-align:center;background:#635bff;color:#fff;padding:12px;border-radius:8px;text-decoration:none;font-weight:600">Pay</a>
<a href="${s.cancel_url}" style="display:block;text-align:center;margin-top:12px;color:#6a7383;font-size:14px">Cancel</a>
</div></body>`;
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      return res.end(html);
    }
    if (req.method === "GET" && p.startsWith("/v1/checkout/sessions/")) {
      const s = state.sessions.get(p.split("/")[4]);
      return s ? json(res, 200, s) : json(res, 404, { error: { message: "No such checkout session" } });
    }
    if (p.startsWith("/v1/customers/")) {
      const c = state.customers.get(p.split("/")[3]);
      if (!c) return json(res, 404, { error: { message: "No such customer" } });
      if (req.method === "POST") {
        const form = new URLSearchParams(body);
        for (const [k, v] of form) {
          const m = k.match(/^metadata\[(.+)\]$/);
          if (m) c.metadata[m[1]] = v;
        }
      }
      return json(res, 200, c);
    }

    // ---- fake Apify
    if (req.method === "POST" && /^\/v2\/acts\/[^/]+\/run-sync-get-dataset-items$/.test(p)) {
      if (url.searchParams.get("token") !== "apify_test_token") return json(res, 401, { error: { message: "Bad Apify token" } });
      const input = body ? JSON.parse(body) : {};
      state.actorCalls.push(input);
      if (input.player === "nobody") return json(res, 200, []);
      if (input.player === "boom") return json(res, 500, { error: { message: "Actor exploded" } });
      const n = Math.max(0, Math.min(Number(input.maxMatches) || 10, 50));
      return json(res, 200, makeRows(n, input));
    }
    json(res, 404, { error: { message: `mock has no route for ${req.method} ${p}` } });
  });

  return new Promise((resolve) => {
    server.listen(port, () => {
      const actual = server.address().port;
      actualPort = actual;
      resolve({ server, state, port: actual, base: `http://localhost:${actual}`, close: () => new Promise((r) => server.close(r)) });
    });
  });
}

if (process.argv[1] && process.argv[1].endsWith("mock-upstreams.js")) {
  const port = Number(process.argv[2]) || 3499;
  startMocks(port).then((m) => console.log(`fake Stripe + Apify on ${m.base} (Apify token: apify_test_token)`));
}
