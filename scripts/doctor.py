"""Record environment; a failed sandbox probe is NOT a broken host driver."""
import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": str(error)}


def collect(context):
    root = Path(__file__).resolve().parents[1]
    packages = {}
    for name in ("torch", "torchvision", "ultralytics", "supervision", "numpy", "opencv-python"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    result = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(), "execution_context": context,
        "platform": platform.platform(), "python": platform.python_version(),
        "cpu": command(["lscpu"]), "memory": command(["free", "-b"]),
        "disk": dict(zip(("total", "used", "free"), shutil.disk_usage(root))),
        "nvidia_smi": command(["nvidia-smi"]),
        "device_nodes": sorted(str(p) for p in Path("/dev").glob("nvidia*")),
        "packages": packages,
    }
    if packages["torch"]:
        try:
            import torch
            available = torch.cuda.is_available()
            result["cuda"] = {"available": available, "torch_cuda_runtime": torch.version.cuda}
            if available:
                x = torch.arange(16, dtype=torch.float32, device="cuda").reshape(4, 4)
                y = x @ x.T
                torch.cuda.synchronize()
                result["cuda"].update({"device": torch.cuda.get_device_name(0),
                    "total_memory_bytes": torch.cuda.get_device_properties(0).total_memory,
                    "matrix_multiply_sum": y.sum().item(), "tensor_device": str(y.device)})
        except Exception as error:
            result["cuda"] = {"error": str(error)}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, choices=["sandbox", "host"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = collect(args.context)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
