import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  parseEnv,
  getConfig,
  issueToken,
  verifyToken,
  encodeForm,
  trimSessions,
  hasSession,
  claimSession,
  getBalance,
  search,
  buildActorInput,
  createCheckout,
  HttpError,
  METADATA_VALUE_LIMIT,
} from "../lib.js";

// ---------------------------------------------------------------------------
// Fake Stripe + Apify behind globalThis.fetch
// ---------------------------------------------------------------------------

const SECRET = "test-secret";
const config = getConfig({
  STRIPE_SECRET_KEY: "sk_test_fake",
  APIFY_TOKEN: "apify_fake",
  TOKEN_SECRET: SECRET,
  PACK_PRICE_CENTS: "2000",
  PACK_CREDITS: "200",
  CREDITS_PER_ROW: "5",
  MAX_ROWS_PER_SEARCH: "50",
  BASE_URL: "http://localhost:3000",
});

const realFetch = globalThis.fetch;
let fake;

function reply(status, data) {
  return { ok: status >= 200 && status < 300, status, json: async () => data };
}

function makeFake({ apify } = {}) {
  const state = {
    customers: {},
    sessions: {},
    calls: [],
    apify: apify || (async () => reply(200, [])),
  };
  globalThis.fetch = async (url, init = {}) => {
    const u = new URL(url);
    const params = init.body ? new URLSearchParams(init.body) : new URLSearchParams();
    state.calls.push({ method: init.method || "GET", path: u.pathname, params, init, url: u });

    if (u.hostname === "api.apify.com") return state.apify(init, u);

    assert.equal(init.headers?.Authorization, "Bearer sk_test_fake", "stripe auth header");
    const m = u.pathname.match(/^\/v1\/(checkout\/sessions|customers)(?:\/([^/]+))?$/);
    if (!m) return reply(404, { error: { message: "unknown route " + u.pathname } });
    const [, resource, id] = m;

    if (resource === "checkout/sessions" && init.method === "POST") {
      const s = { id: "cs_new", url: "https://checkout.stripe.com/c/pay/cs_new", payment_status: "unpaid" };
      state.sessions[s.id] = s;
      return reply(200, s);
    }
    if (resource === "checkout/sessions") {
      const s = state.sessions[id];
      return s ? reply(200, s) : reply(404, { error: { message: `No such checkout session: ${id}` } });
    }
    const c = state.customers[id];
    if (!c) return reply(404, { error: { message: `No such customer: ${id}` } });
    if (init.method === "POST") {
      c.metadata = c.metadata || {};
      for (const [k, v] of params) {
        const mm = k.match(/^metadata\[(.+)\]$/);
        if (mm) c.metadata[mm[1]] = v;
      }
    }
    return reply(200, c);
  };
  return state;
}

function addCustomer(state, id, metadata = {}) {
  state.customers[id] = { id, object: "customer", metadata: { ...metadata } };
  return state.customers[id];
}

function addPaidSession(state, id, customer, credits = "200") {
  state.sessions[id] = { id, payment_status: "paid", customer, metadata: { credits } };
}

beforeEach(() => {
  fake = makeFake();
});
afterEach(() => {
  globalThis.fetch = realFetch;
});

// ---------------------------------------------------------------------------
// .env parsing
// ---------------------------------------------------------------------------

describe("parseEnv", () => {
  test("handles quotes, comments, blank lines and export", () => {
    const text = [
      "# leading comment",
      "",
      "PLAIN=hello",
      'DOUBLE="with spaces # not a comment"',
      "SINGLE='single quoted'",
      "INLINE=value # trailing comment",
      "export EXPORTED=yes",
      "   SPACED   =   trimmed   ",
      "EMPTY=",
      "NOEQUALS",
      "=novalue",
      "URL=https://example.com/?a=1&b=2",
    ].join("\n");
    assert.deepEqual(parseEnv(text), {
      PLAIN: "hello",
      DOUBLE: "with spaces # not a comment",
      SINGLE: "single quoted",
      INLINE: "value",
      EXPORTED: "yes",
      SPACED: "trimmed",
      EMPTY: "",
      URL: "https://example.com/?a=1&b=2",
    });
  });

  test("accepts CRLF line endings", () => {
    assert.deepEqual(parseEnv("A=1\r\nB=2\r\n"), { A: "1", B: "2" });
  });
});

