"""Compare fixed inputs and observation results, excluding per-run identifiers."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from st_memory.records import read_jsonl, verify_run

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("first", type=Path)
parser.add_argument("second", type=Path)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
first, second = [json.loads((p / "run.json").read_text()) for p in (args.first, args.second)]
checks = {key: first[key] == second[key] for key in
          ("config_hash", "model_hash", "components_lock_hash", "code_version")}
checks["both_integrity_passed"] = all(verify_run(p)["ok"] for p in (args.first, args.second))
checks["raw_detections_equal"] = first["artifact_hashes"]["detections.jsonl"] == second["artifact_hashes"]["detections.jsonl"]


def observations(path):
    return [{k: v for k, v in row.items() if k not in ("run_id", "trace_id")}
            for row in read_jsonl(path / "tracks.jsonl")]


checks["track_observations_equal"] = observations(args.first) == observations(args.second)
result = {"first_run": first["run_id"], "second_run": second["run_id"],
          "ok": all(checks.values()), "checks": checks,
          "scope": "fixed input reproduction only; not independent accuracy evaluation"}
body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
if args.output:
    args.output.write_text(body)
print(body, end="")
sys.exit(0 if result["ok"] else 1)
