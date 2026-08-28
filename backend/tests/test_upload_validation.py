from __future__ import annotations

import io
from pathlib import Path

import fitz
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app.services.documents import extract_page_text, render_document_page, validate_upload
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


def test_png_upload_is_supported_with_truthful_raster_ocr(client: TestClient) -> None:
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
    assert result["text_extraction"]["reference_source"] == "rapidocr_onnxruntime"
    assert result["pages"][0]["ocr"]["reference_succeeded"] is True
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


def test_blank_raster_image_is_rejected_as_an_unusable_page(
    client: TestClient,
) -> None:
    output = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(output, format="PNG")

    response = _post_bytes(
        client,
        reference=("blank.png", output.getvalue(), "image/png"),
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "empty_page",
        "message": "The reference image is an unusable blank page.",
        "field": "reference",
        "details": {"page_count": 1, "pages": [1]},
    }


def test_all_white_image_only_pdf_page_is_rejected(client: TestClient) -> None:
    image_bytes = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(image_bytes, format="PNG")
    document = fitz.open()
    page = document.new_page(width=640, height=480)
    page.insert_image(page.rect, stream=image_bytes.getvalue())
    payload = document.tobytes(no_new_id=True)
    document.close()

    response = _post_bytes(
        client,
        reference=("blank-image-page.pdf", payload, "application/pdf"),
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "empty_page"
    assert error["details"] == {"page_count": 1, "pages": [1]}


def test_white_on_white_text_only_pdf_page_is_rejected(client: TestClient) -> None:
    document = fitz.open()
    page = document.new_page(width=360, height=480)
    page.insert_text(
        (40, 70),
        "INVISIBLE SYNTHETIC CONTENT",
        fontsize=14,
        color=(1.0, 1.0, 1.0),
    )
    payload = document.tobytes(no_new_id=True)
    document.close()

    response = _post_bytes(
        client,
        reference=("invisible-text.pdf", payload, "application/pdf"),
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "empty_page"
    assert error["details"] == {"page_count": 1, "pages": [1]}


def test_visible_text_only_pdf_page_is_accepted() -> None:
    document = fitz.open()
    page = document.new_page(width=360, height=480)
    page.insert_text((40, 70), "VISIBLE SYNTHETIC RECORD", fontsize=14)
    payload = document.tobytes(no_new_id=True)
    document.close()

    upload = validate_upload(
        field="reference",
        filename="visible-text.pdf",
        content_type="application/pdf",
        data=payload,
        max_bytes=10 * 1024 * 1024,
    )
    assert upload.page_count == 1


def test_meaningful_raster_image_and_image_only_pdf_are_accepted() -> None:
    canvas = Image.new("RGB", (640, 480), "white")
    drawing = ImageDraw.Draw(canvas)
    drawing.rectangle((80, 80, 560, 400), outline="black", width=4)
    drawing.text((120, 180), "DOCUVERIFY SYNTHETIC RECORD", fill="black")
    image_bytes = io.BytesIO()
    canvas.save(image_bytes, format="PNG")
    payload = image_bytes.getvalue()

    image_upload = validate_upload(
        field="reference",
        filename="meaningful.png",
        content_type="image/png",
        data=payload,
        max_bytes=10 * 1024 * 1024,
    )
    assert image_upload.page_count == 1

    document = fitz.open()
    page = document.new_page(width=640, height=480)
    page.insert_image(page.rect, stream=payload)
    pdf_payload = document.tobytes(no_new_id=True)
    document.close()
    pdf_upload = validate_upload(
        field="reference",
        filename="meaningful.pdf",
        content_type="application/pdf",
        data=pdf_payload,
        max_bytes=10 * 1024 * 1024,
    )
    assert pdf_upload.page_count == 1


def test_transparent_png_uses_white_composite_for_render_and_ocr_input() -> None:
    source = Image.open(SYNTHETIC_DIR / "raster_only_document.png").convert("RGBA")
    pixels = np.asarray(source).copy()
    # Transparent pixels deliberately carry black RGB values. Dropping alpha
    # would expose a black border; correct page compositing renders it white.
    pixels[:24, :, :3] = 0
    pixels[:24, :, 3] = 0
    pixels[:, :24, :3] = 0
    pixels[:, :24, 3] = 0
    output = io.BytesIO()
    Image.fromarray(pixels, mode="RGBA").save(output, format="PNG")

    upload = validate_upload(
        field="reference",
        filename="transparent-record.png",
        content_type="image/png",
        data=output.getvalue(),
        max_bytes=10 * 1024 * 1024,
    )
    rendered = render_document_page(upload, 0, 1800)

    assert tuple(rendered.image[0, 0]) == (255, 255, 255)
    assert int(rendered.image.min()) < 80
    extraction = extract_page_text(upload, rendered)
    assert extraction.succeeded is True
    assert "ORBITAL" in extraction.text.upper()


def test_pdf_render_releases_closed_document_resources(monkeypatch) -> None:
    from backend.app.services.documents import render_document_page

    fixture = SYNTHETIC_DIR / "reference.pdf"
    upload = validate_upload(
        field="reference",
        filename=fixture.name,
        content_type="application/pdf",
        data=fixture.read_bytes(),
        max_bytes=10 * 1024 * 1024,
    )
    shrink_calls: list[int] = []
    real_store_shrink = fitz.TOOLS.store_shrink

    def record_store_shrink(percent: int) -> int | None:
        shrink_calls.append(percent)
        return real_store_shrink(percent)

    monkeypatch.setattr(fitz.TOOLS, "store_shrink", record_store_shrink)
    rendered = render_document_page(upload, 0, 1200)

    assert shrink_calls == [100]
    assert rendered.image.flags.owndata
    assert rendered.image.size > 0


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