describe("getConfig", () => {
  test("applies defaults and parses integers", () => {
    const c = getConfig({});
    assert.equal(c.apifyActor, "perchpermits~tennis-matches-odds-form");
    assert.equal(c.packPriceCents, 2000);
    assert.equal(c.packCredits, 200);
    assert.equal(c.creditsPerRow, 5);
    assert.equal(c.maxRowsPerSearch, 50);
    assert.equal(c.baseUrl, "http://localhost:3000");
    assert.equal(c.port, 3000);
    assert.equal(getConfig({ PORT: "8080", BASE_URL: "https://x.example/" }).port, 8080);
    assert.equal(getConfig({ BASE_URL: "https://x.example/" }).baseUrl, "https://x.example");
    assert.equal(getConfig({ CREDITS_PER_ROW: "abc" }).creditsPerRow, 5);
  });
});

// ---------------------------------------------------------------------------
// Tokens
// ---------------------------------------------------------------------------

describe("tokens", () => {
  test("issue then verify returns the customer id", () => {
    const token = issueToken("cus_ABC123", SECRET);
    assert.match(token, /^cus_ABC123\.[0-9a-f]{64}$/);
    assert.equal(verifyToken(token, SECRET), "cus_ABC123");
  });

  test("tampered signature is rejected", () => {
    const token = issueToken("cus_ABC123", SECRET);
    const last = token.at(-1) === "0" ? "1" : "0";
    assert.equal(verifyToken(token.slice(0, -1) + last, SECRET), null);
  });

  test("tampered customer id is rejected", () => {
    const token = issueToken("cus_ABC123", SECRET);
    assert.equal(verifyToken("cus_XYZ999." + token.split(".")[1], SECRET), null);
  });

  test("wrong secret is rejected", () => {
    assert.equal(verifyToken(issueToken("cus_ABC123", SECRET), "other"), null);
  });

  test("malformed tokens are rejected", () => {
    for (const bad of ["", "cus_ABC123", "cus_ABC123.", ".abc", "cus_ABC123.nothex", "cus_ABC123.abc", 42, null, undefined, "cus_A.B.C"]) {
      assert.equal(verifyToken(bad, SECRET), null, `token ${JSON.stringify(bad)}`);
    }
  });

  test("refuses to work without a secret", () => {
    assert.throws(() => issueToken("cus_ABC123", ""));
    assert.equal(verifyToken("cus_ABC123.".padEnd(75, "a"), ""), null);
  });
});

// ---------------------------------------------------------------------------
// Form encoding and session list trimming
// ---------------------------------------------------------------------------

describe("encodeForm", () => {
  test("uses Stripe bracket syntax for nested objects and arrays", () => {
    const s = encodeForm({
      mode: "payment",
      metadata: { credits: 200 },
      line_items: [{ quantity: 1, price_data: { unit_amount: 2000 } }],
      skip: undefined,
    }).toString();
    assert.equal(
      s,
      "mode=payment&metadata%5Bcredits%5D=200&line_items%5B0%5D%5Bquantity%5D=1&line_items%5B0%5D%5Bprice_data%5D%5Bunit_amount%5D=2000",
    );
  });
});

describe("trimSessions", () => {
  test("prepends the new id and keeps the rest", () => {
    assert.equal(trimSessions("", "cs_1"), "cs_1");
    assert.equal(trimSessions("cs_1", "cs_2"), "cs_2,cs_1");
    assert.equal(trimSessions("cs_1, cs_0", "cs_2"), "cs_2,cs_1,cs_0");
  });

  test("drops the oldest ids to stay under the cap", () => {
    const ids = Array.from({ length: 20 }, (_, i) => "cs_" + String(i).padStart(63, "x"));
    const existing = ids.join(",");
    assert.ok(existing.length > METADATA_VALUE_LIMIT);
    const out = trimSessions(existing, "cs_NEW");
    assert.ok(out.length <= METADATA_VALUE_LIMIT, `length ${out.length}`);
    const kept = out.split(",");
    assert.equal(kept[0], "cs_NEW");
    assert.deepEqual(kept.slice(1), ids.slice(0, kept.length - 1));
    assert.ok(kept.length < ids.length + 1);
  });

  test("hasSession matches exact ids only", () => {
    assert.equal(hasSession("cs_1,cs_22", "cs_2"), false);
    assert.equal(hasSession("cs_1,cs_22", "cs_22"), true);
    assert.equal(hasSession(undefined, "cs_1"), false);
  });
});

// ---------------------------------------------------------------------------
// Checkout
// ---------------------------------------------------------------------------

