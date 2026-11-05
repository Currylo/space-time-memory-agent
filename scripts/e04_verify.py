#!/usr/bin/env python3
"""Independent checks for the E04 rule outputs.

Run from the project directory:
    python3 scripts/e04_verify.py

The verifier re-reads the source run, the E03 zone assignments and the E04
outputs. It does not re-run detection/tracking or use future observations.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.e04_rules import (  # noqa: E402
    attach_zone_assignments,
    candidate_trigger_signature,
    evaluate_rules,
    load_json,
    load_jsonl,
    load_run,
    load_zones,
    prefix_run,
)


def check(condition: bool, label: str, detail: str = "") -> dict:
    result = {"check": label, "ok": bool(condition), "detail": detail}
    print(("PASS  " if condition else "FAIL  ") + label + (f"  [{detail}]" if detail else ""))
    if not condition:
        raise AssertionError(label + (f": {detail}" if detail else ""))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="E04 规则输出核验")
    parser.add_argument("--run", type=Path, default=Path("runs/e00-public-demo-005"))
    parser.add_argument("--zones", type=Path, default=Path("data/e01/zones/zones.json"))
    parser.add_argument(
        "--zone-assignments",
        type=Path,
        default=Path("data/e03/zone_assignments.jsonl"),
    )
    parser.add_argument("--config", type=Path, default=Path("configs/e04_demo_rules.json"))
    parser.add_argument("--rule-run", type=Path, default=Path("data/e04/rule_run.json"))
    parser.add_argument(
        "--candidates", type=Path, default=Path("data/e04/candidates.jsonl")
    )
    parser.add_argument("--demo", type=Path, default=Path("demo/data/rules.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("reports/E04功能验证.json")
    )
    args = parser.parse_args()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_DIR / path

    results: list[dict] = []
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        run = load_run(resolve(args.run))
        zones, _ = load_zones(resolve(args.zones), str(run.manifest["camera_id"]))
        assignments, assignment_details = attach_zone_assignments(
            run, resolve(args.zone_assignments), zones
        )
        config = load_json(resolve(args.config))
        rule_run = load_json(resolve(args.rule_run))
        candidates = load_jsonl(resolve(args.candidates))
        demo = load_json(resolve(args.demo))

        results.append(
            check(
                len(run.points) == 9599
                and len(run.frames) == 300
                and len(run.points_by_track) == 82,
                "输入规模与采用运行一致",
                f"{len(run.points)} 点 / {len(run.frames)} 帧 / {len(run.points_by_track)} 轨迹",
            )
        )
        results.append(
            check(
                assignment_details["assignment_rows"] == 6942
                and assignment_details["points_with_zone"] == 6942,
                "E03 区域派生结果完整",
                f"{assignment_details['assignment_rows']} 条",
            )
        )
        results.append(
            check(
                rule_run["candidate_count"] == len(candidates),
                "候选计数与 candidates.jsonl 一致",
                f"{rule_run['candidate_count']}",
            )
        )
        results.append(
            check(
                len({item["dedup_key"] for item in candidates}) == len(candidates),
                "候选去重键唯一",
            )
        )
        results.append(
            check(
                all(item["status_label"] == "规则条件触发，待人工复核" for item in candidates),
                "候选统一标注为规则条件触发、待人工复核",
            )
        )
        results.append(
            check(
                not any(
                    word in json.dumps(item, ensure_ascii=False)
                    for item in candidates
                    for word in ("危险事件", "确认异常", "已报警")
                ),
                "候选没有自动升级为危险事件",
            )
        )

        # Zone counts at trigger frames are reproducible from E03 assignments.
        counts_by_frame_zone: dict[tuple[int, str], int] = {}
        for (track_id, frame_index), zone_ids in assignments.items():
            for zone_id in zone_ids:
                key = (frame_index, zone_id)
                counts_by_frame_zone[key] = counts_by_frame_zone.get(key, 0) + 1
        zone_stats = rule_run["zone_stats"]
        count_mismatches = 0
        for zone in zone_stats["zones"]:
            for index, count in enumerate(zone["counts"]):
                if count != counts_by_frame_zone.get(
                    (zone_stats["frame_indices"][index], zone["zone_id"]), 0
                ):
                    count_mismatches += 1
        results.append(
            check(
                count_mismatches == 0,
                "逐帧区域观测数与 E03 区域派生一致",
                f"不一致 {count_mismatches}",
            )
        )

        # Long-stay candidates must cross the threshold at the recorded frame.
        point_by_key = {(p.track_id, p.frame_index): p for p in run.points}
        for candidate in candidates:
            if candidate["rule_id"] != "long_stay":
                continue
            point = point_by_key[
                (candidate["track_id"], candidate["condition_met_frame_index"])
            ]
            frame_period = 1.0 / run.fps
            expected_trigger_time = min(
                run.analysis_end, point.video_time + frame_period
            )
            results.append(
                check(
                    abs(candidate["evidence"]["video_time"] - point.video_time) <= 1e-6
                    and abs(
                        candidate["evidence_frame_video_time"] - point.video_time
                    ) <= 1e-6
                    and abs(candidate["condition_met_video_time"] - expected_trigger_time)
                    <= 1e-6
                    and abs(candidate["frame_coverage_end"] - expected_trigger_time)
                    <= 1e-6
                    and all(
                        abs(a - b) <= 1e-3
                        for a, b in zip(candidate["evidence"]["bbox"], point.bbox)
                    ),
                    f"候选 {candidate['candidate_id']} 证据帧/覆盖区间/触发时刻一致",
                    f"frame {point.frame_index} / evidence {point.video_time:.2f}s / "
                    f"trigger {expected_trigger_time:.2f}s",
                )
            )
            dwell_at_trigger = candidate["trigger_features"]["dwell_observed_seconds"]
            threshold = candidate["thresholds"]["min_continuous_observed_dwell_seconds"]
            results.append(
                check(
                    dwell_at_trigger + 1e-9 >= threshold,
                    f"候选 {candidate['candidate_id']} 首次跨过停留阈值",
                    f"{dwell_at_trigger:.2f} ≥ {threshold:.2f}",
                )
            )
            future_only = {
                "end_reason",
                "exit_confirmed",
                "right_censored",
            }
            results.append(
                check(
                    not (future_only & set(candidate["trigger_features"]))
                    and "end_reason" in candidate["final_features"]
                    and "end_reason" in candidate["lifecycle"],
                    f"候选 {candidate['candidate_id']} 触发特征不含后续结束信息",
                )
            )

        # Reversal status must be an honest zero and report the closest miss.
        reversal = next(
            item for item in rule_run["rules"] if item["rule_id"] == "reversal"
        )
        results.append(
            check(
                reversal["evaluation_status"] == "evaluated"
                and reversal["candidate_count"] == 0,
                "折返规则已运行且如实记录零触发",
                reversal["status_text"],
            )
        )
        results.append(
            check(
                reversal["near_misses"]
                and reversal["near_misses"][0]["track_id"] == 77
                and reversal["near_misses"][0]["max_effective_reversals"] == 1,
                "折返最接近轨迹与人工核对一致（目标 077，1 次有效反转）",
            )
        )

        occupancy = next(
            item
            for item in rule_run["rules"]
            if item["rule_id"] == "occupancy_change_60s"
        )
        results.append(
            check(
                occupancy["evaluation_status"] == "insufficient_observation"
                and occupancy["required_seconds"] == 60.0
                and abs(occupancy["observed_seconds"] - 12.0) < 1e-6,
                "60 秒人数变化规则显示观察时长不足",
                f"{occupancy['observed_seconds']:.2f} / 60.00 秒",
            )
        )
        occupancy_rule = next(
            item for item in config["rules"] if item["rule_id"] == "occupancy_change_60s"
        )
        results.append(
            check(
                occupancy_rule["params"]["window_seconds"] == 60.0,
                "演示配置没有缩短 60 秒窗口",
            )
        )

        # Prefix causality: compare the complete past candidate set, including
        # missing/extra candidates and trigger features, not only candidates
        # that happen to appear in both runs.
        def signature_set(items: list[dict], cutoff: float | None = None) -> list[dict]:
            selected = [
                item
                for item in items
                if cutoff is None
                or float(item["condition_met_video_time"]) <= cutoff + 1e-9
            ]
            return sorted(
                (candidate_trigger_signature(item) for item in selected),
                key=lambda item: item["candidate_id"],
            )

        causality_checks = 0
        for cutoff in (6.0, 8.0, 9.0, 10.0, 10.12, 10.5, 11.0, 11.28, 11.5):
            prefix = prefix_run(run, cutoff)
            prefix_result = evaluate_rules(prefix, zones, assignments, config)
            expected = signature_set(candidates, cutoff=cutoff)
            actual = signature_set(prefix_result["candidates"])
            if actual != expected:
                raise AssertionError(
                    f"截断 {cutoff:.2f}s 的过去候选集合不一致："
                    f"actual={len(actual)} expected={len(expected)}"
                )
            causality_checks += len(actual)
        results.append(
            check(
                causality_checks >= 6,
                "截断输入保持完整过去候选集合与触发特征",
                f"{causality_checks} 个过去候选签名一致",
            )
        )

        # Demo JSON and rule_run must agree on candidates, rules, counts and hashes.
        results.append(
            check(
                len(demo["candidates"]) == len(candidates)
                and [item["candidate_id"] for item in demo["candidates"]]
                == [item["candidate_id"] for item in candidates],
                "演示数据候选与规则输出一致",
            )
        )
        results.append(
            check(
                demo["provenance"]["rule_config_sha256"]
                == rule_run["inputs"]["rule_config_sha256"],
                "演示数据保存规则配置哈希",
            )
        )
        results.append(
            check(
                demo["provenance"]["zone_assignments_sha256"]
                == assignment_details["sha256"]
                and demo["provenance"]["tracks_jsonl_sha256"]
                == run.hashes["tracks_jsonl"],
                "演示数据保存输入与区域哈希",
            )
        )
        results.append(
            check(
                len(demo["zone_stats"]["times"]) == 300
                and all(len(zone["counts"]) == 300 for zone in demo["zones"]),
                "演示数据逐帧区域统计完整",
                "300 帧 × 3 区域",
            )
        )
        results.append(
            check(
                all(
                    0 <= coordinate <= max(int(run.manifest["width"]), int(run.manifest["height"]))
                    for zone in zones
                    for point in zone.polygon
                    for coordinate in point
                ),
                "演示区域多边形位于画面坐标范围内",
            )
        )

        summary = {
            "started_at_utc": started_at,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "checks_passed": len(results),
            "checks_failed": 0,
            "rule_run": str(resolve(args.rule_run)),
            "candidates": len(candidates),
            "causality_past_candidates": causality_checks,
            "causality_cutoffs": [6.0, 8.0, 9.0, 10.0, 10.12, 10.5, 11.0, 11.28, 11.5],
            "supervision_crosscheck": rule_run.get("supervision_crosscheck"),
            "checks": results,
        }
        output_path = resolve(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except Exception as exc:
        print(f"\nE04 核验失败：{exc}", file=sys.stderr)
        return 1

    print(f"\n全部 {len(results)} 项检查通过；结果已写入 {resolve(args.output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
