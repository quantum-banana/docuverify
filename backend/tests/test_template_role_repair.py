from __future__ import annotations

import uuid
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.app.core.config import Settings
from backend.app.core.storage import JobStore
from backend.app.forensics.alignment import AlignmentResult
from backend.app.forensics.differences import DifferenceRegion, DifferenceResult
from backend.app.forensics.text import TextChange, compare_text
from backend.app.models.contracts import ComparisonMode, RegionRole
from backend.app.services.documents import TextExtraction, TextWord
from backend.app.services.pipeline import AnalysisManager


@pytest.fixture
def finding_context(tmp_path: Path):
    settings = Settings(runtime_dir=tmp_path / "runtime", worker_count=1)
    store = JobStore(settings.runtime_dir, settings.database_path)
    manager = AnalysisManager(settings, store)
    job_id = str(uuid.uuid4())
    store.create_job(job_id)
    assets_dir = store.job_directory(job_id) / "assets"
    assets_dir.mkdir(parents=True)
    try:
        yield manager, job_id, assets_dir
    finally:
        manager.shutdown()


def test_legitimate_cross_person_template_values_are_suggestions_only(
    finding_context,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference = _fictional_id(
        "Student Cedar",
        "DV-1001",
        "03/02/2004",
        "10 Cedar Lane",
    )
    candidate = _fictional_id(
        "Learner Birch",
        "DV-2042",
        "11/09/2005",
        "44 Maple Road",
    )
    comparison = compare_text(reference, candidate, comparison_mode="template")

    assert len(comparison.changes) == 4
    assert {change.field_label for change in comparison.changes} == {
        "name",
        "student id",
        "date of birth",
        "address",
    }
    assert all(change.role.value == "variable" for change in comparison.changes)

    reference_image, candidate_image = _cross_person_images()
    text_regions = [_region_for_change(change) for change in comparison.changes]
    media_regions = [
        DifferenceRegion(
            90,
            480,
            220,
            680,
            changed_pixels=19_000,
            mean_delta=42.0,
            evidence_sources={"visual_difference"},
        ),
        DifferenceRegion(
            390,
            480,
            540,
            680,
            changed_pixels=17_000,
            mean_delta=58.0,
            evidence_sources={"visual_difference"},
        ),
    ]

    findings, text_suggestions = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        text_regions,
        ComparisonMode.TEMPLATE,
    )
    media_findings, media_suggestions = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        media_regions,
        ComparisonMode.TEMPLATE,
    )

    assert findings == []
    assert media_findings == []
    assert len(text_suggestions + media_suggestions) == 6
    assert all(
        suggestion.role is RegionRole.VARIABLE
        for suggestion in text_suggestions + media_suggestions
    )
    assert list(assets_dir.glob("*.png")) == []


def test_fixed_template_label_alteration_remains_high_risk(finding_context) -> None:
    manager, job_id, assets_dir = finding_context
    reference = _text(
        TextWord("Name", (0.10, 0.25, 0.18, 0.29), 1.0),
        TextWord("Student Cedar", (0.22, 0.25, 0.40, 0.29), 1.0),
    )
    candidate = _text(
        TextWord("Legal Name", (0.10, 0.25, 0.20, 0.29), 1.0),
        TextWord("Student Cedar", (0.22, 0.25, 0.40, 0.29), 1.0),
    )
    change = compare_text(reference, candidate, comparison_mode="template").changes[0]
    assert change.role.value == "fixed"

    image = np.full((800, 600, 3), 255, dtype=np.uint8)
    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        image,
        image.copy(),
        reference,
        candidate,
        [_region_for_change(change)],
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    assert findings[0].category == "fixed_label_change"
    assert findings[0].risk_score >= 76.0
    assert findings[0].region_role is RegionRole.FIXED


def test_manipulated_variable_keeps_paste_and_typography_evidence(
    finding_context,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference = _text(
        TextWord("Name", (0.10, 0.30, 0.18, 0.34), 1.0),
        TextWord("Student Cedar", (0.22, 0.30, 0.42, 0.34), 1.0),
    )
    candidate = _text(
        TextWord("Name", (0.10, 0.30, 0.18, 0.34), 1.0),
        TextWord("Learner Birch", (0.22, 0.30, 0.40, 0.38), 1.0),
    )
    change = compare_text(reference, candidate, comparison_mode="template").changes[0]
    assert change.role.value == "variable"

    reference_image = np.full((800, 600, 3), 255, dtype=np.uint8)
    candidate_image = reference_image.copy()
    region = _region_for_change(change)
    cv2.rectangle(
        candidate_image,
        (region.x0, region.y0),
        (region.x1 - 1, region.y1 - 1),
        (205, 205, 205),
        thickness=-1,
    )
    findings, suggestions = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [region],
        ComparisonMode.TEMPLATE,
    )

    assert len(suggestions) == 1
    assert len(findings) == 1
    assert findings[0].category in {
        "background_compositing",
        "typography_inconsistency",
    }
    assert findings[0].risk_score >= 72.0
    assert findings[0].supporting_measurements[
        "typography_inconsistency_score"
    ] >= 0.35
    assert len(list(assets_dir.glob("*.png"))) == 3