describe("createCheckout", () => {
  test("posts a payment-mode session with inline price data", async () => {
    const { url } = await createCheckout(config);
    assert.equal(url, "https://checkout.stripe.com/c/pay/cs_new");
    const call = fake.calls.find((c) => c.method === "POST" && c.path === "/v1/checkout/sessions");
    assert.ok(call);
    assert.equal(call.init.headers["Content-Type"], "application/x-www-form-urlencoded");
    const p = call.params;
    assert.equal(p.get("mode"), "payment");
    assert.equal(p.get("customer_creation"), "always");
    assert.equal(p.get("line_items[0][price_data][unit_amount]"), "2000");
    assert.equal(p.get("line_items[0][price_data][currency]"), "usd");
    assert.equal(p.get("line_items[0][quantity]"), "1");
    assert.equal(p.get("metadata[credits]"), "200");
    assert.equal(p.get("success_url"), "http://localhost:3000/?session_id={CHECKOUT_SESSION_ID}");
    assert.equal(p.get("cancel_url"), "http://localhost:3000/");
  });
});

// ---------------------------------------------------------------------------
// Claim
// ---------------------------------------------------------------------------

describe("claimSession", () => {
  test("credits a paid session once and returns a valid token", async () => {
    addCustomer(fake, "cus_1");
    addPaidSession(fake, "cs_paid", "cus_1");

    const first = await claimSession(config, "cs_paid");
    assert.equal(first.credits, 200);
    assert.equal(verifyToken(first.token, SECRET), "cus_1");
    assert.equal(fake.customers.cus_1.metadata.credits, "200");
    assert.equal(fake.customers.cus_1.metadata.credited_sessions, "cs_paid");

    const writesBefore = fake.calls.filter((c) => c.method === "POST").length;
    const second = await claimSession(config, "cs_paid");
    assert.equal(second.credits, 200, "same session does not credit twice");
    assert.equal(second.token, first.token);
    assert.equal(fake.customers.cus_1.metadata.credits, "200");
    assert.equal(fake.calls.filter((c) => c.method === "POST").length, writesBefore, "no write on repeat claim");
  });

  test("adds to an existing balance and accepts an expanded customer object", async () => {
    addCustomer(fake, "cus_1", { credits: "37", credited_sessions: "cs_old" });
    addPaidSession(fake, "cs_2", { id: "cus_1" }, "200");
    const r = await claimSession(config, "cs_2");
    assert.equal(r.credits, 237);
    assert.equal(fake.customers.cus_1.metadata.credited_sessions, "cs_2,cs_old");
  });

  test("keeps credited_sessions under the metadata cap", async () => {
    const old = Array.from({ length: 30 }, (_, i) => "cs_" + String(i).padStart(30, "o")).join(",");
    assert.ok(old.length > METADATA_VALUE_LIMIT);
    addCustomer(fake, "cus_1", { credits: "0", credited_sessions: old });
    addPaidSession(fake, "cs_fresh", "cus_1");
    await claimSession(config, "cs_fresh");
    const stored = fake.customers.cus_1.metadata.credited_sessions;
    assert.ok(stored.length <= METADATA_VALUE_LIMIT);
    assert.ok(stored.startsWith("cs_fresh,cs_"));
  });

  test("rejects unpaid, unknown and malformed sessions", async () => {
    addCustomer(fake, "cus_1");
    fake.sessions.cs_unpaid = { id: "cs_unpaid", payment_status: "unpaid", customer: "cus_1", metadata: { credits: "200" } };
    await assert.rejects(claimSession(config, "cs_unpaid"), (e) => e instanceof HttpError && e.status === 402);
    await assert.rejects(claimSession(config, "cs_missing"), (e) => e instanceof HttpError && e.status === 502);
    await assert.rejects(claimSession(config, ""), (e) => e.status === 400);
    await assert.rejects(claimSession(config, "../customers"), (e) => e.status === 400);
    assert.equal(fake.customers.cus_1.metadata.credits, undefined);
  });
});

// ---------------------------------------------------------------------------
// Balance and search
// ---------------------------------------------------------------------------

describe("getBalance", () => {
  test("reads metadata.credits, defaulting to 0", async () => {
    addCustomer(fake, "cus_1", { credits: "42" });
    addCustomer(fake, "cus_2");
    addCustomer(fake, "cus_3", { credits: "junk" });
    assert.deepEqual(await getBalance(config, "cus_1"), { credits: 42 });
    assert.deepEqual(await getBalance(config, "cus_2"), { credits: 0 });
    assert.deepEqual(await getBalance(config, "cus_3"), { credits: 0 });
  });
});

