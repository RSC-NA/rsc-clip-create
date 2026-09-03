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

## Team logos

Upload a logo at `/logos` and tag it with a team name. The renderer matches it against
the `team` token already parsed from each clip's filename, case-insensitively, so every
clip for that team carries the mark with no per-clip work. A team with no logo, an
unreadable image, or a missing file on disk falls back to the text-only label and never
fails a render.

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

## Local development

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
PYTHONPATH=. .venv/bin/python -m pytest backend/tests -q
npm run typecheck
npm run lint
```

The pytest suite covers the Discord auth helpers, the filename parser, the render worker's
success/failure/rejection paths, the job lifecycle, the transition arithmetic (offsets are
asserted against hand-worked frame numbers), and the brand decisions including
reproducibility across processes with different `PYTHONHASHSEED`.

## Still to do

- Admin endpoints have no pagination and issue per-row queries
- Schema changes go through a hand-rolled `ensure_media_columns` rather than Alembic
- No DELETE endpoints for movies, clips, or videos (logos excepted)
- Removing an ID from `ADMIN_DISCORD_IDS` does not revoke an existing admin
- Expired session rows are never reaped
- Raw backend error bodies are rendered into the UI
- No CI workflow and no `npm test` script
- The join pass opens `2n-1` inputs at once, so movies above
  `RENDER_MAX_GRAPH_INPUTS` (24) are rejected rather than rendered in chunks
