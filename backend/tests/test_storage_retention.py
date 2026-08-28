from __future__ import annotations

import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from backend.app.core import storage as storage_module
from backend.app.core.storage import JobStore
from backend.app.models.contracts import JobState, StageId


def _mark_completed(store: JobStore, job_id: str) -> None:
    store.update_job(
        job_id,
        state=JobState.COMPLETED,
        progress=100,
        stage=StageId.COMPLETE,
        message="Complete",
    )


def test_locked_job_directory_keeps_row_and_assets_until_retry(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = JobStore(runtime_dir, runtime_dir / "jobs.sqlite3")
    job_id = str(uuid.uuid4())
    store.create_job(job_id)
    _mark_completed(store, job_id)
    job_dir = store.job_directory(job_id)
    asset_path = job_dir / "assets" / "candidate-page.png"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    store.register_asset(job_id, "candidate-page", asset_path)

    real_rmtree = shutil.rmtree
    clock = storage_module.utc_now()
    monkeypatch.setattr(storage_module, "utc_now", lambda: clock + timedelta(hours=48))

    def locked_directory(_: Path) -> None:
        raise PermissionError("simulated Windows file lock")

    monkeypatch.setattr(storage_module.shutil, "rmtree", locked_directory)
    assert store.cleanup_expired(retention_hours=24) == 0
    assert store.get_job(job_id) is not None
    resolved_asset = store.resolve_asset(job_id, "candidate-page")
    assert resolved_asset is not None
    assert resolved_asset[0].read_bytes().startswith(b"\x89PNG")
    assert job_dir.is_dir()

    monkeypatch.setattr(storage_module.shutil, "rmtree", real_rmtree)
    assert store.cleanup_expired(retention_hours=24) == 1
    assert store.get_job(job_id) is None
    assert store.resolve_asset(job_id, "candidate-page") is None
    assert not job_dir.exists()


def test_missing_expired_directory_allows_database_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = JobStore(runtime_dir, runtime_dir / "jobs.sqlite3")
    job_id = str(uuid.uuid4())
    store.create_job(job_id)
    _mark_completed(store, job_id)
    job_dir = store.job_directory(job_id)
    job_dir.rmdir()
    clock = storage_module.utc_now()
    monkeypatch.setattr(storage_module, "utc_now", lambda: clock + timedelta(hours=48))

    assert store.cleanup_expired(retention_hours=24) == 1
    assert store.get_job(job_id) is None


def test_expiry_cleanup_never_selects_active_jobs(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = JobStore(runtime_dir, runtime_dir / "jobs.sqlite3")
    queued_id = str(uuid.uuid4())
    running_id = str(uuid.uuid4())
    store.create_job(queued_id)
    store.create_job(running_id)
    store.update_job(
        running_id,
        state=JobState.RUNNING,
        progress=50,
        stage=StageId.EXTRACTING_TEXT,
        message="Extracting text",
    )
    queued_dir = store.job_directory(queued_id)
    running_dir = store.job_directory(running_id)
    clock = storage_module.utc_now()
    monkeypatch.setattr(storage_module, "utc_now", lambda: clock + timedelta(hours=48))

    assert store.cleanup_expired(retention_hours=24) == 0
    assert store.get_job(queued_id) is not None
    assert store.get_job(running_id) is not None
    assert queued_dir.is_dir()
    assert running_dir.is_dir()
