from __future__ import annotations

import hashlib
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Iterator
from itertools import islice
from pathlib import Path
from typing import Any

from .video_reader import FFmpegError, probe

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".mts", ".m2ts", ".flv"}


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def source_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:24]


def file_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def validate_directories(input_dir: Path, output_dir: Path) -> None:
    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError("输入目录不存在或不是目录")
    input_resolved, output_resolved = input_dir.resolve(), output_dir.resolve()
    if input_resolved == output_resolved:
        raise ValueError("输入目录和输出目录不能相同")
    try:
        output_resolved.relative_to(input_resolved)
    except ValueError:
        return
    raise ValueError("输出目录不能位于输入目录内部，否则会被再次扫描")


def video_paths(input_dir: Path, output_dir: Path) -> list[Path]:
    validate_directories(input_dir, output_dir)
    return sorted(
        (p for p in input_dir.rglob("*") if p.is_file() and p.suffix.casefold() in VIDEO_EXTENSIONS),
        key=lambda p: natural_key(p.relative_to(input_dir).as_posix()),
    )


def scan_batches(input_dir: Path, paths: list[Path], batch_size: int = 10) -> Iterator[list[dict[str, Any]]]:
    def scan_one(path: Path) -> dict[str, Any]:
        relative = path.relative_to(input_dir).as_posix()
        record: dict[str, Any] = {"source_id": source_id(relative), "source_path": relative, "scan_error": None}
        try:
            record["source_hash"] = file_hash(path)
            record.update(probe(path))
        except (FFmpegError, OSError, ValueError) as exc:
            record.setdefault("source_hash", "")
            record.update({"width": 0, "height": 0, "fps": 0, "fps_info": "", "duration": 0,
                           "frame_count": 0, "codec": "", "pix_fmt": "", "has_audio": False,
                           "scan_error": str(exc)})
        return record

    # Keep only a small number of in-flight probes so cancelling a scan does
    # not launch work for every remaining file.
    workers = min(8, max(1, len(paths)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        paths_iter = iter(paths)
        pending = {executor.submit(scan_one, path) for path in islice(paths_iter, workers * 2)}
        batch: list[dict[str, Any]] = []
        while pending:
            future = next(as_completed(pending))
            pending.remove(future)
            batch.append(future.result())
            next_path = next(paths_iter, None)
            if next_path is not None:
                pending.add(executor.submit(scan_one, next_path))
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


def scan(input_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    paths = video_paths(input_dir, output_dir)
    return sorted((record for batch in scan_batches(input_dir, paths) for record in batch),
                  key=lambda item: natural_key(item["source_path"]))
