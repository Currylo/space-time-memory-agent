#!/usr/bin/env bash
# A1 host-side deployment helper for Frigate 0.18.2.
# Run this from a normal terminal (it will ask for your sudo password locally).
# It does NOT run the preparation script and does NOT overwrite runtime config/input.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE="$ROOT/deploy/frigate/compose.yaml"
IMAGE="ghcr.io/blakeblackshear/frigate:0.18.2"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/reports/a1/frigate/host-$STAMP"
mkdir -p "$OUT"

echo "project: $ROOT"
echo "output:  $OUT"
echo "image:   $IMAGE"

sudo docker version >"$OUT/docker-version.txt" 2>&1
sudo docker compose version >"$OUT/compose-version.txt" 2>&1
sudo docker compose -f "$COMPOSE" config --quiet
sudo docker compose -f "$COMPOSE" config >"$OUT/compose.resolved.yaml"

echo "[1/5] pulling fixed image..."
sudo docker compose -f "$COMPOSE" pull 2>&1 | tee "$OUT/pull.log"
sudo docker image inspect "$IMAGE" --format '{{json .RepoDigests}}' >"$OUT/repo_digests.json"
sudo docker image inspect "$IMAGE" --format '{{.Id}}' >"$OUT/image_id.txt"

echo "[2/5] starting container..."
sudo docker compose -f "$COMPOSE" up -d 2>&1 | tee "$OUT/up.log"
sudo docker compose -f "$COMPOSE" ps >"$OUT/ps.txt" 2>&1

echo "[3/5] waiting for local API on 127.0.0.1:15000 ..."
api_ok=0
for i in $(seq 1 90); do
  if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:15000/api/version >"$OUT/version.txt" 2>"$OUT/version.err"; then
    api_ok=1
    break
  fi
  sleep 2
done

sudo docker compose -f "$COMPOSE" logs --tail 400 frigate >"$OUT/frigate.log" 2>&1 || true
grep -viE 'password|secret|token|credential|api_key' "$OUT/frigate.log" >"$OUT/frigate.sanitized.log" || true

if [ "$api_ok" -ne 1 ]; then
  echo "API did not become ready. See $OUT/frigate.sanitized.log" >&2
  tail -80 "$OUT/frigate.sanitized.log" >&2 || true
  exit 1
fi

echo "[4/5] collecting API evidence..."
cd "$ROOT"
python3 scripts/a1_collect_frigate.py --base-url http://127.0.0.1:15000 --output "$OUT/api" 2>&1 | tee "$OUT/collector.log"

echo "[5/5] saving post-start state..."
sudo docker compose -f "$COMPOSE" ps >"$OUT/ps-after.txt" 2>&1
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:15000/api/stats >"$OUT/stats.json" 2>"$OUT/stats.err" || true
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:15000/api/config >"$OUT/config.json" 2>"$OUT/config.err" || true

echo
echo "DONE. Container is left running."
echo "Evidence directory: $OUT"
echo "Open: http://127.0.0.1:15000/"
echo "To stop: sudo docker compose -f \"$COMPOSE\" stop"
