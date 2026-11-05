#!/usr/bin/env python3
"""Local-only server for the E00-D / E04 public-sample demo.

Serves the static demo UIs, the untouched original video, the derived
browser-compatible replay, and the run preview image. Byte-range requests are
implemented so the browser can seek both videos reliably.

Usage:
    python3 demo/serve.py
    python3 demo/serve.py --port 8765
"""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import os
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

DEMO_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DEMO_DIR.parent
DEFAULT_PORT = 8765
DEFAULT_RUN_ID = "e00-public-demo-005"
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
REVIEW_CANDIDATES = DEMO_DIR / "data" / "review_candidates.json"
REVIEW_LEDGER = PROJECT_DIR / "data" / "a2" / "adapter" / "ledger.sqlite"
REVIEW_LOG = PROJECT_DIR / "reports" / "review" / "reviews.jsonl"
UNIFIED_PLATFORM = DEMO_DIR / "data" / "unified_platform.json"
COVERAGE_PLATFORM = DEMO_DIR / "data" / "unified_platform_coverage.json"

sys.path.insert(0, str(PROJECT_DIR / "src"))
try:
    from st_memory.a2_adapter import Ledger
    from st_memory.unified_review_guard import validate_legacy_review, validate_unified_review
except Exception:  # pragma: no cover - the review page reports the missing dependency
    Ledger = None
    validate_legacy_review = None
    validate_unified_review = None


def find_run_dir() -> Path:
    data_path = DEMO_DIR / "data" / "demo.json"
    if data_path.is_file():
        try:
            with data_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            result_dir = data.get("run", {}).get("result_dir")
            if result_dir:
                candidate = PROJECT_DIR / result_dir
                if candidate.is_dir():
                    return candidate
        except (OSError, json.JSONDecodeError):
            pass
    return PROJECT_DIR / "runs" / DEFAULT_RUN_ID


RUN_DIR = find_run_dir()

MEDIA_ROUTES = {
    "/media/original.mp4": PROJECT_DIR / "data" / "raw" / "people-walking.mp4",
    "/media/analysis_replay.mp4": DEMO_DIR / "media" / "analysis_replay.mp4",
    "/media/preview.jpg": RUN_DIR / "preview.jpg",
}

