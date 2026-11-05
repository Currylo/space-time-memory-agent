"""Explicitly snapshot an accepted environment; never called by the runner."""
import importlib.metadata as metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from st_memory.records import sha256

packages = ("torch", "torchvision", "ultralytics", "supervision", "numpy", "opencv-python",
            "opencv-python-headless", "scipy", "matplotlib", "pandas", "pillow", "requests",
            "psutil", "pyyaml", "tqdm", "defusedxml", "ultralytics-thop", "contourpy",
            "setuptools", "py-cpuinfo", "seaborn")
versions = {name: metadata.version(name) for name in packages}
data = {
    "schema_version": 1, "adapter_version": "0.1.0", "captured_date": "2026-11-03",
    "runtime_versions": versions,
    "runtime_notes": ["CUDA execution requires host device access; default sandbox has no GPU nodes",
        "Existing tep-gpu conda base read-only; .venv-gpu also reads local .venv dependencies via .pth",
        "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1 for old upstream loader and hash-locked official checkpoint",
        "Environment snapshot; clean-room rebuild not yet verified"],
    "components": [
        {"component": "detector", "repo_url": "https://github.com/ultralytics/ultralytics",
         "release_or_commit": versions["ultralytics"], "entrypoint": "ultralytics.YOLO.predict",
         "license_ref": "https://github.com/ultralytics/ultralytics/blob/v8.3.40/LICENSE",
         "license": "AGPL-3.0; official weights follow upstream terms; commercial adoption pending review",
         "adoption_scope": "local E00 reproduction", "adapter_version": "0.1.0"},
        {"component": "tracker_and_visualization", "repo_url": "https://github.com/roboflow/supervision",
         "release_or_commit": versions["supervision"], "entrypoint": "supervision.ByteTrack.update_with_detections",
         "license_ref": "https://pypi.org/project/supervision/0.25.1/", "license": "MIT (installed package metadata)",
         "implementation": "Supervision integrated ByteTrack, not FoundationVision original checkout",
         "adoption_scope": "single-camera E00 reproduction", "adapter_version": "0.1.0"},
        {"component": "video_understanding", "repo_url": "https://github.com/pipixin321/HolmesVAU",
         "release_or_commit": None, "status": "deferred_not_reproduced",
         "reason": "GPU available; dedicated upstream environment and model/ATS checkpoints not prepared",
         "weights_ref": "https://huggingface.co/ppxin321/HolmesVAU-2B", "weights_hash": None,
         "license_ref": "https://github.com/pipixin321/HolmesVAU/blob/master/LICENSE"},
        {"component": "platform", "repo_url": "https://github.com/blakeblackshear/frigate",
         "release_or_commit": "v0.18.2", "status": "source_review_only_not_deployed",
         "license_ref": "https://github.com/blakeblackshear/frigate/blob/v0.18.2/LICENSE"}
    ],
    "assets": {
        "demo_video": {"path": "data/raw/people-walking.mp4",
            "url": "https://media.roboflow.com/supervision/video-examples/people-walking.mp4",
            "source_kind": "public_demo", "split": "development",
            "license_status": "redistribution license unverified; local reproduction only"},
        "detector_weights": {"path": "models/yolov8n.pt",
            "url": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt",
            "license_ref": "https://docs.ultralytics.com/help/FAQ/#ultralytics-yolo-licensing"}
    }
}
for item in data["assets"].values():
    item["sha256"] = sha256(ROOT / item["path"])
    item["size_bytes"] = (ROOT / item["path"]).stat().st_size
    item["checksum_origin"] = "measured_after_download_not_publisher_signed_digest"
path = ROOT / "components.lock.json"
if path.exists():
    raise FileExistsError("Lock already exists; review and version changes explicitly")
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
(ROOT / "requirements.runtime.lock.txt").write_text("".join(f"{n}=={v}\n" for n, v in versions.items()))
print(path)
