#!/usr/bin/env python3
"""Small helpers for the controlled same-source Frigate camera.

The running container does not expose a Docker socket to this session.  We use
Frigate's own config-save endpoint (the same code path as the web config
editor), then *verify* that the API, config, stats and camera process actually
came back.  No success is inferred from the HTTP response alone.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

CAMERA = "a2_unified_mot20_05"
INPUT_PATH = "/input/a2_unified_mot20_05_h264.mp4"


def _system_python() -> str:
    """PyYAML is available in the host Python, not in the vision venv."""
    return shutil.which("python3") or "/usr/bin/python3"


def _yaml_to_json(text: str):
    result = subprocess.run(
        [_system_python(), "-c", "import sys,yaml,json; print(json.dumps(yaml.safe_load(sys.stdin), ensure_ascii=False, sort_keys=False))"],
        input=text, text=True, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yaml parse failed: {result.stderr}")
    return json.loads(result.stdout)


def _json_to_yaml(value) -> str:
    result = subprocess.run(
        [_system_python(), "-c", "import sys,yaml,json; print(yaml.safe_dump(json.load(sys.stdin), sort_keys=False, allow_unicode=True, default_flow_style=False))"],
        input=json.dumps(value, ensure_ascii=False), text=True, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yaml dump failed: {result.stderr}")
    return result.stdout


def request(origin: str, method: str, path: str, body: dict | None = None, text: str | None = None, timeout: float = 30.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = None
    headers = {}
    if text is not None:
        data = text.encode("utf-8")
        headers["Content-Type"] = "text/plain"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(origin + path, data=data, method=method, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as response:
            payload = response.read()
            try:
                return response.status, json.loads(payload)
            except json.JSONDecodeError:
                return response.status, {"text": payload.decode(errors="replace")}
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            return error.code, json.loads(payload)
        except Exception:
            return error.code, {"raw": payload.decode(errors="replace")}
    except Exception as error:  # noqa: BLE001
        return None, {"error": f"{type(error).__name__}: {error}"}


def get_raw_config(origin: str) -> str:
    status, body = request(origin, "GET", "/api/config/raw")
    if status != 200:
        raise RuntimeError(f"cannot read config: {status} {body}")
    if isinstance(body, dict) and "text" in body:
        return body["text"]
    if isinstance(body, str):
        return body
    raise RuntimeError(f"unexpected config payload: {body!r}")


def camera_block(enabled: bool) -> dict:
    return {
        "enabled": enabled,
        "ffmpeg": {
            "inputs": [
                {
                    "path": INPUT_PATH,
                    "input_args": ["-re"],
                    "roles": ["detect", "record"],
                }
            ]
        },
        "detect": {"enabled": True, "width": 826, "height": 540, "fps": 3},
        "zones": {
            "unified_public_observation_v1": {
                "friendly_name": "同源公共观察区（演示，非业务区）",
                "coordinates": "0.05,0.05,0.95,0.05,0.95,0.95,0.05,0.95",
                "objects": ["person"],
                "inertia": 3,
                "loitering_time": 10,
            }
        },
        "review": {"alerts": {"enabled": False}, "detections": {"enabled": False}},
        "motion": {"enabled": True, "threshold": 80, "contour_area": 100},
        "snapshots": {"bounding_box": False},
        "record": {"enabled": True},
    }


def build_config(raw: str, *, enabled: bool) -> tuple[str, dict]:
    value = _yaml_to_json(raw)
    value.pop("version", None)
    cameras = value.setdefault("cameras", {})
    cameras[CAMERA] = camera_block(enabled)
    text = _json_to_yaml(value)
    return text, value


def save_config(origin: str, text: str, *, restart: bool) -> dict:
    started = time.time()
    status, body = request(
        origin, "POST", f"/api/config/save?save_option={'restart' if restart else 'reload'}",
        text=text, timeout=20.0,
    )
    return {
        "save_started_epoch": started,
        "save_finished_epoch": time.time(),
        "http_status": status,
        "body": body,
        "restart_requested": restart,
    }


def wait_ready(origin: str, *, timeout: float = 180.0, expect_camera: str | None = None) -> dict:
    started = time.time()
    attempts = []
    while time.time() - started < timeout:
        status, version = request(origin, "GET", "/api/version", timeout=5.0)
        record = {"elapsed": round(time.time() - started, 2), "status": status}
        if status == 200:
            record["version"] = version
            if expect_camera:
                config_status, config = request(origin, "GET", "/api/config", timeout=10.0)
                record["config_status"] = config_status
                if config_status == 200 and isinstance(config, dict):
                    record["camera_present"] = expect_camera in (config.get("cameras") or {})
                    cam = (config.get("cameras") or {}).get(expect_camera) or {}
                    record["camera_enabled"] = cam.get("enabled")
                    if record["camera_present"]:
                        attempts.append(record)
                        return {
                            "ok": True,
                            "elapsed": round(time.time() - started, 2),
                            "version": version,
                            "config": config,
                            "attempts": attempts,
                        }
            else:
                attempts.append(record)
                return {"ok": True, "elapsed": round(time.time() - started, 2), "version": version, "attempts": attempts}
        attempts.append(record)
        time.sleep(1.0)
    return {"ok": False, "elapsed": round(time.time() - started, 2), "attempts": attempts[-20:]}


def camera_stats(origin: str, camera: str) -> dict | None:
    status, stats = request(origin, "GET", "/api/stats", timeout=10.0)
    if status != 200 or not isinstance(stats, dict):
        return None
    return (stats.get("cameras") or {}).get(camera)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:15000")
    parser.add_argument("command", choices=("show", "build"))
    parser.add_argument("--enabled", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    raw = get_raw_config(args.origin)
    text, value = build_config(raw, enabled=args.enabled)
    if args.command == "show":
        print(text)
        return 0
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(json.dumps({"camera": CAMERA, "enabled": args.enabled, "bytes": len(text)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
