#!/usr/bin/env python3
"""Download, verify and restore the versioned public-demo handoff assets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def destination(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if parts.is_absolute() or ".." in parts.parts or "\\" in relative:
        raise ValueError(f"unsafe asset path: {relative}")
    target = root.joinpath(*parts.parts)
    target.resolve().relative_to(root.resolve())
    if target.is_symlink():
        raise ValueError(f"symlink asset destination: {relative}")
    return target


def verify_existing(path: Path, expected: dict) -> bool:
    if not path.exists():
        return False
    if not path.is_file() or path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
        raise ValueError(f"existing asset differs; preserved without overwriting: {path}")
    return True


def restore_archive(archive: Path, root: Path, entries: list[dict]) -> None:
    expected = {item["path"]: item for item in entries}
    for name, item in expected.items():
        verify_existing(destination(root, name), item)
    seen = set()
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise ValueError(f"unexpected archive entry: {member.name}")
            seen.add(member.name)
            item = expected[member.name]
            if member.size != item["bytes"]:
                raise ValueError(f"archive member size mismatch: {member.name}")
            target = destination(root, member.name)
            if verify_existing(target, item):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with source.extractfile(member) as incoming, tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=target.name + ".", suffix=".part", delete=False,
                ) as output:
                    temporary = Path(output.name)
                    shutil.copyfileobj(incoming, output, 1024 * 1024)
                if digest(temporary) != item["sha256"]:
                    raise ValueError(f"asset checksum mismatch: {member.name}")
                if target.exists():
                    verify_existing(target, item)
                else:
                    temporary.replace(target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    if seen != set(expected):
        raise ValueError(f"archive assets missing: {sorted(set(expected) - seen)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, help="Use already downloaded archives; no network")
    parser.add_argument("--groups", default="demo,baseline-evidence,coverage-evidence,continuation")
    parser.add_argument("--verify-only", action="store_true", help="Check restored files without downloading")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "handoff/manifest.json").read_text())
    names = {f"handoff-20261105-{group.strip()}.tar.gz" for group in args.groups.split(",")}
    archives = [a for a in manifest["archives"] if a["name"] in names]
    if names != {a["name"] for a in archives}:
        raise SystemExit("Unknown handoff group")
    entries = [item for item in manifest["files"] if item["archive"] in names]
    if not args.verify_only:
        cache = args.archive_dir or ROOT / ".cache/handoff" / manifest["tag"]
        cache.mkdir(parents=True, exist_ok=True)
        for item in archives:
            archive = cache / item["name"]
            if not archive.exists():
                if args.archive_dir:
                    raise FileNotFoundError(archive)
                url = f"https://github.com/{manifest['repository']}/releases/download/{manifest['tag']}/{item['name']}"
                partial = archive.with_name(archive.name + ".part")
                print(f"Downloading {item['name']} ({item['bytes']} bytes)", flush=True)
                with urllib.request.urlopen(url, timeout=120) as incoming, partial.open("wb") as output:
                    shutil.copyfileobj(incoming, output, 1024 * 1024)
                if not verify_existing(partial, item):
                    raise ValueError("download not found")
                partial.replace(archive)
            verify_existing(archive, item)
            print(f"Restoring {item['name']}", flush=True)
            restore_archive(archive, ROOT, [e for e in entries if e["archive"] == item["name"]])
        for alias in manifest.get("aliases", []):
            source = destination(ROOT, alias["source"])
            if not source.is_file():
                continue
            target = destination(ROOT, alias["path"])
            info = next(e for e in manifest["files"] if e["path"] == alias["source"])
            if verify_existing(target, info):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    missing = []
    for item in entries:
        if not verify_existing(destination(ROOT, item["path"]), item):
            missing.append(item["path"])
    if missing:
        raise SystemExit(f"Missing handoff assets: {missing}")
    for alias in manifest.get("aliases", []):
        if alias["source"] in {e["path"] for e in entries}:
            info = next(e for e in entries if e["path"] == alias["source"])
            if not verify_existing(destination(ROOT, alias["path"]), info):
                raise SystemExit(f"Missing handoff alias: {alias['path']}")
    print(f"Verified {len(entries)} assets; source commit {manifest['source_commit']}")
    print("Run python3 scripts/init_handoff_ledger.py, then python3 demo/serve.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
