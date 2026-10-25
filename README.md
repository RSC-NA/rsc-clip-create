# Clip Create Studio

Clip Create Studio is a combined `Next.js 16 + FastAPI` application for assembling multiple short source clips into longer-form YouTube videos.

## What is in this repo

- A Next.js App Router frontend at the repo root
- A FastAPI backend in [`backend/`](backend/)
- MySQL-oriented SQLAlchemy models for users, clips, movies, brand sweeps, render jobs,
  render attempts, clip-level diagnostics, and videos
- Admin-only pages for users, videos, and render errors

## Frontend routes

- `/login`
- `/logout`
- `/movies`
- `/movies/new`
- `/sweeps`
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
- `DELETE /api/v1/movies/{movie_id}`
- `GET /api/v1/config`
- `GET /api/v1/clips`
- `POST /api/v1/clips`
- `DELETE /api/v1/clips/{clip_id}`
- `GET /api/v1/sweeps`
- `POST /api/v1/sweeps`
- `GET /api/v1/sweeps/{sweep_id}/file`
- `DELETE /api/v1/sweeps/{sweep_id}`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/videos/{video_id}/location`
- `GET /api/v1/videos/{video_id}/file`
- `DELETE /api/v1/videos/{video_id}`
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
   48kHz track, and its one-line `team · player · tier` label is burned into the
   bottom left.
2. **Join.** The normalized clips are concatenated with hard cuts, and the brand sweep
   is overlaid across each cut so the join happens while the sweep hides the picture.

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

## Brand sweep

Every cut between clips is covered by the **brand sweep** — a stinger with an alpha
channel that sweeps in, hides the picture completely, and breaks away again. The clips
cut underneath it, at the moment it is fully opaque.

The sweep is composited *on top of* the joined timeline rather than spliced into it. That
is the whole design, and it has two consequences worth knowing:

```
clip A ─────────────┐
                    │ hard cut
clip B              └─────────────
          [ sweep overlaid ]
           ░▒▓█████▓▒░
               ▲ the cut, hidden under full coverage
```

- **A transition costs no time.** A movie is exactly as long as its clips:
  `duration = sum(clip durations)`.
- **The sweep must have alpha.** Spliced in as its own segment, its transparent head and
  tail would flatten onto black and dip the picture at every join.

The file bundled at `backend/app/assets/stings/brand-sweep.mov` is used by default, so a
fresh checkout renders the real transition with no setup. It is 1920x1080 QuickTime RLE,
44 frames at 30fps, fully opaque from frame 10 to 32 — so the cut lands on frame 21.

### Changing the sweep

Three places, in the order the renderer consults them:

1. Upload one at `/sweeps` — one per account; re-uploading replaces it.
2. `RENDER_BRAND_SWEEP_PATH`, for a deployment-wide override.
3. The bundled file.

A replacement needs an alpha channel and should reach full opacity somewhere in the
middle; that opaque span is what hides the cut. **Nothing needs recomputing when you swap
it.** Each render measures the sweep's alpha, one mean value per frame, and puts the cut
in the middle of the covered span. A file that never fully covers falls back to its own
midpoint with a warning rather than failing.

Uploads are checked with ffprobe on the way in — the extension is not taken as evidence —
and capped at `MAX_BRAND_SWEEP_BYTES` (200MB), enforced during the write. Any audio on
the sweep is ignored; the movie's audio is the clips, hard-cut.

If an account's sweep row points at a deleted uploaded file, that upload is treated as
unavailable: the configured `RENDER_BRAND_SWEEP_PATH` is selected if set, otherwise the
bundled `brand-sweep.mov` is used. This is not itself a reason for a plain cut. If the
selected sweep is missing or cannot be loaded, cuts render plain instead of failing the
render. A transition is decoration; it never fails a render.

Replacing the brand logo with this dropped three things: the per-movie transition style
and hold controls, the generated blue-and-orange wedge card, and the brand logo that sat
in the middle of it. The `logos` table is dropped at startup; its uploaded files are left
on disk, so run `python -m backend.app.maintenance audit --fix` to clear them.

### Clips shorter than the sweep

A cut only carries the selected sweep when **both** adjacent clips are at least as long
as that sweep (the bundled one is ~1.5s). Otherwise the sweep would run past the clip
on the far side, or collide with the sweep on the other end of it. Such cuts render bare,
with a warning, rather than failing.

That test is on the two adjacent clips alone, never on where the cut lands in the finished
movie — which is what lets the decision survive group rendering unchanged.

### Why rendering happens in groups

Transitions used to be chained `xfade` filters, which buffer heavily: measured at 1080p,
peak memory was 1.69GB for two clips and 4.61GB for seven — at which point the OOM killer
took the worker, and the only thing recorded was 2000 characters of ffmpeg progress
output, because `frame=` lines had pushed the real error out of the captured tail.

Overlaying the sweep is far cheaper, but clips are still joined in groups of
`RENDER_CHUNK_SIZE` (default 4) and the group outputs joined again, recursively, because
that also bounds the number of decoders open at once.

A sweep straddling a group seam is placed at the outer level, against the group outputs.
Deciding a boundary and placing it are separate steps for exactly this reason: a boundary
is placed twice but decided once, so how the work is divided never changes what the
output looks like.

If a render still dies, the error says so plainly — `FFmpeg was killed by SIGKILL` —
rather than dumping progress output. Lower `RENDER_CHUNK_SIZE` or give the container more
memory.

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
```

