from pathlib import Path
import json
import os
import subprocess

from backend.exporter import export_segment
from backend.scanner import scan
from backend.task_manager import TaskManager
from backend.video_reader import count_output_frames, has_audio_stream, probe


def test_scan_and_export(tmp_path: Path):
    source = tmp_path / "source"
    output = tmp_path / "output"
    (source / "nested").mkdir(parents=True)
    video = source / "nested" / "video_2.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=1000", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(video),
    ], check=True)
    records = scan(source, output)
    assert records[0]["source_path"] == "nested/video_2.mp4"
    assert records[0]["frame_count"] == 20
    assert records[0]["has_audio"]
    target = output / "nested" / "video_2__seg001.mp4"
    result = export_segment(video, target, 3, 7)
    assert result["exported_frame_count"] == 5
    assert count_output_frames(target) == 5
    assert not has_audio_stream(target)


def test_task_persistence(tmp_path: Path):
    manager = TaskManager(tmp_path / "progress.sqlite")
    manager.set_project("/in", "/out")
    manager.upsert_videos([{
        "source_id": "abc", "source_path": "a.mp4", "source_hash": "hash", "duration": 1,
        "width": 1, "height": 1, "fps": 10, "fps_info": "10/1", "frame_count": 10,
        "codec": "h264", "pix_fmt": "yuv420p", "has_audio": False, "scan_error": None,
    }])
    manager.add_segment("abc", 1, 3)
    manager.add_segment("abc", 2, 4)
    assert [s["selected_frame_count"] for s in manager.video("abc")["segments"]] == [3, 3]
