import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "..");
const nodeRequire = createRequire(import.meta.url);

// Render the real server page and its components, replacing only the session
// and API calls (the same JSON shape returned by GET /api/v1/admin/videos).
function loadPage(videos) {
  function loadTsx(relativePath) {
    const source = readFileSync(path.join(root, relativePath), "utf8");
    const { outputText } = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
        target: ts.ScriptTarget.ES2022,
      },
      fileName: relativePath,
    });
    const loadedModule = { exports: {} };
    const imports = {
      "@/app/components/page-header": () => loadTsx("app/components/page-header.tsx"),
      "@/app/components/status-badge": () => loadTsx("app/components/status-badge.tsx"),
      "@/app/lib/api": () => ({ getAdminVideos: async () => videos }),
      "@/app/lib/session": () => ({ requireSession: async () => ({ isAdmin: true }) }),
    };
    const localRequire = (name) => (imports[name] ? imports[name]() : nodeRequire(name));
    new Function("require", "module", "exports", outputText)(localRequire, loadedModule, loadedModule.exports);
    return loadedModule.exports;
  }
  return loadTsx("app/(app)/admin/videos/page.tsx").default;
}

test("admin inventory renders source attempt status, not queued retry status", async () => {
  // A completed video from attempt 1 remains succeeded when attempt 2 is queued.
  // The backend HTTP test verifies the API sends succeeded for this scenario.
  const page = loadPage([{
    id: "video-1",
    movieTitle: "Test Movie",
    ownerName: "Test User",
    status: "ready", // Distinct from the source attempt so the badge cannot use video.status.
    latestAttemptStatus: "succeeded",
    storagePath: "/tmp/succeeded.mp4",
    createdAt: "2025-01-01T00:00:00Z",
  }]);
  const html = renderToStaticMarkup(await page());

  assert.match(html, /<h1>Video Inventory<\/h1>/);
  assert.match(html, /<th scope="col">Source Attempt<\/th>/);
  assert.doesNotMatch(html, /Latest Attempt|latest render attempt/i);
  assert.match(html, /source attempt that produced it/i);
  const row = html.match(/<tbody><tr>(.*?)<\/tr><\/tbody>/s)?.[1];
  assert.ok(row, "video row is rendered");
  const cells = [...row.matchAll(/<td>(.*?)<\/td>/gs)].map((match) => match[1]);
  assert.equal(cells.length, 6);
  assert.match(cells[2], /<span class="badge" data-tone="success">ready<\/span>/);
  assert.match(cells[3], /<span class="badge" data-tone="success">succeeded<\/span>/);
  assert.doesNotMatch(cells[3], /queued/);
});