def test_exact_mode_still_reports_changed_variable_content(finding_context) -> None:
    manager, job_id, assets_dir = finding_context
    reference = _text(
        _mixed_word("Name", "Student Cedar", 0.10, 0.22),
    )
    candidate = _text(
        _mixed_word("Name", "Learner Birch", 0.10, 0.22),
    )
    comparison = compare_text(reference, candidate, comparison_mode="exact")
    image = np.full((800, 600, 3), 255, dtype=np.uint8)

    findings, suggestions = _build(
        manager,
        job_id,
        assets_dir,
        image,
        image.copy(),
        reference,
        candidate,
        [_region_for_change(comparison.changes[0])],
        ComparisonMode.EXACT,
    )

    assert suggestions == []
    assert len(findings) == 1
    assert findings[0].category == "text_content_change"
    assert findings[0].region_role is RegionRole.FIXED


def _fictional_id(name: str, identifier: str, dob: str, address: str) -> TextExtraction:
    return _text(
        _mixed_word("Name", name, 0.08, 0.16),
        _mixed_word("Student ID", identifier, 0.08, 0.26),
        _mixed_word("Date of Birth", dob, 0.08, 0.36),
        _mixed_word("Address", address, 0.08, 0.46),
        TextWord("Photo", (0.04, 0.64, 0.13, 0.68), 1.0),
        TextWord("QR Code", (0.54, 0.64, 0.63, 0.68), 1.0),
    )


def _mixed_word(label: str, value: str, x0: float, y0: float) -> TextWord:
    text = f"{label}: {value}"
    return TextWord(text, (x0, y0, x0 + len(text) * 0.008, y0 + 0.04), 1.0)


def _text(*words: TextWord) -> TextExtraction:
    return TextExtraction(
        text=" ".join(word.text for word in words),
        words=words,
        source="deterministic_test",
        confidence=1.0,
    )


def _region_for_change(change: TextChange) -> DifferenceRegion:
    width, height = 600, 800
    x0 = max(0, int(change.bbox[0] * width) - 4)
    y0 = max(0, int(change.bbox[1] * height) - 4)
    x1 = min(width, int(np.ceil(change.bbox[2] * width)) + 4)
    y1 = min(height, int(np.ceil(change.bbox[3] * height)) + 4)
    return DifferenceRegion(
        x0,
        y0,
        x1,
        y1,
        changed_pixels=max(80, (x1 - x0) * (y1 - y0) // 4),
        mean_delta=48.0,
        evidence_sources={"text_change", "visual_difference"},
        text_changes=[change],
    )


def _cross_person_images() -> tuple[np.ndarray, np.ndarray]:
    reference = np.full((800, 600, 3), 255, dtype=np.uint8)
    candidate = reference.copy()
    cv2.rectangle(reference, (90, 480), (219, 679), (170, 170, 170), thickness=-1)
    cv2.rectangle(candidate, (90, 480), (219, 679), (125, 125, 125), thickness=-1)
    for row in range(8):
        for column in range(6):
            if (row + column) % 2 == 0:
                cv2.rectangle(
                    reference,
                    (390 + column * 25, 480 + row * 25),
                    (414 + column * 25, 504 + row * 25),
                    (0, 0, 0),
                    thickness=-1,
                )
            if (row * 3 + column) % 2 == 0:
                cv2.rectangle(
                    candidate,
                    (390 + column * 25, 480 + row * 25),
                    (414 + column * 25, 504 + row * 25),
                    (0, 0, 0),
                    thickness=-1,
                )
    return reference, candidate


def _build(
    manager: AnalysisManager,
    job_id: str,
    assets_dir: Path,
    reference_image: np.ndarray,
    candidate_image: np.ndarray,
    reference_text: TextExtraction,
    candidate_text: TextExtraction,
    regions: list[DifferenceRegion],
    mode: ComparisonMode,
):
    mask = np.zeros(candidate_image.shape[:2], dtype=np.uint8)
    for region in regions:
        mask[region.y0 : region.y1, region.x0 : region.x1] = 255
    alignment = AlignmentResult(
        aligned_reference=reference_image,
        matrix=np.eye(3, dtype=np.float64),
        method="deterministic_test_identity",
        quality=1.0,
        match_count=100,
        inlier_ratio=1.0,
        reprojection_error=0.0,
    )
    differences = DifferenceResult(
        mask=mask,
        intensity_delta=mask.copy(),
        regions=regions,
        global_changed_ratio=float(np.count_nonzero(mask) / mask.size),
        global_mean_delta=0.0,
    )
    return manager._build_findings(
        job_id,
        assets_dir,
        1,
        candidate_image,
        alignment,
        differences,
        reference_text,
        candidate_text,
        mode,
        single_page=True,
    )
