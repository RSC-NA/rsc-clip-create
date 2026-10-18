import assert from "node:assert/strict";
import { File } from "node:buffer";
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
function mountCreateMovieForm(overrides = {}) {
  const formConfig = { ...config, ...overrides };
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
    // Node requires absolute URLs while the browser resolves relative Request URLs.
    Request: class extends Request {
      constructor(url, options) { super(new URL(url, "http://localhost"), options); }
    },
    fetch: async (input, options) => {
      const url = typeof input === "string" ? input : new URL(input.url).pathname;
      const method = options?.method ?? input.method;
      if (method === "POST") posts.push({ url, body: options?.body ?? input });
      return {
        ok: true,
        json: async () => url.endsWith("/clips") ? [{ id: "clip-1" }] : { id: "movie-1" },
      };
    },
  }, { filename: "create-movie-form.js" });

  function render() {
    hookIndex = 0;
    tree = exports.CreateMovieForm({ config: formConfig });
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
      this.selectClips([clip]);
    },
    selectClips(clips) {
      find((node) => node.type === "input" && node.props.id === "movie-files")
        .props.onChange({ target: { files: clips } });
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

test("Create Movie preflights the encoded multipart upload and posts the selected files when it fits", async () => {
  const clips = ["Falcons-Ace-Gold.mp4", "Eagles-Bee-Silver.mp4"].map(
    (name, index) => new File([new Uint8Array(4096).fill(index + 1)], name, { type: "video/mp4" }),
  );
  const upload = new FormData();
  clips.forEach((clip) => upload.append("files", clip));
  const encodedBytes = (await new Request("http://localhost/clips", {
    method: "POST", body: upload,
  }).blob()).size;
  assert.ok(8192 < 8320 && encodedBytes > 8320, "file bytes fit, but the multipart request does not");

  const oversized = mountCreateMovieForm({ maxClipSizeBytes: 4096, maxUploadBytes: 8320 });
  oversized.enterTitle("Spring launch");
  oversized.selectClips(clips);
  await oversized.clickCreate();
  assert.match(oversized.error(), /per upload/);
  assert.equal(oversized.posts.length, 0, "reject before uploading clips or creating a movie");

  const fitting = mountCreateMovieForm({ maxClipSizeBytes: 4096, maxUploadBytes: 10000 });
  fitting.enterTitle("Spring launch");
  fitting.selectClips(clips);
  await fitting.clickCreate();
  assert.equal(fitting.error(), null);
  assert.deepEqual(fitting.posts.map(({ url }) => url), ["/api/backend/clips", "/api/backend/movies"]);
  const posted = await fitting.posts[0].body.clone().formData();
  const postedFiles = posted.getAll("files");
  assert.equal(postedFiles.length, 2);
  for (const [index, file] of postedFiles.entries()) {
    assert.equal(file.name, clips[index].name);
    assert.equal(file.type, clips[index].type);
    assert.deepEqual(new Uint8Array(await file.arrayBuffer()), new Uint8Array(await clips[index].arrayBuffer()));
  }
});

// The pattern GET /api/v1/config serves (backend/app/filename_parser.py
// CLIENT_FILENAME_PATTERN with MAX_SEGMENT_LENGTH = 80).
const segment = String.raw`\s*[^\s\-./\\](?:[^-./\\]{0,78}[^\s\-./\\])?\s*`;
const servedFilenamePattern = String.raw`^${segment}-${segment}-${segment}\.[A-Za-z0-9]+$`;

test("Create Movie preflight counts emoji in a clip name as one character each, like the API", async () => {
  const eagle = "\u{1F985}";
  for (const [team, accepted] of [[eagle.repeat(41), true], [eagle.repeat(80), true], [eagle.repeat(81), false]]) {
    const form = mountCreateMovieForm({ filenamePattern: servedFilenamePattern });
    const name = `${team}-Ace-Gold.mp4`;
    form.enterTitle("Spring launch");
    form.selectClips([new File([new Uint8Array(16)], name, { type: "video/mp4" })]);
    await form.clickCreate();

    if (accepted) {
      assert.equal(form.error(), null);
      assert.deepEqual(form.posts.map(({ url }) => url), ["/api/backend/clips", "/api/backend/movies"]);
      const posted = await form.posts[0].body.clone().formData();
      assert.equal(posted.get("files").name, name);
    } else {
      assert.equal(form.error(), `${name} must use team-player-tier.fileext.`);
      assert.deepEqual(form.posts, []);
    }
  }
});

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
