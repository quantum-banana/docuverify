from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.core.storage import JobStore
from backend.app.main import create_app
from backend.app.models.contracts import StageId


def test_job_store_connection_context_closes_its_sqlite_handle(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = JobStore(runtime_dir, runtime_dir / "jobs.sqlite3")

    with store._connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_page_context_persists_on_create_event_and_store_reopen(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    database_path = runtime_dir / "jobs.sqlite3"
    store = JobStore(runtime_dir, database_path)
    job_id = str(uuid.uuid4())

    created = store.create_job(job_id, total_pages=3)
    persisted = store.get_job(job_id)
    assert created.total_pages == 3
    assert persisted is not None
    assert persisted.current_page == 1
    assert persisted.total_pages == 3

    page_url = f"/api/v1/analyses/{job_id}/assets/candidate-page-2"
    store.append_event(
        job_id,
        event_type="progress",
        stage=StageId.EXTRACTING_TEXT,
        message="Extracting text from page 2 of 3",
        progress=24,
        page_number=2,
        total_pages=3,
        candidate_page_url=page_url,
    )
    reopened = JobStore(runtime_dir, database_path)
    recovered = reopened.get_job(job_id)
    assert recovered is not None
    assert recovered.current_page == 2
    assert recovered.total_pages == 3
    assert recovered.candidate_page_url == page_url


def test_phase1_database_schema_is_migrated_without_losing_jobs(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    database_path = runtime_dir / "jobs.sqlite3"
    job_id = str(uuid.uuid4())
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                progress INTEGER NOT NULL,
                current_stage TEXT NOT NULL,
                current_stage_message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                result_json TEXT,
                error_json TEXT
            )
            """
        )
        timestamp = "2026-01-01T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO jobs VALUES (?, 'queued', 0, 'queued', 'Analysis queued', ?, ?, NULL, NULL)
            """,
            (job_id, timestamp, timestamp),
        )

    store = JobStore(runtime_dir, database_path)
    migrated = store.get_job(job_id)
    assert migrated is not None
    assert migrated.current_page == 1
    assert migrated.total_pages == 1
    assert migrated.candidate_page_url is None
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
    assert {"current_page", "total_pages", "candidate_page_url"} <= columns


def test_app_construction_does_not_recover_jobs_but_lifespan_startup_does(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    database_path = runtime_dir / "jobs.sqlite3"
    seed_store = JobStore(runtime_dir, database_path)
    job_id = str(uuid.uuid4())
    seed_store.create_job(job_id)

    settings = Settings(
        runtime_dir=runtime_dir,
        max_upload_mb=15,
        retention_hours=24,
        cleanup_interval_seconds=30,
        worker_count=1,
        max_render_dimension=1200,
        cors_origins=("http://localhost:5173",),
        ocr_provider_preference="auto",
        ocr_device="cpu",
    )
    application = create_app(settings)

    # Factory construction/import is read-only with respect to job state.
    before_startup = application.state.store.get_job(job_id)
    assert before_startup is not None
    assert before_startup.state.value == "queued"
    assert application.state.store.get_events_after(job_id, 0) == []

    with TestClient(application):
        recovered = application.state.store.get_job(job_id)
        assert recovered is not None
        assert recovered.state.value == "failed"
        assert recovered.error is not None
        assert recovered.error.code == "service_restarted"
        events = application.state.store.get_events_after(job_id, 0)
        assert len(events) == 1
        assert events[0][0] == "error"
        assert events[0][1].stage_id.value == "failed"

        # Explicit startup is idempotent and cannot duplicate recovery events.
        application.state.store.startup()
        assert len(application.state.store.get_events_after(job_id, 0)) == 1
