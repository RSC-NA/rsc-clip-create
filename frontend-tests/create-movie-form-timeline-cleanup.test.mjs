import assert from "node:assert/strict";
import { File } from "node:buffer";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

const require = createRequire(import.meta.url);
// TYPESCRIPT_MODULE lets a runner without node_modules point at a standalone install.
const ts = require(process.env.TYPESCRIPT_MODULE || "typescript");
const source = readFileSync(path.resolve(import.meta.dirname, "../app/components/create-movie-form.tsx"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

const element = (type, props, key) => ({ type, props, key });
const jsxRuntime = (() => {
  try {
    return require("react/jsx-runtime");
  } catch {
    return { jsx: element, jsxs: element, Fragment: "fragment" };
  }
})();

const config = {
  filenamePattern: "^[^-]+-[^-]+-[^.]+\\.mp4$",
  supportedExtensions: ["mp4"],
  minClipCount: 1,
  maxClipCount: 10,
  maxClipSizeBytes: 1000,
  maxUploadBytes: 10000,
  maxClipDurationSeconds: 30,
};

// Render the compiled client component and drive its JSX callbacks, like
// create-movie-form.test.mjs. fetch is the network boundary; each test chooses
// the status of the timeline PUT and the cleanup DELETE.
function mountCreateMovieForm({ timeline, remove = { status: 204 } }) {
  const states = [];
  const calls = [];
  const pushes = [];
  let hookIndex = 0;
  let pending;
  let tree;
  const hooks = {
    useState(initial) {
      const index = hookIndex++;
      if (!(index in states)) states[index] = initial;
      return [states[index], (value) => { states[index] = value; }];
    },
    useTransition() {
      return [false, (callback) => { pending = callback(); }];
    },
  };
  const reply = ({ status, body }) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body ?? {},
  });
  const exports = {};
  vm.runInNewContext(compiled, {
    exports,
    require(specifier) {
      if (specifier === "react") return hooks;
      // The tree walker only reads { type, props }, so a minimal element
      // factory stands in when react is not installed.
      if (specifier === "react/jsx-runtime") return jsxRuntime;
      if (specifier === "next/navigation") {
        return { useRouter: () => ({ push(url) { pushes.push(url); }, refresh() {} }) };
      }
      if (specifier === "@/app/lib/browser-api") return { browserApiPath: (p) => `/api/backend${p}` };
      if (specifier === "@/app/lib/api-error") {
        return {
          readApiError: async (response) => {
            const body = await response.json().catch(() => ({}));
            return new Error(typeof body?.detail === "string" ? body.detail : "API error");
          },
        };
      }
      throw new Error(`Unexpected import: ${specifier}`);
    },
    // Share this realm's Error so the readApiError stub's errors pass the
    // component's `caught instanceof Error` check, as they do in the browser.
    Error,
    FormData,
    Request: class extends Request {
      constructor(url, options) { super(new URL(url, "http://localhost"), options); }
    },
    fetch: async (input, options) => {
      const url = typeof input === "string" ? input : new URL(input.url).pathname;
      const method = options?.method ?? input.method ?? "GET";
      calls.push({ method, url });
      if (method === "POST" && url.endsWith("/clips")) return reply({ status: 201, body: [{ id: "clip-1" }] });
      if (method === "POST" && url.endsWith("/movies")) return reply({ status: 201, body: { id: "movie-1" } });
      if (method === "PUT" && url.endsWith("/movies/movie-1/timeline")) return reply(timeline);
      if (method === "DELETE") {
        if (remove.throws) throw new TypeError("network down");
        return reply(remove);
      }
      return reply({ status: 404, body: { detail: "unexpected" } });
    },
  }, { filename: "create-movie-form.js" });

  function render() {
    hookIndex = 0;
    tree = exports.CreateMovieForm({ config });
  }
  function find(predicate, node = tree) {
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
  render();
  return {
    calls,
    pushes,
    async createWithOneClip() {
      find((node) => node.type === "input" && node.props.id === "movie-title")
        .props.onChange({ target: { value: "Spring launch" } });
      render();
      const clip = new File([new Uint8Array(16)], "Falcons-Ace-Gold.mp4", { type: "video/mp4" });
      find((node) => node.type === "input" && node.props.id === "movie-files")
        .props.onChange({ target: { files: [clip] } });
      render();
      const button = find((node) => node.type === "button" && node.props.children === "Create Movie");
      assert.equal(button.props.disabled, false);
      button.props.onClick();
      await pending;
      render();
    },
    error() {
      return find((node) => node.props?.role === "alert")?.props.children ?? null;
    },
  };
}

const deletesClips = (calls) =>
  calls.some(({ method, url }) => method === "DELETE" && url.includes("/clips"));

test("Create Movie deletes the draft movie it created when the timeline PUT fails", async () => {
  // 500 on the timeline: the created movie must be cleaned up.
  const failed = mountCreateMovieForm({ timeline: { status: 500, body: { detail: "boom" } } });
  await failed.createWithOneClip();
  const last = failed.calls.at(-1);
  assert.deepEqual(
    { method: last.method, url: last.url },
    { method: "DELETE", url: "/api/backend/movies/movie-1" },
    `expected a cleanup DELETE of the created movie; calls were ${JSON.stringify(failed.calls)}`,
  );
  assert.equal(failed.pushes.length, 0);
  assert.equal(failed.error(), "boom");
  assert.equal(deletesClips(failed.calls), false, "uploaded clips must stay in the library");
});

test("a failing cleanup DELETE does not replace the timeline error", async () => {
  // 400 "bad slot" with the cleanup DELETE also failing: the original error stays.
  for (const remove of [{ status: 500, body: { detail: "delete failed" } }, { throws: true }]) {
    const badSlot = mountCreateMovieForm({ timeline: { status: 400, body: { detail: "bad slot" } }, remove });
    await badSlot.createWithOneClip();
    assert.ok(
      badSlot.calls.some(({ method, url }) => method === "DELETE" && url === "/api/backend/movies/movie-1"),
      "a cleanup DELETE must be attempted",
    );
    assert.equal(badSlot.error(), "bad slot");
    assert.equal(badSlot.pushes.length, 0);
    assert.equal(deletesClips(badSlot.calls), false, "uploaded clips must stay in the library");
  }
});

test("a successful Create Movie sends no DELETE and opens the editor once", async () => {
  const ok = mountCreateMovieForm({ timeline: { status: 200, body: {} } });
  await ok.createWithOneClip();
  assert.equal(ok.calls.filter(({ method }) => method === "DELETE").length, 0);
  assert.deepEqual(ok.pushes, ["/movies/movie-1/edit"]);
  assert.equal(ok.error(), null);
});
