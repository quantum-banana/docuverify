from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_endpoint_reports_real_capabilities(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["capabilities"]["pdf_rendering"] is True
    assert body["capabilities"]["visual_comparison"] is True
    assert body["capabilities"]["raster_ocr"] is False
    assert body["capabilities"]["sse"] is True


def test_diagnostics_are_safe_and_honest(client: TestClient) -> None:
    response = client.get("/api/v1/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert body["backend_ready"] is True
    assert body["runtime_writable"] is True
    assert body["ocr_provider"] == "pymupdf_embedded_text"
    assert body["ocr_device"] == "cpu"
    assert body["opencv_version"]
    assert body["pymupdf_version"]
    serialized = response.text.casefold()
    assert "token" not in serialized
    assert "password" not in serialized
    assert "runtime_dir" not in serialized


def test_unknown_job_has_structured_error(client: TestClient) -> None:
    response = client.get("/api/v1/analyses/not-a-real-job")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "job_not_found",
            "message": "Analysis job not found.",
            "field": None,
            "details": {},
        }
    }


def test_missing_multipart_field_has_structured_error(client: TestClient) -> None:
    response = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": "exact"},
        files={"reference": ("reference.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
