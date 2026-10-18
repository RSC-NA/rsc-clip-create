import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

// TYPESCRIPT_MODULE lets a runner without node_modules point at a standalone install.
const require = createRequire(import.meta.url);
const ts = require(process.env.TYPESCRIPT_MODULE || "typescript");
const source = readFileSync(path.resolve(import.meta.dirname, "../app/components/brand-sweep.tsx"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

// A minimal element factory is enough to inspect the rendered tree.
const element = (type, props) => ({ type, props });
const jsxRuntime = { jsx: element, jsxs: element, Fragment: "Fragment" };

function loadBrandSweep() {
  const exports = {};
  vm.runInNewContext(compiled, {
    exports,
    require(specifier) {
      if (specifier === "react") {
        return {
          useState: (initial) => [initial, () => {}],
          useRef: (initial) => ({ current: initial }),
          useTransition: () => [false, () => {}],
        };
      }
      if (specifier === "react/jsx-runtime") return jsxRuntime;
      if (specifier === "next/navigation") return { useRouter: () => ({ refresh() {} }) };
      if (specifier === "@/app/lib/browser-api") return { browserApiPath: (p) => `/api/v1${p}` };
      if (specifier === "@/app/lib/api-error") return { readApiError: async () => new Error("API error") };
      throw new Error(`Unexpected import: ${specifier}`);
    },
    FormData,
    fetch: async () => ({ ok: true }),
  }, { filename: "brand-sweep.js" });
  return exports.BrandSweep;
}

function find(predicate, node) {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = find(predicate, child);
      if (found) return found;
    }
    return null;
  }
  if (!node || typeof node !== "object") return null;
  if (predicate(node)) return node;
  return find(predicate, node.props?.children ?? null);
}

function videoSrc(BrandSweep, sweep) {
  const tree = BrandSweep({ sweep, supportedFormats: ["webm"], maxBrandSweepBytes: 209715200 });
  const video = find((node) => node.type === "video", tree);
  assert.ok(video, "the current sweep must render a <video> preview");
  return video.props.src;
}

test("replacing the sweep file changes the preview video src", () => {
  const BrandSweep = loadBrandSweep();
  const before = videoSrc(BrandSweep, {
    id: "sweep-1",
    originalName: "old.webm",
    storagePath: "/storage/sweeps/a-old.webm",
    durationSeconds: 1.0,
    createdAt: "2026-10-01T00:00:00Z",
  });
  const after = videoSrc(BrandSweep, {
    id: "sweep-1",
    originalName: "new.webm",
    storagePath: "/storage/sweeps/b-new.webm",
    durationSeconds: 1.5,
    createdAt: "2026-10-01T00:00:00Z",
  });

  assert.ok(before.startsWith("/api/v1/sweeps/sweep-1/file"), `unexpected src: ${before}`);
  assert.ok(after.startsWith("/api/v1/sweeps/sweep-1/file"), `unexpected src: ${after}`);
  assert.notEqual(after, before, "a replaced sweep must give the <video> a new src so it reloads");
});
