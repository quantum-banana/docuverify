from __future__ import annotations

import time
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import Settings
from backend.app.main import create_app


SYNTHETIC_DIR = PROJECT_ROOT / "samples" / "synthetic"
EXPECTED_DIR = PROJECT_ROOT / "samples" / "expected"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        runtime_dir=tmp_path / "runtime",
        max_upload_mb=15,
        retention_hours=24,
        worker_count=2,
        max_render_dimension=1200,
        cors_origins=("http://localhost:5173",),
        ocr_provider_preference="auto",
        ocr_device="cpu",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def upload_pair(
    client: TestClient,
    reference_name: str = "reference.pdf",
    candidate_name: str = "tampered_candidate.pdf",
    *,
    media_type: str = "application/pdf",
):
    return client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": "exact"},
        files={
            "reference": (
                reference_name,
                (SYNTHETIC_DIR / reference_name).read_bytes(),
                media_type,
            ),
            "candidate": (
                candidate_name,
                (SYNTHETIC_DIR / candidate_name).read_bytes(),
                media_type,
            ),
        },
    )


def wait_for_completion(
    client: TestClient, status_url: str, timeout_seconds: float = 20.0
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(status_url)
        assert response.status_code == 200
        payload = response.json()
        if payload["state"] in {"completed", "failed"}:
            return payload
        time.sleep(0.02)
    pytest.fail(f"analysis did not complete within {timeout_seconds} seconds")


@pytest.fixture
def completed_demo(client: TestClient) -> dict:
    response = client.post("/api/v1/demo/reference")
    assert response.status_code == 202
    created = response.json()
    completed = wait_for_completion(client, created["status_url"])
    assert completed["state"] == "completed", completed.get("error")
    return completed
