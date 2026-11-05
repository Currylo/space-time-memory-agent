#!/usr/bin/env python3
"""Read-only VOD diagnosis against recorded files and local Frigate HTTP APIs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "deploy/frigate/runtime"
FFMPEG = Path("ffmpeg")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    origin = "http://127.0.0.1:15000"
    result = {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "http": {}, "recordings": []}

    def fetch(name, path, suffix=".txt"):
        url = urllib.parse.urljoin(origin, path)
        if urllib.parse.urlparse(url).netloc != "127.0.0.1:15000":
            raise ValueError("Refusing a non-local VOD resource")
        try:
            with opener.open(url, timeout=20) as response:
                body = response.read(16 * 1024 * 1024 + 1)
                status = response.status
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read(16384)
        (args.output / (name + suffix)).write_bytes(body)
        result["http"][name] = {"url": url, "status": status, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        return status, body, url

    _, body, _ = fetch("active-config", "/api/config", ".json")
    config = json.loads(body)
    inputs = config["cameras"]["a1_public_demo"]["ffmpeg"]["inputs"]
    result["active_inputs"] = [{"path": i["path"], "input_args": i.get("input_args")} for i in inputs]
    # Keep only the public-video camera configuration; avoid persisting unrelated secrets.
    (args.output / "active-config.json").write_text(json.dumps({"public_camera_inputs": result["active_inputs"]}, indent=2) + "\n")
    for name, path in (("version", "/api/version"), ("review", "/api/review?cameras=a1_public_demo&limit=20")):
        fetch(name, path)
    connection = sqlite3.connect(f"file:{RUNTIME / 'config/frigate.db'}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    selected = [dict(r) for r in connection.execute("select path,start_time,end_time,duration from recordings where camera='a1_public_demo' order by start_time limit 2")]
    selected += [dict(r) for r in connection.execute("select path,start_time,end_time,duration from recordings where camera='a1_public_demo' order by start_time desc limit 6")]
    for index, recording in enumerate(selected):
        recording["utc"] = datetime.fromtimestamp(recording["start_time"], timezone.utc).isoformat()
        path = RUNTIME / "media" / Path(recording["path"]).relative_to("/media/frigate")
        probe = subprocess.run([str(FFMPEG), "-hide_banner", "-i", str(path)], text=True, capture_output=True, timeout=15)
        (args.output / f"probe-{index}.txt").write_text(probe.stderr)
        recording["stream_info"] = [line.strip() for line in probe.stderr.splitlines() if "Video:" in line or "Duration:" in line]
        result["recordings"].append(recording)
    oldest = selected[0]
    newest_h264 = next(r for r in selected[2:] if any("Video: h264" in line for line in r["stream_info"]))
    windows = {
        "old": (int(oldest["start_time"]) + 1, int(oldest["end_time"]) - 1),
        "new": (int(newest_h264["start_time"]) + 1, int(newest_h264["end_time"]) - 1),
        "mixed": (int(oldest["start_time"]) + 1, int(newest_h264["end_time"]) - 1),
    }
    ui_start = (int(newest_h264["start_time"]) // 600) * 600
    windows["ui-recent-chunk"] = (ui_start, ui_start + 600)
    result["native_review_link"] = origin + "/review?timestamp=a1_public_demo_" + str(int(newest_h264["start_time"]) + 1)
    for name, (start, end) in windows.items():
        path = f"/vod/a1_public_demo/start/{start}/end/{end}/master.m3u8"
        status, master, master_url = fetch(name + "-master", path, ".m3u8")
        if status != 200:
            continue
        lines = [line.strip() for line in master.decode().splitlines() if line.strip() and not line.startswith("#")]
        status, media, media_url = fetch(name + "-playlist", urllib.parse.urljoin(master_url, lines[0]), ".m3u8")
        if status == 200:
            text = media.decode()
            init = re.search(r'#EXT-X-MAP:URI="([^"]+)"', text)
            init_body = b""
            if init:
                init_status, init_body, _ = fetch(name + "-init", urllib.parse.urljoin(media_url, init.group(1)), ".mp4")
                if init_status != 200:
                    continue
            segments = [line.strip() for line in media.decode().splitlines() if line.strip() and not line.startswith("#")]
            status, segment_body, _ = fetch(name + "-segment", urllib.parse.urljoin(media_url, segments[0]), ".m4s")
            if status == 200:
                # Frigate returns fragmented MP4: media fragment requires its EXT-X-MAP init.
                combined = args.output / (name + "-init-plus-segment.mp4")
                combined.write_bytes(init_body + segment_body)
                decoded = subprocess.run([str(FFMPEG), "-v", "error", "-i", str(combined), "-t", "1", "-f", "null", "-"], text=True, capture_output=True, timeout=20)
                result[name + "_segment_decode"] = {"returncode": decoded.returncode, "stderr": decoded.stderr}
    (args.output / "diagnosis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"active_inputs": result["active_inputs"], "http": result["http"], "segment_decode": {k:v for k,v in result.items() if k.endswith("_segment_decode")}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
