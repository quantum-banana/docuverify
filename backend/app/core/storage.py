"""Small SQLite job/event store and safe runtime asset registry."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.app.models.contracts import (
    AnalysisJob,
    DocumentResult,
    ErrorDetail,
    JobState,
    ProgressEvent,
    StageId,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobStore:
    """Thread-safe persistence with append-only, replayable progress events."""

    def __init__(self, runtime_dir: Path, database_path: Path) -> None:
        self.runtime_dir = runtime_dir.resolve()
        self.jobs_dir = self.runtime_dir / "jobs"
        self.database_path = database_path.resolve()
        self._condition = threading.Condition()
        self._startup_lock = threading.Lock()
        self._startup_complete = False
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def startup(self) -> None:
        """Perform one-time lifecycle recovery after the ASGI app starts.

        Construction intentionally has no job-state side effects. This keeps
        imports, schema tooling, and app-factory inspection from mutating jobs
        owned by an already-running backend process.
        """

        with self._startup_lock:
            if self._startup_complete:
                return
            self._recover_interrupted_jobs()
            self._startup_complete = True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    current_stage TEXT NOT NULL,
                    current_stage_message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    job_id TEXT NOT NULL,
                    event_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (job_id, event_id),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS assets (
                    job_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    PRIMARY KEY (job_id, asset_id),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                """
            )

    def _recover_interrupted_jobs(self) -> None:
        now = utc_now().isoformat()
        error_detail = ErrorDetail(
            code="service_restarted",
            message="Analysis was interrupted by a backend restart. Please submit it again.",
        )
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id, progress FROM jobs WHERE state IN (?, ?)",
                (JobState.QUEUED.value, JobState.RUNNING.value),
            ).fetchall()
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, current_stage = ?, current_stage_message = ?,
                    error_json = ?, updated_at = ?
                WHERE state IN (?, ?)
                """,
                (
                    JobState.FAILED.value,
                    StageId.FAILED.value,
                    "Analysis interrupted by backend restart",
                    error_detail.model_dump_json(),
                    now,
                    JobState.QUEUED.value,
                    JobState.RUNNING.value,
                ),
            )
        for row in rows:
            self.append_event(
                row["job_id"],
                event_type="error",
                stage=StageId.FAILED,
                message=error_detail.message,
                progress=row["progress"],
            )

    def create_job(self, job_id: str) -> AnalysisJob:
        now = utc_now()
        job = AnalysisJob(
            job_id=job_id,
            state=JobState.QUEUED,
            progress=0,
            current_stage=StageId.QUEUED,
            current_stage_message="Analysis queued",
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, state, progress, current_stage, current_stage_message,
                    created_at, updated_at, result_json, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    job.job_id,
                    job.state.value,
                    job.progress,
                    job.current_stage.value,
                    job.current_stage_message,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )
        return job

    def update_job(
        self,
        job_id: str,
        *,
        state: JobState,
        progress: int,
        stage: StageId,
        message: str,
        result: DocumentResult | None = None,
        error: ErrorDetail | None = None,
    ) -> None:
        result_json = result.model_dump_json() if result else None
        error_json = error.model_dump_json() if error else None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET state = ?, progress = ?, current_stage = ?,
                    current_stage_message = ?, updated_at = ?, result_json = ?, error_json = ?
                WHERE job_id = ?
                """,
                (
                    state.value,
                    progress,
                    stage.value,
                    message,
                    utc_now().isoformat(),
                    result_json,
                    error_json,
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
        with self._condition:
            self._condition.notify_all()

    def get_job(self, job_id: str) -> AnalysisJob | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return AnalysisJob(
            job_id=row["job_id"],
            state=row["state"],
            progress=row["progress"],
            current_stage=row["current_stage"],
            current_stage_message=row["current_stage_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            result=(
                DocumentResult.model_validate_json(row["result_json"])
                if row["result_json"]
                else None
            ),
            error=(
                ErrorDetail.model_validate_json(row["error_json"])
                if row["error_json"]
                else None
            ),
        )

    def append_event(
        self,
        job_id: str,
        *,
        event_type: str,
        stage: StageId,
        message: str,
        progress: int,
        finding_count: int = 0,
        candidate_page_url: str | None = None,
    ) -> ProgressEvent:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(event_id), 0) + 1 AS next_id FROM events WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            event = ProgressEvent(
                event_id=int(row["next_id"]),
                job_id=job_id,
                stage_id=stage,
                message=message,
                progress=progress,
                timestamp=utc_now(),
                finding_count=finding_count,
                candidate_page_url=candidate_page_url,
            )
            connection.execute(
                """
                INSERT INTO events (job_id, event_id, event_type, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, event.event_id, event_type, event.model_dump_json()),
            )
            connection.commit()
        with self._condition:
            self._condition.notify_all()
        return event

    def get_events_after(self, job_id: str, event_id: int) -> list[tuple[str, ProgressEvent]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type, payload_json FROM events
                WHERE job_id = ? AND event_id > ? ORDER BY event_id
                """,
                (job_id, event_id),
            ).fetchall()
        return [
            (row["event_type"], ProgressEvent.model_validate_json(row["payload_json"]))
            for row in rows
        ]

    def get_latest_event(self, job_id: str) -> tuple[str, ProgressEvent] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT event_type, payload_json FROM events
                WHERE job_id = ? ORDER BY event_id DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return row["event_type"], ProgressEvent.model_validate_json(row["payload_json"])

    def wait_for_change(self, timeout_seconds: float) -> None:
        with self._condition:
            self._condition.wait(timeout=timeout_seconds)

    def job_directory(self, job_id: str) -> Path:
        # UUID-only IDs are created internally; still enforce a single safe segment.
        if not job_id or any(character not in "0123456789abcdef-" for character in job_id):
            raise ValueError("invalid job id")
        path = (self.jobs_dir / job_id).resolve()
        path.relative_to(self.jobs_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def register_asset(
        self, job_id: str, asset_id: str, file_path: Path, media_type: str = "image/png"
    ) -> None:
        job_dir = self.job_directory(job_id)
        resolved = file_path.resolve()
        relative = resolved.relative_to(job_dir)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO assets (job_id, asset_id, relative_path, media_type)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, asset_id, relative.as_posix(), media_type),
            )

    def resolve_asset(self, job_id: str, asset_id: str) -> tuple[Path, str] | None:
        if not asset_id or len(asset_id) > 80 or not all(
            character.isalnum() or character in "-_" for character in asset_id
        ):
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT relative_path, media_type FROM assets
                WHERE job_id = ? AND asset_id = ?
                """,
                (job_id, asset_id),
            ).fetchone()
        if row is None:
            return None
        job_dir = self.job_directory(job_id)
        resolved = (job_dir / row["relative_path"]).resolve()
        try:
            resolved.relative_to(job_dir)
        except ValueError:
            return None
        if not resolved.is_file():
            return None
        return resolved, row["media_type"]

    def cleanup_expired(self, retention_hours: int) -> int:
        cutoff = utc_now() - timedelta(hours=retention_hours)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT job_id FROM jobs
                WHERE updated_at < ? AND state IN (?, ?)
                """,
                (
                    cutoff.isoformat(),
                    JobState.COMPLETED.value,
                    JobState.FAILED.value,
                ),
            ).fetchall()
        removed_count = 0
        for row in rows:
            job_id = row["job_id"]
            try:
                job_entry = self.jobs_dir / job_id
                if job_entry.is_symlink():
                    continue
                job_dir = job_entry.resolve()
                job_dir.relative_to(self.jobs_dir)
                if job_dir.exists() and not job_dir.is_dir():
                    continue
                if job_dir.is_dir():
                    shutil.rmtree(job_dir)
                if job_dir.exists():
                    # Treat a no-op or partial remover as a cleanup failure.
                    continue
            except (OSError, ValueError):
                # Keep the database row and asset registry so a later cleanup
                # can retry a locked or partially removed Windows directory.
                continue
            # A missing directory is already clean. Delete the row only after
            # filesystem cleanup succeeds; asset/event rows cascade with it.
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM jobs
                    WHERE job_id = ? AND updated_at < ? AND state IN (?, ?)
                    """,
                    (
                        job_id,
                        cutoff.isoformat(),
                        JobState.COMPLETED.value,
                        JobState.FAILED.value,
                    ),
                )
            removed_count += cursor.rowcount
        return removed_count

    def runtime_writable(self) -> bool:
        probe = self.runtime_dir / ".write-probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False
