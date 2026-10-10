#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then echo "请先安装 FFmpeg（需要 ffmpeg 和 ffprobe）" >&2; exit 1; fi
exec conda run --no-capture-output -n data-process python app.py