REQUIRED_FILES = {
    "演示数据": DEMO_DIR / "data" / "demo.json",
    "原视频": MEDIA_ROUTES["/media/original.mp4"],
    "分析回放（浏览器兼容派生文件）": MEDIA_ROUTES["/media/analysis_replay.mp4"],
    "预览图": MEDIA_ROUTES["/media/preview.jpg"],
}


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "E00D-E04Demo/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle(send_body=False)

    def do_GET(self) -> None:  # noqa: N802
        self._handle(send_body=True)

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        if path == "/api/review/submit":
            self._submit_review()
            return
        self._send_error(HTTPStatus.NOT_FOUND, "未找到请求的接口。")

    def _handle(self, send_body: bool) -> None:
        path = unquote(urlsplit(self.path).path)

        if path == "/healthz":
            self._send_health(send_body)
            return
        if path == "/api/a3/readiness":
            readiness = PROJECT_DIR / "reports" / "A3接收就绪检查.json"
            if not readiness.is_file():
                self._send_json({"status":"not_checked","a3_completed":False,"gaps":["尚未执行现场接收检查"],"cameras":{},"effective_camera_hours":0})
            else:
                self._send_json(json.loads(readiness.read_text(encoding="utf-8")))
            return
        if path.startswith("/api/review/") or path == "/api/unified/candidates" or path == "/api/unified/platform" or path.startswith("/api/unified/clip/") or path.startswith("/api/unified/alignment/"):
            self._handle_review_api(path)
            return

        file_path = self._resolve(path)
        if file_path is None:
            self._send_error(HTTPStatus.NOT_FOUND, "未找到请求的文件。")
            return
        if not file_path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, f"文件缺失：{file_path}")
            return

        self._send_file(file_path, send_body)

    def _resolve(self, path: str) -> Path | None:
        if path in ("", "/"):
            return DEMO_DIR / "index.html"
        if path in MEDIA_ROUTES:
            return MEDIA_ROUTES[path]

        relative = path.lstrip("/")
        candidate = (DEMO_DIR / relative).resolve()
        try:
            candidate.relative_to(DEMO_DIR.resolve())
        except ValueError:
            return None
        return candidate

    def _review_candidates(self) -> list[dict]:
        """Legacy three-case review candidates, unchanged for the old page."""
        if not REVIEW_CANDIDATES.is_file():
            return []
        candidates = json.loads(REVIEW_CANDIDATES.read_text(encoding="utf-8"))
        if Ledger is None or not REVIEW_LEDGER.is_file():
            return candidates
        ledger = Ledger(REVIEW_LEDGER)
        try:
            for candidate in candidates:
                row = ledger.get(candidate["key"])
                if row:
                    candidate["review_status"] = row.get("review_status", "pending_review")
                    candidate["platform_status"] = row.get("status")
                    candidate["reviews"] = ledger.reviews(candidate["key"])
                else:
                    candidate["review_status"] = candidate.get("status", "pending_review")
                    candidate["reviews"] = []
        finally:
            ledger.close()
        return candidates

    def _unified_payload(self) -> dict | None:
        profile = parse_qs(urlsplit(getattr(self, "path", "")).query).get("profile", ["baseline"])[0]
        if profile not in ("baseline", "coverage"):
            return None
        data_path = COVERAGE_PLATFORM if profile == "coverage" else UNIFIED_PLATFORM
        if not data_path.is_file():
            return None
        try:
            return json.loads(data_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _unified_payload_current(self) -> dict | None:
        """Return the page dataset with live ledger review state applied."""
        payload = self._unified_payload()
        if payload is None:
            return None
        payload = copy.deepcopy(payload)
        if Ledger is None or not REVIEW_LEDGER.is_file():
            return payload
        ledger = Ledger(REVIEW_LEDGER)
        try:
            pending = 0
            for item in payload.get("candidates") or []:
                key = item.get("ledger_key")
                row = ledger.get(key) if key else None
                if row:
                    item["review_status"] = row.get("review_status", "pending_review")
                    item["reviews"] = ledger.reviews(key)
                    if row.get("status"):
                        item["submit_state"] = row.get("status")
                if item.get("review_status", "pending_review") == "pending_review":
                    pending += 1
            summary = payload.get("summary")
            if isinstance(summary, dict):
                summary["pending_review"] = pending
                summary["reviews_present"] = sum(1 for item in payload.get("candidates") or [] if item.get("reviews"))
        finally:
            ledger.close()
        return payload

    def _unified_round_dir(self) -> Path | None:
        payload = self._unified_payload()
        if not payload:
            return None
        round_id = str(payload.get("round_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", round_id):
            return None
        round_dir = (PROJECT_DIR / "reports" / round_id).resolve()
        try:
            round_dir.relative_to((PROJECT_DIR / "reports").resolve())
        except ValueError:
            return None
        return round_dir

    def _unified_candidates(self) -> list[dict]:
        payload = self._unified_payload()
        if not payload:
            return []
        round_dir = self._unified_round_dir()
        candidates = []
        for item in payload.get("candidates") or []:
            candidate_id = item.get("candidate_id")
            clip_path = round_dir / "clips" / f"{candidate_id}.mp4" if round_dir else None
            candidates.append({
                "candidate_id": candidate_id,
                "key": item.get("ledger_key"),
                "kind": item.get("kind"),
                "title": f"{item.get('kind_label') or item.get('kind')} · {item.get('condition_met_video_time')} 秒",
                "rule": item.get("rule_id"),
                "source_time": item.get("source_video_time"),
                "condition_time": item.get("condition_met_video_time"),
                "event_id": item.get("event_id"),
                "event_url": item.get("platform_event_url"),
                "clip_url": item.get("clip_url") or f"/api/unified/clip/{candidate_id}",
                "evidence": (
                    str((Path("reports") / payload["round_id"] / "clips" / f"{candidate_id}.mp4"))
                    if round_dir and clip_path and clip_path.is_file()
                    else None
                ),
                "status": item.get("review_status", "pending_review"),
                "note": "同源平台回放注入候选；复核默认 pending，由实际操作者填写。",
                "platform_status": item.get("submit_state"),
                "evidence_status": (item.get("evidence") or {}).get("status"),
                "reviews": item.get("reviews") or [],
                "test_only": bool(payload.get("test_only") or item.get("test_only")),
            })
        if Ledger is None or not REVIEW_LEDGER.is_file():
            return candidates
        ledger = Ledger(REVIEW_LEDGER)
        try:
            for candidate in candidates:
                key = candidate.get("key")
                row = ledger.get(key) if key else None
                if row:
                    candidate["review_status"] = row.get("review_status", "pending_review")
                    candidate["platform_status"] = row.get("status")
                    candidate["reviews"] = ledger.reviews(key)
                else:
                    candidate["review_status"] = candidate.get("status", "pending_review")
                    candidate["reviews"] = []
        finally:
            ledger.close()
        return candidates

    def _all_review_candidates(self) -> list[dict]:
        return self._review_candidates() + self._unified_candidates()

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_review_api(self, path: str) -> None:
        if path == "/api/review/candidates":
            self._send_json({"candidates": self._review_candidates()})
            return
        if path == "/api/unified/candidates":
            payload = self._unified_payload()
            self._send_json({
                "round": payload.get("round_id") if payload else None,
                "replay": payload.get("replay") if payload else None,
                "candidates": self._unified_candidates(),
            })
            return
        if path == "/api/unified/platform":
            payload = self._unified_payload_current()
            if payload is None:
                self._send_json({"available": False, "message": "同源平台回放数据缺失。"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(payload)
            return
        if path.startswith("/api/unified/clip/"):
            candidate_id = path[len("/api/unified/clip/"):]
            if not re.fullmatch(r"[0-9a-f]{16}", candidate_id):
                self._send_error(HTTPStatus.NOT_FOUND, "候选标识无效。")
                return
            round_dir = self._unified_round_dir()
            clip = round_dir / "clips" / f"{candidate_id}.mp4" if round_dir else None
            if clip is None or not clip.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "录像文件缺失。")
                return
            self._send_file(clip, True)
            return
        if path.startswith("/api/unified/alignment/"):
            filename = path[len("/api/unified/alignment/"):]
            if not re.fullmatch(r"[0-9a-f]{16}-(window|window-v2|evidence)\.(png|jpg)", filename):
                self._send_error(HTTPStatus.NOT_FOUND, "证据文件名无效。")
                return
            round_dir = self._unified_round_dir()
            file_path = round_dir / "alignment" / filename if round_dir else None
            if file_path is None or not file_path.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "证据文件缺失。")
                return
            self._send_file(file_path, True)
            return
        if path == "/api/review/history":
            query = urlsplit(self.path).query
            candidate_id = None
            for part in query.split("&"):
                key, _, value = part.partition("=")
                if key == "candidate_id":
                    candidate_id = unquote(value)
            candidates = self._all_review_candidates()
            candidate = next((item for item in candidates if item["candidate_id"] == candidate_id), None)
            if candidate is None:
                self._send_error(HTTPStatus.NOT_FOUND, "候选不存在。")
                return
            self._send_json({"candidate": candidate, "reviews": candidate.get("reviews", [])})
            return
        self._send_error(HTTPStatus.NOT_FOUND, "未找到请求的接口。")

    def _submit_review(self) -> None:
        if Ledger is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "复核 ledger 不可用。")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:
            self._send_json({"success": False, "message": f"请求解析失败：{exc}"}, HTTPStatus.BAD_REQUEST)
            return
        candidates = self._all_review_candidates()
        candidate = next((item for item in candidates if item["candidate_id"] == payload.get("candidate_id")), None)
        if candidate is None:
            self._send_json({"success": False, "message": "候选不存在。"}, HTTPStatus.NOT_FOUND)
            return
        if payload.get("key") and payload["key"] != candidate.get("key"):
            self._send_json({"success": False, "message": "候选键不匹配，拒绝写入。"}, HTTPStatus.BAD_REQUEST)
            return
        reviewer = str(payload.get("reviewer", "")).strip()
        decision = str(payload.get("decision", "")).strip()
        note = str(payload.get("note", "")).strip()
        if decision not in ("confirmed", "rejected", "uncertain"):
            self._send_json({"success": False, "message": "复核结论必须是 confirmed / rejected / uncertain。"}, HTTPStatus.BAD_REQUEST)
            return
        evidence_value = candidate.get("evidence")
        if not evidence_value:
            self._send_json({"success": False, "message": "证据文件缺失，不能提交复核。"}, HTTPStatus.BAD_REQUEST)
            return
        evidence = Path(evidence_value)
        if not evidence.is_absolute():
            evidence = PROJECT_DIR / evidence
        if not evidence.is_file():
            self._send_json({"success": False, "message": f"证据文件缺失：{evidence}"}, HTTPStatus.BAD_REQUEST)
            return
        unified_payload = self._unified_payload_current()
        unified_item = None
        if unified_payload:
            unified_item = next(
                (item for item in unified_payload.get("candidates") or [] if item.get("candidate_id") == candidate["candidate_id"]),
                None,
            )
        ledger = Ledger(REVIEW_LEDGER)
        try:
            ledger_row = ledger.get(candidate["key"])
            if ledger_row is None:
                self._send_json({"success": False, "message": "该候选尚未写入 ledger，不能伪造复核。"}, HTTPStatus.CONFLICT)
                return
            if unified_item is not None:
                guard = validate_unified_review(unified_payload, unified_item, ledger_row, evidence) if validate_unified_review else {"ok": False, "errors": ["guard_unavailable"], "checks": {}}
                if not guard["ok"]:
                    self._send_json({
                        "success": False,
                        "message": "服务端拒绝复核：时间对齐或证据状态未通过。" + ",".join(guard["errors"]),
                        "errors": guard["errors"],
                        "checks": guard["checks"],
                    }, HTTPStatus.CONFLICT)
                    return
            else:
                guard = validate_legacy_review(candidate, ledger_row, evidence) if validate_legacy_review else {"ok": False, "errors": ["guard_unavailable"]}
                if not guard["ok"]:
                    self._send_json({
                        "success": False,
                        "message": "服务端拒绝复核：旧案例证据状态未通过。" + ",".join(guard["errors"]),
                        "errors": guard["errors"],
                    }, HTTPStatus.CONFLICT)
                    return
            record = ledger.add_review(candidate["key"], reviewer, decision, note, evidence)
            candidate["review_status"] = decision
            candidate["reviews"] = ledger.reviews(candidate["key"])
        except KeyError:
            self._send_json({"success": False, "message": "该候选尚未写入 ledger，不能伪造复核。"}, HTTPStatus.CONFLICT)
            return
        except Exception as exc:
            self._send_json({"success": False, "message": f"复核保存失败：{exc}"}, HTTPStatus.BAD_REQUEST)
            return
        finally:
            ledger.close()
        REVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with REVIEW_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._send_json({"success": True, "record": record, "candidate": candidate})

    def _send_health(self, send_body: bool) -> None:
        files = {}
        ok = True
        for label, path in REQUIRED_FILES.items():
            exists = path.is_file()
            ok = ok and exists
            files[label] = {
                "path": str(path),
                "exists": exists,
                "bytes": path.stat().st_size if exists else None,
            }
        payload = {
            "ok": ok,
            "service": "E00-D / E04 demo",
            "run": RUN_DIR.name,
            "url": f"http://127.0.0.1:{self.server.server_address[1]}/",
            "files": files,
            "unified_platform": {
                "exists": UNIFIED_PLATFORM.is_file(),
                "path": str(UNIFIED_PLATFORM),
                "round": (self._unified_payload() or {}).get("round_id"),
                "candidates": len((self._unified_payload() or {}).get("candidates") or []),
            },
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        self.send_response(HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _send_file(self, path: Path, send_body: bool) -> None:
        try:
            size = path.stat().st_size
            handle = path.open("rb")
        except OSError as exc:
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"读取文件失败：{exc}")
            return

        with handle:
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            if path.suffix == ".json":
                content_type = "application/json; charset=utf-8"
            elif path.suffix == ".js":
                content_type = "text/javascript; charset=utf-8"
            elif path.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            elif path.suffix == ".html":
                content_type = "text/html; charset=utf-8"
            elif path.suffix == ".svg":
                content_type = "image/svg+xml"

            range_header = self.headers.get("Range")
            start = 0
            end = size - 1
            status = HTTPStatus.OK
            if range_header:
                match = RANGE_RE.match(range_header.strip())
                if not match:
                    self._send_range_error(size)
                    return
                raw_start, raw_end = match.groups()
                if not raw_start and not raw_end:
                    self._send_range_error(size)
                    return
                if raw_start:
                    start = int(raw_start)
                    if raw_end:
                        end = min(int(raw_end), size - 1)
                else:
                    suffix = int(raw_end)
                    start = max(0, size - suffix)
                    end = size - 1
                if start > end or start >= size:
                    self._send_range_error(size)
                    return
                status = HTTPStatus.PARTIAL_CONTENT

            length = max(0, end - start + 1)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()

            if not send_body or self.command == "HEAD":
                return

            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def _send_range_error(self, size: int) -> None:
        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        body = (message + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def print_status(url: str) -> None:
    print()
    print("E00-D / E04 公开样例演示")
    print(f"  访问地址: {url}")
    print(f"  运行结果: {RUN_DIR}")
    print("  停止方式: 按 Ctrl+C")
    print()
    ok = True
    for label, path in REQUIRED_FILES.items():
        exists = path.is_file()
        ok = ok and exists
        state = f"{human_size(path.stat().st_size)}" if exists else "缺失"
        print(f"  [{'OK' if exists else '缺失'}] {label}: {state}")
        if not exists:
            print(f"         期望路径: {path}")
    if not ok:
        print()
        print("  警告：有文件缺失。页面会显示缺失提示，请先补齐后再演示。")
    print()


def main() -> int:
    global REVIEW_LEDGER, UNIFIED_PLATFORM, REVIEW_LOG, COVERAGE_PLATFORM
    parser = argparse.ArgumentParser(description="启动 E00-D 本地演示服务")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="本地端口，默认 8765")
    parser.add_argument("--bind", default="127.0.0.1", help="监听地址，默认仅本机")
    parser.add_argument("--review-ledger", type=Path, default=REVIEW_LEDGER, help="统一复核用的 SQLite ledger")
    parser.add_argument("--review-log", type=Path, default=REVIEW_LOG, help="复核 JSONL 日志")
    parser.add_argument("--unified-platform-data", type=Path, default=UNIFIED_PLATFORM, help="同源平台回放数据 JSON")
    parser.add_argument("--coverage-platform-data", type=Path, default=COVERAGE_PLATFORM, help="增强运行平台数据 JSON")
    args = parser.parse_args()
    REVIEW_LEDGER = args.review_ledger
    REVIEW_LOG = args.review_log
    UNIFIED_PLATFORM = args.unified_platform_data
    COVERAGE_PLATFORM = args.coverage_platform_data

    mimetypes.add_type("video/mp4", ".mp4")
    mimetypes.add_type("image/jpeg", ".jpg")

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    try:
        httpd = Server((args.bind, args.port), DemoHandler)
    except OSError as exc:
        print(f"无法启动服务：{exc}", file=sys.stderr)
        print(f"端口 {args.port} 可能已被占用，可改用：python3 demo/serve.py --port 8766", file=sys.stderr)
        return 1

    url = f"http://{args.bind}:{httpd.server_address[1]}/"
    print_status(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止演示服务。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
