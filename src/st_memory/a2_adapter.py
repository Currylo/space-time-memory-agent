"""Reusable A2 external-candidate adapter with persistent idempotency and review history.

This module deliberately separates three things that were previously conflated:

* candidate identity and timing (source video / condition time / emit time);
* platform write state (pending, writing, created, completed, unknown);
* human review (pending by default; a separate explicit submission adds history).

The SQLite ledger is the source of truth and is independent of per-run evidence
directories, so a new output directory or a process restart cannot reset dedup.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_CANDIDATE_FIELDS = (
    "candidate_id",
    "source_video_sha256",
    "camera",
    "label",
    "rule_id",
    "rule_version",
    "zone_id",
    "zone_version",
    "run_id",
    "loop_index",
    "track_id",
    "source_video_time",
    "condition_met_video_time",
    "evidence_start",
    "evidence_end",
    "score",
)
VALID_DECISIONS = {"confirmed", "rejected", "uncertain"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def candidate_key(candidate: dict) -> str:
    missing = [field for field in REQUIRED_CANDIDATE_FIELDS if field not in candidate]
    if missing:
        raise ValueError(f"candidate missing fields: {missing}")
    fields = list(REQUIRED_CANDIDATE_FIELDS)
    # New same-source candidates carry an explicit replay identity.  Legacy
    # candidates do not, so adding the field conditionally keeps their old
    # ledger keys readable while ensuring a new replay cannot collide with an
    # earlier one.
    if "replay_id" in candidate:
        fields.append("replay_id")
    return hashlib.sha256(canonical_json({k: candidate[k] for k in fields}).encode()).hexdigest()


def sub_label_for(key: str) -> str:
    return "a2:" + key[:24]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ClaimResult:
    state: str  # claimed, duplicate, in_progress, needs_reconcile
    key: str
    row: dict | None


class Ledger:
    def __init__(self, path: Path, stale_seconds: float = 90.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stale_seconds = stale_seconds
        self.connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._init_schema()

    def _init_schema(self):
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS candidates (
                key TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                candidate_json TEXT NOT NULL,
                status TEXT NOT NULL,
                event_id TEXT,
                review_status TEXT NOT NULL DEFAULT 'pending_review',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                platform_event_start REAL,
                platform_event_end REAL,
                has_clip INTEGER,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                state TEXT NOT NULL,
                http_status INTEGER,
                response_json TEXT,
                event_id TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                event_id TEXT,
                reviewer TEXT NOT NULL,
                decision TEXT NOT NULL,
                reviewed_at TEXT NOT NULL,
                note TEXT NOT NULL,
                evidence_path TEXT,
                evidence_sha256 TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS imports (
                key TEXT PRIMARY KEY,
                imported_at TEXT NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )

    def close(self):
        self.connection.close()

    def get(self, key: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM candidates WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def get_by_candidate_id(self, candidate_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM candidates WHERE candidate_id=? ORDER BY updated_at DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        return dict(row) if row else None

    def all_candidates(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM candidates ORDER BY updated_at DESC")]

    def claim(self, candidate: dict) -> ClaimResult:
        key = candidate_key(candidate)
        now_epoch = time.time()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.get(key)
            if row is not None:
                if row["event_id"] or row["status"] in ("created", "completed"):
                    self.connection.execute("COMMIT")
                    return ClaimResult("duplicate", key, row)
                if row["status"] == "writing":
                    updated = datetime.fromisoformat(row["updated_at"]).timestamp()
                    if now_epoch - updated < self.stale_seconds:
                        self.connection.execute("COMMIT")
                        return ClaimResult("in_progress", key, row)
                    self.connection.execute("COMMIT")
                    return ClaimResult("needs_reconcile", key, row)
                if row["status"] == "unknown":
                    self.connection.execute("COMMIT")
                    return ClaimResult("needs_reconcile", key, row)
            timestamp = utc_now()
            self.connection.execute(
                """
                INSERT INTO candidates(key,candidate_id,candidate_json,status,review_status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET status='writing', updated_at=excluded.updated_at, last_error=NULL
                """,
                (key, candidate["candidate_id"], canonical_json(candidate), "writing", "pending_review", timestamp, timestamp),
            )
            self.connection.execute("COMMIT")
            return ClaimResult("claimed", key, self.get(key))
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def mark_writing_attempt(self, key: str, http_status: int | None, response: Any = None, error: str | None = None):
        self.connection.execute(
            "INSERT INTO attempts(key,attempted_at,state,http_status,response_json,error) VALUES(?,?,?,?,?,?)",
            (key, utc_now(), "write_attempt", http_status, canonical_json(response) if response is not None else None, error),
        )
        self.connection.execute(
            "UPDATE candidates SET updated_at=?, last_error=? WHERE key=?",
            (utc_now(), error, key),
        )

    def mark_created(self, key: str, event_id: str, response: Any):
        self.connection.execute(
            "UPDATE candidates SET status='created', event_id=?, updated_at=?, last_error=NULL WHERE key=?",
            (event_id, utc_now(), key),
        )
        self.connection.execute(
            "INSERT INTO attempts(key,attempted_at,state,http_status,response_json,event_id) VALUES(?,?,?,?,?,?)",
            (key, utc_now(), "created", 200, canonical_json(response), event_id),
        )

    def mark_unknown(self, key: str, error: str):
        self.connection.execute(
            "UPDATE candidates SET status='unknown', updated_at=?, last_error=? WHERE key=?",
            (utc_now(), error, key),
        )
        self.connection.execute(
            "INSERT INTO attempts(key,attempted_at,state,error) VALUES(?,?,?,?)",
            (key, utc_now(), "unknown", error),
        )

    def mark_completed(self, key: str, event: dict):
        self.connection.execute(
            "UPDATE candidates SET status='completed', event_id=?, platform_event_start=?, platform_event_end=?, has_clip=?, updated_at=? WHERE key=?",
            (
                event.get("id"),
                event.get("start_time"),
                event.get("end_time"),
                1 if event.get("has_clip") else 0,
                utc_now(),
                key,
            ),
        )

    def add_review(self, key: str, reviewer: str, decision: str, note: str, evidence_path: Path | None):
        if decision not in VALID_DECISIONS:
            raise ValueError(f"decision must be one of {sorted(VALID_DECISIONS)}")
        if not reviewer.strip():
            raise ValueError("reviewer must be non-empty")
        if not note.strip():
            raise ValueError("review note must be non-empty")
        evidence_hash = None
        if evidence_path is not None:
            if not Path(evidence_path).is_file():
                raise FileNotFoundError(evidence_path)
            evidence_hash = sha256_file(Path(evidence_path))
        row = self.get(key)
        if row is None:
            raise KeyError(f"candidate not found: {key}")
        reviewed_at = utc_now()
        self.connection.execute(
            "INSERT INTO reviews(key,event_id,reviewer,decision,reviewed_at,note,evidence_path,evidence_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (key, row.get("event_id"), reviewer, decision, reviewed_at, note, str(evidence_path) if evidence_path else None, evidence_hash, reviewed_at),
        )
        self.connection.execute(
            "UPDATE candidates SET review_status=?, updated_at=? WHERE key=?",
            (decision, utc_now(), key),
        )
        return {
            "key": key,
            "event_id": row.get("event_id"),
            "reviewer": reviewer,
            "decision": decision,
            "reviewed_at": reviewed_at,
            "note": note,
            "evidence_path": str(evidence_path) if evidence_path else None,
            "evidence_sha256": evidence_hash,
        }

    def reviews(self, key: str) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM reviews WHERE key=? ORDER BY id", (key,))]

    def attempts(self, key: str) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM attempts WHERE key=? ORDER BY id", (key,))]

    def import_existing(self, key: str, event: dict, source: str, candidate: dict | None = None):
        timestamp = utc_now()
        candidate_id = candidate["candidate_id"] if candidate else event["id"]
        payload = {"imported_event": event, "candidate": candidate}
        self.connection.execute(
            """
            INSERT INTO candidates(key,candidate_id,candidate_json,status,event_id,review_status,created_at,updated_at,platform_event_start,platform_event_end,has_clip)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET candidate_id=excluded.candidate_id,candidate_json=excluded.candidate_json,event_id=excluded.event_id,status='completed',platform_event_start=excluded.platform_event_start,platform_event_end=excluded.platform_event_end,has_clip=excluded.has_clip,updated_at=excluded.updated_at
            """,
            (
                key, candidate_id, canonical_json(payload), "completed", event["id"],
                "pending_review", timestamp, timestamp, event.get("start_time"), event.get("end_time"),
                1 if event.get("has_clip") else 0,
            ),
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO imports(key,imported_at,source,payload_json) VALUES(?,?,?,?)",
            (key, timestamp, source, canonical_json(event)),
        )


class PlatformClient:
    def __init__(self, origin: str = "http://127.0.0.1:15000", timeout: float = 20.0):
        import urllib.request
        self.origin = origin.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        import urllib.error
        import urllib.request
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.origin + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = {"raw": payload.decode(errors="replace")}
            return error.code, parsed

    def create_event(self, candidate: dict, duration: int, pre_capture: int, sub_label: str, description: str):
        return self.request(
            "POST",
            f"/api/events/{candidate['camera']}/{candidate['label']}/create",
            {
                "score": candidate["score"],
                "duration": duration,
                "include_recording": True,
                "sub_label": sub_label,
                "pre_capture": pre_capture,
                "draw": {},
            },
        )

    def get_event(self, event_id: str):
        return self.request("GET", f"/api/events/{event_id}")

    def set_description(self, event_id: str, description: str):
        return self.request("POST", f"/api/events/{event_id}/description", {"description": description})

    def find_by_sub_label(self, camera: str, sub_label: str):
        status, events = self.request("GET", f"/api/events?cameras={camera}&limit=500")
        if status != 200:
            return None
        for event in events:
            if event.get("sub_label") == sub_label:
                return event
        return None


def description_for(candidate: dict, key: str, emit_mode: str):
    replay = f"replay={candidate['replay_id']}; " if candidate.get("replay_id") else ""
    rule_window = (
        f"rule_window={candidate['rule_window_start']:.3f}-{candidate['rule_window_end']:.3f}; "
        if "rule_window_start" in candidate and "rule_window_end" in candidate
        else ""
    )
    return (
        f"external_candidate_key={key}; candidate_id={candidate['candidate_id']}; "
        f"rule={candidate['rule_id']}@{candidate['rule_version']}; zone={candidate['zone_id']}@{candidate['zone_version']}; "
        f"run={candidate['run_id']}; {replay}loop={candidate['loop_index']}; track={candidate['track_id']}; "
        f"source_video_time={candidate['source_video_time']:.3f}; condition_met_video_time={candidate['condition_met_video_time']:.3f}; "
        f"evidence_window={candidate['evidence_start']:.3f}-{candidate['evidence_end']:.3f}; {rule_window}"
        f"source_video_sha256={candidate['source_video_sha256']}; score={candidate.get('score')} "
        f"({candidate.get('score_type', 'unspecified')}); emit_mode={emit_mode}; "
        f"review_status=pending_review"
    )


def submit_candidate(
    ledger: Ledger,
    client: PlatformClient,
    candidate: dict,
    duration: int,
    pre_capture: int = 2,
    emit_epoch: float | None = None,
    wait_clip: bool = True,
    wait_description: bool = True,
    emit_mode: str | None = None,
    poll_seconds: float = 1.0,
    max_wait: float = 90.0,
):
    claim = ledger.claim(candidate)
    key = claim.key
    if claim.state == "duplicate":
        return {"state": "duplicate", "key": key, "event_id": claim.row.get("event_id")}
    if claim.state == "in_progress":
        return {"state": "in_progress", "key": key, "updated_at": claim.row.get("updated_at")}
    if claim.state == "needs_reconcile":
        existing = reconcile(ledger, client, candidate, key)
        if existing:
            return {"state": "reconciled", "key": key, "event_id": existing.get("id")}
        # No platform event found; the old claim is stale/unknown, so this
        # process may retry once without creating a second concurrent claim.

    sub_label = sub_label_for(key)
    if emit_epoch is not None:
        while time.time() < emit_epoch:
            time.sleep(min(1.0, emit_epoch - time.time()))
    emit_time = time.time()
    if emit_mode is None:
        emit_mode = "online" if emit_epoch else "offline_injection"
    result = {
        "state": "claimed",
        "key": key,
        "sub_label": sub_label,
        "candidate": candidate,
        "emit_epoch": emit_time,
        "condition_met_epoch": emit_epoch,
        "emit_minus_condition_seconds": (emit_time - emit_epoch) if emit_epoch else None,
        "emit_mode": emit_mode,
        "description": description_for(candidate, key, emit_mode),
    }
    try:
        status, response = client.create_event(candidate, duration, pre_capture, sub_label, result["description"])
        ledger.mark_writing_attempt(key, status, response)
    except Exception as error:  # timeout/connection is explicitly uncertain
        ledger.mark_unknown(key, f"{type(error).__name__}: {error}")
        existing = reconcile(ledger, client, candidate, key)
        result.update({"state": "unknown", "error": str(error), "reconciled_event_id": existing.get("id") if existing else None})
        return result
    if status != 200 or not response.get("success"):
        ledger.mark_unknown(key, f"HTTP {status}: {response}")
        existing = reconcile(ledger, client, candidate, key)
        result.update({"state": "failed", "http_status": status, "response": response, "reconciled_event_id": existing.get("id") if existing else None})
        return result
    event_id = response["event_id"]
    ledger.mark_created(key, event_id, response)
    result.update({"state": "created", "event_id": event_id, "create_response": response})
    description = result["description"] + f"; event_id={event_id}"
    if wait_description:
        description_set = False
        for _ in range(20):
            event_status, _ = client.get_event(event_id)
            if event_status == 200:
                description_status, description_body = client.set_description(event_id, description)
                if description_status == 200:
                    description_set = True
                    result["description_write"] = {"status": description_status, "body": description_body}
                    break
            time.sleep(0.5)
        if not description_set:
            result["description_write"] = {"status": None, "error": "event not ready for description"}
    else:
        result["description_write"] = {"status": None, "deferred": True}
    if not wait_clip:
        return result
    deadline = time.time() + max_wait
    event = None
    while time.time() < deadline:
        status, event = client.get_event(event_id)
        if status == 200 and event.get("end_time") is not None:
            break
        time.sleep(poll_seconds)
    if event:
        ledger.mark_completed(key, event)
        result.update({"state": "completed", "event": event})
    return result


def reconcile(ledger: Ledger, client: PlatformClient, candidate: dict, key: str):
    sub_label = sub_label_for(key)
    event = client.find_by_sub_label(candidate["camera"], sub_label)
    if event:
        ledger.mark_created(key, event["id"], {"reconciled": True})
        ledger.mark_completed(key, event)
        return event
    return None
