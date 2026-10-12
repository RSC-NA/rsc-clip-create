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

// Exercise Next's actual /admin/videos route, with its server-side API requests
// served by a controlled HTTP backend. The backend API test independently checks
// that a succeeded source attempt is returned even after a retry is queued.
test("admin inventory HTTP page displays the source attempt, not the queued retry", { timeout: 120_000 }, async () => {
  const cookie = "session=test-admin";
  const requests = [];
  const backend = createServer((request, response) => {
    requests.push({ url: request.url, cookie: request.headers.cookie });
    response.setHeader("content-type", "application/json");
    if (request.headers.cookie !== cookie) {
      response.writeHead(401).end(JSON.stringify({ detail: "Unauthorized" }));
    } else if (request.url === "/api/v1/auth/session") {
      response.writeHead(200).end(JSON.stringify({ id: "admin-1", displayName: "Test Admin", isAdmin: true }));
    } else if (request.url === "/api/v1/admin/videos") {
      // The video was produced by attempt 1 (succeeded); attempt 2 is queued.
      // The API's legacy field name refers to the video's SOURCE attempt.
      response.writeHead(200).end(JSON.stringify([{
        id: "video-1",
        movieTitle: "Test Movie",
        ownerName: "Test Admin",
        status: "ready",
        latestAttemptStatus: "succeeded",
        storagePath: "/tmp/succeeded.mp4",
        createdAt: "2025-01-01T00:00:00Z",
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
        response = await fetch(`http://127.0.0.1:${port}/admin/videos`, {
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
    // Read only the rendered table, not the serialized React Server Component
    // payload (which may repeat the same strings in a different format).
    const table = html.match(/<table>(.*?)<\/table>/s)?.[1];
    assert.ok(table, "inventory table is rendered");
    assert.match(html, /<h1[^>]*>Video Inventory<\/h1>/);
    assert.match(html, /source attempt that produced it/i);
    assert.doesNotMatch(html.slice(0, html.indexOf("<table>")), /latest render attempt/i);
    assert.match(table, /<th scope="col">Source Attempt<\/th>/);
    assert.doesNotMatch(table, /Latest Attempt/);
    const row = table.match(/<tbody><tr>(.*?)<\/tr><\/tbody>/s)?.[1];
    assert.ok(row, "video row is rendered");
    const cells = [...row.matchAll(/<td>(.*?)<\/td>/gs)].map((match) => match[1]);
    assert.equal(cells.length, 6);
    assert.match(cells[2], /<span class="badge" data-tone="success">ready<\/span>/);
    assert.match(cells[3], /<span class="badge" data-tone="success">succeeded<\/span>/);
    assert.doesNotMatch(cells[3], /queued/);
    assert.ok(requests.some((request) => request.url === "/api/v1/auth/session" && request.cookie === cookie));
    assert.ok(requests.some((request) => request.url === "/api/v1/admin/videos" && request.cookie === cookie));
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
