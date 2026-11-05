#!/usr/bin/env python3
"""A1: audit whether videopipe-fastapi is a deployable platform snapshot.

This is a source/asset audit, not a deployment.  It records the repository
commit, the files that are actually present, the files the README says are
required but are absent, and the local container/runtime preconditions.  No
missing file is inferred from documentation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REPO = PROJECT_DIR / "upstream" / "videopipe-fastapi"
DEFAULT_OUTPUT = PROJECT_DIR / "data" / "a1" / "videopipe_source_audit.json"

REQUIRED_ARTIFACTS = {
    "fastapi_entrypoint": ["main.py", "app.py", "backend/main.py", "backend/app.py"],
    "python_requirements": ["requirements.txt", "pyproject.toml", "backend/requirements.txt"],
    "vue_frontend": ["web/package.json", "frontend/package.json", "RuoYi-Vue3-FastAPI/package.json"],
    "kafka_compose": ["docker-compose.yml", "docker-compose.yaml", "kafka/docker-compose.yml"],
    "minio_compose": ["minio/docker-compose.yml", "docker-compose.yml", "docker-compose.yaml"],
    "video_pipeline_source": [
        "module_video",
        "pipeline",
        "videopipe",
        "backend/module_video",
        "src/pipeline",
    ],
    "model_weights": ["models", "weights", "checkpoints"],
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(repo: Path) -> dict:
    if not (repo / ".git").exists():
        return {"available": False}
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, timeout=15
        ).strip()
        subject = subprocess.check_output(
            ["git", "-C", str(repo), "log", "-1", "--pretty=%s"], text=True, timeout=15
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"], text=True, timeout=15
        ).strip()
        return {
            "available": True,
            "commit": commit,
            "subject": subject,
            "worktree_clean": status == "",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "error": str(exc)}


def command_probe(name: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {"available": False, "path": None}
    try:
        output = subprocess.check_output([name, "--version"], text=True, stderr=subprocess.STDOUT, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": True, "path": path, "version_error": str(exc)}
    return {"available": True, "path": path, "version": output.strip().splitlines()[0] if output.strip() else ""}


def sudo_probe() -> dict:
    sudo = shutil.which("sudo")
    if not sudo:
        return {"available": False, "passwordless": False}
    try:
        result = subprocess.run(
            ["sudo", "-n", "true"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        return {
            "available": True,
            "passwordless": result.returncode == 0,
            "stderr": result.stderr.strip(),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": True, "passwordless": False, "error": str(exc)}


def check_artifacts(repo: Path) -> dict:
    result = {}
    for key, candidates in REQUIRED_ARTIFACTS.items():
        found = []
        for relative in candidates:
            path = repo / relative
            if path.exists():
                found.append(
                    {
                        "path": relative,
                        "is_dir": path.is_dir(),
                        "bytes": path.stat().st_size if path.is_file() else None,
                    }
                )
        result[key] = {"present": bool(found), "found": found, "candidates_checked": candidates}
    return result


def count_files(repo: Path) -> dict:
    files = [path for path in repo.rglob("*") if path.is_file() and ".git" not in path.parts]
    by_suffix: dict[str, int] = {}
    for path in files:
        suffix = path.suffix.lower() or "<none>"
        by_suffix[suffix] = by_suffix.get(suffix, 0) + 1
    return {
        "tracked_or_working_files_excluding_git": len(files),
        "by_suffix": dict(sorted(by_suffix.items(), key=lambda item: (-item[1], item[0]))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="A1 videopipe-fastapi source audit")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    repo = args.repo.resolve()
    if not repo.is_dir():
        raise SystemExit(f"repository not found: {repo}")

    readme = repo / "README.md"
    artifacts = check_artifacts(repo)
    missing = [key for key, value in artifacts.items() if not value["present"]]

    compose_files = sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if "compose" in p.name.lower())
    sql_files = sorted(str(p.relative_to(repo)) for p in repo.rglob("*.sql"))

    audit = {
        "schema": "a1-videopipe-source-audit-v1",
        "audited_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": str(repo),
        "git": git_commit(repo),
        "readme": {
            "path": "README.md",
            "sha256": sha256_file(readme) if readme.is_file() else None,
            "states_ai_generated_unfinished": "AI写的有问题还没改完" in readme.read_text(encoding="utf-8", errors="ignore"),
            "declared_stack": [
                "FastAPI", "Vue3", "PostgreSQL", "Milvus", "Kafka", "MinIO", "YOLOv8", "VideoPipe"
            ],
        },
        "artifacts": artifacts,
        "missing_required_artifacts": missing,
        "compose_files": compose_files,
        "sql_files": sql_files,
        "file_inventory": count_files(repo),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "docker": command_probe("docker"),
            "docker_compose": command_probe("docker-compose"),
            "podman": command_probe("podman"),
            "ffmpeg": command_probe("ffmpeg"),
            "sudo": sudo_probe(),
        },
        "deployment_verdict": {
            "deployable": False,
            "reason": (
                "The snapshot contains only an alarm module fragment, a PostgreSQL dump/compose file, "
                "a Milvus compose file, a user manual and diagnostic notes. It has no FastAPI entrypoint, "
                "no Python requirements, no Vue frontend, no video pipeline source, no Kafka/MinIO compose "
                "file and no model weights. The README itself says the AI-generated code is unfinished."
            ),
            "can_accept_a1_with_this_snapshot": False,
            "next_action": (
                "Obtain the complete deployment package or another runnable mature platform; do not treat "
                "component-only startup as A1 completion."
            ),
        },
        "limitations": [
            "This audit checks file presence, not runtime correctness of the present alarm module.",
            "The Baidu Netdisk package referenced by README was not downloaded; access requires credentials.",
            "No Docker daemon is available, so even the present compose files were not started.",
        ],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "git_commit": audit["git"].get("commit"),
        "missing_required_artifacts": missing,
        "docker": audit["environment"]["docker"],
        "sudo": audit["environment"]["sudo"],
        "deployable": audit["deployment_verdict"]["deployable"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
