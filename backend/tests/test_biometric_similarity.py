from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.app.docuvault.repository import DocumentProfile
from backend.app.models.contracts import BoundingBox, CheckStatus
from backend.app.services.biometric_similarity import (
    RegionSelection,
    compare_biometric_regions,
)
from backend.app.services.documents import ValidatedUpload, validate_upload
from backend.tests.conftest import SYNTHETIC_DIR, wait_for_completion


REGION = BoundingBox(x=0.2, y=0.4, width=0.6, height=1 / 3)


def _signature(variant: int = 0) -> np.ndarray:
    image = np.full((120, 360, 3), 255, dtype=np.uint8)
    if variant < 2:
        offset = variant * 2
        points = np.array(
            [
                [18, 75],
                [52, 30 + offset],
                [82, 88],
                [118, 35],
                [150, 76],
                [196, 44 + offset],
                [236, 70],
                [286, 48],
                [338, 62],
            ],
            dtype=np.int32,
        )
        cv2.polylines(image, [points], False, (20, 20, 20), 3, cv2.LINE_AA)
        cv2.ellipse(image, (160, 64 + offset), (52, 25), -8, 20, 330, (20, 20, 20), 2, cv2.LINE_AA)
        cv2.line(image, (60, 95), (310, 86 + offset), (20, 20, 20), 2, cv2.LINE_AA)
    else:
        for x in range(30, 330, 42):
            cv2.line(image, (x, 25), (x + 18, 96), (20, 20, 20), 4, cv2.LINE_AA)
            cv2.line(image, (x + 18, 96), (x + 34, 32), (20, 20, 20), 4, cv2.LINE_AA)
    return image


