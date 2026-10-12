---
name: clip-create
description: Working knowledge of the Clip Create Studio repo — a Next.js 16 App Router frontend plus a FastAPI/SQLAlchemy backend and an FFmpeg render worker that concatenates short labeled clips into one MP4. Load before editing anything under app/, backend/, or scripts/, or when asked about movies, clips, slots, render jobs, the timeline editor, the Discord session flow, or the admin console.
---

# Clip Create Studio

Two apps in one repo, plus a third process:

| Process | Entry point | Port |
|---|---|---|
| Next.js 16 frontend (App Router) | repo root, `app/` | 3000 |
| FastAPI backend | `backend/app/main.py` | 8000 |
| FFmpeg render worker | `backend/app/worker.py` | — (polls DB) |

Everything also runs containerised: `docker compose up -d --build`, then
`./scripts/docker-smoke.sh` to prove uploads, rendering and download work. The API and
worker share one image (both need ffmpeg — the API probes uploads with ffprobe) and a
`clip-storage` volume. `STORAGE_ROOT` must be absolute there, since the default relative
path resolves against each container's working directory.

MySQL 8.4 runs in Docker (`docker compose up -d mysql`). `./scripts/run-dev.sh` starts all
four together and is the preferred way to run the app; it preflights ffmpeg, ffprobe, lsof,
the venv, and both `.env` files, and it leaves MySQL running on Ctrl-C.

## Read this first

`AGENTS.md` is not boilerplate. **Next.js 16 differs from training data.** Before writing
frontend code, read the relevant file under `node_modules/next/dist/docs/01-app/`. Facts
that matter here and are easy to get wrong from memory:

- `params` and `searchParams` are `Promise`s. `cookies()` and `headers()` are async. The
  synchronous compatibility shims from 15 are **removed**. See
  `02-guides/upgrading/version-16.md`.
- Turbopack is the default for `next dev` and `next build`. No `--turbopack` flag needed;
  `turbopack` is a top-level `next.config.ts` key, not `experimental.turbopack`.
- `middleware.ts` is renamed to the `proxy.ts` convention
  (`03-api-reference/03-file-conventions/proxy.md`). This repo has neither.
- `npx next typegen` generates `PageProps<'/route'>` / `LayoutProps` helpers. Prefer them
  over hand-written `{ params: Promise<{...}> }` props.

## Data model (`backend/app/models.py`)

```
User ─┬─ OAuthAccount        (discord)
      ├─ SessionRecord       (sha256 token digest, 14d)
      ├─ Clip                (uploaded file + parsed team/player/tier)
      └─ Movie ─┬─ MovieSlot          (ordered position → clip_id, overlay_text)
                ├─ RenderJob ── RenderJobAttempt ── RenderJobClipResult
                └─ latest_video_id → Video
```

Every id is a `String(36)` UUID generated in Python (`uuid_value`), not a DB sequence.
`TimestampMixin` gives `created_at` / `updated_at` via `server_default=func.now()`.

The three-level render hierarchy (job → attempt → clip result) exists so the admin console
can say *which clip* failed and *why*. Preserve that granularity when touching the worker.

## Request flow

Browser calls go to `/api/backend/*`, which `next.config.ts` rewrites to
`${FASTAPI_PUBLIC_URL}/api/v1/*`. Always build client-side URLs with
`browserApiPath()` from `app/lib/browser-api.ts` — never hardcode a backend origin in a
client component.

Server Components call `app/lib/api.ts`, which is `import "server-only"` and talks to
`FASTAPI_INTERNAL_URL` directly, forwarding the incoming cookie header. Add new
server-side reads as a function there, not as an inline `fetch`.

Auth is a backend-owned httpOnly cookie. `app/(app)/layout.tsx` calls `requireSession()`;
admin pages additionally call `requireSession({ admin: true })`, which redirects rather
than throwing.

## Conventions

**Backend**
- Absolute imports only: `from backend.app.x import y`. Run with `PYTHONPATH=<repo root>`.
- Routers own their HTTP concerns and hand-build response models field by field. Pydantic
  schemas are **camelCase on the wire** (`displayName`, `sortIndex`) while SQLAlchemy
  columns are snake_case. Request payloads are the exception — `MovieSlotPayload` uses
  snake_case (`sort_index`, `clip_id`). Match the surrounding file; don't "fix" one side.
- Ownership is enforced by filtering the query, not by a post-hoc check:
  `select(Movie).where(Movie.id == movie_id, Movie.user_id == user.id)` then 404. Missing
  and forbidden both return 404. Keep that — it avoids leaking existence.
- `get_current_user` / `get_admin_user` from `backend/app/deps.py` are the only auth deps.
- Settings are a single `pydantic-settings` `Settings` object, `@lru_cache`d via
  `get_settings()`. Env files load in order `backend/.env` then `.env`.

**Frontend**
- Pages are async Server Components. Interactivity lives in `app/components/*.tsx` marked
  `"use client"` and receives data as props.
