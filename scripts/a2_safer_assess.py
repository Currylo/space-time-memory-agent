#!/usr/bin/env python3
"""P0: assess SAFER-Activities access and storage without downloading it."""

from __future__ import annotations

import json
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATASET = "SAFER-Activities/SAFER-Activities"
DEFAULT_OUTPUT = PROJECT_DIR / "data/a2/gmd_csa24/safer_access_assessment.json"


def get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "st-memory-a2"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main() -> int:
    info = get_json(f"https://huggingface.co/api/datasets/{DATASET}")
    tree = get_json(f"https://huggingface.co/api/datasets/{DATASET}/tree/main?recursive=true")
    files = [item for item in tree if item.get("type") == "file"]
    total_bytes = sum(int(item.get("size", 0)) for item in files)
    by_top = Counter()
    for item in files:
        by_top[item["path"].split("/")[0]] += int(item.get("size", 0))
    result = {
        "schema": "a2-safer-access-assessment-v1",
        "assessed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": DATASET,
        "url": "https://huggingface.co/datasets/SAFER-Activities/SAFER-Activities",
        "project_page": "https://safer-activities.github.io",
        "paper": "https://arxiv.org/abs/2609.08038",
        "gated": info.get("gated"),
        "private": info.get("private"),
        "license_tags": [tag for tag in info.get("tags", []) if str(tag).startswith("license:")],
        "downloads": info.get("downloads"),
        "last_modified": info.get("lastModified"),
        "files": len(files),
        "total_bytes": total_bytes,
        "total_gib": round(total_bytes / 1024**3, 2),
        "by_top_level_gib": {key: round(value / 1024**3, 2) for key, value in by_top.most_common()},
        "paper_claims": {
            "hours": ">66",
            "participants": 46,
            "action_instances": 85310,
            "classes": 30,
            "fall_instances": 5406,
            "wheelchair_subset": True,
            "frame_level_annotations": True,
            "subject_and_view_splits": True,
            "ood_test_set": True,
        },
        "access_verdict": {
            "anonymous_download": False,
            "reason": "Hugging Face gated=manual; a user account must accept the dataset terms before download.",
            "local_storage_required_gib": round(total_bytes / 1024**3, 2),
            "recommended_first_step": "Use GMDCSA-24 for the small event-level pilot; request SAFER access only after the pilot and storage plan.",
        },
        "limitations": [
            "Metadata was read from the public Hugging Face API; no gated file was downloaded.",
            "Measured repository bytes may differ from the paper's rounded 173 GB statement.",
        ],
    }
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("dataset", "gated", "license_tags", "files", "total_gib", "by_top_level_gib", "access_verdict")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
