#!/usr/bin/env bash
# E00-B reproducible run wrapper: official sample first, then public video.
set -euo pipefail

cd "$(dirname "$0")/.."

PTX="$(realpath .venv-holmes/lib/python3.9/site-packages/triton/third_party/cuda/bin/ptxas)"
PTXAS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/st-memory-ptxas.XXXXXX")"
trap 'rm -f "$PTXAS_DIR/ptxas"; rmdir "$PTXAS_DIR"' EXIT
ln -s "$PTX" "$PTXAS_DIR/ptxas"
export TRITON_PTXAS_PATH="$PTXAS_DIR/ptxas"
export TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1

RUN_ROOT="runs/e00b-holmesvau-001"
mkdir -p "$RUN_ROOT"

echo "=== E00-B official sample ==="
.venv-holmes/bin/python scripts/run_holmesvau.py \
  --video upstream/HolmesVAU/examples/robbery.mp4 \
  --prompt "Could you specify the anomaly events present in the video?" \
  --out "$RUN_ROOT/official-robbery-001" \
  --label official-robbery-001 \
  --select-frames 12 \
  --use-ats \
  2>&1 | tee "$RUN_ROOT/official-robbery-001.log"

echo "=== E00-B public people-walking ==="
.venv-holmes/bin/python scripts/run_holmesvau.py \
  --video data/raw/people-walking.mp4 \
  --prompt "Describe the main observable events in this video. If no anomaly is visible, say so explicitly." \
  --out "$RUN_ROOT/public-people-walking-001" \
  --label public-people-walking-001 \
  --select-frames 12 \
  --use-ats \
  2>&1 | tee "$RUN_ROOT/public-people-walking-001.log"

echo "=== E00-B runs finished ==="
