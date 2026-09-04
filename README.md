# Clip Create Studio

Clip Create Studio is a combined `Next.js 16 + FastAPI` application for assembling multiple 15-second source clips into longer-form YouTube videos.

## What is in this repo

- A Next.js App Router frontend at the repo root
- A FastAPI backend in [`backend/`](/Users/monty/Projects/clip_create/backend)
- MySQL-oriented SQLAlchemy models for users, clips, movies, team logos, render jobs,
  render attempts, clip-level diagnostics, and videos
- Admin-only pages for users, videos, and render errors

## Frontend routes

- `/login`
- `/logout`
- `/movies`
- `/movies/new`
- `/logos`
- `/movies/[movieId]/edit`
- `/admin`
- `/admin/users`
- `/admin/videos`
- `/admin/errors`

## Backend endpoints

- `GET /api/v1/auth/discord/login`
- `GET /api/v1/auth/discord/callback`
- `GET /api/v1/auth/session`
- `POST /api/v1/auth/logout`
- `GET /api/v1/movies`
- `POST /api/v1/movies`
- `GET /api/v1/movies/{movie_id}`
- `PUT /api/v1/movies/{movie_id}/timeline`
- `POST /api/v1/movies/{movie_id}/render`
- `GET /api/v1/config`
- `GET /api/v1/clips`
- `POST /api/v1/clips`
- `GET /api/v1/logos`
- `POST /api/v1/logos`
- `GET /api/v1/logos/{logo_id}/file`
- `DELETE /api/v1/logos/{logo_id}`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/videos/{video_id}/location`
- `GET /api/v1/videos/{video_id}/file`
- `GET /api/v1/admin/dashboard`
- `GET /api/v1/admin/users`
- `GET /api/v1/admin/videos`
- `GET /api/v1/admin/errors`

## Clip rendering

Uploads must use the strict filename format `team-player-tier.fileext`. The browser
validates against `GET /api/v1/config` so the client and server rules cannot drift.
The backend stores the parsed metadata, requires at least two clips for a render, and
renders in two FFmpeg passes:

1. **Normalize.** Each clip is scaled and padded to 1920x1080 at 30fps with a stereo
   48kHz track, and its three-line `team / player / tier` label is burned into the
   bottom left, next to the team logo if one is on file.
2. **Join.** The normalized clips are chained with `xfade`, passing through a generated
   blue-and-orange brand sting at every cut.

Install FFmpeg and FFprobe on the worker host, then run the worker alongside the API:

```bash
python -m backend.app.worker
```

Limits and supported extensions can be configured with `MIN_CLIP_COUNT`,
`MAX_CLIP_COUNT`, `MAX_CLIP_SIZE_BYTES`, `MAX_CLIP_DURATION_SECONDS`, and
`SUPPORTED_CLIP_EXTENSIONS`.

## Clip labels

Each clip carries one line in the bottom-left corner, its parts separated by middle
dots and drawn straight onto the frame with no backing plate:

```
Falcons · Ace Rivera · Gold
```

The text is stroked rather than boxed. White type over pale footage is unreadable on
its own, and the stroke keeps it legible without putting a rectangle on the picture.
Set `RENDER_LABEL_STROKE_WIDTH=0` for bare glyphs.

### The font

The renderer draws server-side with Pillow, so the face has to be a real font file, not
a webfont link. **Anton** is vendored at `backend/app/assets/fonts/` (SIL OFL, licence
alongside it) and is the default, which also keeps rendering identical on every host.

**Anton stands in for Ultimatum, which is not published on Google Fonts** — there is no
family by that name in the catalogue. To use the real Ultimatum, drop the `.ttf`
somewhere the API and worker can both read and point `RENDER_FONT_PATH` at it:

```yaml
# docker-compose.yml, on both the api and worker services
environment:
  RENDER_FONT_PATH: /app/storage/fonts/Ultimatum.ttf