- Styling is plain CSS classes from `app/globals.css` (`panel`, `stack-md`, `page-shell`,
  `button button-primary`, `badge[data-tone]`), with occasional Tailwind utilities. There
  is no component library. Reuse `PageHeader`, `SummaryCard`, `StatusBadge`.
- JSX props are alphabetized in existing components. Keep it.

**Labels** are one line, middle-dot separated (`Falcons · Ace · Gold`), text only, drawn
with a stroke and no backing plate. The face is Anton, vendored at
`backend/app/assets/fonts/` -- Ultimatum, which was asked for, is not on Google Fonts.
`RENDER_FONT_PATH` overrides it. A replacement font needs a `·` (U+00B7) glyph.

**The brand sweep** is the stinger overlaid across every cut. The bundled file at
`backend/app/assets/stings/brand-sweep.mov` is the default; `RENDER_BRAND_SWEEP_PATH`
overrides it deployment-wide, and a `BrandSweep` row (one per user, unique on `user_id`)
overrides both. It replaced a brand logo composited into a generated wedge card, which
replaced a per-team logo library; `drop_legacy_logo_table` clears the last of that.

**Filenames are the contract.** Uploads must be `team-player-tier.ext`, parsed by
`backend/app/filename_parser.py` into the one-line label burned bottom-left. The regex is
duplicated in `app/components/create-movie-form.tsx` and the two copies **do not agree**
(see Known issues). If you touch either, fix both or delete the client copy.

## Verify with

```bash
pytest                    # fast: unit + API, ~2s
pytest -m integration     # real ffmpeg, ~50s
pytest -m ""              # everything
npx tsc --noEmit && npx eslint && npm run build
```

`pyproject.toml` sets `pythonpath`, so no `PYTHONPATH=.` is needed, and integration
tests are deselected by default.

**Two things in `conftest.py` are load-bearing.** `PRAGMA foreign_keys=ON` -- SQLite
leaves enforcement off, and a constraint bug that passes on an unenforced fixture is
worse than no test. And `check_same_thread=False` **with** `StaticPool`, because
TestClient serves requests on a different thread than the test body. `TestClient(app)`
is deliberately not used as a context manager: entering it runs `lifespan`, which calls
`create_all` against the real MySQL engine.

To render real footage, drop clips named `team-player-tier.ext` into
`backend/tests/fixtures/samples/` and run `pytest -m integration`; it writes
`test-output/samples/report.html` with a frame from the middle of every clip and one at
every cut -- where the sweep should be covering the picture completely.

## Render pipeline (read before touching renderer.py)

Two passes, in `backend/app/services/`:

1. `renderer.normalize_clip` -- one ffmpeg call per clip: scale/pad to 1920x1080 at 30fps,
   always emit a stereo 48kHz track, burn the label from `brand.create_label_image`.
2. `renderer.build_chain_command` -- one `concat` joining the normalized clips with hard
   cuts, then one `overlay` per cut compositing the brand sweep across it.

**The sweep is overlaid, never spliced in.** It has an alpha channel; as its own segment
the transparent head and tail would flatten onto black and dip the picture at every join.
The consequence to hold onto: **a transition costs no time**, so a movie is exactly
`sum(clip frames)` long. Anything that adds a hold has reintroduced the old model.

**Timing is planned in integer frames, never seconds.** `trim=end_frame` and `tpad=start`
both take frame counts, and 30fps/48kHz means one frame is exactly 1600 samples, so one
plan is exact for video and audio at once. If you touch the arithmetic,
`backend/tests/unit/test_renderer_planning.py` asserts placements against hand-worked
numbers -- run it before anything else.

Where `C_i` is the cumulative frame count through clip `i` and `cover` is the frame of the
sweep at which it hides the picture:

```
start_i = C_i - cover                       total = sum(clip frames)
```

`start` is relative to whichever segment list is being joined, so it must be recomputed
per group -- `place_boundaries` does that, `decide_boundaries` must not.

`build_chain_command` is a pure function returning `list[str]` -- keep it that way. The
graph string is the part most likely to be subtly wrong and this is what makes it
assertable without running ffmpeg.

## Brand decisions (`services/brand.py`)

Pure: Pillow for the label, arithmetic for the sweep. No ffmpeg, no DB. It used to hold
the transition families, the seeded style/direction/colour picks and the wedge-card
generator; all of that went with the logo.

`cover_frame()` is the one decision left. Given one mean-alpha value per frame it returns
the middle of the fully-covered span -- the frame the clips should cut on. The threshold
is 250, not 255, so a replacement sweep with a soft edge still reads as covering. A sweep
that never covers falls back to its own midpoint. Measuring the alpha is `renderer`'s job
(it needs ffmpeg); choosing from it is this module's, which is what makes it testable
against a hand-written list.

Nothing about the sweep may fail a render: a missing, unreadable, or alpha-less file
degrades to bare cuts. Same for clips too short to carry it.

## Known issues -- check before "fixing" something that looks intentional

- Admin endpoints have no pagination and issue per-row queries.
- Schema changes go through `migrations.ensure_media_columns` /
  `ensure_nullable_columns` / `ensure_foreign_key_actions` -- hand-rolled idempotent
  helpers, not Alembic. New tables come from `create_all`.
