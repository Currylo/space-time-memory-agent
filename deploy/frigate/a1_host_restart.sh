#!/usr/bin/env bash
# A1 host-side restart + evidence collection after a config change.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE="$ROOT/deploy/frigate/compose.yaml"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/reports/a1/frigate/restart-$STAMP"
mkdir -p "$OUT"

# Authenticate only through the local terminal; never request a password via chat.
# Reuse direct Docker access when the caller already has it.
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  if [[ -t 0 ]]; then
    sudo -v
  elif ! sudo -n true 2>/dev/null; then
    echo "Docker access requires sudo authentication in a local terminal. Restart was NOT performed." >&2
    echo "Run this script from your local terminal; do not send a password to the model." >&2
    exit 3
  fi
  DOCKER=(sudo -n docker)
fi

echo "restarting Frigate..."
"${DOCKER[@]}" compose -f "$COMPOSE" restart frigate 2>&1 | tee "$OUT/restart.log"
"${DOCKER[@]}" compose -f "$COMPOSE" ps >"$OUT/ps.txt" 2>&1

echo "waiting for API..."
READY=false
for i in $(seq 1 90); do
  if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:15000/api/version >"$OUT/version.txt" 2>"$OUT/version.err"; then
    READY=true
    break
  fi
  sleep 2
done
if [[ "$READY" != true ]]; then
  echo "API did not become ready; no startup acceptance is recorded." >&2
  exit 4
fi

# Give motion calibration and the detector a chance to run before collecting.
echo "warm-up 45s..."
sleep 45
"${DOCKER[@]}" compose -f "$COMPOSE" logs --tail 300 frigate >"$OUT/frigate.log" 2>&1 || true
grep -viE 'password|secret|token|credential|api_key' "$OUT/frigate.log" >"$OUT/frigate.sanitized.log" || true
cd "$ROOT"
python3 scripts/a1_collect_frigate.py --base-url http://127.0.0.1:15000 --output "$OUT/api" 2>&1 | tee "$OUT/collector.log"
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:15000/api/stats >"$OUT/stats.json" 2>"$OUT/stats.err" || true
curl --noproxy '*' -fsS --max-time 5 'http://127.0.0.1:15000/api/review/activity/motion?cameras=a1_public_demo' >"$OUT/motion_activity.json" 2>"$OUT/motion_activity.err" || true
python3 scripts/a1_diagnose_vod.py --output "$OUT/vod"
echo "Evidence collected: $OUT. Inspect active input, HLS init/media and browser playback before acceptance."
