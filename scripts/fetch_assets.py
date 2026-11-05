"""Download ONLY the two public E00 assets and enforce the recorded SHA256."""
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from st_memory.records import sha256

lock = json.loads((ROOT / "components.lock.json").read_text())
for name, item in lock["assets"].items():
    destination = ROOT / item["path"]
    if destination.exists():
        if sha256(destination) != item["sha256"]:
            raise ValueError(f"{name}: existing file checksum mismatch; preserved for inspection")
        print(f"{name}: already verified")
        continue
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(item["url"], timeout=120) as response, open(partial, "wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    if sha256(partial) != item["sha256"]:
        raise ValueError(f"{name}: download checksum mismatch; partial preserved")
    partial.replace(destination)
    print(f"{name}: downloaded and verified")
