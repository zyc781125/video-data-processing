from __future__ import annotations

import json
import os
import shutil
import subprocess
import re
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path
from typing import Any


class FFmpegError(RuntimeError):
    pass


def find_binary(name: str) -> str | None:
    return shutil.which(name)


def require_ffmpeg() -> tuple[str, str]:
    ffmpeg = find_binary("ffmpeg")
    ffprobe = find_binary("ffprobe")
    if not ffmpeg or not ffprobe:
        raise FFmpegError("未找到 ffmpeg 或 ffprobe。请安装 FFmpeg，并确保它们位于 PATH 中。")
    return ffmpeg, ffprobe


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise FFmpegError(str(exc)) from exc
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace")[-3000:]
        raise FFmpegError(detail or f"命令失败: {' '.join(args)}")
    return result


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fps(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    try:
        num, den = value.split("/", 1)
        return float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path) -> dict[str, Any]:
    _, ffprobe = require_ffmpeg()
    result = _run([
        ffprobe, "-v", "error", "-count_frames", "-show_streams", "-show_format",
        "-of", "json", os.fspath(path),
    ])
    try:
        data = json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"无法解析 ffprobe 输出: {exc}") from exc
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise FFmpegError("文件中没有可读取的视频流")
    frame_count = int(video.get("nb_read_frames") or video.get("nb_frames") or 0)
    fps_value = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0"
    duration = _number(video.get("duration"), _number(data.get("format", {}).get("duration")))
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": _fps(fps_value),
        "fps_info": fps_value,
        "duration": duration,
        "frame_count": frame_count,
        "codec": video.get("codec_name") or "",
        "pix_fmt": video.get("pix_fmt") or "",
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def extract_frame(path: Path, frame_index: int) -> tuple[bytes, dict[str, Any]]:
    """Decode one frame by ordinal. ffmpeg's select filter counts decoded frames."""
    ffmpeg, _ = require_ffmpeg()
    if frame_index < 0:
        raise FFmpegError("帧序号不能为负数")
    vf = f"select=eq(n\\,{frame_index})"
    result = _run([
        ffmpeg, "-v", "error", "-i", os.fspath(path), "-vf", vf,
        "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "pipe:1",
    ], check=False)
    if result.returncode or not result.stdout:
        raise FFmpegError("无法解码所请求的帧")
    return result.stdout, {"frame_index": frame_index}


def count_output_frames(path: Path) -> int:
    _, ffprobe = require_ffmpeg()
    result = _run([
        ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_entries", "stream=nb_read_frames", "-of", "json", os.fspath(path),
    ])
    data = json.loads(result.stdout.decode("utf-8"))
    streams = data.get("streams", [])
    return int(streams[0].get("nb_read_frames") or 0) if streams else 0


def frame_timestamp(path: Path, frame_index: int) -> float | None:
    """Return the decoded frame timestamp, including for VFR sources."""
    values = frame_timestamps(path)
    return values[frame_index] if 0 <= frame_index < len(values) else None


def frame_timestamps(path: Path) -> tuple[float, ...]:
    info = path.stat()
    return _cached_frame_timestamps(str(path.resolve()), info.st_mtime_ns, info.st_size)


@lru_cache(maxsize=16)
def _cached_frame_timestamps(path: str, mtime_ns: int, size: int) -> tuple[float, ...]:
    _, ffprobe = require_ffmpeg()
    result = _run([
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", os.fspath(path),
    ])
    values = []
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        match = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", line)
        if match:
            values.append(float(match.group(1)))
    if not values:
        raise FFmpegError("无法读取逐帧时间戳")
    return tuple(values)


def frame_at_timestamp(path: Path, timestamp: float) -> tuple[int, float]:
    if not 0 <= timestamp < float("inf"):
        raise ValueError("时间戳无效")
    values = frame_timestamps(path)
    index = max(0, min(len(values) - 1, bisect_right(values, timestamp + 0.000001) - 1))
    return index, values[index]


def has_audio_stream(path: Path) -> bool:
    _, ffprobe = require_ffmpeg()
    result = _run([
        ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index",
        "-of", "json", os.fspath(path),
    ])
    return bool(json.loads(result.stdout.decode("utf-8")).get("streams"))
