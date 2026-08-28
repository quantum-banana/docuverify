from __future__ import annotations

import fitz
import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.app.forensics.differences import DifferenceRegion
from backend.app.models.contracts import ComparisonMode, RegionRole
from backend.app.services import pipeline
from backend.app.services.documents import TextExtraction, TextWord
from backend.tests.conftest import SYNTHETIC_DIR, wait_for_completion


def test_exact_mode_reports_bounded_metadata_change_on_full_page(
    client: TestClient,
) -> None:
    reference = _pdf_with_metadata("Trusted synthetic record")
    candidate = _pdf_with_metadata("Changed synthetic record")

    created, result = _analyse_bytes(client, reference, candidate)

    finding = next(
        item
        for item in result["pages"][0]["findings"]
        if item["category"] == "metadata_change"
    )
    assert finding["bounding_box"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    assert 15.0 <= finding["risk_score"] < 40.0
    assert finding["supporting_measurements"]["changed_fields"] == "title"
    assert result["overall_tampering_risk"] == finding["risk_score"]

    client.app.state.manager.wait(created["job_id"])
    assert created["job_id"] not in client.app.state.manager._futures


def test_ocr_failure_is_zero_risk_evidence_and_visual_analysis_continues(
    client: TestClient, monkeypatch
) -> None:
    failed = TextExtraction(
        text="",
        words=(),
        source="rapidocr_onnxruntime",
        confidence=None,
        succeeded=False,
        coverage=0.0,
        error="simulated provider failure",
    )
    monkeypatch.setattr(pipeline, "_extract_page_text", lambda *args, **kwargs: failed)
    data = (SYNTHETIC_DIR / "reference.pdf").read_bytes()

    _, result = _analyse_bytes(client, data, data)

    page = result["pages"][0]
    finding = next(item for item in page["findings"] if item["category"] == "ocr_uncertainty")
    assert finding["risk_score"] == 0.0
    assert finding["severity"] == "info"
    assert page["risk_score"] == 0.0
    assert page["coverage_score"] == 70.0
    assert page["ocr"]["reference_succeeded"] is False
    assert page["ocr"]["candidate_succeeded"] is False


def test_stable_moved_text_uses_layout_displacement_category() -> None:
    reference = _text("ARCHIVE", (0.20, 0.30, 0.34, 0.34))
    candidate = _text("ARCHIVE", (0.20, 0.39, 0.34, 0.43))
    region = DifferenceRegion(100, 220, 240, 370, changed_pixels=900, mean_delta=55.0)

    displacement = pipeline._layout_displacement_score(
        region, reference, candidate, 600, 800
    )
    category, title, explanation, risk = pipeline._describe_finding(
        comparison_mode=ComparisonMode.EXACT,
        role=RegionRole.FIXED,
        has_text_change=False,
        before="",
        after="",
        base_risk=66.0,
        background_score=0.0,
        typography_score=0.0,
        layout_displacement=displacement,
    )

    assert displacement >= 0.08
    assert category == "layout_displacement"
    assert "moved" in title.casefold()
    assert "position" in explanation.casefold()
    assert risk == 66.0


def test_shifted_emblem_like_geometry_uses_non_identity_category() -> None:
    reference = np.full((240, 240, 3), 255, dtype=np.uint8)
    candidate = reference.copy()
    _draw_emblem(reference, (50, 190))
    _draw_emblem(candidate, (82, 190))
    region = DifferenceRegion(25, 165, 108, 215, changed_pixels=1100, mean_delta=62.0)
    empty = TextExtraction("", (), "test", None)

    score = pipeline._logo_seal_displacement_score(
        region, reference, candidate, empty, empty
    )
    category, _, explanation, _ = pipeline._describe_finding(
        comparison_mode=ComparisonMode.EXACT,
        role=RegionRole.FIXED,
        has_text_change=False,
        before="",
        after="",
        base_risk=70.0,
        background_score=0.0,
        typography_score=0.0,
        logo_seal_displacement=score,
    )

    assert score >= 0.46
    assert category == "logo_seal_displacement"
    assert "does not identify" in explanation


def _analyse_bytes(
    client: TestClient, reference: bytes, candidate: bytes
) -> tuple[dict, dict]:
    response = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": "exact"},
        files={
            "reference": ("reference.pdf", reference, "application/pdf"),
            "candidate": ("candidate.pdf", candidate, "application/pdf"),
        },
    )
    assert response.status_code == 202, response.text
    created = response.json()
    completed = wait_for_completion(client, created["status_url"])
    assert completed["state"] == "completed", completed.get("error")
    return created, completed["result"]


def _pdf_with_metadata(title: str) -> bytes:
    document = fitz.open()
    page = document.new_page(width=360, height=480)
    page.insert_text((40, 70), "SYNTHETIC TRUSTED RECORD FOR AUDIT TESTING")
    document.set_metadata({"title": title})
    payload = document.tobytes(no_new_id=True)
    document.close()
    return payload


def _text(value: str, bbox: tuple[float, float, float, float]) -> TextExtraction:
    return TextExtraction(
        text=value,
        words=(TextWord(value, bbox, 1.0),),
        source="test",
        confidence=1.0,
    )


def _draw_emblem(image: np.ndarray, center: tuple[int, int]) -> None:
    cv2.circle(image, center, 18, (20, 110, 120), 3)
    cv2.line(
        image,
        (center[0] - 12, center[1]),
        (center[0] + 12, center[1]),
        (20, 110, 120),
        2,
    )
