import assert from "node:assert/strict";
import { File } from "node:buffer";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(path.resolve(import.meta.dirname, "../app/components/brand-sweep.tsx"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

// Drive the rendered input and button callbacks; model the browser's rule that
// selecting the input's current file does not produce another change event.
function mountBrandSweep(responses = [true, true], { maxBrandSweepBytes = 209715200 } = {}) {
  const states = [];
  const posts = [];
  let hookIndex = 0;
  let pending;
  let tree;
  let inputProps;
  let inputValue = "";
  const input = {
    files: [],
    get value() { return inputValue; },
    set value(value) {
      inputValue = value;
      if (value === "") this.files = [];
    },
  };
  const hooks = {
    useState(initial) {
      const index = hookIndex++;
      if (!(index in states)) states[index] = initial;
      return [states[index], (value) => { states[index] = value; }];
    },
    useRef(initial) {
      const index = hookIndex++;
      if (!(index in states)) states[index] = { current: initial };
      return states[index];
    },
    useTransition() {
      return [false, (callback) => { pending = callback(); }];
    },
  };
  const exports = {};
  vm.runInNewContext(compiled, {
    exports,
    require(specifier) {
      if (specifier === "react") return hooks;
      if (specifier === "react/jsx-runtime") return require(specifier);
      if (specifier === "next/navigation") return { useRouter: () => ({ refresh() {} }) };
      if (specifier === "@/app/lib/browser-api") return { browserApiPath: (p) => `/api/v1${p}` };
      if (specifier === "@/app/lib/api-error") return { readApiError: async () => new Error("API error") };
      throw new Error(`Unexpected import: ${specifier}`);
    },
    FormData,
    fetch: async (url, options) => {
      posts.push({ url, ...options });
      return { ok: responses[posts.length - 1] };
    },
  }, { filename: "brand-sweep.js" });

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
  function render() {
    hookIndex = 0;
    tree = exports.BrandSweep({
      sweep: { id: "sweep-1", originalName: "previous.webm", durationSeconds: null },
      supportedFormats: ["webm"],
      maxBrandSweepBytes,
    });
    inputProps = find((node) => node.type === "input" && node.props.id === "sweep-file").props;
    if (typeof inputProps.ref === "function") inputProps.ref(input);
    else if (inputProps.ref) inputProps.ref.current = input;
  }
  render();
  return {
    input,
    posts,
    button() { return find((node) => node.type === "button" && node.props.children === "Replace Sweep"); },
    alert() { return find((node) => node.props?.role === "alert"); },
    select(file) {
      const nextValue = `C:\\fakepath\\${file.name}`;
      if (input.value === nextValue) return;
      input.value = nextValue;
      input.files = [file];
      inputProps.onChange({ target: input, currentTarget: input });
      render();
    },
    async clickReplace() {
      const button = this.button();
      assert.equal(button.props.disabled, false, "replacement must be enabled before clicking");
      button.props.onClick();
      await pending;
      render();
    },
  };
}

test("successful upload clears the picker so the same file can be selected and posted again", async () => {
  const sweep = mountBrandSweep();
  const file = new File(["video"], "transition.webm", { type: "video/webm" });
  sweep.select(file);
  await sweep.clickReplace();

  assert.deepEqual(sweep.posts.map(({ url, method }) => [url, method]), [["/api/v1/sweeps", "POST"]]);
  assert.equal(sweep.posts[0].body.get("file").name, file.name);
  assert.equal(sweep.input.value, "", "the native input must be reset after a successful POST");
  assert.equal(sweep.button().props.disabled, true, "selected File state must be cleared");

  sweep.select(file);
  assert.equal(sweep.button().props.disabled, false, "the same file is selectable again");
  await sweep.clickReplace();
  assert.deepEqual(sweep.posts.map(({ url, method }) => [url, method]), [
    ["/api/v1/sweeps", "POST"], ["/api/v1/sweeps", "POST"],
  ]);
  assert.equal(sweep.posts[1].body.get("file").name, file.name);
  assert.equal(sweep.input.value, "");
  assert.equal(sweep.button().props.disabled, true);
});

test("failed upload keeps the file selected for retry", async () => {
  const sweep = mountBrandSweep([false, true]);
  const file = new File(["video"], "transition.webm", { type: "video/webm" });
  sweep.select(file);
  await sweep.clickReplace();

  assert.notEqual(sweep.input.value, "");
  assert.equal(sweep.button().props.disabled, false);
  await sweep.clickReplace();
  assert.equal(sweep.posts.length, 2);
  assert.equal(sweep.posts[1].body.get("file").name, file.name);
  assert.equal(sweep.input.value, "");
});

test("a sweep over maxBrandSweepBytes is refused without a request, in the API's wording", async () => {
  const sweep = mountBrandSweep([true], { maxBrandSweepBytes: 10 });
  const file = new File(["x".repeat(11)], "transition.webm", { type: "video/webm" });
  assert.equal(file.size, 11);
  sweep.select(file);
  await sweep.clickReplace();

  assert.equal(sweep.posts.length, 0, "no fetch for an over-limit sweep");
  const alert = sweep.alert();
  assert.ok(alert, "the error must render in a role=alert element");
  assert.equal(alert.props.children, "transition.webm exceeds the 10 byte limit.");
  assert.notEqual(sweep.input.value, "", "the selection is kept so another file can be chosen");
  assert.equal(sweep.button().props.disabled, false);
});

test("a sweep exactly at maxBrandSweepBytes still uploads", async () => {
  const sweep = mountBrandSweep([true], { maxBrandSweepBytes: 10 });
  const file = new File(["x".repeat(10)], "transition.webm", { type: "video/webm" });
  assert.equal(file.size, 10);
  sweep.select(file);
  await sweep.clickReplace();

  assert.deepEqual(sweep.posts.map(({ url, method }) => [url, method]), [["/api/v1/sweeps", "POST"]]);
  assert.equal(sweep.posts[0].body.get("file").name, file.name);
  assert.equal(sweep.alert(), null);
});