describe("buildActorInput", () => {
  test("forwards known fields, drops empties, clamps maxMatches", () => {
    const body = { player: "Alcaraz", tournament: "", mode: "results", tour: "atp", junk: "x", maxMatches: 500 };
    assert.deepEqual(buildActorInput(body, 1000, config), { player: "Alcaraz", mode: "results", tour: "atp", maxMatches: 50 });
    assert.equal(buildActorInput({ maxMatches: 500 }, 12, config).maxMatches, 2, "limited by credits");
    assert.equal(buildActorInput({ maxMatches: 3 }, 1000, config).maxMatches, 3, "limited by request");
    assert.equal(buildActorInput({}, 1000, config).maxMatches, 50, "defaults to MAX_ROWS_PER_SEARCH");
    assert.equal(buildActorInput({ maxMatches: -4 }, 1000, config).maxMatches, 50, "ignores bad values");
    assert.equal(buildActorInput({ maxMatches: "7" }, 1000, config).maxMatches, 7, "accepts numeric strings");
  });
});

describe("search", () => {
  const rows = [{ match_id: 1 }, { match_id: 2 }, { match_id: 3 }];

  test("calls the actor and deducts rows * CREDITS_PER_ROW", async () => {
    addCustomer(fake, "cus_1", { credits: "100" });
    let received;
    fake.apify = async (init, u) => {
      received = { body: JSON.parse(init.body), url: u };
      return reply(200, rows);
    };
    const r = await search(config, "cus_1", { player: "Sinner", maxMatches: 10 });
    assert.deepEqual(r, { rows, charged: 15, credits_left: 85 });
    assert.equal(fake.customers.cus_1.metadata.credits, "85");
    assert.equal(received.url.pathname, "/v2/acts/perchpermits~tennis-matches-odds-form/run-sync-get-dataset-items");
    assert.equal(received.url.searchParams.get("token"), "apify_fake");
    assert.equal(received.url.searchParams.get("timeout"), "120");
    assert.deepEqual(received.body, { player: "Sinner", maxMatches: 10 });
  });

  test("clamps maxMatches to what the balance affords", async () => {
    addCustomer(fake, "cus_1", { credits: "23" });
    let body;
    fake.apify = async (init) => {
      body = JSON.parse(init.body);
      return reply(200, rows.slice(0, 2));
    };
    const r = await search(config, "cus_1", { maxMatches: 50 });
    assert.equal(body.maxMatches, 4);
    assert.equal(r.charged, 10);
    assert.equal(r.credits_left, 13);
  });

  test("refuses when the balance is below one row and never calls the actor", async () => {
    addCustomer(fake, "cus_1", { credits: "4" });
    let called = false;
    fake.apify = async () => {
      called = true;
      return reply(200, rows);
    };
    await assert.rejects(search(config, "cus_1", {}), (e) => e instanceof HttpError && e.status === 402);
    assert.equal(called, false);
    assert.equal(fake.customers.cus_1.metadata.credits, "4");
  });

  test("charges nothing when the actor returns zero rows", async () => {
    addCustomer(fake, "cus_1", { credits: "50" });
    const writes = () => fake.calls.filter((c) => c.method === "POST" && c.path.startsWith("/v1/customers")).length;
    const r = await search(config, "cus_1", { player: "nobody" });
    assert.deepEqual(r, { rows: [], charged: 0, credits_left: 50 });
    assert.equal(writes(), 0);
  });

  test("actor error returns 502 with the message and deducts nothing", async () => {
    addCustomer(fake, "cus_1", { credits: "50" });
    fake.apify = async () => reply(500, { error: { type: "actor-failed", message: "Actor run failed" } });
    await assert.rejects(
      search(config, "cus_1", {}),
      (e) => e instanceof HttpError && e.status === 502 && e.message === "Actor run failed",
    );
    assert.equal(fake.customers.cus_1.metadata.credits, "50");
    assert.equal(fake.calls.filter((c) => c.method === "POST" && c.path.startsWith("/v1/customers")).length, 0);
  });

  test("network failure and non-array responses are 502 too", async () => {
    addCustomer(fake, "cus_1", { credits: "50" });
    fake.apify = async () => {
      throw new Error("ECONNRESET");
    };
    await assert.rejects(search(config, "cus_1", {}), (e) => e.status === 502 && /ECONNRESET/.test(e.message));
    fake.apify = async () => reply(200, { not: "an array" });
    await assert.rejects(search(config, "cus_1", {}), (e) => e.status === 502);
    assert.equal(fake.customers.cus_1.metadata.credits, "50");
  });

  test("never charges more than the balance if the actor over-returns", async () => {
    addCustomer(fake, "cus_1", { credits: "7" });
    fake.apify = async () => reply(200, rows);
    const r = await search(config, "cus_1", {});
    assert.equal(r.charged, 7);
    assert.equal(r.credits_left, 0);
    assert.equal(fake.customers.cus_1.metadata.credits, "0");
  });
});
