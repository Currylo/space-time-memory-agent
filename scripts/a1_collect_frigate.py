#!/usr/bin/env python3
"""Read local Frigate APIs and preserve evidence; no automatic A1/A2 acceptance."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CAMERA = "a1_public_demo"
ZONE = "a1_demo_zone"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:15000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    url = urllib.parse.urlparse(args.base_url)
    if url.scheme != "http" or url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("This collector only reads the local public-video test instance")
    output = args.output or ROOT / "reports/a1/frigate" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    endpoints = {
        "version": "/api/version", "stats": "/api/stats", "config": "/api/config",
        "events": f"/api/events?cameras={CAMERA}&limit=100",
        "review": f"/api/review?cameras={CAMERA}&limit=100",
        "latest": f"/api/{CAMERA}/latest.jpg?bbox=1&timestamp=1&zones=1",
    }
    responses = {}
    parsed = {}
    for name, path in endpoints.items():
        try:
            with opener.open(args.base_url.rstrip("/") + path, timeout=10) as response:
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise ValueError("Unexpected response size; not saved")
                suffix = ".jpg" if name == "latest" else ".txt" if name == "version" else ".json"
                (output / (name + suffix)).write_bytes(body)
                responses[name] = {"http_status": response.status, "bytes": len(body)}
                if suffix == ".json":
                    parsed[name] = json.loads(body)
                if name == "version":
                    parsed[name] = body.decode("utf-8", errors="replace").strip()
                if name == "latest":
                    responses[name]["jpeg_header_valid"] = body.startswith(b"\xff\xd8")
        except (OSError, ValueError, urllib.error.URLError) as error:
            responses[name] = {"error": str(error)}
    events = parsed.get("events", [])
    if not isinstance(events, list):
        events = []
    people = [event for event in events if event.get("camera") == CAMERA and event.get("label") == "person"]
    zoned = [event for event in people if ZONE in event.get("zones", [])]
    result = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(), "base_url": args.base_url,
        "status": "api_evidence_collected_pending_manual_acceptance" if all("error" not in r for r in responses.values()) else "unavailable_or_incomplete",
        "responses": responses, "platform_version_response": parsed.get("version"),
        "observations": {"person_events": len(people), "zone_person_events": len(zoned),
                         "events_with_clip_flag": sum(bool(e.get("has_clip")) for e in people)},
        "a1_passed": None, "platform_a2_passed": False,
        "manual_checks_required": ["Container/config validation and pinned image digest",
            "Real video decoding, detector activity and visible tracked boxes",
            "Zone loitering evidence consistent with actual platform configuration",
            "Actual recording/clip playback in platform UI; has_clip flag alone is insufficient",
            "Screenshots, logs and source/platform time alignment record"],
    }
    (output / "collection.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "api_evidence_collected_pending_manual_acceptance" else 2


if __name__ == "__main__":
    raise SystemExit(main())
