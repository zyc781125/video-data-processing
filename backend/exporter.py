from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .video_reader import FFmpegError, count_output_frames, frame_timestamp, has_audio_stream, require_ffmpeg


def export_segment(source: Path, target: Path, start: int, end: int) -> dict[str, Any]:
    if start < 0 or end < start:
        raise ValueError("起止帧无效")
    ffmpeg, _ = require_ffmpeg()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Keep an MP4 suffix so FFmpeg selects the intended muxer for the temporary file.
    temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.mp4")
    filter_expr = f"select=between(n\\,{start}\\,{end}),setpts=PTS-STARTPTS"
    command = [ffmpeg, "-y", "-v", "error", "-i", os.fspath(source), "-vf", filter_expr,
               "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
               "-vsync", "0", os.fspath(temporary)]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise FFmpegError(result.stderr.decode("utf-8", errors="replace")[-4000:])
        exported_count = count_output_frames(temporary)
        expected = end - start + 1
        if exported_count != expected:
            raise FFmpegError(f"输出帧数不一致：期望 {expected}，实际 {exported_count}")
        if has_audio_stream(temporary):
            raise FFmpegError("输出文件仍包含音频流")
        os.replace(temporary, target)
        return {"exported_frame_count": exported_count, "output_duration": _duration(target),
                "start_timestamp": frame_timestamp(source, start),
                "end_frame_timestamp": frame_timestamp(source, end),
                "end_boundary_timestamp": frame_timestamp(source, end + 1)}
    finally:
        if temporary.exists():
            temporary.unlink()


def _duration(path: Path) -> float:
    from .video_reader import _run
    _, ffprobe = require_ffmpeg()
    result = _run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", os.fspath(path)])
    try:
        return float(result.stdout.decode().strip())
    except ValueError:
        return 0.0
