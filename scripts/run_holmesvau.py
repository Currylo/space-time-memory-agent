#!/usr/bin/env python3
"""Run the official Holmes-VAU inference path with local evidence capture.

This wrapper keeps the upstream model, ATS sampler and prompt flow unchanged.
It only records metadata, raw stdout, sampled frames, timings and memory.

Run from st_memory_experiments with the isolated E00-B environment:
    .venv-holmes/bin/python scripts/run_holmesvau.py \
      --video upstream/HolmesVAU/examples/robbery.mp4 \
      --prompt "Could you specify the anomaly events present in the video?" \
      --out runs/e00b-holmesvau-001/official-robbery
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = PROJECT_DIR / "upstream" / "HolmesVAU"
MODEL_DIR = PROJECT_DIR / "models" / "HolmesVAU-2B"
ATS_PATH = REPO_DIR / "holmesvau" / "ATS" / "anomaly_scorer.pth"
EXPECTED_MODEL_SHA256 = "1cfbaee788d91d90b09a7dfe0790e7b204922c388b02f6648fe06aaff71309e4"
EXPECTED_ATS_SHA256 = "8fe8182d9a7bf61c4ec4e068a5865b3be8d9f8960f1ba8197041b0a90efbced4"

sys.path.insert(0, str(REPO_DIR))

import numpy as np  # noqa: E402
import torch  # noqa: E402
from decord import VideoReader, cpu  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from holmesvau.holmesvau_utils import generate, load_model  # noqa: E402


def sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(path: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def peak_rss_mib() -> float | None:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmHWM:"):
                    return round(int(line.split()[1]) / 1024, 2)
    except OSError:
        pass
    return None


def nvidia_smi() -> dict:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        name, driver, total, used = [part.strip() for part in output.split(",")]
        return {"name": name, "driver": driver, "memory_total_mib": int(total), "memory_used_mib": int(used)}
    except Exception as exc:
        return {"error": str(exc)}


def environment() -> dict:
    import accelerate
    import decord
    import numpy
    import PIL
    import scipy
    import transformers

    result = {
        "python": sys.version,
        "executable": sys.executable,
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "transformers": transformers.__version__,
        "accelerate": accelerate.__version__,
        "decord": decord.__version__,
        "numpy": numpy.__version__,
        "pillow": PIL.__version__,
        "scipy": scipy.__version__,
    }
    try:
        import flash_attn

        result["flash_attn"] = flash_attn.__version__
    except Exception as exc:
        result["flash_attn"] = f"unavailable: {exc}"
    if torch.cuda.is_available():
        result["gpu"] = torch.cuda.get_device_name(0)
        result["capability"] = list(torch.cuda.get_device_capability(0))
    return result


def frame_contact_sheet(reader: VideoReader, indices: list[int], output: Path) -> None:
    thumbs = []
    for index in indices:
        frame = reader[index].asnumpy()
        image = Image.fromarray(frame).convert("RGB")
        image.thumbnail((360, 240))
        thumbs.append((index, image))

    if not thumbs:
        return
    cell_w = 380
    cell_h = 290
    columns = 3
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), "#111827")
    draw = ImageDraw.Draw(sheet)
    for offset, (index, image) in enumerate(thumbs):
        x = (offset % columns) * cell_w + 10
        y = (offset // columns) * cell_h + 10
        sheet.paste(image, (x, y))
        draw.text((x, y + image.height + 6), f"frame {index}", fill="#e5e7eb")
    sheet.save(output, quality=88)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run official Holmes-VAU inference with evidence capture")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--label", default="run")
    parser.add_argument("--model", type=Path, default=MODEL_DIR)
    parser.add_argument("--ats", type=Path, default=ATS_PATH)
    parser.add_argument("--select-frames", type=int, default=12)
    parser.add_argument("--dense-sample-freq", type=int, default=16)
    parser.add_argument("--use-ats", action="store_true", default=True)
    parser.add_argument("--no-ats", dest="use_ats", action="store_false")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    args = parser.parse_args()

    video = args.video if args.video.is_absolute() else PROJECT_DIR / args.video
    model_dir = args.model if args.model.is_absolute() else PROJECT_DIR / args.model
    ats_path = args.ats if args.ats.is_absolute() else PROJECT_DIR / args.ats
    out_dir = args.out if args.out.is_absolute() else PROJECT_DIR / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "sampled_frames"
    frames_dir.mkdir(exist_ok=True)

    started_wall = datetime.now(timezone.utc)
    started = time.monotonic()
    metadata: dict = {
        "label": args.label,
        "status": "started",
        "started_at_utc": started_wall.isoformat(timespec="seconds"),
        "command": sys.argv,
        "cwd": str(Path.cwd()),
        "video": str(video),
        "prompt": args.prompt,
        "select_frames": args.select_frames,
        "dense_sample_freq": args.dense_sample_freq,
        "use_ats": bool(args.use_ats),
        "device": args.device,
        "seed": args.seed,
        "max_new_tokens": args.max_new_tokens,
        "model_dir": str(model_dir),
        "ats_path": str(ats_path),
        "repo_commit": git_commit(REPO_DIR),
        "environment": environment(),
        "nvidia_smi_before": nvidia_smi(),
    }

    try:
        if not video.is_file():
            raise FileNotFoundError(f"video not found: {video}")
        if not model_dir.is_dir():
            raise FileNotFoundError(f"model dir not found: {model_dir}")
        if not ats_path.is_file():
            raise FileNotFoundError(f"ATS checkpoint not found: {ats_path}")

        torch.manual_seed(args.seed)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
        device = torch.device(args.device)

        reader = VideoReader(str(video), ctx=cpu(0), num_threads=1)
        metadata["video_info"] = {
            "frames": len(reader),
            "fps": float(reader.get_avg_fps()),
            "duration_seconds": round(len(reader) / float(reader.get_avg_fps()), 3),
            "height": int(reader[0].shape[0]),
            "width": int(reader[0].shape[1]),
            "sha256": sha256_file(video),
        }
        model_sha = sha256_file(model_dir / "model.safetensors")
        ats_sha = sha256_file(ats_path)
        metadata["model_files"] = {
            "model.safetensors": {
                "path": str(model_dir / "model.safetensors"),
                "bytes": (model_dir / "model.safetensors").stat().st_size,
                "sha256": model_sha,
                "expected_sha256": EXPECTED_MODEL_SHA256,
            },
            "anomaly_scorer.pth": {
                "path": str(ats_path),
                "bytes": ats_path.stat().st_size,
                "sha256": ats_sha,
                "expected_sha256": EXPECTED_ATS_SHA256,
            },
        }
        if model_sha != EXPECTED_MODEL_SHA256:
            raise RuntimeError(f"model SHA256 mismatch: {model_sha}")
        if ats_sha != EXPECTED_ATS_SHA256:
            raise RuntimeError(f"ATS SHA256 mismatch: {ats_sha}")

        print(f"[E00-B] load model from {model_dir}", flush=True)
        load_started = time.monotonic()
        model, tokenizer, generation_config, sampler = load_model(
            str(model_dir), str(ats_path), device
        )
        metadata["load_seconds"] = round(time.monotonic() - load_started, 3)
        metadata["chat_template"] = getattr(model.conv_template, "name", None)
        metadata["system_message"] = getattr(model, "system_message", None)
        generation_config["max_new_tokens"] = args.max_new_tokens
        print(f"[E00-B] model loaded in {metadata['load_seconds']} s", flush=True)

        infer_started = time.monotonic()
        pred, history, frame_indices, anomaly_score = generate(
            str(video),
            args.prompt,
            model,
            tokenizer,
            generation_config,
            sampler,
            dense_sample_freq=args.dense_sample_freq,
            select_frames=args.select_frames,
            use_ATS=args.use_ats,
        )
        infer_seconds = round(time.monotonic() - infer_started, 3)
        metadata["inference_seconds"] = infer_seconds

        (out_dir / "prompt.txt").write_text(args.prompt + "\n", encoding="utf-8")
        (out_dir / "response.txt").write_text(str(pred) + "\n", encoding="utf-8")
        (out_dir / "history.json").write_text(
            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        sampled = [int(index) for index in frame_indices]
        dense_indices = list(range(len(reader)))[:: args.dense_sample_freq]
        anomaly_payload = {
            "dense_frame_indices": dense_indices,
            "dense_frame_times_seconds": [round(i / float(reader.get_avg_fps()), 3) for i in dense_indices],
            "anomaly_scores": None
            if anomaly_score is None
            else [float(value) for value in np.asarray(anomaly_score).reshape(-1)],
            "selected_frame_indices": sampled,
            "selected_frame_times_seconds": [round(i / float(reader.get_avg_fps()), 3) for i in sampled],
        }
        (out_dir / "anomaly_scores.json").write_text(
            json.dumps(anomaly_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        for index in sampled:
            frame = reader[index].asnumpy()
            Image.fromarray(frame).convert("RGB").save(
                frames_dir / f"frame_{index:06d}.jpg", quality=92
            )
        frame_contact_sheet(reader, sampled, out_dir / "sampled_frames_contact_sheet.jpg")

        if torch.cuda.is_available():
            metadata["peak_vram_allocated_mib"] = round(torch.cuda.max_memory_allocated() / 1024**2, 2)
            metadata["peak_vram_reserved_mib"] = round(torch.cuda.max_memory_reserved() / 1024**2, 2)
        metadata["peak_rss_mib"] = peak_rss_mib()
        metadata["nvidia_smi_after"] = nvidia_smi()
        metadata["frame_indices"] = sampled
        metadata["anomaly_score_count"] = None if anomaly_score is None else int(len(np.asarray(anomaly_score).reshape(-1)))
        metadata["response"] = str(pred)
        metadata["response_chars"] = len(str(pred))
        metadata["status"] = "success"
        metadata["finished_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        metadata["total_seconds"] = round(time.monotonic() - started, 3)
        (out_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n[E00-B] raw response:\n" + str(pred), flush=True)
        print(f"[E00-B] wrote {out_dir}", flush=True)
        return 0
    except Exception as exc:
        metadata["status"] = "failed"
        metadata["error_type"] = type(exc).__name__
        metadata["error"] = str(exc)
        metadata["traceback"] = traceback.format_exc()
        metadata["finished_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        metadata["total_seconds"] = round(time.monotonic() - started, 3)
        (out_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[E00-B] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
