// HTTP layer. All business logic lives in lib.js.

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  loadEnv,
  getConfig,
  HttpError,
  verifyToken,
  createCheckout,
  claimSession,
  getBalance,
  search,
} from "./lib.js";

const here = path.dirname(fileURLToPath(import.meta.url));
loadEnv(path.join(here, ".env"));
const config = getConfig();

if (!config.tokenSecret) {
  console.error("TOKEN_SECRET is not set. Add a long random string to .env.");
  process.exit(1);
}

const INDEX_PATH = path.join(here, "public", "index.html");
const MAX_BODY = 16 * 1024;

function sendJson(res, status, data) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
  });
  res.end(body);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        reject(new HttpError(413, "Body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (!chunks.length) return resolve({});
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch {
        reject(new HttpError(400, "Body must be JSON"));
      }
    });
    req.on("error", reject);
  });
}

function requireCustomer(req) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  const customerId = verifyToken(token, config.tokenSecret);
  if (!customerId) throw new HttpError(401, "Missing or invalid token");
  return customerId;
}

async function route(req, res) {
  const url = new URL(req.url, config.baseUrl);
  const { method } = req;

  if (method === "GET" && url.pathname === "/") {
    const html = fs.readFileSync(INDEX_PATH);
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Content-Length": html.length });
    res.end(html);
    return;
  }

  if (method === "POST" && url.pathname === "/api/checkout") {
    return sendJson(res, 200, await createCheckout(config));
  }

  if (method === "GET" && url.pathname === "/api/claim") {
    return sendJson(res, 200, await claimSession(config, url.searchParams.get("session_id")));
  }

  if (method === "GET" && url.pathname === "/api/balance") {
    const customerId = requireCustomer(req);
    return sendJson(res, 200, await getBalance(config, customerId));
  }

  if (method === "POST" && url.pathname === "/api/search") {
    const customerId = requireCustomer(req);
    const body = await readJson(req);
    return sendJson(res, 200, await search(config, customerId, body));
  }

  throw new HttpError(404, "Not found");
}

const server = http.createServer((req, res) => {
  route(req, res).catch((err) => {
    const status = err instanceof HttpError ? err.status : 500;
    if (status >= 500) console.error(err);
    sendJson(res, status, { error: err.message || "Server error" });
  });
});

server.listen(config.port, () => {
  console.log(`Listening on ${config.baseUrl} (port ${config.port}), actor ${config.apifyActor}`);
});
