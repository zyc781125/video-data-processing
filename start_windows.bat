@echo off
setlocal
title 视频片段筛选器
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 ffmpeg。请安装 FFmpeg 并将其加入系统 PATH。
    echo 需要同时可以运行 ffmpeg 和 ffprobe。
    pause
    exit /b 1
)
where ffprobe >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 ffprobe。请安装 FFmpeg 并将其加入系统 PATH。
    pause
    exit /b 1
)

set "CONDA_CMD="
if defined CONDA_EXE if exist "%CONDA_EXE%" set "CONDA_CMD=%CONDA_EXE%"
if not defined CONDA_CMD for /f "delims=" %%I in ('where conda 2^>nul') do if not defined CONDA_CMD set "CONDA_CMD=%%I"
if not defined CONDA_CMD if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\miniconda3\Scripts\conda.exe"
if not defined CONDA_CMD if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\anaconda3\Scripts\conda.exe"
if not defined CONDA_CMD if exist "%LOCALAPPDATA%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%LOCALAPPDATA%\miniconda3\Scripts\conda.exe"
if not defined CONDA_CMD if exist "%ProgramData%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%ProgramData%\miniconda3\Scripts\conda.exe"

if not defined CONDA_CMD (
    echo [错误] 未找到 Conda。
    echo 请安装 Miniconda/Anaconda，或先在 Anaconda Prompt 中运行此脚本。
    pause
    exit /b 1
)

echo 正在使用 Conda: %CONDA_CMD%
echo 正在启动本地服务，请访问 http://127.0.0.1:5000
call "%CONDA_CMD%" run --no-capture-output -n data-process python app.py
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo [错误] 服务启动失败，错误代码: %EXIT_CODE%
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
