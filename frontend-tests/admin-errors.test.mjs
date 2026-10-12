import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");

async function freePort() {
  const server = createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = server.address().port;
  server.close();
  await once(server, "close");
  return port;
}

// Exercise the actual Next /admin/errors route over HTTP, with its session and
// admin API calls served by a controlled backend (the API is tested separately).
test("admin diagnostics HTTP page identifies rejected clips on a succeeded render", { timeout: 120_000 }, async () => {
  const cookie = "session=test-admin";
  const requests = [];
  const backend = createServer((request, response) => {
    requests.push({ url: request.url, cookie: request.headers.cookie });
    response.setHeader("content-type", "application/json");
    if (request.headers.cookie !== cookie) {
      response.writeHead(401).end(JSON.stringify({ detail: "Unauthorized" }));
    } else if (request.url === "/api/v1/auth/session") {
      response.writeHead(200).end(JSON.stringify({ id: "admin-1", displayName: "Test Admin", isAdmin: true }));
    } else if (request.url === "/api/v1/admin/errors") {
      response.writeHead(200).end(JSON.stringify([{
        jobId: "job-1",
        movieTitle: "Test Movie",
        ownerName: "Test Admin",
        attemptNumber: 1,
        status: "succeeded",
        failureStage: null,
        errorCategory: null,
        errorSummary: null,
        failedClipCount: 1,
        createdAt: "2025-01-01T00:00:00Z",
        clipResults: [{
          id: "result-1", clipId: "clip-1", clipName: "Broken.mp4",
          clipOrderIndex: 0, status: "rejected", reasonCode: "decode_error",
          reasonSummary: "Clip could not be decoded", technicalDetail: null, providerMessage: null,
        }],
      }]));
    } else {
      response.writeHead(404).end(JSON.stringify({ detail: "Not found" }));
    }
  });
  backend.listen(0, "127.0.0.1");
  await once(backend, "listening");

  const port = await freePort();
  const next = spawn(process.execPath, ["node_modules/next/dist/bin/next", "dev", "--hostname", "127.0.0.1", "--port", String(port)], {
    cwd: root,
    env: {
      ...process.env,
      FASTAPI_INTERNAL_URL: `http://127.0.0.1:${backend.address().port}`,
      NEXT_TELEMETRY_DISABLED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  for (const stream of [next.stdout, next.stderr]) {
    stream.on("data", (chunk) => { output = (output + chunk.toString()).slice(-10_000); });
  }

  try {
    let response;
    const deadline = Date.now() + 90_000;
    while (Date.now() < deadline) {
      if (next.exitCode !== null) throw new Error(`Next exited early: ${output}`);
      try {
        response = await fetch(`http://127.0.0.1:${port}/admin/errors`, {
          headers: { cookie, accept: "text/html" },
          signal: AbortSignal.timeout(15_000),
          redirect: "manual",
        });
        break;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
    }
    assert.ok(response, `Next did not start: ${output}`);
    const html = await response.text();
    assert.equal(response.status, 200, `Page request failed: ${html.slice(0, 1000)}\n${output}`);
    // Check rendered markup rather than the serialized React Server Component payload.
    const article = html.match(/<article>(.*?)<\/article>/s)?.[1];
    assert.ok(article, `diagnostic row is rendered: ${html.slice(0, 1500)}`);
    assert.match(article, /Clip could not be decoded/);
    assert.match(article, /<span class="badge" data-tone="success">succeeded<\/span>/);
    assert.match(article, /<span class="badge" data-tone="danger">rejected<\/span>/);
    assert.doesNotMatch(article, /Render attempt failed\.|Stage: unknown|Category: unknown/);
    assert.doesNotMatch(html, /<div class="empty-state">/);
    assert.ok(requests.some((request) => request.url === "/api/v1/auth/session" && request.cookie === cookie));
    assert.ok(requests.some((request) => request.url === "/api/v1/admin/errors" && request.cookie === cookie));
  } finally {
    next.kill("SIGTERM");
    if (next.exitCode === null && next.signalCode === null) {
      await Promise.race([once(next, "exit"), new Promise((resolve) => setTimeout(resolve, 5_000))]);
      if (next.exitCode === null && next.signalCode === null) next.kill("SIGKILL");
    }
    backend.close();
    await once(backend, "close");
  }
});
