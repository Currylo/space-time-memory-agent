"""Read-only query; inclusive video-relative seconds. No ML dependencies."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from st_memory.records import query

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("run_dir", type=Path)
parser.add_argument("--camera-id")
parser.add_argument("--track-id", type=int)
parser.add_argument("--start", type=float)
parser.add_argument("--end", type=float)
parser.add_argument("--zone-id")
args = parser.parse_args()
for row in query(args.run_dir / "tracks.jsonl", args.camera_id, args.track_id,
                 args.start, args.end, args.zone_id):
    print(json.dumps(row, ensure_ascii=False))
