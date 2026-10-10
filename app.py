from __future__ import annotations

import base64
import os
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from backend.exporter import export_segment
from backend.metadata import write_metadata
from backend.scanner import scan_batches, video_paths, validate_directories
from backend.task_manager import TaskManager
from backend.video_reader import FFmpegError, extract_frame, frame_timestamp, frame_timestamps, frame_at_timestamp, require_ffmpeg

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder="frontend/templates", static_folder="frontend/static")
manager: TaskManager | None = None
export_lock = threading.Lock()
export_threads: dict[str, threading.Thread] = {}
scan_jobs: dict[str, dict] = {}
scan_lock = threading.Lock()


def get_manager() -> TaskManager:
    global manager
    if manager is None:
        manager = TaskManager(BASE_DIR / "project" / "progress.sqlite")
    return manager


def json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def configured_project(key: str | None = None) -> tuple[Path, Path]:
    project = get_manager().project_by_id(key) if key else get_manager().project()
    if not project:
        raise ValueError("请先选择输入目录和输出目录")
    return Path(project["input_dir"]), Path(project["output_dir"])


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    project = get_manager().project()
    with scan_lock:
        active_scan = next((job_id for job_id, job in reversed(list(scan_jobs.items()))
                            if job["status"] == "scanning" and project and job["project_id"] == project["project_id"]), None)
    return jsonify({"project": project, "counts": get_manager().counts(), "videos": get_manager().videos(),
                    "scan_id": active_scan})


