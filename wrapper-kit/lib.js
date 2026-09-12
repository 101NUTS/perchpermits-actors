// Pure logic for the wrapper kit. No server, no globals except fetch.
// Everything here is testable by swapping globalThis.fetch.

import crypto from "node:crypto";
import fs from "node:fs";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// Actor input fields the search endpoint forwards. Edit this list when you
// point the kit at a different actor. `maxMatches` is handled separately.
export const INPUT_FIELDS = ["player", "tournament", "date", "mode", "tour"];

// Name of the actor input that limits the number of rows returned.
export const ROW_LIMIT_FIELD = "maxMatches";

// Stripe metadata values are capped at 500 characters.
export const METADATA_VALUE_LIMIT = 500;

/** Parse the text of a .env file into a plain object. */
export function parseEnv(text) {
  const out = {};
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim().replace(/^export\s+/, "");
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length >= 2) ||
      (value.startsWith("'") && value.endsWith("'") && value.length >= 2)
    ) {
      value = value.slice(1, -1);
    } else {
      // Unquoted values may carry a trailing comment.
      const hash = value.indexOf(" #");
      if (hash >= 0) value = value.slice(0, hash).trim();
    }
    out[key] = value;
  }
  return out;
}

/** Load a .env file into `target` without overriding keys already set. */
export function loadEnv(path = ".env", target = process.env) {
  let text;
  try {
    text = fs.readFileSync(path, "utf8");
  } catch {
    return target;
  }
  for (const [k, v] of Object.entries(parseEnv(text))) {
    if (target[k] === undefined) target[k] = v;
  }
  return target;
}

