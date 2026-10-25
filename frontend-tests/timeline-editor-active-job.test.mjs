import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

// TYPESCRIPT_MODULE lets a runner without node_modules point at a standalone install.
const require = createRequire(import.meta.url);
const ts = require(process.env.TYPESCRIPT_MODULE || "typescript");
// A minimal element factory is enough to inspect the rendered tree.
const element = (type, props) => ({ type, props });
const jsxRuntime = { jsx: element, jsxs: element, Fragment: "Fragment" };
const source = readFileSync(path.resolve(import.meta.dirname, "../app/components/timeline-editor.tsx"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

const ACTIVE_NOTE = "Wait for the current render to finish before saving or rendering.";

// Render the whole compiled client component with a small hook adapter and
// read its JSX buttons; fetch is the network boundary.
function mountTimelineEditor(jobStatus) {
  const states = [];
  const requests = [];
  let hookIndex = 0;
  let pending;
  let tree;
  const hooks = {
    useState(initial) {
      const index = hookIndex++;
      if (!(index in states)) states[index] = initial;
      return [states[index], (value) => {
        states[index] = typeof value === "function" ? value(states[index]) : value;
      }];
    },
    useTransition() {
      return [false, (callback) => { pending = callback(); }];
    },
    useEffect() {},
  };
  const exports = {};
  vm.runInNewContext(compiled, {
    exports,
    require(specifier) {
      if (specifier === "react") return hooks;
      if (specifier === "react/jsx-runtime") return jsxRuntime;
      if (specifier === "next/navigation") return { useRouter: () => ({ push() {}, refresh() {} }) };
      if (specifier === "@/app/lib/browser-api") return { browserApiPath: (p) => `/api/backend${p}` };
      if (specifier === "@/app/lib/api-error") return { readApiError: async () => new Error("API error") };
      if (specifier === "@/app/components/status-badge") return { StatusBadge: () => null };
      throw new Error(`Unexpected import: ${specifier}`);
    },
    fetch: async (url, options) => {
      requests.push({ url, method: options?.method ?? "GET" });
      return {
        ok: false,
        status: 409,
        json: async () => ({ detail: "A render is already queued or running for this movie." }),
      };
    },
  }, { filename: "timeline-editor.js" });

  const props = {
    movieId: "movie-1",
    initialSlots: [
      { id: "slot-1", sortIndex: 0, overlayText: "", clipId: "clip-1" },
      { id: "slot-2", sortIndex: 1, overlayText: "", clipId: "clip-2" },
    ],
    availableClips: [
      { id: "clip-1", name: "A-B-C.mp4", team: "A", player: "B", tier: "C", durationSeconds: 3 },
      { id: "clip-2", name: "D-E-F.mp4", team: "D", player: "E", tier: "F", durationSeconds: 3 },
    ],
    initialJob: {
      id: "job-1",
      status: jobStatus,
      finalStatusReason: null,
      acceptedClipCount: 0,
      failedClipCount: 0,
      attempts: [],
    },
    minClipCount: 2,
  };

  function render() {
    hookIndex = 0;
    tree = exports.TimelineEditor(props);
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
  const button = (label) => find((node) => node.type === "button" && node.props.children === label);
  return {
    requests,
    button,
    hasText: (text) => Boolean(find((node) => node.props?.children === text)),
    // A browser does not dispatch click to a disabled button.
    async click(label) {
      const node = button(label);
      if (node.props.disabled) return;
      node.props.onClick();
      await pending;
      render();
    },
    // Calls the handler even if the button is disabled, to check the guard.
    async invoke(label) {
      pending = undefined;
      button(label).props.onClick();
      await pending;
      render();
    },
  };
}

const writes = (requests) =>
  requests.filter(({ method }) => method === "PUT" || method === "POST");

test("Timeline editor disables Queue Render and Save Timeline while a render is queued or running", async () => {
  for (const status of ["queued", "running"]) {
    const editor = mountTimelineEditor(status);
    assert.equal(editor.button("Queue Render").props.disabled, true,
      `Queue Render must be disabled while the job is ${status} (POST /render returns 409)`);
    assert.equal(editor.button("Save Timeline").props.disabled, true,
      `Save Timeline must be disabled while the job is ${status} (PUT /timeline returns 409)`);
    assert.ok(editor.hasText(ACTIVE_NOTE), `the editor must explain why while the job is ${status}`);
    await editor.click("Queue Render");
    await editor.click("Save Timeline");
    assert.deepEqual(writes(editor.requests), [],
      `no timeline save or render request may be issued while the job is ${status}`);
  }
});

test("Timeline editor handlers issue no save or render request while a render is active", async () => {
  for (const status of ["queued", "running"]) {
    const editor = mountTimelineEditor(status);
    await editor.invoke("Queue Render");
    await editor.invoke("Save Timeline");
    assert.deepEqual(writes(editor.requests), [],
      `handlers must not fetch while the job is ${status}`);
  }
});

test("Timeline editor re-enables Queue Render and Save Timeline once the render is terminal", async () => {
  for (const status of ["succeeded", "failed"]) {
    const editor = mountTimelineEditor(status);
    assert.equal(editor.button("Queue Render").props.disabled, false,
      `Queue Render must be enabled once the job has ${status}`);
    assert.equal(editor.button("Save Timeline").props.disabled, false,
      `Save Timeline must be enabled once the job has ${status}`);
    assert.equal(editor.hasText(ACTIVE_NOTE), false,
      `the active-render note must be absent once the job has ${status}`);
    await editor.click("Queue Render");
    assert.deepEqual(writes(editor.requests), [
      { url: "/api/backend/movies/movie-1/render", method: "POST" },
    ]);
  }
});