- Removing an ID from `ADMIN_DISCORD_IDS` does not revoke an existing admin
  (`upsert_discord_user` uses `is_admin or ...`). Expired sessions are never reaped.
- `SameSite=lax` is the only CSRF defence.
- The join pass opens `2n-1` inputs at once -- one per clip, one per swept cut -- so
  movies above `render_max_graph_inputs` (24) are refused at queue time rather than
  chunked. The limit is file descriptors and decoders, not argv length.
- `Movie.transition_style` and `transition_hold_seconds` are gone from the model but
  still exist as columns on any pre-existing MySQL table. Nothing reads them.
- The bundled sweep is 90MB of QuickTime RLE in git. Swapping it adds another 90MB to
  history; git cannot delta-compress it.
- `_video_summary().downloadUrl` is absolute to `fastapi_public_url` rather than the
  `/api/backend` rewrite, so inline playback breaks once frontend and backend are on
  different hosts. `brand-sweep.tsx` does it correctly; that page is the outlier.
- Uploads have no progress indicator; `fetch` cannot report it, so this needs XHR.
- The local `backend/.env` ships `AUTH_ENABLED=false` with `AUTH_BYPASS_ADMIN=true`.
  It is gitignored, but it means every unauthenticated request resolves to one shared
  admin user locally. Test fixtures pin auth explicitly rather than inheriting it.

## Traps that have already bitten

Each of these was a real bug; the pattern matters more than the instance.

- **Python regex handed to `new RegExp`.** `(?P<name>)` is Python-only; JS uses
  `(?<name>)`. Serving `FILENAME_RE.pattern` to the browser crashed `/movies/new` on
  render, and neither `tsc` nor `eslint` catches it. `CLIENT_FILENAME_PATTERN` exists
  for this; a test compiles it under Node.
- **`Path(f"{uuid}-{filename}").name`** collapses to the basename when the filename
  contains a separator, discarding the uniqueness prefix. Use `safe_upload_name`.
- **Flushing a model before its NOT NULL columns are set.** `MovieSlot.sort_index`.
- **Deleting a row something else references.** There is almost no `ondelete` in
  `models.py`; cascades are ORM-level only. See the unwind orders in the delete
  handlers, and note a clip is referenced from *two* tables, not one.
- **`useState` seeded from props never re-syncs** after `router.refresh()`, which
  re-renders the Server Component without remounting the client one.
- **Chained `xfade` buffered ~0.8GB per clip at 1080p.** Seven clips in one graph hit
  4.61GB and were OOM-killed. Overlaying the sweep is far cheaper, but `join_segments`
  still renders in groups of `render_chunk_size` (4) to bound open decoders.
  `decide_boundaries` and `place_boundaries` are split for this: a boundary is placed
  twice (inside its group, then between group outputs) but decided once, so grouping
  never changes the output. A decision that consulted the cumulative timeline instead of
  the two adjacent clips would break that.
- **`media["transitions"]` must report the GLOBAL placement.** `join_segments` re-places
  a seam boundary against the group outputs it joins, leaving a group-relative `start`
  on the object; `render_movie` captures the placement before joining for this reason.
- **`scale` negotiates its output format with whatever is downstream** and will drop to
  an alpha-less one, which turns the stinger into an opaque rectangle and makes
  `alphaextract` unsatisfiable ("could not choose their formats"). `sweep_chain` pins
  `format=rgba` on *both* sides of the scale.
- **`overlay` needs `eof_action=pass`.** The sweep is 44 frames against a whole movie;
  the default repeats its last frame over everything after it.
- **ffmpeg needs `-nostats`.** Progress lines flood stderr and `_run` keeps only the
  last 2000 chars, so a real error gets pushed out. This hid an OOM kill completely.
- **`setpts` clears a filter link's frame rate.** ffmpeg 7's `concat` and `overlay`
  reject an input reading `1/0`; ffmpeg 8 tolerates it. Every branch feeding them
  re-asserts `fps` *after* `setpts`. Development is on 8.x and the container ships 7.x, so verify filter
  changes against both.
- **Next buffers the entire proxied request body, rewrites included**, capped at 10MB
  by default. This is not exclusive to `proxy.ts` -- assuming otherwise cost a working
  upload path. `MAX_UPLOAD_BYTES` is one budget shared by `proxyClientMaxBodySize`
  (build time), the API's 413, and the browser's pre-flight total check.
- **`rewrites()` is evaluated at `next build`**, not at runtime, and serialised into
  `routes-manifest.json`. The backend origin is a `BACKEND_ORIGIN` build arg. A
  `proxy.ts` would be runtime-configurable but buffers the request body (10MB default),
  which breaks clip uploads.
- **A module run as `python -m` reports `__name__` as `"__main__"`.** That logger sits
  outside the configured `backend` tree and inherits root's WARNING level, which silently
  dropped every worker INFO line. `logging_config` now configures `__main__` explicitly.