@app.post("/api/project")
def create_project():
    payload = request.get_json(silent=True) or {}
    input_dir, output_dir = Path(str(payload.get("input_dir", "")).strip()).expanduser(), Path(str(payload.get("output_dir", "")).strip()).expanduser()
    try:
        require_ffmpeg()
        validate_directories(input_dir, output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = video_paths(input_dir, output_dir)
        project = get_manager().set_project(str(input_dir.resolve()), str(output_dir.resolve()))
    except (ValueError, OSError, FFmpegError) as exc:
        return json_error(str(exc))
    job_id = uuid.uuid4().hex
    with scan_lock:
        for job in scan_jobs.values():
            if job["status"] == "scanning":
                job["cancelled"] = True
        scan_jobs[job_id] = {"status": "scanning", "error": "", "project_id": project["project_id"],
                             "total": len(paths), "scanned": 0, "cancelled": False}
    thread = threading.Thread(target=run_scan, args=(job_id, project["project_id"], input_dir, paths), daemon=True)
    thread.start()
    return jsonify({"scan_id": job_id, "status": "scanning", "project": project,
                    "total": len(paths), "scanned": 0, "videos": get_manager().videos(key=project["project_id"]),
                    "counts": get_manager().counts(key=project["project_id"])}), 202


def run_scan(job_id: str, key: str, input_dir: Path, paths: list[Path]) -> None:
    try:
        for batch in scan_batches(input_dir, paths):
            with scan_lock:
                if scan_jobs[job_id]["cancelled"]:
                    scan_jobs[job_id]["status"] = "cancelled"
                    return
                get_manager().upsert_videos(batch, key=key)
                scan_jobs[job_id]["scanned"] += len(batch)
        result = ("completed", "")
    except Exception as exc:
        result = ("failed", str(exc))
    with scan_lock:
        scan_jobs[job_id].update({"status": result[0], "error": result[1]})


@app.get("/api/scans/<job_id>")
def scan_status(job_id: str):
    with scan_lock:
        result = scan_jobs.get(job_id)
        if not result:
            return json_error("扫描任务不存在", 404)
        response = dict(result)
        response.update({"project": get_manager().project_by_id(result["project_id"]),
                         "counts": get_manager().counts(key=result["project_id"]),
                         "videos": get_manager().videos(key=result["project_id"])})
    return jsonify(response)


@app.get("/api/videos/<source_id>")
def video_detail(source_id: str):
    item = get_manager().video_in_project(source_id)
    if not item:
        return json_error("视频不存在", 404)
    return jsonify(item)


@app.get("/api/videos/<source_id>/file")
def video_file(source_id: str):
    item = get_manager().video_in_project(source_id)
    if not item:
        return json_error("视频不存在", 404)
    try:
        input_dir, _ = configured_project(item["project_id"])
        path = (input_dir / item["source_path"]).resolve()
        if input_dir.resolve() not in path.parents or not path.is_file():
            return json_error("非法路径", 400)
        return send_file(path)
    except (ValueError, OSError) as exc:
        return json_error(str(exc), 404)


@app.get("/api/videos/<source_id>/frame/<int:frame_index>")
def video_frame(source_id: str, frame_index: int):
    item = get_manager().video_in_project(source_id)
    if not item:
        return json_error("视频不存在", 404)
    if item["frame_count"] and frame_index >= item["frame_count"]:
        return json_error("帧序号超出范围")
    try:
        input_dir, _ = configured_project(item["project_id"])
        path = (input_dir / item["source_path"]).resolve()
        image, info = extract_frame(path, frame_index)
    except (ValueError, OSError, FFmpegError) as exc:
        return json_error(str(exc), 422)
    timestamp = frame_timestamp(path, info["frame_index"])
    return jsonify({"frame_index": info["frame_index"], "fps": item["fps"], "timestamp": timestamp, "image": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")})


@app.get("/api/videos/<source_id>/frame-at")
def video_frame_at(source_id: str):
    item = get_manager().video_in_project(source_id)
    if not item:
        return json_error("视频不存在", 404)
    try:
        timestamp = float(request.args["timestamp"])
        input_dir, _ = configured_project(item["project_id"])
        index, actual = frame_at_timestamp(input_dir / item["source_path"], timestamp)
        return jsonify({"frame_index": index, "timestamp": actual})
    except (KeyError, ValueError, OSError, FFmpegError) as exc:
        return json_error(str(exc), 422)


@app.get("/api/videos/<source_id>/timestamps")
def video_timestamps(source_id: str):
    item = get_manager().video_in_project(source_id)
    if not item:
        return json_error("视频不存在", 404)
    try:
        input_dir, _ = configured_project(item["project_id"])
        return jsonify({"timestamps": frame_timestamps(input_dir / item["source_path"])})
    except (ValueError, OSError, FFmpegError) as exc:
        return json_error(str(exc), 422)


@app.post("/api/videos/<source_id>/draft")
def draft(source_id: str):
    if not get_manager().video_in_project(source_id):
        return json_error("视频不存在", 404)
    payload = request.get_json(silent=True) or {}
    try:
        get_manager().update_draft(source_id, payload.get("start"), payload.get("end"))
    except (ValueError, TypeError) as exc:
        return json_error(str(exc))
    return jsonify(get_manager().video(source_id))


@app.post("/api/videos/<source_id>/segments")
def add_segment(source_id: str):
    if not get_manager().video_in_project(source_id):
        return json_error("视频不存在", 404)
    payload = request.get_json(silent=True) or {}
    try:
        segment = get_manager().add_segment(source_id, int(payload.get("start")), int(payload.get("end")))
    except (TypeError, ValueError) as exc:
        return json_error(str(exc))
    return jsonify(segment)


@app.delete("/api/videos/<source_id>/segments/<int:segment_id>")
def remove_segment(source_id: str, segment_id: int):
    item = get_manager().video_in_project(source_id)
    if not item:
        return json_error("视频不存在", 404)
    with export_lock:
        active_export = export_threads.get(source_id)
        if active_export and active_export.is_alive():
            return json_error("该视频正在导出，请稍后删除片段", 409)
    segment = next((entry for entry in item["segments"] if entry["id"] == segment_id), None)
    if not segment:
        return json_error("片段不存在", 404)

    output_dir = None
    output_path = segment.get("output_path")
    if output_path:
        try:
            _, output_dir = configured_project(item["project_id"])
            candidate = (output_dir / output_path).resolve()
            output_root = output_dir.resolve()
            if output_root not in candidate.parents or candidate == output_root:
                return json_error("片段输出路径非法", 400)
            # A path should belong to one segment, but avoid deleting a file
            # still referenced by another record if old data contains a duplicate.
            if sum(1 for entry in item["segments"] if entry.get("output_path") == output_path) == 1:
                candidate.unlink(missing_ok=True)
        except (OSError, ValueError) as exc:
            return json_error(f"删除片段文件失败：{exc}", 500)

    get_manager().delete_segment(source_id, segment_id)
    updated = get_manager().video(source_id)
    try:
        if output_dir is None:
            _, output_dir = configured_project(item["project_id"])
        write_metadata(output_dir, updated)
    except (OSError, ValueError) as exc:
        return json_error(f"片段已删除，但元数据更新失败：{exc}", 500)
    return jsonify(updated)


@app.post("/api/videos/<source_id>/decision")
def decision(source_id: str):
    if not get_manager().video_in_project(source_id):
        return json_error("视频不存在", 404)
    payload = request.get_json(silent=True) or {}
    try:
        get_manager().decision(source_id, str(payload.get("decision")))
    except ValueError as exc:
        return json_error(str(exc))
    item = get_manager().video(source_id)
    try:
        _, output_dir = configured_project()
        write_metadata(output_dir, item)
    except (ValueError, OSError):
        pass
    return jsonify(item)


def run_export(source_id: str) -> None:
    task = get_manager()
    try:
        video = task.video(source_id)
        if not video:
            return
        input_dir, output_dir = configured_project(video["project_id"])
        source = (input_dir / video["source_path"]).resolve()
        task.mark_exporting(source_id)
        for segment in video["segments"]:
            if segment["status"] == "completed" and segment.get("output_path") and (output_dir / segment["output_path"]).exists():
                continue
            relative_parent = Path(video["source_path"]).parent
            filename = f"{Path(video['source_path']).stem}__seg{segment['segment_index']:03d}.mp4"
            relative_output = (relative_parent / filename).as_posix()
            target = output_dir / relative_output
            try:
                result = export_segment(source, target, segment["start_frame"], segment["end_frame"])
                task.update_segment(segment["id"], output_path=relative_output, status="completed", error=None, **result)
            except Exception as exc:  # retain successful siblings and failure reason
                task.update_segment(segment["id"], status="failed", error=str(exc))
        refreshed = task.video(source_id)
        failed = [s for s in refreshed["segments"] if s["status"] != "completed"]
        final_status = "failed" if failed else "completed"
        task.finish(source_id, final_status, "; ".join(s.get("error", "") or "" for s in failed) or None)
        write_metadata(output_dir, task.video(source_id))
    except Exception as exc:
        task.finish(source_id, "failed", str(exc))
    finally:
        with export_lock:
            export_threads.pop(source_id, None)


@app.post("/api/videos/<source_id>/export")
def export(source_id: str):
    item = get_manager().video_in_project(source_id)
    if not item:
        return json_error("视频不存在", 404)
    if not item["segments"]:
        return json_error("没有片段，请标记为拒绝或无片段")
    with export_lock:
        existing = export_threads.get(source_id)
        if existing and existing.is_alive():
            return json_error("该视频正在导出", 409)
        thread = threading.Thread(target=run_export, args=(source_id,), daemon=True)
        export_threads[source_id] = thread
        thread.start()
    return jsonify({"status": "exporting"}), 202


if __name__ == "__main__":
    app.run(host=os.environ.get("VIDEO_TOOL_HOST", "127.0.0.1"), port=int(os.environ.get("VIDEO_TOOL_PORT", "5000")), debug=False)