```

Any other font works the same way. Whatever you choose needs a glyph for `·` (U+00B7)
or every separator renders as a `.notdef` box; there is a test that checks this for the
bundled face.

## Brand logo

One logo per account, shown in the centre of every transition sting. Upload it at
`/logos`; re-uploading replaces it.

This used to be a per-team library: logos were tagged with a team name, matched against
the `team` token in each clip's filename, and composited into the clip's bottom-left
label. Clip labels are text-only now, and the mark appears at the transitions instead.
The migration collapses any existing per-team rows to one per user, keeping the most
recent, and drops the team columns.

A missing logo, an unreadable file, or a row pointing at a deleted file all degrade to a
plain sting -- it never fails a render. `RENDER_STING_LOGO_SCALE` (default `0.34`) sets
the size as a fraction of the frame's shorter side.

Note the migration removes surplus rows in SQL, which does not unlink their files. Run
`python -m backend.app.maintenance audit --fix` afterwards to clear them.

## Transitions

Clips are joined by a brand sting rather than a hard cut. Each movie chooses a style and
a hold duration in the editor; the renderer then varies the specific transition,
direction and lead colour at every boundary.

| Style | Character |
| --- | --- |
| `sweep` | Wipes, slides and covers |
| `slice` | Hard-edged slices and diagonals |
| `dissolve` | Dissolves, pixelisation and grain |
| `pop` | Circles, zooms and squeezes |
| `storm` | Wind and blur |
| `classic` | A plain crossfade through the card |

Choices are seeded from `sha256(movie_id:boundary_index)`, so adjacent transitions differ
while a re-render of the same movie reproduces the same sequence. A sting is left by the
mirror of the transition that entered it, so motion carries through the card.

Timing is planned in **integer frames, never seconds**. `xfade` snaps its offsets to frame
boundaries while `acrossfade` is sample-exact, so a plan expressed in floating-point
seconds drifts audio away from video a little at every boundary and the error accumulates.
At 30fps/48kHz one frame is exactly 1600 samples, so a frame-based plan is exact for both
streams.

A transition adds its hold to the running time:

```
duration = sum(clip durations) + (clip count - 1) x hold
```

Set the hold to `0` for hard cuts. Clips too short to carry the blend clamp it
automatically, and degrade to a hard cut rather than failing.

## Render diagnostics

The backend records detailed render outcomes in two dedicated tables:

- `render_job_attempts`
  Stores per-attempt failure stage, error category, normalized codes, and debug
  payloads. Re-rendering a movie adds an attempt rather than a new job, so the retry
  history stays in one place.
- `render_job_clip_results`
  Stores clip-by-clip acceptance or rejection, the exact order submitted, local/provider reason codes, human summary, technical detail, and provider message text.

That data is surfaced in the editor and in the admin error console.

## Running in Docker

The whole stack — MySQL, the API, the render worker and the frontend — runs from one
compose file:

```bash
docker compose up -d --build
./scripts/docker-smoke.sh     # uploads, renders and downloads, to prove it works
```

Then open http://localhost:3000. Authentication is disabled by default (see below).

| Service | Image | Port | Notes |
| --- | --- | --- | --- |
| `mysql` | mysql:8.4 | 3306 | healthcheck gates everything else |
| `api` | `backend/Dockerfile` | 8000 | runs the schema creation and migrations at startup |
| `worker` | same image, different command | — | waits for the API so the tables exist |
| `web` | `Dockerfile` | 3000 | Next.js standalone output |

The API and the worker share a `clip-storage` volume: the worker reads uploaded clips
and writes renders back to where the API serves them from. `STORAGE_ROOT` is set to the
absolute `/app/storage`, because the default relative path would otherwise resolve
against each container's own working directory.

### Two things worth knowing before changing configuration

**The backend origin is baked into the frontend at build time.** Next evaluates
`rewrites()` during `next build` and serialises the result into
`routes-manifest.json`; it is not read at runtime. The compose file passes it as the
`BACKEND_ORIGIN` build arg, so pointing the image at a different backend means
rebuilding `web`. A `proxy.ts` would be runtime-configurable, but Next buffers the
request body when one is used (10MB by default), which would break clip uploads far
below the 500MB the API accepts.

**Compose reads `./.env` for `${...}` substitution.** That is the same file the local
Next dev server uses, so values there silently apply to the containers too. Override
explicitly if that is not what you want:

```bash
docker compose --env-file .env.docker up -d
```

### Configuration

Every value has a working default. The ones you are most likely to change:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AUTH_ENABLED` | `false` | **Set to `true` before exposing this anywhere.** Left false, every request resolves to one shared local admin. |
| `AUTH_BYPASS_ADMIN` | `true` | Whether that bypass user is an admin. |
| `DISCORD_CLIENT_ID` / `_SECRET` | placeholders | Required once `AUTH_ENABLED=true`. |
| `DISCORD_REDIRECT_URI` | `http://localhost:8000/api/v1/auth/discord/callback` | Must match the Discord app exactly. |
| `ADMIN_DISCORD_IDS` | `[]` | JSON array string, e.g. `["123456789012345678"]`. |
| `FASTAPI_PUBLIC_URL` | `http://localhost:8000` | Browser-facing; the API builds absolute download and logo URLs from it, so it must be reachable from the host, not from inside the compose network. |
| `SECURE_COOKIES` | `false` | Set to `true` behind HTTPS. |
| `MAX_UPLOAD_BYTES` | `536870912` (512 MB) | **Total bytes per upload request**, across every file in it. See below. |

### Upload size

Next buffers the **entire** request body of anything it proxies — `rewrites()`
included, not only `proxy.ts` — and the default cap is 10MB. Clip uploads are
multipart with several files, so a routine upload silently exceeded it: the body was
truncated and the upstream connection reset, which surfaced as an opaque failure with
no useful message anywhere.

`MAX_UPLOAD_BYTES` is therefore a single budget shared by three places, and they must
stay in step:

