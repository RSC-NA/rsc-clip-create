# Clip Create Studio

Clip Create Studio is a combined `Next.js 16 + FastAPI` application for assembling multiple 15-second source clips into longer-form YouTube videos.

## What is in this repo

- A Next.js App Router frontend at the repo root
- A FastAPI backend in [`backend/`](/Users/monty/Projects/clip_create/backend)
- MySQL-oriented SQLAlchemy models for users, clips, movies, render jobs, render attempts, clip-level provider diagnostics, and videos
- Admin-only pages for users, videos, and render errors

## Frontend routes

- `/login`
- `/logout`
- `/movies`
- `/movies/new`
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
- `GET /api/v1/clips`
- `POST /api/v1/clips`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/videos/{video_id}/location`
- `GET /api/v1/admin/dashboard`
- `GET /api/v1/admin/users`
- `GET /api/v1/admin/videos`
- `GET /api/v1/admin/errors`

## Clip rendering

Uploads must use the strict filename format `team-player-tier.fileext`. The backend
stores the parsed metadata, requires at least two clips for a render, and uses an
FFmpeg/Pillow worker to normalize clips to 1920x1080, burn a three-line bottom-left label,
and concatenate them into a downloadable MP4.

Install FFmpeg and FFprobe on the worker host, then run the worker alongside the API:

```bash
python -m backend.app.worker
```

Limits and supported extensions can be configured with `MIN_CLIP_COUNT`,
`MAX_CLIP_COUNT`, `MAX_CLIP_SIZE_BYTES`, `MAX_CLIP_DURATION_SECONDS`, and
`SUPPORTED_CLIP_EXTENSIONS`.

## Render diagnostics

The backend now records detailed Veo submission outcomes in two dedicated tables:

- `render_job_attempts`
  Stores provider-level attempts, failure stage, error category, normalized/internal codes, raw provider response, and debug payloads.
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

The current implementation was validated with:

- `npm run typecheck`
- `npm run lint`
- `python3 -m compileall backend`

## Tests to add to your regular CI

- Backend pytest:
  - Discord auth helper safety
  - Render worker success path
  - Clip rejection and partial-failure scenarios
  - Admin API coverage for users/videos/errors
- Frontend E2E:
  - login redirect flow
  - create movie flow
  - timeline insertion/removal
  - render queue visibility
  - admin page authorization

## Security note

There is an untracked local file named `test` in the repo root that contains what appears to be a plaintext API key. I did not modify it. It should be removed from the working tree and the exposed key should be rotated.
