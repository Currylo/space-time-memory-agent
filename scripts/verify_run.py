import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from st_memory.records import verify_run

result = verify_run(sys.argv[1])
print(json.dumps(result, ensure_ascii=False, indent=2))
sys.exit(0 if result["ok"] else 1)
