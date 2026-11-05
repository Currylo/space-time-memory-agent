#!/usr/bin/env python3
"""HTTP negative/positive checks for the server-side review guard (R3).

Each variant uses a fresh copy of the real ledger (SQLite backup) and a
separate server process.  The production ledger is never written.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REAL_LEDGER = ROOT / "data/a2/adapter/ledger.sqlite"
PAGE_DATA = ROOT / "demo/data/unified_platform.json"
SERVER = ROOT / "demo/serve.py"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def copy_ledger(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source), timeout=10)
    dst = sqlite3.connect(str(target))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()


def review_count(path: Path) -> int:
    connection = sqlite3.connect(str(path))
    try:
        return int(connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0])
    finally:
        connection.close()


def wait_health(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def request_json(port: int, path: str, body: dict | None = None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read()
            try:
                return response.status, json.loads(payload)
            except json.JSONDecodeError:
                return response.status, {"raw": payload.decode(errors="replace")}
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            return error.code, json.loads(payload)
        except Exception:
            return error.code, {"raw": payload.decode(errors="replace")}
    except Exception as error:  # noqa: BLE001
        return None, {"error": f"{type(error).__name__}: {error}"}


def run_variant(work: Path, name: str, data: dict, port: int, candidate_id: str, key: str, expect_success: bool) -> dict:
    variant_path = work / f"{name}.json"
    variant_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger_path = work / f"{name}.sqlite"
    copy_ledger(REAL_LEDGER, ledger_path)
    log_path = work / f"{name}.server.log"
    review_log = work / f"{name}.reviews.jsonl"
    before = review_count(ledger_path)
    process = subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(port), "--review-ledger", str(ledger_path),
         "--unified-platform-data", str(variant_path), "--review-log", str(review_log)],
        cwd=str(ROOT), stdout=log_path.open("w", encoding="utf-8"), stderr=subprocess.STDOUT,
    )
    try:
        healthy = wait_health(port)
        if not healthy:
            return {"name": name, "healthy": False, "passed": False, "error": "server did not become healthy", "log": str(log_path)}
        status, body = request_json(port, "/api/review/submit", {
            "candidate_id": candidate_id,
            "key": key,
            "reviewer": "r3_guard_test_operator",
            "decision": "uncertain",
            "note": "R3 guard check; not a real review.",
        })
        after = review_count(ledger_path)
        history_status, history = request_json(port, f"/api/review/history?candidate_id={candidate_id}")
        passed = (status == 200 and after == before + 1) if expect_success else (status is not None and 400 <= status < 500 and after == before)
        return {
            "name": name,
            "healthy": True,
            "expected_success": expect_success,
            "http_status": status,
            "response": body,
            "reviews_before": before,
            "reviews_after": after,
            "review_count_unchanged": after == before,
            "history_http_status": history_status,
            "history": history,
            "passed": passed,
            "ledger": str(ledger_path.relative_to(ROOT)),
            "variant": str(variant_path.relative_to(ROOT)),
            "review_log": str(review_log.relative_to(ROOT)),
            "server_log": str(log_path.relative_to(ROOT)),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-dir", type=Path, required=True)
    args = parser.parse_args()
    round_dir = args.round_dir.resolve()
    work = round_dir / "corrections" / "review_guard"
    work.mkdir(parents=True, exist_ok=True)
    data = json.loads(PAGE_DATA.read_text(encoding="utf-8"))
    first = data["candidates"][0]
    candidate_id = first["candidate_id"]
    key = first["ledger_key"]
    results = []
    variants = []

    def variant(name, mutate, expect_success=False, cid=None, candidate_key=None):
        payload = copy.deepcopy(data)
        mutate(payload)
        variants.append((name, payload, expect_success, cid or candidate_id, candidate_key or key))

    variant("valid", lambda payload: None, expect_success=True)
    variant("clock_needs_review", lambda payload: payload["clock"].__setitem__("status", "needs_review"))
    variant("evidence_partial", lambda payload: (
        payload["candidates"][0]["evidence"].__setitem__("status", "partial_or_failed"),
        payload["candidates"][0]["clip"]["coverage"].__setitem__("status", "partial_or_failed"),
    ))
    variant("clip_missing", lambda payload: (
        payload["candidates"][0]["clip"].__setitem__("http_status", 404),
        payload["candidates"][0]["clip"].__setitem__("decoded", False),
        payload["candidates"][0]["evidence"].__setitem__("status", "partial_or_failed"),
    ))
    def tail_partial(payload):
        candidate = payload["candidates"][0]
        candidate["clip"]["coverage"].update({
            "status": "partial",
            "problems": ["clip_ends_0.120s_before_coverage_end"],
            "missing_tail_seconds": 0.12,
            "required_seconds": 10.0,
            "covered_seconds": 9.88,
        })
        candidate["evidence"]["status"] = "verified"
        candidate["evidence"]["window"]["condition_ok"] = True
    variant("tail_partial", tail_partial)
    variant("identity_mismatch", lambda payload: payload["candidates"][0].__setitem__("event_id", "wrong-event-id"))
    # Legacy three-case positive path: no clock/evidence fields, only old key/file.
    legacy = json.loads((ROOT / "demo/data/review_candidates.json").read_text(encoding="utf-8"))[0]
    variant("legacy_valid", lambda payload: None, expect_success=True, cid=legacy["candidate_id"], candidate_key=legacy["key"])

    for index, (name, payload, expect_success, cid, candidate_key) in enumerate(variants):
        results.append(run_variant(work, name, payload, 8770 + index, cid, candidate_key, expect_success))
    output = {
        "schema": "unified-review-guard-check-v1",
        "created_at_utc": utc_now(),
        "production_ledger": str(REAL_LEDGER.relative_to(ROOT)),
        "production_ledger_reviews": review_count(REAL_LEDGER),
        "test_note": "Each variant uses a SQLite backup copy; production ledger is not written.",
        "results": results,
        "passed": all(item.get("passed") for item in results),
    }
    (work / "backend_review_guard_v2.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": output["passed"],
        "production_ledger_reviews": output["production_ledger_reviews"],
        "results": [{k: item.get(k) for k in ("name", "http_status", "reviews_before", "reviews_after", "passed", "error")} for item in results],
    }, ensure_ascii=False, indent=2))
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
