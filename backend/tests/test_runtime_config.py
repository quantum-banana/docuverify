from __future__ import annotations

from pathlib import Path

from backend.app.core.config import BACKEND_DIR, Settings


def test_runtime_directory_can_be_redirected_before_app_construction(
    tmp_path: Path, monkeypatch
) -> None:
    redirected = tmp_path / "redirected-application-runtime"
    monkeypatch.setenv("DOCUVERIFY_RUNTIME_DIR", str(redirected))

    settings = Settings()

    assert settings.runtime_dir == redirected.resolve()
    assert settings.runtime_dir != (BACKEND_DIR / "runtime").resolve()
    assert settings.database_path.parent == settings.runtime_dir


def test_production_runtime_default_is_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("DOCUVERIFY_RUNTIME_DIR", raising=False)

    settings = Settings()

    assert settings.runtime_dir == (BACKEND_DIR / "runtime").resolve()
    assert settings.database_path == settings.runtime_dir / "jobs.sqlite3"
