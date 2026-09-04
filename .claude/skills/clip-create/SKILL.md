---
name: clip-create
description: Working knowledge of the Clip Create Studio repo — a Next.js 16 App Router frontend plus a FastAPI/SQLAlchemy backend and an FFmpeg render worker that concatenates 15-second labeled clips into one MP4. Load before editing anything under app/, backend/, or scripts/, or when asked about movies, clips, slots, render jobs, the timeline editor, the Discord session flow, or the admin console.
---

# Clip Create Studio

Two apps in one repo, plus a third process:

| Process | Entry point | Port |
|---|---|---|
| Next.js 16 frontend (App Router) | repo root, `app/` | 3000 |
| FastAPI backend | `backend/app/main.py` | 8000 |
| FFmpeg render worker | `backend/app/worker.py` | — (polls DB) |

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

**Filenames are the contract.** Uploads must be `team-player-tier.ext`, parsed by
`backend/app/filename_parser.py` into the three-line label burned bottom-left. The regex is
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
`test-output/samples/report.html` with frames from the middle of every clip and sting.

## Render pipeline (read before touching renderer.py)

Two passes, in `backend/app/services/`:

1. `renderer.normalize_clip` -- one ffmpeg call per clip: scale/pad to 1920x1080 at 30fps,
   always emit a stereo 48kHz track, burn the label from `brand.create_label_image`.
2. `renderer.build_chain_command` -- one filter graph joining the normalized clips with
   `xfade`, passing through a generated brand sting at each boundary.

**Timing is planned in integer frames, never seconds.** `xfade` snaps offsets to frame
boundaries while `acrossfade` is sample-exact, so a plan in floating-point seconds drifts
audio from video at every boundary and accumulates. 30fps/48kHz means one frame is exactly
1600 samples. If you touch the arithmetic, `backend/tests/test_renderer_planning.py`
asserts the offsets against hand-worked numbers -- run it before anything else.

The running offsets, where `L_i` is the accumulated length after clip `i`:

```
O_sting(i)  = L_i - b        O_clip(i+1) = L_i + d        L_(i+1) = L_i + d + N_(i+1)
```

`offset` is relative to the *accumulated* first input, so it must be recomputed after every
join. Total = `sum(clip frames) + (n-1) x hold`.

`build_chain_command` is a pure function returning `list[str]` -- keep it that way. The
graph string is the part most likely to be subtly wrong and this is what makes it
assertable without running ffmpeg.

## Brand decisions (`services/brand.py`)

Pure Pillow, no ffmpeg, no DB. Style/direction/colour are seeded by
`sha256(f"{movie_id}:{index}:{salt}")`. **Never `random.seed()`** (global state in a
long-lived worker) and **never `hash()`** (randomized per process by PYTHONHASHSEED --
there is a test that catches a regression to it). A sting is left by the `MIRROR` of the
transition that entered it; lead colour strictly alternates on boundary parity rather than
hashing, because hashing clumps three blues in a row often enough to look like a bug.

Logos are matched by `normalize_team()` against the `team` token from the filename. A
missing, unmatched, or unreadable logo must always degrade to the text-only label -- it
must never fail a render.

## Known issues -- check before "fixing" something that looks intentional

- Admin endpoints have no pagination and issue per-row queries.
- Schema changes go through `migrations.ensure_media_columns` /
  `ensure_nullable_columns` / `ensure_foreign_key_actions` -- hand-rolled idempotent
  helpers, not Alembic. New tables come from `create_all`.
- Removing an ID from `ADMIN_DISCORD_IDS` does not revoke an existing admin
  (`upsert_discord_user` uses `is_admin or ...`). Expired sessions are never reaped.
- `SameSite=lax` is the only CSRF defence.
- The join pass opens `2n-1` inputs at once, so movies above
  `render_max_graph_inputs` (24) are refused at queue time rather than chunked. The
  limit is file descriptors and decoders, not argv length -- the graph is ~17KB against
  an `ARG_MAX` of 1MB.
- `_video_summary().downloadUrl` is absolute to `fastapi_public_url` rather than the
  `/api/backend` rewrite, so inline playback breaks once frontend and backend are on
  different hosts. `logo-library.tsx` does it correctly; that page is the outlier.
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