Before running the smoke check, install `ffmpeg` and `ffprobe` on the host running
`./scripts/docker-smoke.sh`. The FFmpeg tools installed inside the containers do not
provide these commands on the host.

```bash
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
| `FASTAPI_PUBLIC_URL` | `http://localhost:8000` | Browser-facing; the API builds absolute download and brand sweep URLs from it, so it must be reachable from the host, not from inside the compose network. |
| `SECURE_COOKIES` | `false` | Set to `true` behind HTTPS. |
| `MAX_UPLOAD_BYTES` | `536870912` (512 MB) | **Total bytes per upload request**, across every file in it. See below. |
| `RENDER_BRAND_SWEEP_PATH` | the bundled sweep | Deployment-wide override for the stinger played over every cut. An account's own upload still wins. **Not forwarded by compose** — set it on the `worker` service in `docker-compose.yml` (see below). |
| `MAX_BRAND_SWEEP_BYTES` | `209715200` (200 MB) | Cap on a brand sweep upload. The bundled file is 90MB; alpha-capable codecs are not small. **Not forwarded by compose** — set it on the `api` service in `docker-compose.yml` (see below). |

**`RENDER_BRAND_SWEEP_PATH` and `MAX_BRAND_SWEEP_BYTES` are set in `docker-compose.yml`,
not in `.env`.** The compose file does not pass either one through to the containers, so
putting them in `./.env` or the file given to `--env-file` (e.g. `.env.docker`) has no
effect: the worker keeps using the bundled sweep and the API keeps the 200MB cap. Add
them to the `environment:` block of the right service, the same way as `RENDER_FONT_PATH`
above — `RENDER_BRAND_SWEEP_PATH` on the `worker`, because that is where renders run, and
`MAX_BRAND_SWEEP_BYTES` on the `api`, because the API enforces the upload cap:

```yaml
# docker-compose.yml
services:
  api:
    environment:
      MAX_BRAND_SWEEP_BYTES: 314572800
  worker:
    environment:
      RENDER_BRAND_SWEEP_PATH: /app/storage/stings/my-sweep.mov
```

Add these lines to the existing `environment:` entries of each service rather than
replacing them. The sweep file must be readable inside the worker container, for example
under the shared `/app/storage` volume.

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
link's frame-rate metadata, and ffmpeg 7's `concat` and `overlay` reject an input whose
rate reads as `1/0` where 8 tolerates it. Every branch feeding them therefore re-asserts
`fps` *after* `setpts`. If you change the filter graph, run the integration suite against the
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

The suite covers the Discord auth helpers, the filename parser, the label and the
choice of where in the sweep the cut lands, the sweep placement asserted against
hand-worked frame numbers, every HTTP endpoint
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
- `frames/clip-N.png` — the middle of each clip, to check the label
- `frames/cut-N.png` — the exact frame of each cut, where the sweep should be covering
  the picture completely; if the clips are visible in one, the sweep drifted off its cut
- `report.html` — the computed plan side by side with the measured result
- `summary.json` — the same, machine-readable

The sample tests skip cleanly when that directory is empty, so the suite stays green
until footage is added. Include at least one clip shorter than the sweep (under ~1.5s)
to exercise the bare-cut path alongside the swept one.

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
