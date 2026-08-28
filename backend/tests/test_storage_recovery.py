from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.core.storage import JobStore
from backend.app.main import create_app


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
