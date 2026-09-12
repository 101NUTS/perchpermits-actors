// End-to-end: the real server against fake Stripe and fake Apify.
// Buy, come back with a key, check balance, search, spend down, claim twice,
// actor failures. No network, no keys, no money.

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startMocks } from "./mock-upstreams.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(here, "..");

let mocks, kit, base;

function waitForListen(child) {
  return new Promise((resolve, reject) => {
    let out = "";
    child.stdout.on("data", (d) => { out += d; if (out.includes("Listening on")) resolve(); });
    child.stderr.on("data", (d) => { out += d; });
    child.on("exit", (code) => reject(new Error(`server exited ${code}: ${out}`)));
    setTimeout(() => reject(new Error("server did not start: " + out)), 8000);
  });
}

before(async () => {
  mocks = await startMocks(0);
  const port = 3480 + Math.floor(Math.random() * 400);
  base = `http://localhost:${port}`;
  kit = spawn(process.execPath, [path.join(ROOT, "server.js")], {
    env: {
      ...process.env,
      STRIPE_SECRET_KEY: "sk_test_fake", APIFY_TOKEN: "apify_test_token", TOKEN_SECRET: "e2e-secret",
      PORT: String(port), BASE_URL: base, STRIPE_API_BASE: mocks.base, APIFY_API_BASE: mocks.base,
      PACK_CREDITS: "20", PACK_PRICE_CENTS: "2000", CREDITS_PER_ROW: "5", MAX_ROWS_PER_SEARCH: "3",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  await waitForListen(kit);
});

after(async () => {
  kit?.kill();
  await mocks?.close();
});

const j = async (res) => ({ status: res.status, data: await res.json() });

test("the whole flow: checkout, pay, claim, balance, search, spend down", async () => {
  // 1. the page serves
  const page = await fetch(`${base}/`);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /Search matches/);

  // 2. checkout returns the (fake) Stripe URL
  const co = await j(await fetch(`${base}/api/checkout`, { method: "POST" }));
  assert.equal(co.status, 200);
  assert.match(co.data.url, /\/checkout\/cs_test_/);

  // 3. the visitor pays; Stripe redirects to the success_url with the session id
  const payUrl = co.data.url + "/pay";
  const redirect = await fetch(payUrl, { redirect: "manual" });
  assert.equal(redirect.status, 302);
  const back = new URL(redirect.headers.get("location"));
  assert.equal(back.origin, base);
  const sessionId = back.searchParams.get("session_id");
  assert.match(sessionId, /^cs_test_/);

  // 4. the page claims the session and gets a key
  const claim = await j(await fetch(`${base}/api/claim?session_id=${sessionId}`));
  assert.equal(claim.status, 200, JSON.stringify(claim.data));
  assert.equal(claim.data.credits, 20);
  const token = claim.data.token;
  assert.match(token, /^cus_test_\d+\.[0-9a-f]{64}$/);
  const auth = { Authorization: `Bearer ${token}` };

  // 5. claiming the same session again adds nothing
  const again = await j(await fetch(`${base}/api/claim?session_id=${sessionId}`));
  assert.equal(again.data.credits, 20);
  assert.equal(again.data.token, token);

  // 6. balance
  const bal = await j(await fetch(`${base}/api/balance`, { headers: auth }));
  assert.deepEqual(bal.data, { credits: 20 });

  // 7. a search: asks for 10, capped to 3 by MAX_ROWS_PER_SEARCH, charged 15
  const s1 = await j(await fetch(`${base}/api/search`, {
    method: "POST", headers: { ...auth, "Content-Type": "application/json" },
    body: JSON.stringify({ player: "Sabalenka", mode: "results", maxMatches: 10 }),
  }));
  assert.equal(s1.status, 200, JSON.stringify(s1.data));
  assert.equal(s1.data.rows.length, 3);
  assert.equal(s1.data.charged, 15);
  assert.equal(s1.data.credits_left, 5);
  assert.equal(s1.data.rows[0].home.name, "Sabalenka A.");
  assert.equal(s1.data.rows[0].enrichment.market.fair_prob_home, 0.631);
  const sent = mocks.state.actorCalls.at(-1);
  assert.equal(sent.maxMatches, 3);
  assert.equal(sent.player, "Sabalenka");
  assert.equal(sent.mode, "results");

  // 8. with 5 credits left, a search is clamped to 1 row
  const s2 = await j(await fetch(`${base}/api/search`, {
    method: "POST", headers: { ...auth, "Content-Type": "application/json" }, body: JSON.stringify({ maxMatches: 3 }),
  }));
  assert.equal(s2.data.rows.length, 1);
  assert.equal(s2.data.credits_left, 0);

  // 9. broke: refused before the actor is called
  const calls = mocks.state.actorCalls.length;
  const s3 = await j(await fetch(`${base}/api/search`, {
    method: "POST", headers: { ...auth, "Content-Type": "application/json" }, body: JSON.stringify({}),
  }));
  assert.equal(s3.status, 402);
  assert.equal(mocks.state.actorCalls.length, calls);

  // 10. a second pack tops up the same customer
  const co2 = await j(await fetch(`${base}/api/checkout`, { method: "POST" }));
  const r2 = await fetch(co2.data.url + "/pay", { redirect: "manual" });
  const sid2 = new URL(r2.headers.get("location")).searchParams.get("session_id");
  const claim2 = await j(await fetch(`${base}/api/claim?session_id=${sid2}`));
  assert.equal(claim2.status, 200);
  // the fake Stripe makes a new customer per session, as real Stripe does for a
  // guest checkout, so this is a second key with its own 20 credits
  assert.equal(claim2.data.credits, 20);
});

test("empty result charges nothing; actor error charges nothing and reports it", async () => {
  const co = await j(await fetch(`${base}/api/checkout`, { method: "POST" }));
  const r = await fetch(co.data.url + "/pay", { redirect: "manual" });
  const sid = new URL(r.headers.get("location")).searchParams.get("session_id");
  const { data: { token } } = await j(await fetch(`${base}/api/claim?session_id=${sid}`));
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };

  const empty = await j(await fetch(`${base}/api/search`, { method: "POST", headers, body: JSON.stringify({ player: "nobody" }) }));
  assert.equal(empty.status, 200);
  assert.deepEqual(empty.data, { rows: [], charged: 0, credits_left: 20 });

  const boom = await j(await fetch(`${base}/api/search`, { method: "POST", headers, body: JSON.stringify({ player: "boom" }) }));
  assert.equal(boom.status, 502);
  assert.match(boom.data.error, /Actor exploded/);
  const bal = await j(await fetch(`${base}/api/balance`, { headers }));
  assert.equal(bal.data.credits, 20);
});

test("bad inputs are refused cleanly", async () => {
  assert.equal((await fetch(`${base}/api/balance`)).status, 401);
  assert.equal((await fetch(`${base}/api/balance`, { headers: { Authorization: "Bearer cus_x.deadbeef" } })).status, 401);
  assert.equal((await fetch(`${base}/api/claim`)).status, 400);
  assert.equal((await fetch(`${base}/api/claim?session_id=cs_test_999999`)).status, 502);
  const unpaid = await j(await fetch(`${base}/api/checkout`, { method: "POST" }));
  const sid = unpaid.data.url.split("/").pop();
  assert.equal((await fetch(`${base}/api/claim?session_id=${sid}`)).status, 402);
  assert.equal((await fetch(`${base}/nope`)).status, 404);
  const big = await fetch(`${base}/api/search`, {
    method: "POST", headers: { Authorization: "Bearer x.y", "Content-Type": "application/json" }, body: "x".repeat(20000),
  });
  assert.ok([401, 413].includes(big.status));
});