def _handwriting(variant: int = 0) -> np.ndarray:
    image = np.full((120, 360, 3), 255, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SCRIPT_SIMPLEX if variant < 2 else cv2.FONT_HERSHEY_DUPLEX
    text = "local sample" if variant < 2 else "BLOCK 7429"
    cv2.putText(
        image,
        text,
        (12, 72 + min(variant, 1)),
        font,
        1.35,
        (20, 20, 20),
        2 if variant < 2 else 4,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        "verification" if variant < 2 else "TEST DATA",
        (46, 106),
        font,
        0.7,
        (20, 20, 20),
        1 if variant < 2 else 3,
        cv2.LINE_AA,
    )
    return image


def _upload(image: np.ndarray, field: str) -> ValidatedUpload:
    encoded, payload = cv2.imencode(".png", image)
    assert encoded
    return validate_upload(
        field=field,
        filename=f"{field}.png",
        content_type="image/png",
        data=payload.tobytes(),
        max_bytes=2_000_000,
    )


def _candidate_page(
    tmp_path: Path,
    sample: np.ndarray,
    *,
    outer_value: int = 255,
) -> SimpleNamespace:
    page = np.full((360, 600, 3), outer_value, dtype=np.uint8)
    page[144:264, 120:480] = sample
    path = tmp_path / f"candidate-{outer_value}.png"
    assert cv2.imwrite(str(path), page)
    return SimpleNamespace(page_number=1, image_path=path)


def _profile(kind: str) -> DocumentProfile:
    security_regions = {name: [] for name in ("logo", "seal", "photo", "signature", "handwriting", "qr", "barcode")}
    security_regions[kind] = [
        {
            "region_id": kind,
            "page": 1,
            "box": REGION.model_dump(),
            "label": f"Expected {kind} region",
        }
    ]
    return DocumentProfile(
        profile_id="fictional-biometric-test",
        manifest={"security_regions": security_regions},
        fingerprint="0" * 64,
        enabled=True,
        source_name="synthetic-test",
    )


def test_handwriting_ensemble_reports_consistency_and_mismatch(tmp_path: Path) -> None:
    exemplar = _upload(_handwriting(0), "handwriting_exemplar")
    selection = (RegionSelection(page_number=1, bounding_box=REGION),)

    consistent = compare_biometric_regions(
        kind="handwriting",
        candidate_pages=[_candidate_page(tmp_path, _handwriting(1))],
        exemplars=[exemplar],
        profile=None,
        user_regions=selection,
    )
    mismatch = compare_biometric_regions(
        kind="handwriting",
        candidate_pages=[_candidate_page(tmp_path, _handwriting(2))],
        exemplars=[exemplar],
        profile=None,
        user_regions=selection,
    )

    assert consistent.status in {CheckStatus.PASSED, CheckStatus.WARNING}
    assert consistent.similarity_score is not None
    assert mismatch.similarity_score is not None
    assert consistent.similarity_score > mismatch.similarity_score
    assert mismatch.status is CheckStatus.FAILED
    assert consistent.closest_exemplar == "exemplar_1"
    assert "not legal identity" in consistent.limitations[0]


def test_signature_ensemble_separates_identity_and_compositing(tmp_path: Path) -> None:
    exemplars = [
        _upload(_signature(0), "signature_exemplar_1"),
        _upload(_signature(1), "signature_exemplar_2"),
    ]
    clean = compare_biometric_regions(
        kind="signature",
        candidate_pages=[_candidate_page(tmp_path, _signature(0))],
        exemplars=exemplars,
        profile=_profile("signature"),
    )
    pasted = compare_biometric_regions(
        kind="signature",
        candidate_pages=[_candidate_page(tmp_path, _signature(0), outer_value=225)],
        exemplars=exemplars,
        profile=_profile("signature"),
    )
    mismatch = compare_biometric_regions(
        kind="signature",
        candidate_pages=[_candidate_page(tmp_path, _signature(2))],
        exemplars=exemplars,
        profile=None,
        user_regions=(RegionSelection(page_number=1, bounding_box=REGION),),
    )

    assert clean.similarity_score is not None and clean.similarity_score >= 66
    assert pasted.similarity_score == clean.similarity_score
    assert pasted.compositing_score is not None
    assert clean.compositing_score is not None
    assert pasted.compositing_score >= 65
    assert pasted.compositing_score > clean.compositing_score
    assert pasted.status is CheckStatus.WARNING
    assert mismatch.similarity_score is not None
    assert mismatch.similarity_score < clean.similarity_score
    assert mismatch.status is CheckStatus.FAILED
    assert {"contour_hu", "skeleton", "gradient_hog", "keypoints", "texture"} <= set(
        clean.region_evidence[0].measurements
    )


def test_signature_requires_two_exemplars(tmp_path: Path) -> None:
    result = compare_biometric_regions(
        kind="signature",
        candidate_pages=[_candidate_page(tmp_path, _signature(0))],
        exemplars=[_upload(_signature(0), "signature_exemplar")],
        profile=None,
        user_regions=(RegionSelection(page_number=1, bounding_box=REGION),),
    )

    assert result.status is CheckStatus.SKIPPED
    assert "At least 2" in result.explanation


def test_api_runs_biometric_stages_with_bounded_region_input(
    client: TestClient,
) -> None:
    region_payload = json.dumps(
        [{"page_number": 1, "bounding_box": REGION.model_dump()}]
    )
    response = client.post(
        "/api/v1/analyses/reference",
        data={
            "comparison_mode": "template",
            "handwriting_regions": region_payload,
            "signature_regions": region_payload,
        },
        files=[
            (
                "reference",
                (
                    "reference.pdf",
                    (SYNTHETIC_DIR / "template_reference.pdf").read_bytes(),
                    "application/pdf",
                ),
            ),
            (
                "candidate",
                (
                    "candidate.pdf",
                    (SYNTHETIC_DIR / "template_legitimate_candidate.pdf").read_bytes(),
                    "application/pdf",
                ),
            ),
            (
                "handwriting_exemplars",
                ("handwriting.png", _upload(_handwriting(0), "h").data, "image/png"),
            ),
            (
                "signature_exemplars",
                ("signature-1.png", _upload(_signature(0), "s1").data, "image/png"),
            ),
            (
                "signature_exemplars",
                ("signature-2.png", _upload(_signature(1), "s2").data, "image/png"),
            ),
        ],
    )

    assert response.status_code == 202, response.text
    completed = wait_for_completion(client, response.json()["status_url"])
    assert completed["state"] == "completed", completed.get("error")
    result = completed["result"]
    assert result["handwriting"]["status"] != "not_applicable"
    assert result["signature_similarity"]["status"] != "not_applicable"
    events = client.get(response.json()["events_url"]).text
    assert '"stage_id":"comparing_handwriting"' in events
    assert '"stage_id":"comparing_signatures"' in events