function intOr(value, fallback) {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

/** Build the runtime configuration from an environment object. */
export function getConfig(env = process.env) {
  return {
    stripeSecretKey: env.STRIPE_SECRET_KEY || "",
    apifyToken: env.APIFY_TOKEN || "",
    apifyActor: env.APIFY_ACTOR || "perchpermits~tennis-matches-odds-form",
    packPriceCents: intOr(env.PACK_PRICE_CENTS, 2000),
    packCredits: intOr(env.PACK_CREDITS, 200),
    creditsPerRow: intOr(env.CREDITS_PER_ROW, 5),
    maxRowsPerSearch: intOr(env.MAX_ROWS_PER_SEARCH, 50),
    tokenSecret: env.TOKEN_SECRET || "",
    baseUrl: (env.BASE_URL || "http://localhost:3000").replace(/\/+$/, ""),
    port: intOr(env.PORT, 3000),
    stripeApiBase: env.STRIPE_API_BASE || "https://api.stripe.com",
    apifyApiBase: env.APIFY_API_BASE || "https://api.apify.com",
  };
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// Tokens
// ---------------------------------------------------------------------------

function hmac(secret, value) {
  return crypto.createHmac("sha256", secret).update(value).digest("hex");
}

/** token = <customerId>.<hex HMAC-SHA256(secret, customerId)> */
export function issueToken(customerId, secret) {
  if (!secret) throw new Error("TOKEN_SECRET is not set");
  return `${customerId}.${hmac(secret, customerId)}`;
}

/** Returns the customer id for a valid token, otherwise null. */
export function verifyToken(token, secret) {
  if (!secret || typeof token !== "string") return null;
  const dot = token.lastIndexOf(".");
  if (dot <= 0 || dot === token.length - 1) return null;
  const customerId = token.slice(0, dot);
  const given = token.slice(dot + 1);
  if (!/^[A-Za-z0-9_]+$/.test(customerId)) return null;
  if (!/^[0-9a-f]{64}$/.test(given)) return null;
  const expected = hmac(secret, customerId);
  const a = Buffer.from(given, "hex");
  const b = Buffer.from(expected, "hex");
  if (a.length !== b.length) return null;
  return crypto.timingSafeEqual(a, b) ? customerId : null;
}

// ---------------------------------------------------------------------------
// Stripe REST client (form-encoded, no SDK)
// ---------------------------------------------------------------------------

/** Encode a nested object as application/x-www-form-urlencoded with Stripe's
 *  bracket syntax: { metadata: { credits: 5 } } -> metadata[credits]=5 */
export function encodeForm(obj, prefix = "", params = new URLSearchParams()) {
  for (const [key, value] of Object.entries(obj)) {
    if (value === undefined || value === null) continue;
    const name = prefix ? `${prefix}[${key}]` : key;
    if (Array.isArray(value)) {
      value.forEach((v, i) => {
        if (typeof v === "object" && v !== null) encodeForm(v, `${name}[${i}]`, params);
        else params.append(`${name}[${i}]`, String(v));
      });
    } else if (typeof value === "object") {
      encodeForm(value, name, params);
    } else {
      params.append(name, String(value));
    }
  }
  return params;
}

async function stripeRequest(config, method, path, body) {
  if (!config.stripeSecretKey) throw new HttpError(500, "STRIPE_SECRET_KEY is not set");
  const headers = { Authorization: `Bearer ${config.stripeSecretKey}` };
  const init = { method, headers };
  if (body) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    init.body = encodeForm(body).toString();
  }
  const res = await fetch(`${config.stripeApiBase}/v1${path}`, init);
  let data;
  try {
    data = await res.json();
  } catch {
    data = {};
  }
  if (!res.ok) {
    const msg = data?.error?.message || `Stripe ${method} ${path} failed with ${res.status}`;
    throw new HttpError(502, msg);
  }
  return data;
}

export const stripeGet = (config, path) => stripeRequest(config, "GET", path);
export const stripePost = (config, path, body) => stripeRequest(config, "POST", path, body);

/** Integer credits stored in customer metadata; anything unparsable is 0. */
export function readCredits(customer) {
  const n = Number.parseInt(customer?.metadata?.credits ?? "0", 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

// ---------------------------------------------------------------------------
// Checkout and claim
// ---------------------------------------------------------------------------

export async function createCheckout(config) {
  const session = await stripePost(config, "/checkout/sessions", {
    mode: "payment",
    customer_creation: "always",
    line_items: [
      {
        quantity: 1,
        price_data: {
          currency: "usd",
          unit_amount: config.packPriceCents,
          product_data: { name: `${config.packCredits} credits` },
        },
      },
    ],
    metadata: { credits: config.packCredits },
    success_url: `${config.baseUrl}/?session_id={CHECKOUT_SESSION_ID}`,
    cancel_url: `${config.baseUrl}/`,
  });
  if (!session.url) throw new HttpError(502, "Stripe did not return a checkout URL");
  return { url: session.url };
}

/** Prepend `sessionId` to a comma-separated list, keeping the most recent ids
 *  that fit within `limit` characters. */
export function trimSessions(existing, sessionId, limit = METADATA_VALUE_LIMIT) {
  const ids = [sessionId, ...String(existing || "").split(",").map((s) => s.trim()).filter(Boolean)];
  const kept = [];
  let length = 0;
  for (const id of ids) {
    const add = id.length + (kept.length ? 1 : 0);
    if (length + add > limit) break;
    kept.push(id);
    length += add;
  }
  return kept.join(",");
}

export function hasSession(existing, sessionId) {
  return String(existing || "").split(",").map((s) => s.trim()).includes(sessionId);
}

/** Credit a paid checkout session once and return the customer's API token. */
export async function claimSession(config, sessionId) {
  if (!sessionId || !/^[A-Za-z0-9_]+$/.test(sessionId)) throw new HttpError(400, "session_id is required");
  const session = await stripeGet(config, `/checkout/sessions/${sessionId}`);
  if (session.payment_status !== "paid") throw new HttpError(402, "Session is not paid");
  const customerId = typeof session.customer === "string" ? session.customer : session.customer?.id;
  if (!customerId) throw new HttpError(502, "Session has no customer");

  const customer = await stripeGet(config, `/customers/${customerId}`);
  const meta = customer.metadata || {};
  let credits = readCredits(customer);

  if (!hasSession(meta.credited_sessions, sessionId)) {
    const add = Number.parseInt(session.metadata?.credits ?? "0", 10);
    credits += Number.isFinite(add) && add > 0 ? add : 0;
    await stripePost(config, `/customers/${customerId}`, {
      metadata: {
        credits: String(credits),
        credited_sessions: trimSessions(meta.credited_sessions, sessionId),
      },
    });
  }

  return { token: issueToken(customerId, config.tokenSecret), credits };
}

// ---------------------------------------------------------------------------
// Balance and search
// ---------------------------------------------------------------------------

export async function getBalance(config, customerId) {
  const customer = await stripeGet(config, `/customers/${customerId}`);
  return { credits: readCredits(customer) };
}

/** Pick the forwarded input fields and clamp the row limit. */
export function buildActorInput(body, credits, config) {
  const input = {};
  for (const key of INPUT_FIELDS) {
    const v = body?.[key];
    if (v === undefined || v === null || v === "") continue;
    input[key] = typeof v === "string" ? v.slice(0, 200) : v;
  }
  const requested = Number.parseInt(body?.[ROW_LIMIT_FIELD], 10);
  const wanted = Number.isFinite(requested) && requested > 0 ? requested : config.maxRowsPerSearch;
  const affordable = Math.floor(credits / config.creditsPerRow);
  input[ROW_LIMIT_FIELD] = Math.max(1, Math.min(wanted, affordable, config.maxRowsPerSearch));
  return input;
}

async function runActor(config, input) {
  if (!config.apifyToken) throw new HttpError(500, "APIFY_TOKEN is not set");
  const url =
    `${config.apifyApiBase}/v2/acts/${encodeURIComponent(config.apifyActor)}` +
    `/run-sync-get-dataset-items?token=${encodeURIComponent(config.apifyToken)}&timeout=120`;
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  } catch (err) {
    throw new HttpError(502, `Actor request failed: ${err.message}`);
  }
  let data;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const msg = data?.error?.message || `Actor returned ${res.status}`;
    throw new HttpError(502, msg);
  }
  if (!Array.isArray(data)) throw new HttpError(502, "Actor returned no dataset");
  return data;
}

/** Run the actor for a customer and deduct credits for the rows returned. */
export async function search(config, customerId, body) {
  const customer = await stripeGet(config, `/customers/${customerId}`);
  const credits = readCredits(customer);
  if (credits < config.creditsPerRow) {
    throw new HttpError(402, `Not enough credits: ${credits} left, ${config.creditsPerRow} needed per row`);
  }

  const input = buildActorInput(body, credits, config);
  const rows = await runActor(config, input);

  const charged = Math.min(credits, rows.length * config.creditsPerRow);
  const creditsLeft = credits - charged;
  if (charged > 0) {
    await stripePost(config, `/customers/${customerId}`, {
      metadata: { credits: String(creditsLeft) },
    });
  }
  return { rows, charged, credits_left: creditsLeft };
}
