from __future__ import annotations

import io
from pathlib import Path

import fitz
from fastapi.testclient import TestClient
from PIL import Image

from backend.tests.conftest import SYNTHETIC_DIR, upload_pair, wait_for_completion


def _post_bytes(
    client: TestClient,
    *,
    reference: tuple[str, bytes, str],
    candidate: tuple[str, bytes, str] | None = None,
    comparison_mode: str = "exact",
):
    return client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": comparison_mode},
        files={
            "reference": reference,
            "candidate": candidate or reference,
        },
    )


def test_valid_pdf_upload_returns_202_and_completes(client: TestClient) -> None:
    response = upload_pair(client)
    assert response.status_code == 202
    created = response.json()
    assert created["state"] == "queued"
    assert created["status_url"].endswith(created["job_id"])
    assert created["events_url"].endswith(f"{created['job_id']}/events")
    job = wait_for_completion(client, created["status_url"])
    assert job["state"] == "completed"
    assert job["result"]["candidate"]["content_type"] == "application/pdf"


def test_png_upload_is_supported_without_claiming_ocr(client: TestClient) -> None:
    response = upload_pair(
        client,
        reference_name="reference.png",
        candidate_name="tampered_candidate.png",
        media_type="image/png",
    )
    assert response.status_code == 202
    job = wait_for_completion(client, response.json()["status_url"])
    assert job["state"] == "completed"
    result = job["result"]
    assert result["text_extraction"]["reference_source"] == "unavailable_for_raster"
    assert result["finding_count"] >= 1


def test_jpeg_upload_is_supported(client: TestClient) -> None:
    source = Image.open(SYNTHETIC_DIR / "reference.png").convert("RGB")
    output = io.BytesIO()
    source.save(output, format="JPEG", quality=95, optimize=False, progressive=False)
    jpeg = output.getvalue()
    response = _post_bytes(
        client,
        reference=("reference.jpg", jpeg, "image/jpeg"),
        candidate=("candidate.jpeg", jpeg, "image/jpeg"),
    )
    assert response.status_code == 202
    job = wait_for_completion(client, response.json()["status_url"])
    assert job["state"] == "completed"
    assert job["result"]["overall_tampering_risk"] <= 15


def test_unsupported_extension_is_rejected(client: TestClient) -> None:
    response = _post_bytes(
        client,
        reference=("document.txt", b"not a document", "text/plain"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_file_type"


def test_mime_extension_mismatch_is_rejected(client: TestClient) -> None:
    pdf = (SYNTHETIC_DIR / "reference.pdf").read_bytes()
    response = _post_bytes(
        client,
        reference=("document.pdf", pdf, "image/png"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "mime_type_mismatch"


def test_empty_file_is_rejected(client: TestClient) -> None:
    response = _post_bytes(
        client,
        reference=("empty.pdf", b"", "application/pdf"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_file"


def test_corrupt_pdf_is_rejected(client: TestClient) -> None:
    response = _post_bytes(
        client,
        reference=("corrupt.pdf", b"%PDF-1.7\nthis is not a PDF", "application/pdf"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "corrupt_pdf"


def test_corrupt_png_is_rejected(client: TestClient) -> None:
    response = _post_bytes(
        client,
        reference=("corrupt.png", b"\x89PNG\r\n\x1a\ninvalid", "image/png"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "corrupt_image"


def test_multi_page_pdf_is_rejected_with_page_count(client: TestClient) -> None:
    document = fitz.open()
    document.new_page()
    document.new_page()
    payload = document.tobytes(no_new_id=True)
    document.close()
    response = _post_bytes(
        client,
        reference=("two-pages.pdf", payload, "application/pdf"),
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "single_page_required"
    assert error["details"]["page_count"] == 2


def test_only_exact_comparison_mode_is_accepted(client: TestClient) -> None:
    pdf = (SYNTHETIC_DIR / "reference.pdf").read_bytes()
    response = _post_bytes(
        client,
        reference=("reference.pdf", pdf, "application/pdf"),
        comparison_mode="semantic",
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_comparison_mode"
