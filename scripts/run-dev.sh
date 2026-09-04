#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKEND_PID=""
WORKER_PID=""
FRONTEND_PID=""
MYSQL_STARTED="false"

fail() {
  echo "run-dev: $1" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker is required."
command -v npm >/dev/null 2>&1 || fail "Node.js/npm is required."
command -v lsof >/dev/null 2>&1 || fail "lsof is required to check development ports."
command -v ffmpeg >/dev/null 2>&1 || fail "FFmpeg is required. Install it with: brew install ffmpeg"
command -v ffprobe >/dev/null 2>&1 || fail "FFprobe is required and is included with FFmpeg."

[[ -x "$ROOT_DIR/.venv/bin/python" ]] || fail "Create the backend virtualenv first: python3 -m venv .venv"
[[ -f "$ROOT_DIR/.env" ]] || fail "Copy .env.example to .env first."
[[ -f "$ROOT_DIR/backend/.env" ]] || fail "Copy backend/.env.example to backend/.env first."

if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Port 8000 is already in use. Stop the existing backend/container first."
fi
if lsof -nP -iTCP:3000 -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Port 3000 is already in use. Stop the existing frontend/container first."
fi

cleanup() {
  trap - EXIT INT TERM
  echo
  echo "Stopping Clip Create services..."
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  [[ -n "$WORKER_PID" ]] && kill "$WORKER_PID" 2>/dev/null || true
  [[ -n "$BACKEND_PID" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  if [[ "$MYSQL_STARTED" == "true" ]]; then
    echo "Local services stopped. MySQL remains running in Docker."
  else
    echo "Local services stopped. MySQL was not confirmed running."
  fi
}

trap cleanup EXIT INT TERM

echo "Starting MySQL..."
docker compose up -d mysql
MYSQL_STARTED="true"

# `up -d` returns when the container starts, not when MySQL accepts
# connections. On a first run the data directory takes 10-40s to initialise,
# and the backend's create_all would be refused, killing uvicorn and taking
# the whole script down with it.
echo "Waiting for MySQL to accept connections..."
MYSQL_READY="false"
for _ in $(seq 1 60); do
  if docker compose exec -T mysql mysqladmin ping -h 127.0.0.1 -uroot -proot >/dev/null 2>&1; then
    MYSQL_READY="true"
    break
  fi
  sleep 2
done
[[ "$MYSQL_READY" == "true" ]] || fail "MySQL did not become ready in time. Check: docker compose logs mysql"
echo "MySQL is ready."

echo "Starting FastAPI backend on http://127.0.0.1:8000..."
PYTHONPATH="$ROOT_DIR" "$ROOT_DIR/.venv/bin/python" -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "Starting FFmpeg render worker..."
PYTHONPATH="$ROOT_DIR" "$ROOT_DIR/.venv/bin/python" -m backend.app.worker &
WORKER_PID=$!

echo "Starting Next.js frontend on http://127.0.0.1:3000..."
npm run dev:frontend &
FRONTEND_PID=$!

echo
echo "Clip Create is running at http://127.0.0.1:3000"
echo "Press Ctrl-C to stop the backend, worker, and frontend."

while true; do
  for PID in "$BACKEND_PID" "$WORKER_PID" "$FRONTEND_PID"; do
    if ! kill -0 "$PID" 2>/dev/null; then
      set +e
      wait "$PID"
      STATUS=$?
      set -e
      echo "A Clip Create process exited unexpectedly with status $STATUS." >&2
      exit "$STATUS"
    fi
  done
  sleep 1
done
