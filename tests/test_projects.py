import sqlite3
import time
from pathlib import Path

import app as app_module
from backend.task_manager import TaskManager


def record(name: str, source_hash: str = "hash") -> dict:
    return {"source_id": name, "source_path": name, "source_hash": source_hash,
            "duration": 1, "width": 64, "height": 64, "fps": 5, "fps_info": "5/1",
            "frame_count": 5, "codec": "h264", "pix_fmt": "yuv420p", "has_audio": False,
            "scan_error": None}


def wait_for_scan(client, job_id: str):
    observed = []
    for _ in range(100):
        response = client.get(f"/api/scans/{job_id}").get_json()
        observed.append((response["scanned"], len(response["videos"])))
        if response["status"] != "scanning":
            return response, observed
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def test_project_switch_preserves_separate_progress(tmp_path: Path):
    manager = TaskManager(tmp_path / "progress.sqlite")
    first = manager.set_project(str(tmp_path / "input_a"), str(tmp_path / "out"))
    manager.upsert_videos([record("video_2.mp4")], key=first["project_id"])
    manager.add_segment("video_2.mp4", 0, 2)
    second = manager.set_project(str(tmp_path / "input_b"), str(tmp_path / "out"))
    assert manager.videos() == []
    manager.upsert_videos([record("video_2.mp4", "other")], key=second["project_id"])
    assert manager.videos()[0]["source_id"] != "video_2.mp4"
    assert manager.videos()[0]["segment_count"] == 0
    manager.set_project(first["input_dir"], first["output_dir"])
    assert manager.video("video_2.mp4")["segments"][0]["end_frame"] == 2
    assert len(manager.videos()) == 1


def test_active_project_rejects_video_from_other_project(tmp_path: Path):
    app_module.manager = None; app_module.BASE_DIR = tmp_path
    manager = app_module.get_manager()
    first = manager.set_project(str(tmp_path / "a"), str(tmp_path / "out"))
    manager.upsert_videos([record("old.mp4")])
    manager.set_project(str(tmp_path / "b"), str(tmp_path / "out"))
    client = app_module.app.test_client()
    assert client.get('/api/status').get_json()["videos"] == []
    assert client.get('/api/videos/old.mp4').status_code == 404
    assert client.post('/api/videos/old.mp4/segments', json={"start":0,"end":1}).status_code == 404
    assert manager.videos(key=first["project_id"])[0]["source_path"] == 'old.mp4'


def test_scan_batches_visible_during_scan(tmp_path: Path, monkeypatch):
    source = tmp_path / "input"; source.mkdir()
    for index in range(21):
        (source / f"video_{index}.mp4").write_bytes(b"x")
    app_module.manager = None; app_module.BASE_DIR = tmp_path
    def batches(input_dir, paths):
        for offset in range(0, len(paths), 10):
            yield [record(path.name) for path in paths[offset:offset + 10]]
            time.sleep(0.05)
    monkeypatch.setattr(app_module, "scan_batches", batches)
    client = app_module.app.test_client()
    response = client.post('/api/project', json={"input_dir": str(source), "output_dir": str(tmp_path / 'output')})
    assert response.status_code == 202
    result, observations = wait_for_scan(client, response.get_json()["scan_id"])
    assert result["status"] == "completed"
    assert (10, 10) in observations
    assert (20, 20) in observations
    assert len(result["videos"]) == 21
    assert [item["source_path"] for item in result["videos"][:3]] == ["video_0.mp4", "video_1.mp4", "video_2.mp4"]


def test_legacy_database_migrates_without_losing_segments(tmp_path: Path):
    db = tmp_path / "progress.sqlite"
    manager = TaskManager(db)
    source = tmp_path / "input"; source.mkdir()
    (source / "old.mp4").write_bytes(b"old")
    manager.set_project(str(source), str(tmp_path / "output"))
    manager.upsert_videos([record("old.mp4", "old")])
    manager.add_segment("old.mp4", 1, 2)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE project SET project_id=NULL")
        conn.execute("UPDATE videos SET project_id=NULL")
    restored = TaskManager(db)
    assert restored.videos()[0]["source_path"] == "old.mp4"
    assert restored.video("old.mp4")["segments"][0]["start_frame"] == 1
