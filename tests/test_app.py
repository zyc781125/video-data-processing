import json
import subprocess
import time
from pathlib import Path

import app as app_module


def test_project_and_api_export(tmp_path: Path):
    source, output = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=5:d=1",
        str(source / "video_10.mp4"),
    ], check=True)
    app_module.manager = None
    app_module.BASE_DIR = tmp_path
    client = app_module.app.test_client()
    response = client.post("/api/project", json={"input_dir": str(source), "output_dir": str(output)})
    assert response.status_code == 202
    job_id = response.get_json()["scan_id"]
    for _ in range(30):
        scan = client.get(f"/api/scans/{job_id}").get_json()
        if scan["status"] != "scanning":
            break
        time.sleep(0.1)
    assert scan["status"] == "completed", scan
    source_id = scan["videos"][0]["source_id"]
    frame_at = client.get(f"/api/videos/{source_id}/frame-at?timestamp=0.41").get_json()
    assert frame_at["frame_index"] == 2
    assert client.get(f"/api/videos/{source_id}/frame/2").status_code == 200
    assert client.post(f"/api/videos/{source_id}/segments", json={"start": 1, "end": 3}).status_code == 200
    assert client.post(f"/api/videos/{source_id}/export").status_code == 202
    for _ in range(30):
        time.sleep(0.1)
        item = client.get(f"/api/videos/{source_id}").get_json()
        if item["status"] != "exporting":
            break
    assert item["status"] == "completed", item
    assert (output / "video_10__seg001.mp4").exists()
    assert (output / "video_10__metadata.json").exists()
    deleted = client.delete(f"/api/videos/{source_id}/segments/{item['segments'][0]['id']}")
    assert deleted.status_code == 200, deleted.get_json()
    assert not (output / "video_10__seg001.mp4").exists()
    assert deleted.get_json()["segments"] == []
    assert json.loads((output / "video_10__metadata.json").read_text(encoding="utf-8"))["segments"] == []


def test_delete_failure_keeps_segment_record(tmp_path: Path, monkeypatch):
    app_module.manager = None
    app_module.BASE_DIR = tmp_path
    manager = app_module.get_manager()
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    project = manager.set_project(str(source), str(output))
    manager.upsert_videos([{
        "source_id": "clip", "source_path": "clip.mp4", "source_hash": "hash", "duration": 1,
        "width": 1, "height": 1, "fps": 5, "fps_info": "5/1", "frame_count": 5,
        "codec": "h264", "pix_fmt": "yuv420p", "has_audio": False, "scan_error": None,
    }], key=project["project_id"])
    segment = manager.add_segment("clip", 0, 2)
    manager.update_segment(segment["id"], output_path="clip__seg001.mp4", status="completed")
    target = output / "clip__seg001.mp4"
    target.write_bytes(b"video")

    original_unlink = Path.unlink
    def deny_target(path, *args, **kwargs):
        if path == target:
            raise PermissionError("denied")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", deny_target)

    response = app_module.app.test_client().delete(f"/api/videos/clip/segments/{segment['id']}")
    assert response.status_code == 500
    assert target.exists()
    assert len(manager.video("clip")["segments"]) == 1
