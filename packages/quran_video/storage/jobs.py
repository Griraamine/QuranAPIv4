from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from quran_video.config import get_settings
from quran_video.models.domain import RenderJobRecord, RenderStatus


def _now() -> datetime:
    return datetime.now(UTC)


class JobStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or get_settings().sqlite_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        self._rotate_readonly_database()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS render_jobs (
                    job_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    progress REAL NOT NULL,
                    eta_seconds REAL,
                    error_summary TEXT,
                    video_path TEXT,
                    thumbnail_path TEXT,
                    expires_at TEXT,
                    request_json TEXT,
                    logs TEXT NOT NULL DEFAULT '[]'
                )
                """
            )

    def _rotate_readonly_database(self) -> None:
        if not self.path.exists() or os.access(self.path, os.W_OK):
            return
        if not os.access(self.path.parent, os.W_OK):
            raise PermissionError(
                f"SQLite database is readonly and parent directory is not writable: {self.path}"
            )
        timestamp = _now().strftime("%Y%m%d%H%M%S")
        for candidate in [
            self.path,
            self.path.with_name(f"{self.path.name}-wal"),
            self.path.with_name(f"{self.path.name}-shm"),
        ]:
            if not candidate.exists():
                continue
            backup = candidate.with_name(f"{candidate.name}.readonly-{timestamp}")
            index = 1
            while backup.exists():
                backup = candidate.with_name(f"{candidate.name}.readonly-{timestamp}.{index}")
                index += 1
            candidate.replace(backup)

    def create(self, job_id: str, request_json: dict[str, Any]) -> RenderJobRecord:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO render_jobs
                (job_id, created_at, updated_at, status, phase, progress, request_json, logs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    now.isoformat(),
                    now.isoformat(),
                    RenderStatus.queued.value,
                    "queued",
                    0.0,
                    json.dumps(request_json, ensure_ascii=False),
                    "[]",
                ),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> RenderJobRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM render_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._record(row)

    def get_request(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json FROM render_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None or not row["request_json"]:
            raise KeyError(job_id)
        return json.loads(row["request_json"])

    def update(self, job_id: str, **fields: Any) -> RenderJobRecord:
        allowed = {
            "status",
            "phase",
            "progress",
            "eta_seconds",
            "error_summary",
            "video_path",
            "thumbnail_path",
            "expires_at",
            "request_json",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        updates["updated_at"] = _now().isoformat()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = []
        for key, value in updates.items():
            if isinstance(value, datetime):
                values.append(value.isoformat())
            elif key == "request_json" and isinstance(value, dict):
                values.append(json.dumps(value, ensure_ascii=False))
            else:
                values.append(value)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE render_jobs SET {assignments} WHERE job_id = ?",
                [*values, job_id],
            )
        return self.get(job_id)

    def append_log(self, job_id: str, message: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT logs FROM render_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            logs = json.loads(row["logs"]) if row else []
            logs.append({"timestamp": _now().isoformat(), "message": message})
            connection.execute(
                "UPDATE render_jobs SET logs = ?, updated_at = ? WHERE job_id = ?",
                (json.dumps(logs[-200:], ensure_ascii=False), _now().isoformat(), job_id),
            )

    def logs(self, job_id: str) -> list[dict[str, str]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT logs FROM render_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return json.loads(row["logs"]) if row else []

    def mark_complete(self, job_id: str, video_path: Path, thumbnail_path: Path) -> RenderJobRecord:
        return self.update(
            job_id,
            status=RenderStatus.complete.value,
            phase="complete",
            progress=100.0,
            video_path=str(video_path),
            thumbnail_path=str(thumbnail_path),
            expires_at=_now() + timedelta(hours=72),
        )

    def delete(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM render_jobs WHERE job_id = ?", (job_id,))

    def expired_outputs(self) -> list[RenderJobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM render_jobs WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (_now().isoformat(),),
            ).fetchall()
        return [self._record(row) for row in rows]

    def cleanup_expired_outputs(self) -> int:
        cleaned = 0
        for record in self.expired_outputs():
            for path_value in [record.video_path, record.thumbnail_path]:
                if not path_value:
                    continue
                path = Path(path_value)
                root = get_settings().renders_dir.resolve()
                try:
                    path.resolve().relative_to(root)
                except ValueError:
                    continue
                if path.exists():
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
            output_dir = get_settings().renders_dir / record.job_id
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            self.delete(record.job_id)
            cleaned += 1
        return cleaned

    def _record(self, row: sqlite3.Row) -> RenderJobRecord:
        return RenderJobRecord(
            job_id=row["job_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            status=row["status"],
            phase=row["phase"],
            progress=float(row["progress"]),
            eta_seconds=row["eta_seconds"],
            error_summary=row["error_summary"],
            video_path=row["video_path"],
            thumbnail_path=row["thumbnail_path"],
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        )