- `experimental.proxyClientMaxBodySize` in `next.config.ts` (read at **build** time)
- the API's `max_upload_bytes`, which returns a clear 413 when exceeded
- the browser, which checks the total selected size before sending anything

Note this is a **per-request total**, not per file: the per-clip cap
(`MAX_CLIP_SIZE_BYTES`) still applies on top. Because Next holds the whole body in
memory, raising this raises the frontend's peak memory per concurrent upload — prefer
uploading in smaller batches over setting it very high.

### FFmpeg version

The image installs Debian's ffmpeg (currently 7.1.x); development happens against 8.x.
The renderer is written to work on both, but that took a fix: `setpts` clears a filter
link's frame-rate metadata, and ffmpeg 7's `xfade` rejects an input whose rate reads as
`1/0` where 8 tolerates it. Every branch feeding `xfade` therefore re-asserts `fps`
*after* `setpts`. If you change the filter graph, run the integration suite against the
container's ffmpeg as well as your own — this class of bug is invisible locally.

## Local development

Without Docker, or for iterating on the frontend with hot reload:

### Prerequisites

### 1. Start MySQL

```bash
docker compose up -d mysql
```

### 2. Configure env vars

Copy the examples:

```bash
cp .env.example .env
cp backend/.env.example backend/.env
```

Important note:
- `ADMIN_DISCORD_IDS` should be a JSON array string, for example `["123456789012345678"]`
- For local testing without Discord, set `AUTH_ENABLED=false` in `backend/.env`. The
  backend will use a stable local test user; `AUTH_BYPASS_ADMIN=true` also enables
  the admin pages. Do not disable authentication in a shared or production environment.

### 3. Install dependencies

Frontend:

```bash
npm install
```

Backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### 4. Run the apps

To start MySQL, the backend, the FFmpeg worker, and the frontend together:

```bash
./scripts/run-dev.sh
```

Press `Ctrl-C` to stop the local application processes. The MySQL container is
left running so its data is preserved for the next launch.

For separate terminals, use the commands below.

Frontend:

```bash
npm run dev:frontend
```

Backend:

```bash
npm run dev:backend
```

## Verification

```bash
pytest                    # fast suite: unit + API (~2s)
pytest -m integration     # real ffmpeg renders (~50s)
pytest -m ""              # everything
npm run typecheck && npm run lint && npm run build
```

`pytest` runs from the repo root with no `PYTHONPATH` (see `pyproject.toml`), and
integration tests are deselected by default because they shell out to ffmpeg.

The suite covers the Discord auth helpers, the filename parser, brand decisions
(including reproducibility across processes with different `PYTHONHASHSEED`), the
transition arithmetic asserted against hand-worked frame numbers, every HTTP endpoint
(auth, ownership, validation, 404/409/422), deletion ordering against enforced foreign
keys, and real ffmpeg renders.

Tests run on SQLite with `PRAGMA foreign_keys=ON`, because production is MySQL and a
constraint bug that passes on an unenforced fixture is worse than no test at all.

## Testing with your own footage

Drop clips named `team-player-tier.ext` into `backend/tests/fixtures/samples/` and run:

```bash
pytest -m integration
```

That renders them and writes `test-output/samples/` for inspection:

- `render.mp4` — the finished reel
- `frames/clip-N.png` — the middle of each clip, to check the label and logo
- `frames/sting-N.png` — the middle of each transition, to check colour and direction
- `report.html` — the computed plan side by side with the measured result
- `summary.json` — the same, machine-readable

The sample tests skip cleanly when that directory is empty, so the suite stays green
until footage is added. Cover both label layouts by giving one team a logo and leaving
another without one — the harness does this automatically for the first team it finds.

## Maintenance

Hard deletion unlinks files after committing the row, and a killed render can leave
bytes behind, so storage and the database can drift. Reconcile them with:

```bash
python -m backend.app.maintenance audit        # report
python -m backend.app.maintenance audit --fix  # delete orphaned files
```

Rows pointing at missing files are reported but never repaired automatically —
removing them would discard render history.

## Still to do

- Admin endpoints have no pagination and issue per-row queries
- Schema changes go through a hand-rolled `ensure_media_columns` rather than Alembic
- Removing an ID from `ADMIN_DISCORD_IDS` does not revoke an existing admin
- Expired session rows are never reaped
- No CI workflow
- The join pass opens `2n-1` inputs at once, so movies above
  `RENDER_MAX_GRAPH_INPUTS` (24) are refused at queue time rather than rendered in
  chunks
- The clip picker is a plain dropdown of the whole library, with no search
- Uploads have no progress indicator (`fetch` cannot report it; needs XHR)
- `latestVideo.downloadUrl` is absolute to `FASTAPI_PUBLIC_URL` rather than going
  through the `/api/backend` rewrite, so inline playback will break once the frontend
  and backend are on different hosts
