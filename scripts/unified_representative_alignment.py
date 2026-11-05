#!/usr/bin/env python3
"""Dense per-second alignment for representative first/middle/last clips."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from st_memory.unified_platform import SourceIndex  # noqa: E402

SOURCE = ROOT / "data/a2/unified/MOT20-05-raw.mp4"
ROUND = ROOT / "reports/unified-platform-20261104T1507Z"


def main() -> int:
    import cv2

    records = [json.loads(line) for line in (ROUND / "candidate_event_mapping.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    index = SourceIndex(SOURCE, fps=25.0)
    # first, middle, last by condition time
    ordered = sorted(records, key=lambda r: r["condition_met_video_time"])
    chosen = [ordered[0], ordered[len(ordered) // 2], ordered[-1]]
    results = []
    for record in chosen:
        clip_path = ROUND / "clips" / f"{record['candidate_id']}.mp4"
        cap = cv2.VideoCapture(str(clip_path))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        samples = []
        # every 1 s plus the final frame
        positions = [int(round(second * fps)) for second in range(0, max(1, int(count / fps)))]
        positions = sorted(set(min(count - 1, p) for p in positions + [count - 1]))
        for position in positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, position)
            ok, frame = cap.read()
            if not ok:
                continue
            match = index.match(frame)
            actual = match.get("watermark_source_time", match.get("source_time"))
            samples.append({
                "clip_time": position / fps,
                "watermark": (match.get("watermark") or {}).get("frame_number"),
                "actual_source_time": actual,
                "image_frame_index": match.get("frame_index"),
                "methods_agree": match.get("methods_agree"),
            })
        cap.release()
        if not samples:
            results.append({"candidate_id": record["candidate_id"], "decoded": False})
            continue
        base = samples[0]["actual_source_time"]
        residuals = []
        for sample in samples:
            expected = base + sample["clip_time"]
            sample["expected_source_time"] = expected
            sample["residual_seconds"] = (
                None if sample["actual_source_time"] is None else sample["actual_source_time"] - expected
            )
            if sample["residual_seconds"] is not None:
                residuals.append(sample["residual_seconds"])
        # linear fit actual = a + b*clip_time; report slope/drift
        xs = [s["clip_time"] for s in samples if s["actual_source_time"] is not None]
        ys = [s["actual_source_time"] for s in samples if s["actual_source_time"] is not None]
        if len(xs) >= 2:
            mx = sum(xs) / len(xs)
            my = sum(ys) / len(ys)
            denom = sum((x - mx) ** 2 for x in xs)
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else 1.0
        else:
            slope = 1.0
        results.append({
            "candidate_id": record["candidate_id"],
            "kind": record["kind"],
            "event_id": record["event_id"],
            "condition_met_video_time": record["condition_met_video_time"],
            "sample_count": len(samples),
            "sample_interval_seconds": 1.0,
            "clip_duration_seconds": count / fps,
            "slope_source_per_clip_second": slope,
            "drift_ppm": (slope - 1.0) * 1_000_000.0,
            "max_abs_residual_seconds": max(abs(v) for v in residuals) if residuals else None,
            "mean_residual_seconds": sum(residuals) / len(residuals) if residuals else None,
            "methods_agree_all": all(s.get("methods_agree") is not False for s in samples),
            "samples": samples,
        })
    output = {
        "schema": "unified-platform-representative-alignment-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": "first / middle / last candidate by condition time",
        "method": "burned-in frame number OCR on one frame per second of the downloaded platform clip; image-hash match as independent check",
        "results": results,
    }
    path = ROUND / "alignment" / "representative_alignment.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Attach a short summary to the evidence manifest without rewriting clips.
    manifest_path = ROUND / "evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["representative_alignment"] = {
        "path": str(path.relative_to(ROOT)),
        "selection": output["selection"],
        "summary": [
            {
                "candidate_id": item["candidate_id"],
                "kind": item["kind"],
                "sample_count": item.get("sample_count"),
                "drift_ppm": item.get("drift_ppm"),
                "max_abs_residual_seconds": item.get("max_abs_residual_seconds"),
                "methods_agree_all": item.get("methods_agree_all"),
            }
            for item in results
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["representative_alignment"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
