#!/usr/bin/env python3
"""Fetch exactly one MOT17 training sequence from the official archive.

MOTChallenge publishes one large MOT17.zip.  Downloading the whole archive is
unnecessary for a single-sequence E02 smoke baseline, so this script reads the
official ZIP central directory and downloads only the selected sequence's
image/GT/seqinfo entries with HTTP Range requests.  It never uses public
detections or GT boxes as system predictions.

The script records the official archive URL, size, ETag/Last-Modified, the
selected entry list, per-file SHA256 values and the extracted GT/seqinfo
copies.  The official MOT17Labels.zip is downloaded separately for an
independent GT checksum/source cross-check.

Usage:
    python3 scripts/e02_fetch_mot17_sequence.py \
      --sequence MOT17-02-FRCNN \
      --output data/e02/raw/MOT17-02-FRCNN
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://motchallenge.net/data/MOT17.zip"
DEFAULT_LABELS_URL = "https://motchallenge.net/data/MOT17Labels.zip"
DEFAULT_SEQUENCE = "MOT17-02-FRCNN"


def human(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def http_head(url: str) -> dict:
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=60) as response:
        return {
            "status": response.status,
            "url": response.geturl(),
            "content_length": int(response.headers.get("Content-Length", "0")),
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "accept_ranges": response.headers.get("Accept-Ranges"),
            "content_type": response.headers.get("Content-Type"),
        }


class RemoteRangeFile(io.RawIOBase):
    """Small read-only seekable HTTP Range file used by zipfile."""

    def __init__(self, url: str, size: int, chunk_size: int = 512 * 1024):
        super().__init__()
        self.url = url
        self.size = int(size)
        self.chunk_size = int(chunk_size)
        self.pos = 0
        self.cache_start = 0
        self.cache = b""
        self.requests = 0
        self.bytes_fetched = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            new_pos = int(offset)
        elif whence == os.SEEK_CUR:
            new_pos = self.pos + int(offset)
        elif whence == os.SEEK_END:
            new_pos = self.size + int(offset)
        else:
            raise ValueError(f"invalid whence: {whence}")
        if new_pos < 0:
            raise ValueError("negative seek position")
        self.pos = min(new_pos, self.size)
        return self.pos

    def _fetch(self, start: int, length: int) -> bytes:
        end = min(self.size - 1, start + max(length, self.chunk_size) - 1)
        request = urllib.request.Request(
            self.url, headers={"Range": f"bytes={start}-{end}"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
            self.requests += 1
            self.bytes_fetched += len(data)
            if response.status == 200:
                # Server ignored Range; keep only the requested slice.
                data = data[start : start + length]
        self.cache_start = start
        self.cache = data
        return data

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self.size - self.pos
        size = min(int(size), self.size - self.pos)
        if size <= 0:
            return b""
        start = self.pos
        end = start + size
        cache_end = self.cache_start + len(self.cache)
        if self.cache_start <= start and end <= cache_end:
            data = self.cache[start - self.cache_start : end - self.cache_start]
        else:
            data = self._fetch(start, size)
            # _fetch deliberately reads a larger cached chunk.  A file-like
            # read must return at most the requested number of bytes.
            data = data[:size]
        self.pos += len(data)
        return data

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def safe_extract_member(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, output: Path, relative_name: str
) -> None:
    target = (output / relative_name).resolve()
    try:
        target.relative_to(output.resolve())
    except ValueError as exc:
        raise RuntimeError(f"unsafe ZIP member path: {info.filename}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(info, "r") as source, target.open("wb") as destination:
        shutil.copyfileobj(source, destination, length=1024 * 1024)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_metadata(url: str) -> tuple[dict, list[zipfile.ZipInfo], zipfile.ZipFile, RemoteRangeFile]:
    head = http_head(url)
    remote = RemoteRangeFile(url, head["content_length"])
    zf = zipfile.ZipFile(remote, "r")
    return head, zf.infolist(), zf, remote


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch one MOT17 sequence from the official ZIP")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--labels-url", default=DEFAULT_LABELS_URL)
    parser.add_argument("--sequence", default=DEFAULT_SEQUENCE)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_DIR / "data" / "e02" / "raw" / DEFAULT_SEQUENCE,
    )
    parser.add_argument(
        "--labels-output",
        type=Path,
        default=PROJECT_DIR / "data" / "e02" / "source" / "MOT17Labels.zip",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--chunk-kb", type=int, default=512)
    args = parser.parse_args()

    output = args.output if args.output.is_absolute() else PROJECT_DIR / args.output
    labels_output = (
        args.labels_output
        if args.labels_output.is_absolute()
        else PROJECT_DIR / args.labels_output
    )
    output.mkdir(parents=True, exist_ok=True)
    labels_output.parent.mkdir(parents=True, exist_ok=True)

    print(f"official archive: {args.url}", flush=True)
    head = http_head(args.url)
    print(json.dumps(head, ensure_ascii=False, indent=2), flush=True)
    if head["content_length"] <= 0:
        raise SystemExit("official archive has no Content-Length")

    # Read only the ZIP central directory first, then download selected entries.
    prefix = f"MOT17/train/{args.sequence}/"
    metadata_remote = RemoteRangeFile(
        args.url, head["content_length"], chunk_size=args.chunk_kb * 1024
    )
    with zipfile.ZipFile(metadata_remote, "r") as zf:
        all_infos = zf.infolist()
        selected = [
            info
            for info in all_infos
            if not info.is_dir()
            and info.filename.startswith(prefix)
            and (
                "/img1/" in info.filename
                or info.filename.endswith("/gt/gt.txt")
                or info.filename.endswith("/seqinfo.ini")
            )
        ]
    if not selected:
        raise SystemExit(f"no entries found for {prefix}")
    images = [info for info in selected if "/img1/" in info.filename]
    gt = [info for info in selected if info.filename.endswith("/gt/gt.txt")]
    if not images or not gt:
        raise SystemExit("selected sequence is missing img1 or gt/gt.txt")
    print(
        f"selected {len(images)} images, {len(gt)} gt, "
        f"{len(selected) - len(images) - len(gt)} seqinfo entries",
        flush=True,
    )
    print(
        "compressed bytes selected: "
        + human(sum(info.compress_size for info in selected)),
        flush=True,
    )

    started = time.monotonic()
    # Each worker has its own Range reader and ZIP handle; this parallelizes
    # image transfers while preserving official ZIP bytes and CRC checks.
    chunks = [selected[i:: args.workers] for i in range(args.workers)]
    stats = {"requests": 0, "bytes": 0, "files": 0}

    def worker(items: list[zipfile.ZipInfo]) -> dict:
        remote = RemoteRangeFile(
            args.url, head["content_length"], chunk_size=args.chunk_kb * 1024
        )
        with zipfile.ZipFile(remote, "r") as zf:
            for info in items:
                relative = info.filename[len(prefix) :]
                safe_extract_member(zf, info, output, relative)
        return {
            "requests": remote.requests,
            "bytes": remote.bytes_fetched,
            "files": len(items),
        }

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(worker, chunk) for chunk in chunks if chunk]
        for future in as_completed(futures):
            result = future.result()
            for key in stats:
                stats[key] += result[key]
            print(
                f"  extracted {stats['files']}/{len(selected)} entries, "
                f"fetched {human(stats['bytes'])} via {stats['requests']} range requests",
                flush=True,
            )

    # Download official labels archive for an independent checksum.
    if not labels_output.is_file():
        print(f"downloading official labels archive: {args.labels_url}", flush=True)
        urllib.request.urlretrieve(args.labels_url, labels_output)
    labels_sha = sha256_file(labels_output)

    # Extract GT/seqinfo from the independent labels archive and compare.
    with zipfile.ZipFile(labels_output, "r") as labels_zip:
        gt_name = f"train/{args.sequence}/gt/gt.txt"
        ini_name = f"train/{args.sequence}/seqinfo.ini"
        if gt_name not in labels_zip.namelist() or ini_name not in labels_zip.namelist():
            raise SystemExit("official labels archive lacks selected GT/seqinfo")
        labels_dir = output.parent / "labels-crosscheck" / args.sequence
        labels_dir.mkdir(parents=True, exist_ok=True)
        for name in (gt_name, ini_name):
            target = labels_dir / Path(name).name
            target.write_bytes(labels_zip.read(name))

    gt_path = output / "gt" / "gt.txt"
    ini_path = output / "seqinfo.ini"
    labels_gt = output.parent / "labels-crosscheck" / args.sequence / "gt.txt"
    labels_ini = output.parent / "labels-crosscheck" / args.sequence / "seqinfo.ini"
    if gt_path.read_bytes() != labels_gt.read_bytes():
        raise SystemExit("GT from MOT17.zip and MOT17Labels.zip differ")
    if ini_path.read_bytes() != labels_ini.read_bytes():
        raise SystemExit("seqinfo from MOT17.zip and MOT17Labels.zip differ")

    files = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": str(path.relative_to(output)),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    manifest = {
        "schema": "e02-mot17-sequence-source-v1",
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_url": args.url,
        "source_head": head,
        "labels_url": args.labels_url,
        "labels_sha256": labels_sha,
        "sequence": args.sequence,
        "official_sequence_index": "MOT17 training sequence; one scene, one detector variant (FRCNN); not three datasets",
        "selection_prefix": prefix,
        "selected_entries": [
            {
                "name": info.filename,
                "compress_size": info.compress_size,
                "file_size": info.file_size,
                "crc32": f"{info.CRC:08x}",
            }
            for info in selected
        ],
        "selected_compressed_bytes": sum(info.compress_size for info in selected),
        "range_fetch": stats,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "files": files,
        "file_count": len(files),
        "image_count": len(images),
        "gt_lines": sum(1 for line in gt_path.read_text().splitlines() if line.strip()),
        "gt_crosscheck": "identical to MOT17Labels.zip train/MOT17-02-FRCNN/gt/gt.txt",
        "seqinfo_crosscheck": "identical to MOT17Labels.zip train/MOT17-02-FRCNN/seqinfo.ini",
        "license": "CC BY-NC-SA 3.0 (official MOTChallenge page); third-party source conditions may also apply",
        "license_url": "https://creativecommons.org/licenses/by-nc-sa/3.0/",
        "usage_scope": "public benchmark development evaluation only; not field data; not a held-out test set; no redistribution",
        "download_method": "official MOT17.zip HTTP Range extraction of one sequence plus independent MOT17Labels.zip checksum",
    }
    manifest_path = output / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: manifest[k] for k in ("sequence", "image_count", "gt_lines", "file_count", "labels_sha256")}, ensure_ascii=False, indent=2))
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
