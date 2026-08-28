"""Environment-backed settings without a dependency on pydantic-settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]


def _runtime_dir() -> Path:
    configured = Path(os.getenv("DOCUVERIFY_RUNTIME_DIR", "runtime"))
    if not configured.is_absolute():
        configured = BACKEND_DIR / configured
    return configured.resolve()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _origins() -> tuple[str, ...]:
    raw = os.getenv("DOCUVERIFY_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return tuple(value.strip() for value in raw.split(",") if value.strip())


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in choices:
        allowed = ", ".join(sorted(choices))
        raise ValueError(f"{name} must be one of: {allowed}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    runtime_dir: Path = field(
        default_factory=_runtime_dir
    )
    max_upload_mb: int = field(
        default_factory=lambda: _env_int("DOCUVERIFY_MAX_UPLOAD_MB", 15, 1, 100)
    )
    retention_hours: int = field(
        default_factory=lambda: _env_int("DOCUVERIFY_RETENTION_HOURS", 24, 1, 720)
    )
    cleanup_interval_seconds: int = field(
        default_factory=lambda: _env_int(
            "DOCUVERIFY_CLEANUP_INTERVAL_SECONDS", 300, 30, 3600
        )
    )
    worker_count: int = field(
        default_factory=lambda: _env_int("DOCUVERIFY_WORKERS", 2, 1, 4)
    )
    max_render_dimension: int = field(
        default_factory=lambda: _env_int("DOCUVERIFY_MAX_RENDER_DIMENSION", 1800, 1000, 2400)
    )
    max_pages: int = field(
        default_factory=lambda: _env_int("DOCUVERIFY_MAX_PAGES", 10, 1, 10)
    )
    cors_origins: tuple[str, ...] = field(default_factory=_origins)
    ocr_provider_preference: str = field(
        default_factory=lambda: _env_choice(
            "DOCUVERIFY_OCR_PROVIDER", "auto", {"auto", "rapidocr", "none"}
        )
    )
    ocr_device: str = field(
        default_factory=lambda: _env_choice(
            "DOCUVERIFY_OCR_DEVICE", "cpu", {"cpu", "gpu"}
        )
    )

    def __post_init__(self) -> None:
        if self.ocr_device == "gpu":
            raise ValueError(
                "GPU OCR is not enabled by the verified Phase 2 environment; "
                "set DOCUVERIFY_OCR_DEVICE=cpu"
            )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def database_path(self) -> Path:
        return self.runtime_dir / "jobs.sqlite3"
