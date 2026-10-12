import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(path.resolve(import.meta.dirname, "../app/components/create-movie-form.tsx"), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

const config = {
  filenamePattern: "^[^-]+-[^-]+-[^.]+\\.mp4$",
  supportedExtensions: ["mp4"],
  minClipCount: 1,
  maxClipCount: 10,
  maxClipSizeBytes: 1000,
  maxUploadBytes: 10000,
  maxClipDurationSeconds: 30,
};

// Render the entire compiled client component, then drive its JSX input and
// button callbacks rather than extracting submit() and supplying synthetic state.
// The hook adapter rerenders when an input changes; fetch is the network boundary.
function mountCreateMovieForm() {
  const states = [];
  const posts = [];
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
  const exports = {};
  vm.runInNewContext(compiled, {
    exports,
    require(specifier) {
      if (specifier === "react") return hooks;
      if (specifier === "react/jsx-runtime") return require(specifier);
      if (specifier === "next/navigation") return { useRouter: () => ({ push() {}, refresh() {} }) };
      if (specifier === "@/app/lib/browser-api") return { browserApiPath: (p) => `/api/backend${p}` };
      if (specifier === "@/app/lib/api-error") return { readApiError: async () => new Error("API error") };
      throw new Error(`Unexpected import: ${specifier}`);
    },
    FormData,
    fetch: async (url, options) => {
      if (options.method === "POST") posts.push({ url, body: options.body });
      return {
        ok: true,
        json: async () => url.endsWith("/clips") ? [{ id: "clip-1" }] : { id: "movie-1" },
      };
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
    posts,
    enterTitle(value) {
      find((node) => node.type === "input" && node.props.id === "movie-title")
        .props.onChange({ target: { value } });
      render();
    },
    selectClip() {
      const clip = new Blob(["clip"], { type: "video/mp4" });
      clip.name = "Falcons-Ace-Gold.mp4";
      find((node) => node.type === "input" && node.props.id === "movie-files")
        .props.onChange({ target: { files: [clip] } });
      render();
    },
    async clickCreate() {
      const button = find((node) => node.type === "button" && node.props.children === "Create Movie");
      assert.equal(button.props.disabled, false, "the title must reach the submit handler");
      button.props.onClick();
      await pending;
      render();
    },
    error() {
      return find((node) => node.props?.role === "alert")?.props.children ?? null;
    },
  };
}

test("Create Movie rejects a 256-character title before either POST, but accepts 255", async () => {
  for (const title of ["x".repeat(255), "x".repeat(256), "🎬".repeat(255)]) {
    const form = mountCreateMovieForm();
    form.enterTitle(title);
    form.selectClip();
    await form.clickCreate();

    if (Array.from(title).length > 255) {
      assert.deepEqual(form.posts, [], "an invalid title must not upload orphan clips or create a movie");
      assert.match(form.error(), /255 characters or fewer/);
    } else {
      assert.deepEqual(form.posts.map(({ url }) => url), ["/api/backend/clips", "/api/backend/movies"]);
      assert.equal(JSON.parse(form.posts[1].body).title, title);
      assert.equal(form.error(), null);
    }
  }
});
