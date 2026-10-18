import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(path.resolve(import.meta.dirname, "../app/lib/api-error.ts"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const apiError = {};
vm.runInNewContext(compiled, { exports: apiError }, { filename: "api-error.js" });

const DETAIL = "That upload is 600 MB; the limit is 512 MB per request. Upload fewer clips at a time.";
const GENERIC = "That upload is too large.";

// Drive the module the way client components do: hand it the failed fetch Response.
async function messageFor(status, body, contentType = "application/json") {
  const response = new Response(body, { status, headers: { "content-type": contentType } });
  const error = await apiError.readApiError(response);
  assert.ok(error instanceof Error || error?.constructor?.name === "Error");
  return error.message;
}

test("a 413 from POST /clips shows the API's string detail with size and limit", async () => {
  assert.equal(apiError.formatApiError(413, JSON.stringify({ detail: DETAIL })), DETAIL);
  assert.equal(await messageFor(413, JSON.stringify({ detail: DETAIL })), DETAIL);
});

test("a 413 without a string detail falls back to the generic upload message", async () => {
  assert.equal(apiError.formatApiError(413, "<html>Payload Too Large</html>"), GENERIC);
  assert.equal(await messageFor(413, "<html>Payload Too Large</html>", "text/html"), GENERIC);
  assert.equal(apiError.formatApiError(413, ""), GENERIC);
  assert.equal(await messageFor(413, "", "text/plain"), GENERIC);
  assert.equal(apiError.formatApiError(413, "Request Entity Too Large"), GENERIC);
  assert.equal(apiError.formatApiError(413, JSON.stringify({ detail: [] })), GENERIC);
  assert.equal(apiError.formatApiError(413, JSON.stringify({ detail: [{ loc: ["body", "x"], msg: "bad" }] })), GENERIC);
  assert.equal(apiError.formatApiError(413, JSON.stringify({ message: "nope" })), GENERIC);
  assert.equal(apiError.formatApiError(413, "null"), GENERIC);
});

test("a 401 still asks the user to sign in again regardless of body", async () => {
  const expected = "Your session has expired. Sign in again to continue.";
  assert.equal(apiError.formatApiError(401, JSON.stringify({ detail: "Unauthorized" })), expected);
  assert.equal(apiError.formatApiError(401, "<html>401</html>"), expected);
  assert.equal(await messageFor(401, JSON.stringify({ detail: "Unauthorized" })), expected);
});

test("other statuses keep their existing behavior", () => {
  assert.equal(apiError.formatApiError(400, JSON.stringify({ detail: "Bad title" })), "Bad title");
  assert.equal(
    apiError.formatApiError(422, JSON.stringify({ detail: [{ loc: ["body", "title"], msg: "required" }] })),
    "title: required",
  );
  assert.equal(apiError.formatApiError(400, "plain text"), "plain text");
  assert.equal(apiError.formatApiError(404, ""), "Request failed (404).");
  assert.equal(
    apiError.formatApiError(502, "<html>Bad Gateway</html>"),
    "The server had a problem completing that request. Try again shortly.",
  );
});
