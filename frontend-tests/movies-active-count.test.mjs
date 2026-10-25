import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

// TYPESCRIPT_MODULE lets a runner without node_modules point at a standalone install.
const require = createRequire(import.meta.url);
const ts = require(process.env.TYPESCRIPT_MODULE || "typescript");
const pagePath = path.resolve(import.meta.dirname, "../app/(app)/movies/page.tsx");
const source = readFileSync(pagePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;

// A minimal element factory is enough to inspect the rendered tree.
const element = (type, props) => ({ type, props });
const jsxRuntime = { jsx: element, jsxs: element, Fragment: "Fragment" };
const SummaryCard = function SummaryCard() { return null; };

function loadMoviesPage(movies) {
  const exports = {};
  vm.runInNewContext(compiled, {
    exports,
    require(specifier) {
      if (specifier === "react/jsx-runtime") return jsxRuntime;
      if (specifier === "next/link") return { __esModule: true, default: "a" };
      if (specifier === "@/app/components/delete-button") return { DeleteButton: () => null };
      if (specifier === "@/app/components/page-header") return { PageHeader: () => null };
      if (specifier === "@/app/components/status-badge") return { StatusBadge: () => null };
      if (specifier === "@/app/components/summary-card") return { SummaryCard };
      if (specifier === "@/app/lib/api") return { getMovies: async () => movies };
      throw new Error(`Unexpected import: ${specifier}`);
    },
    Date,
  }, { filename: "movies-page.js" });
  return exports.default;
}

function collect(predicate, node, out = []) {
  if (Array.isArray(node)) {
    for (const child of node) collect(predicate, child, out);
    return out;
  }
  if (!node || typeof node !== "object") return out;
  if (predicate(node)) out.push(node);
  collect(predicate, node.props?.children ?? null, out);
  return out;
}

async function cards(movies) {
  const MoviesPage = loadMoviesPage(movies);
  const tree = await MoviesPage();
  const result = {};
  for (const card of collect((node) => node.type === SummaryCard, tree)) {
    result[card.props.label] = card.props.value;
  }
  return result;
}

function movie(id, status) {
  return { id, title: `Movie ${id}`, status, updatedAt: "2025-01-01T00:00:00Z", slotCount: 1, latestVideo: null };
}

test("movies page Active Movies card excludes succeeded movies", async () => {
  const four = await cards([
    movie("m1", "draft"),
    movie("m2", "editing"),
    movie("m3", "queued"),
    movie("m4", "succeeded"),
  ]);
  assert.equal(four["Total Movies"], 4, `cards: ${JSON.stringify(four)}`);
  assert.equal(four["Active Movies"], 3, `cards: ${JSON.stringify(four)}`);

  const two = await cards([movie("s1", "succeeded"), movie("s2", "succeeded")]);
  assert.equal(two["Total Movies"], 2, `cards: ${JSON.stringify(two)}`);
  assert.equal(two["Active Movies"], 0, `cards: ${JSON.stringify(two)}`);

  assert.doesNotMatch(source, /archived/, "page.tsx must not filter on a status the backend never sets");
});
