@echo off
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (echo 请先安装 FFmpeg ^(需要 ffmpeg 和 ffprobe^) & exit /b 1)
where ffprobe >nul 2>nul || (echo 请先安装 FFmpeg ^(需要 ffmpeg 和 ffprobe^) & exit /b 1)
conda run --no-capture-output -n data-process python app.py
