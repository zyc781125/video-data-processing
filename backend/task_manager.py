from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .scanner import natural_key


STATUSES = {"pending", "editing", "exporting", "completed", "rejected", "no_segment", "failed", "needs_reexport"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_id(input_dir: str, output_dir: str) -> str:
    value = f"{Path(input_dir).resolve()}\0{Path(output_dir).resolve()}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


class TaskManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS project (
                id INTEGER PRIMARY KEY CHECK (id = 1), input_dir TEXT NOT NULL,
                output_dir TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                project_id TEXT
            );
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY, input_dir TEXT NOT NULL, output_dir TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS videos (
                source_id TEXT PRIMARY KEY, project_id TEXT, source_path TEXT NOT NULL, source_hash TEXT NOT NULL,
                duration REAL NOT NULL DEFAULT 0, width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0, fps REAL NOT NULL DEFAULT 0,
                fps_info TEXT NOT NULL DEFAULT '', frame_count INTEGER NOT NULL DEFAULT 0,
                codec TEXT NOT NULL DEFAULT '', pix_fmt TEXT NOT NULL DEFAULT '',
                has_audio INTEGER NOT NULL DEFAULT 0, scan_error TEXT, status TEXT NOT NULL DEFAULT 'pending',
                decision TEXT, draft_start INTEGER, draft_end INTEGER, error TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL REFERENCES videos(source_id) ON DELETE CASCADE,
                segment_index INTEGER NOT NULL, start_frame INTEGER NOT NULL, end_frame INTEGER NOT NULL,
                output_path TEXT, output_duration REAL, selected_frame_count INTEGER NOT NULL,
                exported_frame_count INTEGER, start_timestamp REAL, end_frame_timestamp REAL,
                end_boundary_timestamp REAL, remove_audio INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending', error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(source_id, segment_index)
            );
            CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
            """)

            project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(project)")}
            video_columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
            if "project_id" not in project_columns:
                conn.execute("ALTER TABLE project ADD COLUMN project_id TEXT")
            if "project_id" not in video_columns:
                conn.execute("ALTER TABLE videos ADD COLUMN project_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_project ON videos(project_id)")
            active = conn.execute("SELECT * FROM project WHERE id=1").fetchone()
            if active and not active["project_id"]:
                key = project_id(active["input_dir"], active["output_dir"])
                conn.execute("INSERT OR IGNORE INTO projects VALUES(?,?,?,?,?)",
                             (key, active["input_dir"], active["output_dir"], active["created_at"], active["updated_at"]))
                conn.execute("UPDATE project SET project_id=? WHERE id=1", (key,))
                for row in conn.execute("SELECT source_id,source_path FROM videos WHERE project_id IS NULL"):
                    belongs = (Path(active["input_dir"]) / row["source_path"]).is_file()
                    conn.execute("UPDATE videos SET project_id=? WHERE source_id=?",
                                 (key if belongs else "legacy", row["source_id"]))

    def set_project(self, input_dir: str, output_dir: str) -> dict[str, Any]:
        timestamp = now()
        key = project_id(input_dir, output_dir)
        with self.connection() as conn:
            conn.execute("""INSERT INTO projects(project_id,input_dir,output_dir,created_at,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET updated_at=excluded.updated_at""",
                         (key, input_dir, output_dir, timestamp, timestamp))
            conn.execute("""INSERT INTO project(id,input_dir,output_dir,created_at,updated_at,project_id) VALUES(1,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET input_dir=excluded.input_dir, output_dir=excluded.output_dir,
                updated_at=excluded.updated_at, project_id=excluded.project_id""",
                         (input_dir, output_dir, timestamp, timestamp, key))
        return self.project()

    def project(self) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM project WHERE id=1").fetchone()
        return dict(row) if row else None

    def project_by_id(self, key: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM projects WHERE project_id=?", (key,)).fetchone()
        return dict(row) if row else None

    def upsert_videos(self, records: list[dict[str, Any]], key: str | None = None) -> None:
        key = key or (self.project() or {}).get("project_id")
        if not key:
            raise ValueError("请先创建项目")
        with self.connection() as conn:
            for item in records:
                existing = conn.execute("SELECT source_id FROM videos WHERE project_id=? AND source_path=?", (key, item["source_path"])).fetchone()
                if not existing:
                    existing = conn.execute("SELECT source_id FROM videos WHERE project_id='legacy' AND source_path=? AND source_hash=?",
                                            (item["source_path"], item["source_hash"])).fetchone()
                if existing:
                    identifier = existing[0]
                else:
                    supplied = item.get("source_id")
                    identifier = supplied if supplied and not conn.execute("SELECT 1 FROM videos WHERE source_id=?", (supplied,)).fetchone() else hashlib.sha256(f"{key}\0{item['source_path']}".encode()).hexdigest()[:24]
                conn.execute("""INSERT INTO videos(source_id,project_id,source_path,source_hash,duration,width,height,fps,fps_info,
                    frame_count,codec,pix_fmt,has_audio,scan_error,status,updated_at)
                    VALUES(:source_id,:project_id,:source_path,:source_hash,:duration,:width,:height,:fps,:fps_info,:frame_count,
                    :codec,:pix_fmt,:has_audio,:scan_error,'pending',:updated_at)
                    ON CONFLICT(source_id) DO UPDATE SET source_path=excluded.source_path, source_hash=excluded.source_hash,
                    project_id=excluded.project_id,
                    duration=excluded.duration,width=excluded.width,height=excluded.height,fps=excluded.fps,
                    fps_info=excluded.fps_info,frame_count=excluded.frame_count,codec=excluded.codec,pix_fmt=excluded.pix_fmt,
                    has_audio=excluded.has_audio,scan_error=excluded.scan_error,
                    status=CASE WHEN videos.source_hash != excluded.source_hash AND videos.status IN ('completed','needs_reexport') THEN 'needs_reexport' ELSE videos.status END,
                    error=CASE WHEN videos.source_hash != excluded.source_hash THEN '源文件已变化，请检查并重新导出' ELSE videos.error END,
                    updated_at=excluded.updated_at""",
                    {**item, "source_id": identifier, "project_id": key, "updated_at": now()})

    def videos(self, status: str | None = None, key: str | None = None) -> list[dict[str, Any]]:
        key = key or (self.project() or {}).get("project_id")
        if not key:
            return []
        with self.connection() as conn:
            query = "SELECT videos.*, (SELECT COUNT(*) FROM segments WHERE segments.source_id=videos.source_id) AS segment_count FROM videos WHERE project_id=?"
            params: tuple[Any, ...] = (key,)
            if status and status != "all":
                query += " AND status = ?"
                params = (key, status)
            rows = conn.execute(query, params).fetchall()
            result = [dict(row) for row in rows]
        return sorted(result, key=lambda item: natural_key(item["source_path"]))

    def video(self, source_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM videos WHERE source_id=?", (source_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["segments"] = [dict(r) for r in conn.execute("SELECT * FROM segments WHERE source_id=? ORDER BY segment_index", (source_id,)).fetchall()]
        return item

    def video_in_project(self, source_id: str) -> dict[str, Any] | None:
        item = self.video(source_id)
        active = self.project()
        return item if item and active and item["project_id"] == active["project_id"] else None

    def update_draft(self, source_id: str, start: int | None, end: int | None) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE videos SET draft_start=?,draft_end=?,status=CASE WHEN status IN ('pending','editing') THEN 'editing' ELSE status END,updated_at=? WHERE source_id=?", (start, end, now(), source_id))

    def add_segment(self, source_id: str, start: int, end: int) -> dict[str, Any]:
        if start < 0 or end < start:
            raise ValueError("起止帧无效")
        with self.connection() as conn:
            video = conn.execute("SELECT frame_count FROM videos WHERE source_id=?", (source_id,)).fetchone()
            if not video:
                raise ValueError("视频不存在")
            if video[0] and end >= video[0]:
                raise ValueError("结束帧超出视频范围")
            index = conn.execute("SELECT COALESCE(MAX(segment_index),0)+1 FROM segments WHERE source_id=?", (source_id,)).fetchone()[0]
            timestamp = now()
            cursor = conn.execute("""INSERT INTO segments(source_id,segment_index,start_frame,end_frame,selected_frame_count,status,created_at,updated_at)
                VALUES(?,?,?,?,?,'pending',?,?)""", (source_id, index, start, end, end - start + 1, timestamp, timestamp))
            conn.execute("UPDATE videos SET status=CASE WHEN status IN ('completed','rejected','no_segment') THEN 'needs_reexport' ELSE 'editing' END,decision=NULL,updated_at=? WHERE source_id=?", (timestamp, source_id))
            row = conn.execute("SELECT * FROM segments WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def delete_segment(self, source_id: str, segment_id: int) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM segments WHERE id=? AND source_id=?", (segment_id, source_id))
            conn.execute("UPDATE segments SET segment_index=segment_index-1,updated_at=? WHERE source_id=? AND segment_index > (SELECT COALESCE(MAX(segment_index),0) FROM segments WHERE source_id=? )", (now(), source_id, source_id))
            conn.execute("UPDATE videos SET status=CASE WHEN status='completed' THEN 'needs_reexport' ELSE 'editing' END,updated_at=? WHERE source_id=?", (now(), source_id))

    def decision(self, source_id: str, value: str) -> None:
        if value not in {"rejected", "no_segment"}:
            raise ValueError("无效决定")
        with self.connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM segments WHERE source_id=?", (source_id,)).fetchone()[0]
            if count:
                raise ValueError("已有片段，不能标记为无片段或拒绝")
            conn.execute("UPDATE videos SET status=?,decision=?,updated_at=? WHERE source_id=?", (value, value, now(), source_id))

    def mark_exporting(self, source_id: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE videos SET status='exporting',error=NULL,updated_at=? WHERE source_id=?", (now(), source_id))

    def update_segment(self, segment_id: int, **values: Any) -> None:
        allowed = {"output_path", "output_duration", "exported_frame_count", "start_timestamp", "end_frame_timestamp", "end_boundary_timestamp", "status", "error"}
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = now()
        assignments = ",".join(f"{key}=:{key}" for key in values)
        with self.connection() as conn:
            conn.execute(f"UPDATE segments SET {assignments} WHERE id=:segment_id", {**values, "segment_id": segment_id})

    def finish(self, source_id: str, status: str, error: str | None = None) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE videos SET status=?,error=?,updated_at=? WHERE source_id=?", (status, error, now(), source_id))

    def counts(self, key: str | None = None) -> dict[str, int]:
        key = key or (self.project() or {}).get("project_id")
        if not key:
            return {}
        with self.connection() as conn:
            rows = conn.execute("SELECT status,COUNT(*) AS count FROM videos WHERE project_id=? GROUP BY status", (key,)).fetchall()
        return {row["status"]: row["count"] for row in rows}
