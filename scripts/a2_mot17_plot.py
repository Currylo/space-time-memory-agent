#!/usr/bin/env python3
"""A2: plot per-scene MOT17 metrics for the ByteTrack baseline and BoT-SORT reference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BASE = PROJECT_DIR / "reports/a2/mot17/metrics.json"
DEFAULT_BOTSORT = PROJECT_DIR / "reports/a2/mot17/botsort/metrics.json"
DEFAULT_OUTPUT = PROJECT_DIR / "reports/a2/mot17/figures/scene_metrics.png"


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot A2 MOT17 per-scene metrics")
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--botsort", type=Path, default=DEFAULT_BOTSORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    base = json.loads(args.base.read_text(encoding="utf-8"))
    bot = json.loads(args.botsort.read_text(encoding="utf-8"))
    sequences = sorted(base["per_sequence"])
    short = [name.replace("MOT17-", "").replace("-FRCNN", "") for name in sequences]
    metrics = ["HOTA", "MOTA", "IDF1"]
    x = np.arange(len(sequences))
    width = 0.36

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2), sharey=True)
    for ax, metric in zip(axes, metrics):
        base_values = [base["per_sequence"][name][metric] for name in sequences]
        bot_values = [bot["per_sequence"][name][metric] for name in sequences]
        ax.bar(x - width / 2, base_values, width, label="ByteTrack", color="#4C78A8")
        ax.bar(x + width / 2, bot_values, width, label="BoT-SORT ref", color="#F58518")
        ax.set_title(metric)
        ax.set_xticks(x)
        ax.set_xticklabels(short, rotation=0)
        ax.grid(axis="y", alpha=0.25)
        ax.set_ylim(0, 65)
    axes[0].set_ylabel("percent")
    axes[0].legend(fontsize=8)
    fig.suptitle("MOT17 seven-scene evaluation (video-derived frames, fixed YOLOv8n)", fontsize=11)
    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=160)
    plt.close(fig)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
