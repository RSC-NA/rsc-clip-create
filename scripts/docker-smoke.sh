#!/usr/bin/env bash
# End-to-end check of the containerised stack: upload two clips through the
# frontend proxy, queue a render, wait for the worker, download the result.
#
#   ./scripts/docker-smoke.sh
#
# Assumes `docker compose up -d` has already been run.

set -Eeuo pipefail

WEB="${WEB_URL:-http://localhost:3000}"
API="${API_URL:-http://localhost:8000}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "smoke: $1" >&2; exit 1; }
step() { printf '\n== %s\n' "$1"; }

command -v curl >/dev/null || fail "curl is required."
command -v ffmpeg >/dev/null || fail "ffmpeg is required to generate the test clips."
command -v python3 >/dev/null || fail "python3 is required."

step "Waiting for the stack"
for _ in $(seq 1 60); do
  curl -fsS "$API/health" >/dev/null 2>&1 && curl -fsS -o /dev/null "$WEB/login" 2>/dev/null && break
  sleep 2
done
curl -fsS "$API/health" >/dev/null || fail "API never became healthy. Try: docker compose logs api"
echo "   api and web are up"

step "Generating two clips"
for spec in "Smoke-Alpha-Gold:440" "Smoke-Bravo-Silver:660"; do
  name="${spec%%:*}"; freq="${spec##*:}"
  ffmpeg -y -hide_banner -loglevel error \
    -f lavfi -i "testsrc2=d=2:s=640x360:r=30" \
    -f lavfi -i "sine=frequency=$freq:d=2" \
    -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$WORK/$name.mp4"
done
echo "   ok"

step "Uploading through the Next proxy"
curl -fsS -X POST "$WEB/api/backend/clips" \
  -F "files=@$WORK/Smoke-Alpha-Gold.mp4;type=video/mp4" \
  -F "files=@$WORK/Smoke-Bravo-Silver.mp4;type=video/mp4" \
  -o "$WORK/clips.json" || fail "upload failed"
read -r CLIP_A CLIP_B <<<"$(python3 -c "
import json;print(' '.join(c['id'] for c in json.load(open('$WORK/clips.json'))))")"
[ -n "$CLIP_A" ] && [ -n "$CLIP_B" ] || fail "upload returned no clips"
echo "   uploaded 2 clips"

step "Creating a movie and its timeline"
MOVIE="$(curl -fsS -X POST "$WEB/api/backend/movies" -H 'content-type: application/json' \
  -d '{"title":"Docker Smoke Test"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -fsS -X PUT "$WEB/api/backend/movies/$MOVIE/timeline" -H 'content-type: application/json' \
  -d "{\"slots\":[
        {\"id\":null,\"sort_index\":0,\"clip_id\":\"$CLIP_A\",\"overlay_text\":\"\"},
        {\"id\":null,\"sort_index\":1,\"clip_id\":\"$CLIP_B\",\"overlay_text\":\"\"}],
      \"transition_style\":\"sweep\",\"transition_hold_seconds\":0.6}" >/dev/null
echo "   movie $MOVIE"

step "Queueing the render"
curl -fsS -X POST "$WEB/api/backend/movies/$MOVIE/render" >/dev/null || fail "could not queue the render"

step "Waiting for the worker"
STATUS=""
for _ in $(seq 1 90); do
  STATUS="$(curl -fsS "$WEB/api/backend/movies/$MOVIE" \
    | python3 -c 'import sys,json;print((json.load(sys.stdin).get("latestJob") or {}).get("status",""))')"
  case "$STATUS" in
    succeeded) break ;;
    failed) fail "render failed. Try: docker compose logs worker" ;;
  esac
  sleep 2
done
[ "$STATUS" = "succeeded" ] || fail "render did not finish in time (last status: ${STATUS:-none})"
echo "   render succeeded"

step "Downloading the output"
VIDEO="$(curl -fsS "$WEB/api/backend/movies/$MOVIE" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["latestVideo"]["id"])')"
curl -fsS "$WEB/api/backend/videos/$VIDEO/file" -o "$WORK/out.mp4" || fail "download failed"

DURATION="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$WORK/out.mp4")"
SIZE="$(wc -c <"$WORK/out.mp4" | tr -d ' ')"
python3 - "$DURATION" <<'PY'
import sys
# two 2s clips plus one 0.6s hold
expected, actual = 4.6, float(sys.argv[1])
if abs(actual - expected) > 0.1:
    raise SystemExit(f"smoke: expected ~{expected}s, got {actual}s")
PY
echo "   ${SIZE} bytes, ${DURATION}s"

printf '\nSmoke test passed. The stack uploads, renders and serves correctly.\n'
